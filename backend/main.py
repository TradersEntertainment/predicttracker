import asyncio
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import (
    init_db, get_whales, add_whale, remove_whale, 
    reactivate_whale, delete_whale_permanently, invalidate_whales_cache,
    get_orderbook_monitors_db, add_orderbook_monitor_db, delete_orderbook_monitor_db
)
from tracker import tracker_loop, scanner_state
from balance_tracker import balance_tracker_loop, get_all_balances
from orderbook_tracker import orderbook_tracker_loop, format_orderbook_message
from limitless_tracker import limitless_tracker_loop, format_limitless_message
from database import get_limitless_wallets_db, add_limitless_wallet_db, delete_limitless_wallet_db
from bot_engine import send_notification, start_bot_polling

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Predict Whale & Orderbook Tracker...")
    await init_db()
    
    tracker_task = asyncio.create_task(tracker_loop())
    balance_task = asyncio.create_task(balance_tracker_loop())
    orderbook_task = asyncio.create_task(orderbook_tracker_loop())
    limitless_task = asyncio.create_task(limitless_tracker_loop())
    bot_task = asyncio.create_task(start_bot_polling())
    logger.info("All tracker loops and Telegram Bot Polling started!")
    
    yield
    
    logger.info("Shutting down Predict Whale & Orderbook Tracker...")
    tracker_task.cancel()
    balance_task.cancel()
    orderbook_task.cancel()
    limitless_task.cancel()
    bot_task.cancel()
    try:
        await tracker_task
        await balance_task
        await orderbook_task
        await limitless_task
        await bot_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Predict Whale & Orderbook Tracker API", lifespan=lifespan)

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

class OrderbookMonitorCreate(BaseModel):
    name: str
    market_id: Optional[str] = None
    min_shares: float = 2000.0
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

@app.get("/api/orderbook_monitors")
async def api_get_orderbook_monitors():
    try:
        monitors = await get_orderbook_monitors_db()
        return {"monitors": monitors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/orderbook_monitors")
async def api_add_orderbook_monitor(mon: OrderbookMonitorCreate):
    try:
        monitor_id = await add_orderbook_monitor_db(mon.name, mon.market_id, mon.min_shares, mon.chat_id)
        return {"success": True, "id": monitor_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/orderbook_monitors/{monitor_id}")
async def api_delete_orderbook_monitor(monitor_id: str):
    try:
        await delete_orderbook_monitor_db(monitor_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/test_orderbook_telegram")
async def api_test_orderbook_telegram(payload: dict = None):
    try:
        chat_id = (payload or {}).get("chat_id")
        min_shares = float((payload or {}).get("min_shares") or 2000.0)
        msg = format_orderbook_message(
            "Bitcoin Up or Down - 5 Minute (TEST)",
            "btc-updown-5m-1784715000",
            "BUY (BID)",
            0.550,
            min_shares,
            min_shares
        )
        await send_notification(msg, chat_id=chat_id)
        return {"success": True, "message": "Orderbook test sent successfully"}
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

@app.get("/api/scanner_status")
async def api_scanner_status():
    import time
    last_scan = scanner_state.get("last_scan_time", 0)
    seconds_ago = int(time.time() - last_scan) if last_scan > 0 else -1
    return {
        "status": scanner_state.get("status", "unknown"),
        "last_scan_time": last_scan,
        "seconds_since_last_scan": seconds_ago,
        "total_scans_count": scanner_state.get("scans_count", 0),
        "last_trade_info": scanner_state.get("last_trade_info")
    }

@app.post("/api/test_telegram")
async def api_test_telegram(payload: dict = None):
    try:
        from tracker import fetch_user_events, format_telegram_message
        import aiohttp
        
        address = (payload or {}).get("address") or "0x17C99cd6ca9032910de5ccFA2a2FeBCc22319A86"
        chat_id = (payload or {}).get("chat_id")
        nickname = (payload or {}).get("name") or "Predict Balina 1"
        
        async with aiohttp.ClientSession() as session:
            events = await fetch_user_events(session, address)
            if events and len(events) > 0:
                last_ev = events[0]
                msg = "🧪 <b>PREDICT TRACKER TEST BİLDİRİMİ (Gerçek İşlem)</b>\n" + format_telegram_message(address, last_ev, nickname)
            else:
                msg = (
                    f"🧪 <b>PREDICT TRACKER TEST BİLDİRİMİ</b>\n"
                    f"🟢 <b>BUY</b> $1.05 | <b>DOWN</b> | 💰 $0.500\n"
                    f"📊 <b>Bitcoin Up or Down - July 21, 9:25PM-9:30PM ET</b>\n"
                    f"📦 Adet: 2.10 Shares\n"
                    f"👤 <a href='https://predict.fun/portfolio/{address}'>{nickname}</a> | ⏰ Jul 22, 01:25 AM\n"
                    f"🔗 <a href='https://bscscan.com/address/{address}'>BscScan Tx</a>"
                )
            await send_notification(msg, chat_id=chat_id)
            return {"success": True, "message": "Test notification sent successfully"}
    except Exception as e:
        logger.error(f"Test notification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/whales/{address}/test")
async def api_test_whale_telegram(address: str):
    try:
        whales = await get_whales()
        matching = [w for w in whales if w.get("address", "").lower() == address.lower()]
        whale = matching[0] if matching else {"address": address, "name": "Predict Balina 1"}
        
        return await api_test_telegram({"address": whale["address"], "name": whale.get("name"), "chat_id": whale.get("chat_id")})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = os.path.join(frontend_dist, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")

# Trigger fresh Railway deploy
