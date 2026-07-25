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

async def fetch_limitless_market(session: aiohttp.ClientSession, condition_id: str) -> dict:
    if not condition_id:
        return {}
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
                if markets:
                    _market_cache[cid_lower] = markets[0]
                    return markets[0]
    except Exception as e:
        logger.error(f"Error searching Limitless market for conditionId {cid_lower}: {e}")
    return {}

def format_limitless_message(wallet_name: str, wallet_address: str, tx_hash: str, method: str, market_title: str, amount_str: str, chat_id: str = None) -> str:
    short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    method_display = "Redeem Position 🎯" if "redeem" in method.lower() else method
    
    msg = f"🌀 <b>LIMITLESS EXCHANGE BALİNA İŞLEMİ!</b>\n\n"
    msg += f"👤 Balina: <b>{wallet_name}</b> (<code>{short_addr}</code>)\n"
    if market_title:
        msg += f"📊 Market: <b>{market_title}</b>\n"
    msg += f"⚡ İşlem: <b>{method_display}</b>\n"
    if amount_str:
        msg += f"💰 Tutar: <b>{amount_str}</b>\n"
    msg += f"\n🔗 <a href='https://limitless.exchange/profile/{wallet_address}'>Limitless Profil</a> | <a href='https://basescan.org/tx/{tx_hash}'>Basescan</a>"
    return msg

async def process_wallet_transactions(session: aiohttp.ClientSession, wallet: dict):
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
                    
                # Mark activity in DB
                await record_activity(address, tx_hash, str(tx.get("timestamp") or ""))
                
                # If initial startup run, seed history without spamming old notifications
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
                            for log in log_items:
                                decoded = log.get("decoded") or {}
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
                    
                logger.info(f"🌀 [LIMITLESS ALERT] {wallet_name} | Tx: {tx_hash[:10]} | Method: {method} | Market: {market_title}")
                msg = format_limitless_message(wallet_name, address, tx_hash, method, market_title, amount_str, custom_chat_id)
                await send_notification(msg, chat_id=custom_chat_id)
                
            _first_run_wallets.add(address)
                
    except Exception as e:
        logger.error(f"Error processing Limitless wallet {address}: {e}")

async def limitless_tracker_loop():
    logger.info("Limitless Exchange Tracker loop started")
    connector = aiohttp.TCPConnector(limit=50, enable_cleanup_closed=True, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=8)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=HEADERS) as session:
        while True:
            try:
                wallets = await get_limitless_wallets_db()
                active_wallets = [w for w in wallets if w.get("status", "tracking") == "tracking"]
                
                for wallet in active_wallets:
                    await process_wallet_transactions(session, wallet)
                    
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Limitless tracker loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
