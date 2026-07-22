import asyncio
import time
import aiohttp
import logging
from datetime import datetime
from database import (
    get_whales, is_activity_seen, record_activity,
    load_seen_cache, is_activity_seen_fast, mark_activity_seen_fast,
    batch_record_activities, get_whales_cached
)
from bot_engine import send_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PREDICT_GRAPHQL_URL = "https://graphql.predict.fun/graphql"
POLL_INTERVAL = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://predict.fun",
    "Referer": "https://predict.fun/"
}

GRAPHQL_QUERY = """
query GetUserActivity($address: Address!) {
  account(address: $address) {
    address
    name
    ordersEventLog {
      edges {
        node {
          event
          timestamp
          transactionHash
          amountFilled
          priceExecuted
          order {
            id
            amount
            quoteType
            status
          }
          market {
            id
            title
            question
          }
          outcome {
            index
            name
          }
        }
      }
    }
  }
}
"""

def format_telegram_message(wallet: str, event_node: dict, nickname: str = None) -> str:
    order = event_node.get("order") or {}
    market = event_node.get("market") or {}
    outcome = event_node.get("outcome") or {}
    
    quote_type = str(order.get("quoteType") or "").upper()
    if quote_type == "BID":
        side = "BUY"
    elif quote_type == "ASK":
        side = "SELL"
    else:
        side = quote_type or "TRADE"

    raw_amount = float(event_node.get("amountFilled") or 0)
    raw_price = float(event_node.get("priceExecuted") or 0)
    
    shares = raw_amount / 1e18 if raw_amount > 1e12 else raw_amount
    price = raw_price / 1e18 if raw_price > 1e12 else raw_price
    total_spent = round(shares * price, 2)
    
    title = str(market.get("title") or market.get("question") or "Predict Market")
    outcome_name = str(outcome.get("name") or "Outcome")
    
    if side == "BUY":
        emoji = "🟢"
    elif side == "SELL":
        emoji = "🔴"
    else:
        emoji = "🔵"
        
    display_title = title
    if len(display_title) > 80:
        display_title = display_title[:77] + "..."

    name_display = f"{nickname}" if nickname else f"{wallet[:6]}...{wallet[-4:]}"
    
    timestamp = event_node.get("timestamp", "")
    time_str = ""
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d, %I:%M %p")
        except Exception:
            time_str = str(timestamp)[:19]
            
    tx_hash = event_node.get("transactionHash") or ""
    tx_link = f"https://bscscan.com/tx/{tx_hash}" if tx_hash else f"https://predict.fun/portfolio/{wallet}"
    profile_link = f"https://predict.fun/portfolio/{wallet}"

    msg = f"{emoji} <b>{side}</b> ${total_spent:.2f} | <b>{outcome_name.upper()}</b> | 💰 ${price:.3f}\n"
    msg += f"📊 <b>{display_title}</b>\n"
    msg += f"📦 Adet: {shares:,.2f} Shares\n"
    msg += f"👤 <a href='{profile_link}'>{name_display}</a>"
    if time_str:
        msg += f" | ⏰ {time_str}"
    if tx_hash:
        msg += f"\n🔗 <a href='{tx_link}'>BscScan Tx</a>"

    return msg

async def fetch_user_events(session: aiohttp.ClientSession, address: str):
    payload = {
        "query": GRAPHQL_QUERY,
        "variables": {"address": address}
    }
    try:
        async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                data_obj = data.get("data") or {}
                account = data_obj.get("account") or {}
                orders_log = account.get("ordersEventLog") or {}
                edges = orders_log.get("edges") or []
                events = []
                for edge in edges:
                    if isinstance(edge, dict):
                        node = edge.get("node")
                        if node and isinstance(node, dict) and node.get("transactionHash"):
                            events.append(node)
                return events
            else:
                logger.error(f"GraphQL returned HTTP {response.status} for {address}")
                return None
    except Exception as e:
        logger.error(f"Fetch error for {address}: {e}")
        return None

async def process_wallet(session: aiohttp.ClientSession, whale: dict):
    address = whale['address']
    nickname = whale.get('name')
    
    events = await fetch_user_events(session, address)
    if not events:
        return
    
    new_events = []
    for ev in events:
        tx_hash = ev.get("transactionHash")
        if not tx_hash:
            continue
        if is_activity_seen_fast(address, tx_hash):
            continue
        new_events.append(ev)
        
    if not new_events:
        return
        
    logger.info(f"⚡ {len(new_events)} new events for {nickname} ({address[:6]}...)")
    
    db_records = []
    for ev in reversed(new_events):
        try:
            tx_hash = ev.get("transactionHash")
            msg = format_telegram_message(address, ev, nickname)
            await send_notification(msg, chat_id=whale.get('chat_id'))
            mark_activity_seen_fast(address, tx_hash)
            db_records.append((address, tx_hash, ev.get("timestamp")))
        except Exception as e:
            logger.error(f"Error processing trade {ev.get('transactionHash')}: {e}")
            
    await batch_record_activities(db_records)

async def tracker_loop():
    logger.info("Predict.fun Tracker loop started")
    count = await load_seen_cache()
    logger.info(f"📦 Loaded {count} seen tx hashes into memory cache")
    
    connector = aiohttp.TCPConnector(limit=50, enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=10)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=HEADERS) as session:
        try:
            whales = await get_whales_cached()
            active_whales = [w for w in whales if w.get('status', 'tracking') == 'tracking']
            if active_whales:
                first_whale = active_whales[0]
                events = await fetch_user_events(session, first_whale['address'])
                if events and len(events) > 0:
                    last_ev = events[0]
                    msg = format_telegram_message(first_whale['address'], last_ev, first_whale.get('name'))
                    await send_notification(f"✅ <b>PREDICT TRACKER BAŞARIYLA BAŞLATILDI!</b>\n🐋 {len(active_whales)} balina takipte\n📦 {count} kayıtlı işlem hafızada\n⏱ Tarama aralığı: {POLL_INTERVAL}s\n\nSon işlem örneği:\n{msg}")
                else:
                    await send_notification(f"✅ <b>PREDICT TRACKER BAŞARIYLA BAŞLATILDI!</b>\n🐋 {len(active_whales)} balina takipte\nBağlantı başarılı ancak henüz işlem bulunamadı.")
            else:
                await send_notification("✅ <b>PREDICT TRACKER BAŞARIYLA BAŞLATILDI!</b>\nLütfen takip için aktif bir cüzdan adresi ekleyin.")
        except Exception as e:
            logger.error(f"Startup check failed: {e}")
            await send_notification(f"⚠️ <b>PREDICT TRACKER BAŞLATILDI ANCAK GRAPHQL BAĞLANTISINDA SORUN VAR!</b>\n{e}")
        
        while True:
            try:
                whales = await get_whales_cached()
                active_whales = [w for w in whales if w.get('status', 'tracking') == 'tracking']
                if not active_whales:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                tasks = [process_wallet(session, whale) for whale in active_whales]
                await asyncio.gather(*tasks, return_exceptions=True)
                
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
