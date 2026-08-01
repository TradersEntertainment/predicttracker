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
        direction = "UP Al\u0131yor"
    else:
        side_emoji = "\U0001f534"
        direction = "DOWN Al\u0131yor"

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
# LIMITLESS WALLET TRACKER (TOKEN-TRANSFERS)
# ==========================================
USDC_BASE_ADDRESS = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
LIMITLESS_METHODS = {"matchOrders", "redeemPositions", "fillOrder", "fillLimitOrder"}


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

    default_m = {"title": "BTC Up or Down - 5 Min"}
    _market_cache[cid_lower] = default_m
    return default_m


def format_limitless_message(wallet_name, wallet_address, tx_hash, action_type, market_title, amount_str, direction="UP", chat_id=None):
    short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"

    if action_type == "BUY":
        if direction == "DOWN":
            action_emoji = "\U0001f534"
            action_display = "DOWN Al\u0131yor"
        else:
            action_emoji = "\U0001f7e2"
            action_display = "UP Al\u0131yor"
    elif action_type == "SELL":
        action_emoji = "\U0001f534"
        action_display = "Pozisyon Sat\u0131\u015f\u0131 (SELL)"
    elif action_type == "REDEEM":
        action_emoji = "\U0001f3af"
        action_display = "Redeem (Kazan\u00e7 \u00c7ekme)"
    else:
        action_emoji = "\u26a1"
        action_display = action_type

    msg = f"{action_emoji} <b>LIMITLESS BAL\u0130NA \u0130\u015eLEM\u0130!</b>\n\n"
    msg += f"\U0001f464 Balina: <b>{wallet_name}</b> (<code>{short_addr}</code>)\n"
    if market_title:
        msg += f"\U0001f4ca Market: <b>{market_title}</b>\n"
    msg += f"\u26a1 \u0130\u015flem: <b>{action_display}</b>\n"
    if amount_str:
        msg += f"\U0001f4b0 Tutar: <b>{amount_str}</b>\n"
    msg += f"\n\U0001f517 <a href='https://limitless.exchange/profile/{wallet_address}'>Limitless Profil</a> | <a href='https://basescan.org/tx/{tx_hash}'>Basescan</a>"
    return msg


def classify_token_transfers(transfers, whale_address):
    """
    Classify a group of token transfers (same tx_hash) as BUY, SELL, or REDEEM.
    Also return the bought token_id (ERC-1155).
    """
    whale = whale_address.lower()
    zero_addr = "0x0000000000000000000000000000000000000000"

    usdc_in = 0.0
    usdc_out = 0.0
    erc1155_in = False
    erc1155_out = False
    erc1155_burn = False
    bought_token_id = None

    for t in transfers:
        token = t.get("token") or {}
        token_type = token.get("type", "")
        token_addr = (token.get("address_hash") or token.get("address") or "").lower()
        from_addr = ((t.get("from") or {}).get("hash") or "").lower()
        to_addr = ((t.get("to") or {}).get("hash") or "").lower()
        total = t.get("total") or {}
        raw_value = total.get("value") or "0"

        if token_type == "ERC-20" and token_addr == USDC_BASE_ADDRESS:
            try:
                usdc_val = int(raw_value) / 1e6
            except (ValueError, TypeError):
                usdc_val = 0.0
            if from_addr == whale:
                usdc_out += usdc_val
            elif to_addr == whale:
                usdc_in += usdc_val

        elif token_type == "ERC-1155":
            tid = str(t.get("token_id") or total.get("token_id") or "")
            if to_addr == whale:
                erc1155_in = True
                if tid:
                    bought_token_id = tid
            elif from_addr == whale:
                if to_addr == zero_addr:
                    erc1155_burn = True
                else:
                    erc1155_out = True

    # Classify
    if erc1155_burn and usdc_in > 0:
        return "REDEEM", usdc_in, bought_token_id
    elif erc1155_in and usdc_out > 0:
        return "BUY", usdc_out, bought_token_id
    elif erc1155_out and usdc_in > 0:
        return "SELL", usdc_in, bought_token_id
    elif erc1155_in:
        return "BUY", usdc_out, bought_token_id
    elif erc1155_burn:
        return "REDEEM", usdc_in, bought_token_id
    elif erc1155_out:
        return "SELL", usdc_in, bought_token_id
    else:
        return "OTHER", max(usdc_in, usdc_out), bought_token_id


async def process_wallet_transactions(session, wallet):
    """
    Token-transfers tabanli wallet tracker.
    /token-transfers endpoint'i kullanarak hem matchOrders (BUY/SELL) hem redeemPositions yakalar.
    """
    address = wallet["address"].lower()
    wallet_name = wallet.get("name") or "Limitless Balina"
    custom_chat_id = wallet.get("chat_id")

    is_initial_run = address not in _first_run_wallets

    url = f"{BLOCKSCOUT_API_BASE}/addresses/{address}/token-transfers"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            items = data.get("items") or []

            # Group transfers by tx_hash
            by_tx = {}
            for tt in items:
                tx_hash = tt.get("transaction_hash") or tt.get("tx_hash")
                if not tx_hash:
                    continue
                if tx_hash not in by_tx:
                    by_tx[tx_hash] = {
                        "method": tt.get("method") or "unknown",
                        "timestamp": tt.get("timestamp") or "",
                        "transfers": []
                    }
                by_tx[tx_hash]["transfers"].append(tt)

            # Filter to only Limitless-relevant transactions
            for tx_hash, tx_info in by_tx.items():
                method = tx_info["method"]

                has_erc1155 = any(
                    (t.get("token") or {}).get("type") == "ERC-1155"
                    for t in tx_info["transfers"]
                )
                is_limitless_method = method in LIMITLESS_METHODS
                if not has_erc1155 and not is_limitless_method:
                    continue

                seen = await is_activity_seen(address, tx_hash)
                if seen:
                    continue

                await record_activity(address, tx_hash, tx_info["timestamp"])

                if is_initial_run:
                    continue

                # Classify this transaction
                action_type, usdc_amount, bought_token_id = classify_token_transfers(tx_info["transfers"], address)

                amount_str = ""
                if usdc_amount > 0.01:
                    amount_str = f"${usdc_amount:,.2f} USDC"

                # Try to find market title and outcome direction (UP vs DOWN)
                # Direction detection uses on-chain TransferBatch logs:
                # TransferBatch.ids[0] = YES/UP token, ids[1] = NO/DOWN token
                # TransferSingle TO whale = which token the whale bought
                market_title = ""
                direction = "UP"
                try:
                    url_logs = f"{BLOCKSCOUT_API_BASE}/transactions/{tx_hash}/logs"
                    async with session.get(url_logs, timeout=5) as log_resp:
                        if log_resp.status == 200:
                            log_data = await log_resp.json()
                            log_items = log_data.get("items") or []

                            # Step 1: Find conditionId for market title
                            for log_item in log_items:
                                decoded = log_item.get("decoded") or {}
                                params = decoded.get("parameters") or []
                                for p in params:
                                    if p.get("name") == "conditionId" and p.get("value"):
                                        m_info = await fetch_limitless_market(session, str(p["value"]))
                                        if m_info:
                                            market_title = m_info.get("title") or m_info.get("description") or ""
                                        break
                                if market_title:
                                    break

                            # Step 2: Detect UP vs DOWN from TransferBatch + TransferSingle
                            # TransferBatch (from PositionSplit): ids[0]=YES/UP, ids[1]=NO/DOWN
                            # TransferSingle TO whale: which outcome token the whale received
                            if action_type == "BUY":
                                outcome_ids = {}  # {token_id_str: outcome_index}
                                log_bought_tid = None

                                for log_item in log_items:
                                    decoded = log_item.get("decoded") or {}
                                    method_call = decoded.get("method_call") or ""
                                    params = decoded.get("parameters") or []

                                    if "TransferBatch" in method_call:
                                        for p in params:
                                            if p.get("name") == "ids":
                                                ids_val = p.get("value")
                                                if isinstance(ids_val, list) and len(ids_val) >= 2:
                                                    outcome_ids[str(ids_val[0])] = 0  # YES/UP
                                                    outcome_ids[str(ids_val[1])] = 1  # NO/DOWN

                                    if "TransferSingle" in method_call and not log_bought_tid:
                                        to_val = None
                                        id_val = None
                                        for p in params:
                                            if p.get("name") == "to":
                                                to_val = str(p.get("value") or "").lower()
                                            elif p.get("name") == "id":
                                                id_val = str(p.get("value") or "")
                                        if to_val == address and id_val:
                                            log_bought_tid = id_val

                                if outcome_ids and log_bought_tid and log_bought_tid in outcome_ids:
                                    idx = outcome_ids[log_bought_tid]
                                    direction = "UP" if idx == 0 else "DOWN"

                except Exception as e:
                    logger.error(f"Error fetching tx logs for Limitless tx {tx_hash}: {e}")

                logger.info(f"\U0001f300 [LIMITLESS] {wallet_name} | {action_type} {direction} | ${usdc_amount:,.2f} | {market_title} | {tx_hash[:16]}")
                msg = format_limitless_message(wallet_name, address, tx_hash, action_type, market_title, amount_str, direction, custom_chat_id)
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
