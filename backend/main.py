import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import (
    init_db, get_whales, add_whale, remove_whale, 
    reactivate_whale, delete_whale_permanently, invalidate_whales_cache
)
from tracker import tracker_loop
from balance_tracker import balance_tracker_loop, get_all_balances

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Predict Whale Tracker...")
    await init_db()
    
    tracker_task = asyncio.create_task(tracker_loop())
    balance_task = asyncio.create_task(balance_tracker_loop())
    
    yield
    
    logger.info("Shutting down Predict Whale Tracker...")
    tracker_task.cancel()
    balance_task.cancel()
    try:
        await tracker_task
        await balance_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Predict Whale Tracker API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class WhaleCreate(BaseModel):
    address: str
    name: str
    chat_id: Optional[str] = None

@app.get("/api/whales")
async def api_get_whales():
    try:
        whales = await get_whales()
        return {"whales": whales}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/whales")
async def api_add_whale(whale: WhaleCreate):
    try:
        success = await add_whale(whale.address, whale.name, whale.chat_id)
        if success:
            invalidate_whales_cache()
            return {"success": True, "message": "Whale added successfully"}
        else:
            raise HTTPException(status_code=400, detail="Whale already exists")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/whales/{address}")
async def api_remove_whale(address: str):
    try:
        success = await remove_whale(address)
        invalidate_whales_cache()
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/whales/{address}/reactivate")
async def api_reactivate_whale(address: str):
    try:
        success = await reactivate_whale(address)
        invalidate_whales_cache()
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/whales/{address}/permanent")
async def api_permanent_remove_whale(address: str):
    try:
        success = await delete_whale_permanently(address)
        invalidate_whales_cache()
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/balances")
async def api_get_balances():
    try:
        balances = get_all_balances()
        return {"balances": balances}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
