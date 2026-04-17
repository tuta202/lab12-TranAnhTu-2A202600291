import json
import logging
import time
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import settings
from .auth import verify_api_key
from .rate_limiter import check_rate_limit, r
from .cost_guard import check_budget
# mock_llm.py was copied to app/utils/mock_llm.py
from .utils.mock_llm import ask

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# State management for graceful shutdown
_is_ready = False
_in_flight_requests = 0

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info("Starting up application...")
    # Simulate loading model / warming up
    await asyncio.sleep(0.5)
    _is_ready = True
    logger.info("Application is ready!")
    
    yield
    
    # Graceful shutdown
    _is_ready = False
    logger.info("Shutting down... waiting for in-flight requests")
    shutdown_timeout = 30
    elapsed = 0
    while _in_flight_requests > 0 and elapsed < shutdown_timeout:
        await asyncio.sleep(1)
        elapsed += 1
    logger.info("Shutdown complete.")

app = FastAPI(title="Production Ready AI Agent", lifespan=lifespan)

@app.middleware("http")
async def track_requests(request, call_next):
    global _in_flight_requests
    _in_flight_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        _in_flight_requests -= 1

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/ready")
def ready():
    if not _is_ready:
        return JSONResponse(status_code=503, content={"status": "not ready"})
    try:
        r.ping()
        return {"status": "ready"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": f"not ready: {str(e)}"})

class AskRequest(BaseModel):
    question: str
    user_id: str
    session_id: str | None = None

@app.post("/ask")
def ask_endpoint(
    body: AskRequest,
    api_key_user: str = Depends(verify_api_key)
    # the auth checking user is not necessarily the request 'user_id' in a generic app,
    # but let's strictly check by the one in payload
):
    if not _is_ready:
        raise HTTPException(status_code=503, detail="Service unavailable.")
        
    check_rate_limit(body.user_id)
    check_budget(body.user_id)
    
    session_id = body.session_id or f"sess_{body.user_id}_{int(time.time())}"
    history_key = f"history:{session_id}"
    
    # Simple history retrieval
    history_json = r.lrange(history_key, 0, -1)
    history = [json.loads(h) for h in history_json]
    
    # Append user question
    r.rpush(history_key, json.dumps({"role": "user", "content": body.question}))
    
    # In reality, pass history to LLM... here just use mock
    answer = ask(body.question)
    
    # Append assistant response
    r.rpush(history_key, json.dumps({"role": "assistant", "content": answer}))
    # Retain for 1 hour
    r.expire(history_key, 3600)
    
    return {
        "session_id": session_id,
        "question": body.question,
        "answer": answer,
        "history_length": len(history) + 2
    }
