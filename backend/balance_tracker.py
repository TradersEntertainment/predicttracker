import asyncio
import time
import aiohttp
import logging
import os
from bot_engine import send_notification
from database import get_whales_cached

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BSC_RPCS = [
    os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/"),
    "https://rpc.ankr.com/bsc",
    "https://bsc.drpc.org",
    "https://binance.llamarpc.com"
]

USDT_BSC_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"
PREDICT_GRAPHQL_URL = "https://graphql.predict.fun/graphql"

BALANCE_CHECK_INTERVAL = 60
LOW_BALANCE_THRESHOLD = 1000
BALANCE_ALERTS_CHAT_ID = os.getenv("BALANCE_ALERTS_CHAT_ID", "")

_balance_cache: dict = {}

async def fetch_usdt_balance(session: aiohttp.ClientSession, address: str) -> float:
    clean_address = address.lower().replace("0x", "").zfill(64)
    data = f"0x70a08231{clean_address}"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": USDT_BSC_CONTRACT, "data": data}, "latest"],
        "id": 1
    }
    for rpc_url in BSC_RPCS:
        try:
            async with session.post(rpc_url, json=payload, timeout=5) as response:
                if response.status == 200:
                    result = await response.json()
                    hex_val = result.get("result")
                    if hex_val and hex_val != "0x":
                        return int(hex_val, 16) / 1e18
        except Exception:
            pass
    return -1

async def fetch_portfolio_value(session: aiohttp.ClientSession, address: str) -> float:
    query = """
    query GetPositions($address: Address!) {
      account(address: $address) {
        positions {
          edges {
            node {
              valueUsd
            }
          }
        }
      }
    }
    """
    payload = {"query": query, "variables": {"address": address}}
    try:
        async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=8) as response:
            if response.status == 200:
                data = await response.json()
                data_obj = data.get("data") or {}
                account_obj = data_obj.get("account") or {}
                positions_obj = account_obj.get("positions") or {}
                edges = positions_obj.get("edges") or []
                total_val = sum([float((e.get("node") or {}).get("valueUsd") or 0) for e in edges if isinstance(e, dict)])
                return total_val
    except Exception as e:
        logger.error(f"Portfolio value fetch error for {address}: {e}")
    return 0.0

async def check_whale_balance(session: aiohttp.ClientSession, whale: dict):
    address = whale['address']
    nickname = whale.get('name', address[:8])
    
    usdt_bal, portfolio_val = await asyncio.gather(
        fetch_usdt_balance(session, address),
        fetch_portfolio_value(session, address)
    )
    
    if usdt_bal < 0:
        usdt_bal = 0
        
    now = time.time()
    total_val = usdt_bal + portfolio_val
    
    prev = _balance_cache.get(address)
    was_notified = prev.get("low_balance_notified", False) if prev else False
    low_start = prev.get("low_balance_started_at", None) if prev else None
    
    if total_val < LOW_BALANCE_THRESHOLD:
        if low_start is None:
            low_start = now
    else:
        low_start = None
        was_notified = False
        
    _balance_cache[address] = {
        "usdc_balance": max(usdt_bal, 0),
        "portfolio_value": max(portfolio_val, 0),
        "last_updated": now,
        "nickname": nickname,
        "low_balance_notified": was_notified,
        "low_balance_started_at": low_start
    }
    
    if prev is None:
        logger.info(f"💰 İlk bakiye kaydı (Predict): {nickname} | USDT: ${usdt_bal:.2f} | Portfolio: ${portfolio_val:.2f}")
        return

def get_all_balances() -> dict:
    return {
        address: {
            "usdc_balance": info.get("usdc_balance", 0),
            "portfolio_value": info.get("portfolio_value", 0),
            "last_updated": info.get("last_updated", 0),
            "nickname": info.get("nickname", "")
        }
        for address, info in _balance_cache.items()
    }

async def balance_tracker_loop():
    logger.info("💰 Predict Balance tracker loop started")
    connector = aiohttp.TCPConnector(limit=20, enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        while True:
            try:
                whales = await get_whales_cached()
                if whales:
                    tasks = [check_whale_balance(session, w) for w in whales]
                    await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(BALANCE_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Balance loop error: {e}")
                await asyncio.sleep(BALANCE_CHECK_INTERVAL)
