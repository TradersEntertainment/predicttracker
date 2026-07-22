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
# Ultra-Fast 1-second polling frequency
POLL_INTERVAL = 1

scanner_state = {
    "last_scan_time": 0,
    "scans_count": 0,
    "last_trade_time": 0,
    "last_trade_info": None,
    "status": "starting"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://predict.fun",
    "Referer": "https://predict.fun/"
}

QUERY_GLOBAL_MATCHES = """
query GetGlobalMatchEvents {
  matchEventLog(pagination: { first: 50 }) {
    edges {
      node {
        timestamp
        transactionHash
        priceExecuted
        amountFilled
        quoteType
        account {
          address
          name
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
"""

QUERY_USER_ORDERS_LOG = """
query GetUserActivity($address: Address!) {
  account(address: $address) {
    address
    name
    ordersEventLog(pagination: { first: 20 }) {
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

def build_event_key(wallet: str, tx_hash: str, order_id: str, timestamp: str, price: str, amount: str) -> str:
    return f"{wallet.lower()}_{tx_hash or 'notx'}_{order_id or 'noorder'}_{timestamp or 'notime'}_{price or '0'}_{amount or '0'}"

def format_telegram_message(wallet: str, event_node: dict, nickname: str = None) -> str:
    order = event_node.get("order") or {}
    market = event_node.get("market") or {}
    outcome = event_node.get("outcome") or {}
    
    quote_type = str(event_node.get("quoteType") or order.get("quoteType") or "").upper()
    if quote_type in ["BID", "BUY"]:
        side = "BUY"
    elif quote_type in ["ASK", "SELL"]:
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

async def fetch_global_matches(session: aiohttp.ClientSession):
    payload = {"query": QUERY_GLOBAL_MATCHES}
    try:
        async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                edges = (data.get("data") or {}).get("matchEventLog", {}).get("edges") or []
                return [e.get("node") for e in edges if isinstance(e, dict) and e.get("node")]
    except Exception as e:
        logger.error(f"Fetch global matches error: {e}")
    return []

async def fetch_user_events(session: aiohttp.ClientSession, address: str):
    payload = {"query": QUERY_USER_ORDERS_LOG, "variables": {"address": address}}
    try:
        async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                account = (data.get("data") or {}).get("account") or {}
                edges = (account.get("ordersEventLog") or {}).get("edges") or []
                return [e.get("node") for e in edges if isinstance(e, dict) and e.get("node")]
    except Exception as e:
        logger.error(f"Fetch user events error for {address}: {e}")
    return []

async def process_global_matches(session: aiohttp.ClientSession, tracked_map: dict, is_first_run: bool = False):
    matches = await fetch_global_matches(session)
    if not matches:
        return
        
    db_records = []
    for node in reversed(matches):
        account_info = node.get("account") or {}
        addr = account_info.get("address", "").lower()
        if addr in tracked_map:
            whale = tracked_map[addr]
            nickname = whale.get("name")
            tx_hash = node.get("transactionHash") or ""
            price = str(node.get("priceExecuted") or "0")
            amount = str(node.get("amountFilled") or "0")
            timestamp = str(node.get("timestamp") or "")
            event_key = build_event_key(addr, tx_hash, "match", timestamp, price, amount)
            
            if is_activity_seen_fast(addr, event_key):
                continue
                
            if is_first_run:
                mark_activity_seen_fast(addr, event_key)
                db_records.append((addr, event_key, timestamp))
                continue
                
            logger.info(f"⚡ [ULTRA-FAST MATCH] New trade for {nickname} ({addr[:6]}...)")
            scanner_state["last_trade_time"] = time.time()
            scanner_state["last_trade_info"] = f"{nickname}: {tx_hash[:10]}"
            try:
                msg = format_telegram_message(addr, node, nickname)
                await send_notification(msg, chat_id=whale.get("chat_id"))
                mark_activity_seen_fast(addr, event_key)
                db_records.append((addr, event_key, timestamp))
            except Exception as e:
                logger.error(f"Error processing global match trade {tx_hash}: {e}")
                
    if db_records:
        await batch_record_activities(db_records)

async def process_wallet(session: aiohttp.ClientSession, whale: dict, is_first_run: bool = False):
    address = whale["address"]
    nickname = whale.get("name")
    
    events = await fetch_user_events(session, address)
    if not events:
        return
        
    db_records = []
    for ev in reversed(events):
        order = ev.get("order") or {}
        order_id = str(order.get("id") or "")
        tx_hash = str(ev.get("transactionHash") or "")
        price = str(ev.get("priceExecuted") or "")
        amount = str(ev.get("amountFilled") or "")
        timestamp = str(ev.get("timestamp") or "")
        event_key = build_event_key(address, tx_hash, order_id, timestamp, price, amount)
        
        if is_activity_seen_fast(address, event_key):
            continue
            
        if is_first_run:
            mark_activity_seen_fast(address, event_key)
            db_records.append((address, event_key, timestamp))
            continue
            
        logger.info(f"⚡ [ULTRA-FAST EVENT] New event for {nickname} ({address[:6]}...)")
        scanner_state["last_trade_time"] = time.time()
        scanner_state["last_trade_info"] = f"{nickname}: {order_id}"
        try:
            msg = format_telegram_message(address, ev, nickname)
            await send_notification(msg, chat_id=whale.get("chat_id"))
            mark_activity_seen_fast(address, event_key)
            db_records.append((address, event_key, timestamp))
        except Exception as e:
            logger.error(f"Error processing user event {event_key}: {e}")
            
    if db_records:
        await batch_record_activities(db_records)

async def tracker_loop():
    logger.info("Predict.fun Ultra-Fast 1s Tracker loop started")
    count = await load_seen_cache()
    logger.info(f"📦 Loaded {count} seen tx hashes into memory cache")
    scanner_state["status"] = "active"
    
    # Optimized TCP Connector with keepalive and nodelay
    connector = aiohttp.TCPConnector(limit=100, enable_cleanup_closed=True, ttl_dns_cache=300, keepalive_timeout=60)
    timeout = aiohttp.ClientTimeout(total=5)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=HEADERS) as session:
        is_first_run = (count == 0)
        try:
            whales = await get_whales_cached()
            active_whales = [w for w in whales if w.get("status", "tracking") == "tracking"]
            tracked_map = {w["address"].lower(): w for w in active_whales}
            
            if active_whales:
                await process_global_matches(session, tracked_map, is_first_run=is_first_run)
                for w in active_whales:
                    await process_wallet(session, w, is_first_run=is_first_run)
                await send_notification(f"⚡ <b>ULTRA-FAST PREDICT TRACKER AKTİF!</b>\n🐋 {len(active_whales)} balina takipte\n⏱ Tarama hızı: <b>Her 1 saniyede bir (Ultra-Fast)</b>\n🔥 Anlık salise hızında bildirim gönderimi aktif!")
            else:
                await send_notification("✅ <b>PREDICT TRACKER BAŞARIYLA BAŞLATILDI!</b>\nLütfen takip için aktif bir cüzdan adresi ekleyin.")
        except Exception as e:
            logger.error(f"Startup check failed: {e}")
            await send_notification(f"⚠️ <b>PREDICT TRACKER BAŞLATILDI ANCAK GRAPHQL BAĞLANTISINDA SORUN VAR!</b>\n{e}")
        
        while True:
            try:
                scanner_state["last_scan_time"] = time.time()
                scanner_state["scans_count"] += 1
                
                whales = await get_whales_cached()
                active_whales = [w for w in whales if w.get("status", "tracking") == "tracking"]
                if not active_whales:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                tracked_map = {w["address"].lower(): w for w in active_whales}
                
                # Parallel Engine Execution (Concurrently fetch global matches & user events in parallel!)
                tasks = [process_global_matches(session, tracked_map, is_first_run=False)]
                for whale in active_whales:
                    tasks.append(process_wallet(session, whale, is_first_run=False))
                    
                await asyncio.gather(*tasks, return_exceptions=True)
                
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                scanner_state["status"] = "stopped"
                break
            except Exception as e:
                logger.error(f"Tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
