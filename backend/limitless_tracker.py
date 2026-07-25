import asyncio
import time
import aiohttp
import logging
from database import get_limitless_wallets_db, is_activity_seen, record_activity
from bot_engine import send_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 2.0
BLOCKSCOUT_API_BASE = "https://base.blockscout.com/api/v2"
LIMITLESS_API_BASE = "https://api.limitless.exchange"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

_market_cache = {}
_first_run_wallets = set()

# ==========================================
# LIMITLESS ORDERBOOK WALL TRACKER (5M BTC)
# ==========================================
ORDERBOOK_POLL_INTERVAL = 0.8
ORDERBOOK_MIN_CONTRACTS = 8000
ORDERBOOK_PRICE_MIN = 0.02
ORDERBOOK_PRICE_MAX = 0.98
_ob_seen_walls = {}


def get_current_5m_slug():
    now = int(time.time())
    current_slot = now - (now % 300)
    return f"btc-up-or-down-5-min-{current_slot}", current_slot


def format_limitless_wall_message(slug, side, price, contracts, total_usd, market_url):
    price_pct = int(price * 100)
    if side == "BUY":
        side_emoji = "\U0001f7e2"
        direction = "UP"
    else:
        side_emoji = "\U0001f534"
        direction = "DOWN"

    msg = "\U0001f6a8\U0001f6a8\U0001f6a8 <b>\u00d6NEML\u0130! LIMITLESS BAL\u0130NA EM\u0130R!</b> \U0001f6a8\U0001f6a8\U0001f6a8\n\n"
    msg += f"{side_emoji} <b>{side} Emri - {direction}</b>\n"
    msg += f"\U0001f4e6 Kontrat: <b>{contracts:,.0f}</b>\n"
    msg += f"\U0001f4ca Fiyat: <b>{price_pct}c ({price:.3f})</b>\n"
    msg += f"\U0001f4b0 Toplam: <b>${total_usd:,.2f}</b>\n"
    msg += f"\U0001f3af Market: <b>BTC 5 Dakika</b>\n"
    msg += f"\n\U0001f517 <a href='{market_url}'>Limitless Market</a>"
    return msg


async def scan_limitless_orderbook(session):
    slug, slot_ts = get_current_5m_slug()
    now = int(time.time())

    time_in_slot = now - slot_ts
    if time_in_slot > 330:
        return

    url = f"{LIMITLESS_API_BASE}/markets/{slug}/orderbook"
    try:
        async with session.get(url, timeout=4) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
    except Exception as e:
        logger.error(f"Limitless OB fetch error for {slug}: {e}")
        return

    bids = data.get("bids") or []
    asks = data.get("asks") or []
    market_url = f"https://limitless.exchange/markets/{slug}"

    all_orders = []
    for order in bids:
        all_orders.append(("BUY", order.get("price", 0), order.get("size", 0)))
    for order in asks:
        all_orders.append(("SELL", order.get("price", 0), order.get("size", 0)))

    for side, price, raw_size in all_orders:
        contracts = raw_size / 1e6

        if price < ORDERBOOK_PRICE_MIN or price > ORDERBOOK_PRICE_MAX:
            continue

        if contracts < ORDERBOOK_MIN_CONTRACTS:
            continue

        wall_key = f"{slug}_{price}_{side}"
        if wall_key in _ob_seen_walls:
            old_size = _ob_seen_walls[wall_key]
            if contracts <= old_size * 1.2:
                continue

        _ob_seen_walls[wall_key] = contracts

        logger.info(f"\U0001f6a8 [LIMITLESS OB WALL] {side} {contracts:,.0f} contracts @ {price} | {slug}")
        total_usd = contracts * price
        msg = format_limitless_wall_message(slug, side, price, contracts, total_usd, market_url)
        await send_notification(msg)

    expired_keys = [k for k in _ob_seen_walls if slug not in k]
    for k in expired_keys:
        del _ob_seen_walls[k]


# ==========================================
# LIMITLESS WALLET TRACKER (ON-CHAIN)
# ==========================================

async def fetch_limitless_market(session, condition_id):
    if not condition_id:
        return {"title": "BTC Up or Down - 5 Min"}
    cid_lower = condition_id.lower()
    if cid_lower in _market_cache:
        return _market_cache[cid_lower]

    try:
        url = f"{LIMITLESS_API_BASE}/markets/search?query={cid_lower}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                markets = data.get("markets") or []
                for m in markets:
                    if (m.get("conditionId") or "").lower() == cid_lower:
                        _market_cache[cid_lower] = m
                        return m
    except Exception as e:
        logger.error(f"Error searching Limitless market for conditionId {cid_lower}: {e}")

    # Fallback for 5M BTC markets when historical search is settled
    default_m = {"title": "BTC Up or Down - 5 Min"}
    _market_cache[cid_lower] = default_m
    return default_m


def format_limitless_message(wallet_name, wallet_address, tx_hash, method, market_title, amount_str, chat_id=None):
    short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    if "redeem" in method.lower():
        method_display = "Redeem Position \U0001f3af"
    else:
        method_display = method

    msg = "\U0001f300 <b>LIMITLESS EXCHANGE BAL\u0130NA \u0130\u015eLEM\u0130!</b>\n\n"
    msg += f"\U0001f464 Balina: <b>{wallet_name}</b> (<code>{short_addr}</code>)\n"
    if market_title:
        msg += f"\U0001f4ca Market: <b>{market_title}</b>\n"
    msg += f"\u26a1 \u0130\u015flem: <b>{method_display}</b>\n"
    if amount_str:
        msg += f"\U0001f4b0 Tutar: <b>{amount_str}</b>\n"
    msg += f"\n\U0001f517 <a href='https://limitless.exchange/profile/{wallet_address}'>Limitless Profil</a> | <a href='https://basescan.org/tx/{tx_hash}'>Basescan</a>"
    return msg


async def process_wallet_transactions(session, wallet):
    address = wallet["address"].lower()
    wallet_name = wallet.get("name") or "Limitless Balina"
    custom_chat_id = wallet.get("chat_id")

    is_initial_run = address not in _first_run_wallets

    url = f"{BLOCKSCOUT_API_BASE}/addresses/{address}/transactions"
    try:
        async with session.get(url, timeout=6) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            items = data.get("items") or []

            for tx in reversed(items):
                tx_hash = tx.get("hash")
                if not tx_hash:
                    continue

                seen = await is_activity_seen(address, tx_hash)
                if seen:
                    continue

                await record_activity(address, tx_hash, str(tx.get("timestamp") or ""))

                if is_initial_run:
                    continue

                method = tx.get("method") or "Contract Call"
                market_title = ""
                amount_str = ""

                try:
                    url_logs = f"{BLOCKSCOUT_API_BASE}/transactions/{tx_hash}/logs"
                    async with session.get(url_logs, timeout=5) as log_resp:
                        if log_resp.status == 200:
                            log_data = await log_resp.json()
                            log_items = log_data.get("items") or []
                            for log_item in log_items:
                                decoded = log_item.get("decoded") or {}
                                params = decoded.get("parameters") or []
                                for p in params:
                                    p_name = p.get("name")
                                    p_val = p.get("value")
                                    if p_name == "conditionId" and p_val:
                                        m_info = await fetch_limitless_market(session, str(p_val))
                                        if m_info:
                                            market_title = m_info.get("title") or m_info.get("description") or ""
                                    elif p_name in ["payout", "value"] and p_val:
                                        try:
                                            val_num = float(p_val)
                                            if val_num > 1000:
                                                usdc_val = val_num / 1e6
                                                if 0.1 <= usdc_val <= 1e7:
                                                    amount_str = f"~${usdc_val:,.2f} USDC"
                                        except Exception:
                                            pass
                except Exception as e:
                    logger.error(f"Error fetching tx logs for Limitless tx {tx_hash}: {e}")

                logger.info(f"\U0001f300 [LIMITLESS ALERT] {wallet_name} | Tx: {tx_hash[:10]} | Method: {method} | Market: {market_title}")
                msg = format_limitless_message(wallet_name, address, tx_hash, method, market_title, amount_str, custom_chat_id)
                await send_notification(msg, chat_id=custom_chat_id)

            _first_run_wallets.add(address)

    except Exception as e:
        logger.error(f"Error processing Limitless wallet {address}: {e}")


async def limitless_tracker_loop():
    """Ana Limitless tracker dongusu - hem wallet hem orderbook tarar"""
    logger.info("Limitless Exchange Tracker loop started (wallet + orderbook)")
    connector = aiohttp.TCPConnector(limit=50, enable_cleanup_closed=True, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=8)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=HEADERS) as session:
        cycle = 0
        while True:
            try:
                await scan_limitless_orderbook(session)

                if cycle % 3 == 0:
                    wallets = await get_limitless_wallets_db()
                    active_wallets = [w for w in wallets if w.get("status", "tracking") == "tracking"]
                    for wallet in active_wallets:
                        await process_wallet_transactions(session, wallet)

                cycle += 1
                await asyncio.sleep(ORDERBOOK_POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Limitless tracker loop error: {e}")
                await asyncio.sleep(ORDERBOOK_POLL_INTERVAL)
