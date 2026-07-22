import asyncio
import time
import aiohttp
import logging
from database import get_orderbook_monitors_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PREDICT_GRAPHQL_URL = "https://graphql.predict.fun/graphql"
POLL_INTERVAL = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://predict.fun",
    "Referer": "https://predict.fun/"
}

QUERY_MARKETS_ORDERBOOK = """
query GetMarketsOrderbook {
  markets(pagination: { first: 50 }) {
    edges {
      node {
        id
        title
        question
        isTradingEnabled
        status
        orderbook {
          marketId
          updateTimestampMs
          bids
          asks
        }
      }
    }
  }
}
"""

_seen_walls = {}
_WALL_EXPIRY_SECONDS = 120

def clean_expired_walls():
    now = time.time()
    expired = [k for k, t in _seen_walls.items() if now - t > _WALL_EXPIRY_SECONDS]
    for k in expired:
        del _seen_walls[k]

def is_wall_seen(wall_key: str) -> bool:
    clean_expired_walls()
    return wall_key in _seen_walls

def mark_wall_seen(wall_key: str):
    _seen_walls[wall_key] = time.time()

def format_orderbook_message(market_title: str, market_id: str, side: str, price: float, shares: float, min_shares: float) -> str:
    usd_val = round(shares * price, 2)
    emoji = "🟢" if side == "BUY (BID)" else "🔴"
    
    msg = f"🧱 <b>PREDICT ORDERBOOK LİKİDİTE DUVARI TESPİT EDİLDİ!</b>\n\n"
    msg += f"📊 <b>{market_title}</b>\n"
    msg += f"{emoji} <b>{side}</b>\n"
    msg += f"💰 Fiyat: <b>${price:.3f}</b>\n"
    msg += f"📦 Miktar: <b>{shares:,.2f} Shares</b> (~${usd_val:,.2f})\n"
    msg += f"🎯 Eşik: <b>{min_shares:,.0f}+ Shares</b>\n\n"
    msg += f"🔗 <a href='https://predict.fun/market/{market_id}'>Predict.fun Market</a>"
    
    return msg

async def fetch_markets_orderbook(session: aiohttp.ClientSession):
    payload = {"query": QUERY_MARKETS_ORDERBOOK}
    try:
        async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                edges = (data.get("data") or {}).get("markets", {}).get("edges") or []
                return [e.get("node") for e in edges if isinstance(e, dict) and e.get("node")]
    except Exception as e:
        logger.error(f"Fetch markets orderbook error: {e}")
    return []

async def get_active_orderbook_monitors():
    try:
        monitors = await get_orderbook_monitors_db()
        active = [m for m in monitors if m.get("status", "active") == "active"]
        if active:
            return active
    except Exception as e:
        logger.error(f"Error fetching orderbook monitors: {e}")

    # Fallback to 100% automatic scanning mode out of the box (2000+ shares)
    return [{
        "id": "default_auto",
        "name": "Bitcoin 5M Likidite Duvarı (Otomatik)",
        "market_id": None,
        "min_shares": 2000.0,
        "chat_id": None
    }]

async def orderbook_tracker_loop():
    logger.info("Orderbook Liquidity Wall Tracker loop started")
    from bot_engine import send_notification
    
    connector = aiohttp.TCPConnector(limit=50, enable_cleanup_closed=True, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=5)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=HEADERS) as session:
        while True:
            try:
                monitors = await get_active_orderbook_monitors()
                if not monitors:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                markets = await fetch_markets_orderbook(session)
                if not markets:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                for monitor in monitors:
                    min_shares = monitor["min_shares"]
                    target_chat_id = monitor.get("chat_id")
                    target_market_id = (monitor.get("market_id") or "").strip().lower()

                    # Dynamic market selection:
                    # If market_id is empty or 'auto' -> Scan ALL active markets (includes live 5M & next 5M)!
                    # If market_id is '5m' or 'btc' -> Scan all 5M Bitcoin markets (current live & upcoming)!
                    # Otherwise -> Filter by specific market ID/slug keyword.
                    if not target_market_id or target_market_id == "auto":
                        target_markets = markets
                    elif target_market_id in ["5m", "btc", "5m_btc"]:
                        target_markets = [
                            m for m in markets 
                            if "5m" in (m.get("title") or "").lower() or "5m" in (m.get("question") or "").lower() or "bitcoin" in (m.get("title") or "").lower()
                        ]
                    else:
                        target_markets = [
                            m for m in markets 
                            if target_market_id in str(m.get("id")).lower() or target_market_id in str(m.get("title")).lower()
                        ]

                    for m in target_markets:
                        title = m.get("title") or m.get("question") or "Predict Market"
                        m_id = str(m.get("id"))
                        ob = m.get("orderbook") or {}
                        bids = ob.get("bids") or []
                        asks = ob.get("asks") or []

                        # Check Bids (Buy Walls) - Ignore extreme bond prices (< 0.05 or > 0.95)
                        for b in bids:
                            if isinstance(b, list) and len(b) >= 2:
                                price, shares = float(b[0]), float(b[1])
                                if price < 0.05 or price > 0.95:
                                    continue
                                if shares >= min_shares:
                                    wall_key = f"{m_id}_BID_{price:.3f}_{int(shares / 50)}"
                                    if not is_wall_seen(wall_key):
                                        mark_wall_seen(wall_key)
                                        logger.info(f"🧱 BID WALL: {shares} shares @ ${price} in {title}")
                                        msg = format_orderbook_message(title, m_id, "BUY (BID)", price, shares, min_shares)
                                        await send_notification(msg, chat_id=target_chat_id)

                        # Check Asks (Sell Walls) - Ignore extreme bond prices (< 0.05 or > 0.95)
                        for a in asks:
                            if isinstance(a, list) and len(a) >= 2:
                                price, shares = float(a[0]), float(a[1])
                                if price < 0.05 or price > 0.95:
                                    continue
                                if shares >= min_shares:
                                    wall_key = f"{m_id}_ASK_{price:.3f}_{int(shares / 50)}"
                                    if not is_wall_seen(wall_key):
                                        mark_wall_seen(wall_key)
                                        logger.info(f"🧱 ASK WALL: {shares} shares @ ${price} in {title}")
                                        msg = format_orderbook_message(title, m_id, "SELL (ASK)", price, shares, min_shares)
                                        await send_notification(msg, chat_id=target_chat_id)

                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Orderbook tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
