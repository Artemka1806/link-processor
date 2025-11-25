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

load_dotenv()

app = FastAPI(title="Link Processor")

SECRET_KEY = os.getenv("SECRET_KEY", "default-key-please-change")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# ---- NEW: список юзерів, які вже переходили ----
visited_states = set()

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
async def redirect(token: str, state: Optional[str] = None, background_tasks: BackgroundTasks = None):
    if state is None:
        raise HTTPException(status_code=400, detail="State is required")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        callback_url = payload.get("callback_url")
        seconds = payload.get("seconds")
        redirect_url = payload.get("redirect_url")

        if not all([callback_url, seconds, redirect_url]):
            raise HTTPException(status_code=400, detail="Invalid link parameters")

        # ---- NEW: перевірка повторного переходу ----
        if state in visited_states:
            logging.info(f"User {state} already visited — no callback will be scheduled.")
            return RedirectResponse(url=redirect_url)

        # Якщо перший перехід
        visited_states.add(state)

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
