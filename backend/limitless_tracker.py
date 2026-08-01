import asyncio
import time
import aiohttp
from datetime import datetime, timezone, timedelta
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

# Whale share accumulator per time slot
# Key: f"{address}_{slot_start}", Value: {"shares": float, "direction": str, "alerted": bool}
_whale_slot_shares = {}
WHALE_SHARES_THRESHOLD = 5000


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


def _get_time_slot(market_title, tx_timestamp):
    """
    Market title ve tx timestamp'inden zaman araligi hesaplar.
    Ornek: 'BTC Up or Down - 5 Min' + '2026-08-01T02:17:00Z' -> 'Aug 1, 02:15 - 02:20 UTC'
    """
    if not tx_timestamp:
        return ""

    try:
        ts_str = str(tx_timestamp).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except Exception:
        return ""

    title_lower = (market_title or "").lower()
    if "5 min" in title_lower or "5-min" in title_lower:
        interval_sec = 300
    elif "15 min" in title_lower or "15-min" in title_lower:
        interval_sec = 900
    elif "1 hour" in title_lower or "1h" in title_lower or "hourly" in title_lower:
        interval_sec = 3600
    elif "4 hour" in title_lower or "4h" in title_lower:
        interval_sec = 14400
    else:
        return ""

    epoch = int(dt.timestamp())
    slot_start = epoch - (epoch % interval_sec)
    slot_end = slot_start + interval_sec

    dt_start = datetime.fromtimestamp(slot_start, tz=timezone.utc)
    dt_end = datetime.fromtimestamp(slot_end, tz=timezone.utc)

    if dt_start.date() == dt_end.date():
        return f"{dt_start.strftime('%b %d')}, {dt_start.strftime('%H:%M')} \u2013 {dt_end.strftime('%H:%M')} UTC"
    else:
        return f"{dt_start.strftime('%b %d %H:%M')} \u2013 {dt_end.strftime('%b %d %H:%M')} UTC"


def format_limitless_message(wallet_name, wallet_address, tx_hash, action_type, market_title, amount_str, direction="UP", tx_timestamp="", chat_id=None):
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
        time_slot = _get_time_slot(market_title, tx_timestamp)
        msg += f"\U0001f4ca Market: <b>{market_title}</b>\n"
        if time_slot:
            msg += f"\u23f0 Aral\u0131k: <b>{time_slot}</b>\n"
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
    total_shares_raw = 0

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
                try:
                    total_shares_raw += int(raw_value)
                except (ValueError, TypeError):
                    pass
            elif from_addr == whale:
                if to_addr == zero_addr:
                    erc1155_burn = True
                else:
                    erc1155_out = True

    # Classify
    if erc1155_burn and usdc_in > 0:
        return "REDEEM", usdc_in, bought_token_id, total_shares_raw / 1e6
    elif erc1155_in and usdc_out > 0:
        return "BUY", usdc_out, bought_token_id, total_shares_raw / 1e6
    elif erc1155_out and usdc_in > 0:
        return "SELL", usdc_in, bought_token_id, total_shares_raw / 1e6
    elif erc1155_in:
        return "BUY", usdc_out, bought_token_id, total_shares_raw / 1e6
    elif erc1155_burn:
        return "REDEEM", usdc_in, bought_token_id, total_shares_raw / 1e6
    elif erc1155_out:
        return "SELL", usdc_in, bought_token_id, total_shares_raw / 1e6
    else:
        return "OTHER", max(usdc_in, usdc_out), bought_token_id, total_shares_raw / 1e6


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
                action_type, usdc_amount, bought_token_id, tx_shares = classify_token_transfers(tx_info["transfers"], address)

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

                logger.info(f"\U0001f300 [LIMITLESS] {wallet_name} | {action_type} {direction} | ${usdc_amount:,.2f} | {market_title} | shares={tx_shares:.1f} | {tx_hash[:16]}")
                msg = format_limitless_message(wallet_name, address, tx_hash, action_type, market_title, amount_str, direction, tx_info["timestamp"], custom_chat_id)
                await send_notification(msg, chat_id=custom_chat_id)



            _first_run_wallets.add(address)

    except Exception as e:
        logger.error(f"Error processing Limitless wallet {address}: {e}")


async def check_whale_positions(session, wallets):
    """
    Holders API ile balinalarin aktif marketlerdeki toplam pozisyonunu kontrol eder.
    5000+ hisse gecerse ozel bildirim gonderir.
    """
    # Check active 5-min and 15-min markets
    now = int(time.time())
    slugs_to_check = []

    # 5-min market
    slot_5 = now - (now % 300)
    slugs_to_check.append((f"btc-up-or-down-5-min-{slot_5}", "BTC Up or Down - 5 Min", 300, slot_5))

    # 15-min market
    slot_15 = now - (now % 900)
    slugs_to_check.append((f"btc-up-or-down-15-min-{slot_15}", "BTC Up or Down - 15 Min", 900, slot_15))

    whale_addresses = {w["address"].lower(): w for w in wallets}

    for slug, title, interval, slot_start in slugs_to_check:
        try:
            url = f"{LIMITLESS_API_BASE}/markets/{slug}/holders"
            async with session.get(url, timeout=5) as resp:
                if resp.status != 200:
                    continue
                holders_data = await resp.json()
        except Exception as e:
            logger.error(f"Error fetching holders for {slug}: {e}")
            continue

        for side_key, direction in [("yes", "UP"), ("no", "DOWN")]:
            side_data = holders_data.get(side_key) or {}
            holders_list = side_data.get("data") or []

            for holder in holders_list:
                holder_addr = (holder.get("user") or "").lower()
                if holder_addr not in whale_addresses:
                    continue

                contracts_str = holder.get("contractsFormatted") or "0"
                try:
                    contracts = float(contracts_str.replace(",", ""))
                except (ValueError, TypeError):
                    contracts = 0.0

                value_str = holder.get("valueUSDCFormatted") or "0"
                try:
                    value_usd = float(value_str.replace(",", ""))
                except (ValueError, TypeError):
                    value_usd = 0.0

                if contracts < WHALE_SHARES_THRESHOLD:
                    continue

                # Check if we already alerted for this whale + slot + direction
                alert_key = f"{holder_addr}_{slot_start}_{direction}"
                if alert_key in _whale_slot_shares:
                    continue
                _whale_slot_shares[alert_key] = True

                wallet_info = whale_addresses[holder_addr]
                wallet_name = wallet_info.get("name") or "Limitless Balina"
                custom_chat_id = wallet_info.get("chat_id")

                dt_start = datetime.fromtimestamp(slot_start, tz=timezone.utc)
                dt_end = datetime.fromtimestamp(slot_start + interval, tz=timezone.utc)
                time_slot = f"{dt_start.strftime('%b %d')}, {dt_start.strftime('%H:%M')} \u2013 {dt_end.strftime('%H:%M')} UTC"

                dir_emoji = "\U0001f7e2" if direction == "UP" else "\U0001f534"

                alert_msg = f"\U0001f6a8\U0001f6a8\U0001f6a8 <b>BAL\u0130NA B\u00dcY\u00dcK POZ\u0130SYON!</b> \U0001f6a8\U0001f6a8\U0001f6a8\n\n"
                alert_msg += f"{dir_emoji} <b>{direction} - {contracts:,.0f} Hisse!</b>\n"
                alert_msg += f"\U0001f464 Balina: <b>{wallet_name}</b>\n"
                alert_msg += f"\U0001f4ca Market: <b>{title}</b>\n"
                alert_msg += f"\u23f0 Aral\u0131k: <b>{time_slot}</b>\n"
                alert_msg += f"\U0001f4e6 Toplam Hisse: <b>{contracts:,.2f}</b>\n"
                alert_msg += f"\U0001f4b0 Piyasa De\u011feri: <b>${value_usd:,.2f}</b>\n"
                alert_msg += f"\n\U0001f517 <a href='https://limitless.exchange/profile/{holder_addr}'>Limitless Profil</a>"

                logger.info(f"\U0001f6a8 [WHALE ALERT] {wallet_name} | {direction} | {contracts:,.0f} shares | ${value_usd:,.2f} | {slug}")
                await send_notification(alert_msg, chat_id=custom_chat_id)

    # Cleanup old slot keys
    try:
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        expired = [k for k in _whale_slot_shares if not isinstance(_whale_slot_shares[k], bool) or int(k.split("_")[1]) < now_epoch - 1200]
        for k in expired:
            try:
                slot_ts = int(k.split("_")[1])
                if slot_ts < now_epoch - 1200:
                    del _whale_slot_shares[k]
            except (ValueError, IndexError):
                pass
    except Exception:
        pass


async def limitless_tracker_loop():
    """Ana Limitless tracker dongusu - hem wallet hem orderbook tarar"""
    logger.info("Limitless Exchange Tracker loop started (wallet + orderbook + holders)")
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

                    # Check whale positions via holders API (every ~2.4 seconds)
                    await check_whale_positions(session, active_wallets)

                cycle += 1
                await asyncio.sleep(ORDERBOOK_POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Limitless tracker loop error: {e}")
                await asyncio.sleep(ORDERBOOK_POLL_INTERVAL)
