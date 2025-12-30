from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl, validator
import jwt
from typing import Optional
import httpx
import asyncio
from datetime import datetime, timedelta
import uvicorn
import os
from dotenv import load_dotenv
import logging
import hashlib
from redis.asyncio import Redis, from_url

load_dotenv()

app = FastAPI(title="Link Processor")

SECRET_KEY = os.getenv("SECRET_KEY", "default-key-please-change")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
VISITED_TTL_SECONDS = int(os.getenv("VISITED_TTL_SECONDS", str(30 * 24 * 60 * 60)))

redis_client: Optional[Redis] = None


def visited_key(base_url: str, redirect_url: str) -> str:
    key_source = f"{base_url}|{redirect_url}"
    digest = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    return f"visited:{digest}"


@app.on_event("startup")
async def startup() -> None:
    global redis_client
    redis_client = from_url(REDIS_URL, decode_responses=True)


@app.on_event("shutdown")
async def shutdown() -> None:
    if redis_client is not None:
        await redis_client.close()

class LinkRequest(BaseModel):
    callback_url: HttpUrl
    seconds: int
    redirect_url: HttpUrl
    state: str

    @validator('seconds')
    def validate_seconds(cls, v):
        if v <= 0:
            raise ValueError("Seconds must be positive")
        if v > 3600:
            raise ValueError("Maximum delay is 3600 seconds (1 hour)")
        return v


def get_base_url(request: Request) -> str:
    headers = request.headers

    forwarded_proto = headers.get("x-forwarded-proto")
    forwarded_host = headers.get("x-forwarded-host")

    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"

    host = headers.get("host")
    if host:
        scheme = request.url.scheme
        return f"{scheme}://{host}"

    url = request.url
    if url.port:
        return f"{url.scheme}://{url.hostname}:{url.port}"

    return f"{url.scheme}://{url.hostname}"


async def send_callback(callback_url: str, state: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                callback_url,
                json={"state": state, "timestamp": datetime.now().isoformat()}
            )
            print(f"Callback sent to {callback_url} with state {state}, response: {response.status_code}")
            return response.status_code
        except Exception as e:
            print(f"Error sending callback: {e}")
            return None


@app.post("/create-link", status_code=201)
async def create_link(link_data: LinkRequest, request: Request):
    base_url = get_base_url(request)

    payload = {
        "callback_url": str(link_data.callback_url),
        "seconds": link_data.seconds,
        "redirect_url": str(link_data.redirect_url),
        "exp": datetime.utcnow() + timedelta(days=30)
    }

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    link = f"{base_url}/redirect/{token}?state={link_data.state}"

    return {"link": link}


@app.get("/redirect/{token}")
async def redirect(
    token: str,
    request: Request,
    state: Optional[str] = None,
    background_tasks: BackgroundTasks = None,
):
    if state is None:
        raise HTTPException(status_code=400, detail="State is required")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        callback_url = payload.get("callback_url")
        seconds = payload.get("seconds")
        redirect_url = payload.get("redirect_url")

        if not all([callback_url, seconds, redirect_url]):
            raise HTTPException(status_code=400, detail="Invalid link parameters")

        if redis_client is None:
            raise HTTPException(status_code=503, detail="Redis is not available")

        base_url = get_base_url(request)
        key = visited_key(base_url, redirect_url)
        already_visited = await redis_client.sismember(key, state)
        if already_visited:
            logging.info(f"User {state} already visited {redirect_url} — no callback will be scheduled.")
            return RedirectResponse(url=redirect_url)

        await redis_client.sadd(key, state)
        ttl = await redis_client.ttl(key)
        if ttl == -1:
            await redis_client.expire(key, VISITED_TTL_SECONDS)

        background_tasks.add_task(
            schedule_callback,
            callback_url=callback_url,
            state=state,
            delay=seconds
        )

        return RedirectResponse(url=redirect_url)

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Link has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid link")


async def schedule_callback(callback_url: str, state: str, delay: int):
    await asyncio.sleep(delay)
    await send_callback(callback_url, state)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
