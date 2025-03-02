from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl, validator
import jwt
from typing import Optional
import httpx
import asyncio
import time
from datetime import datetime, timedelta
import uvicorn
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Link Processor")

SECRET_KEY = os.getenv("SECRET_KEY", "default-key-please-change")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

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

async def send_callback(callback_url: str, state: str):
    """Send an HTTP POST request to the callback URL with the state."""
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
async def create_link(link_data: LinkRequest):
    """Create a link with the provided parameters."""
    payload = {
        "callback_url": str(link_data.callback_url),
        "seconds": link_data.seconds,
        "redirect_url": str(link_data.redirect_url),
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    link = f"{BASE_URL}/redirect/{token}?state={link_data.state}"
    
    return {"link": link}

@app.get("/redirect/{token}")
async def redirect(token: str, state: Optional[str] = None, background_tasks: BackgroundTasks = None):
    """Handle the redirect and schedule the callback."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        callback_url = payload.get("callback_url")
        seconds = payload.get("seconds")
        redirect_url = payload.get("redirect_url")
        
        if not all([callback_url, seconds, redirect_url]):
            raise HTTPException(status_code=400, detail="Invalid link parameters")
        
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
    """Schedule a callback after the specified delay."""
    await asyncio.sleep(delay)
    await send_callback(callback_url, state)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)