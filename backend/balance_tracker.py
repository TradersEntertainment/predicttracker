import asyncio
import time
import aiohttp
import logging
import os
from bot_engine import send_notification
from database import get_whales_cached, get_limitless_wallets_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BSC_RPCS = [
    os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/"),
    "https://rpc.ankr.com/bsc",
    "https://bsc.drpc.org",
    "https://binance.llamarpc.com"
]

BASE_RPCS = [
    "https://mainnet.base.org",
    "https://rpc.ankr.com/base",
    "https://base.drpc.org"
]

USDT_BSC_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"
USDC_BASE_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PREDICT_GRAPHQL_URL = "https://graphql.predict.fun/graphql"
LIMITLESS_API_BASE = "https://api.limitless.exchange"

BALANCE_CHECK_INTERVAL = 15
_balance_cache: dict = {}

async def fetch_usdt_balance_bsc(session: aiohttp.ClientSession, address: str) -> float:
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
    return 0.0

async def fetch_usdc_balance_base(session: aiohttp.ClientSession, address: str) -> float:
    clean_address = address.lower().replace("0x", "").zfill(64)
    data = f"0x70a08231{clean_address}"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": USDC_BASE_CONTRACT, "data": data}, "latest"],
        "id": 1
    }
    for rpc_url in BASE_RPCS:
        try:
            async with session.post(rpc_url, json=payload, timeout=5) as response:
                if response.status == 200:
                    result = await response.json()
                    hex_val = result.get("result")
                    if hex_val and hex_val != "0x":
                        return int(hex_val, 16) / 1e6
        except Exception:
            pass
    return 0.0

async def fetch_limitless_pnl_and_balance(session: aiohttp.ClientSession, address: str) -> tuple:
    usdc_bal = await fetch_usdc_balance_base(session, address)
    pnl_val = 0.0
    try:
        url = f"{LIMITLESS_API_BASE}/portfolio/{address}/pnl-chart?timeframe=all"
        async with session.get(url, timeout=6) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("data") or []
                if items:
                    pnl_val = float(items[-1].get("value") or 0.0)
    except Exception as e:
        logger.error(f"Limitless PnL error for {address}: {e}")
    return usdc_bal, pnl_val

async def fetch_predict_portfolio_and_pnl(session: aiohttp.ClientSession, raw_address: str) -> tuple:
    query = """
    query GetPositionsAndPnl($address: Address!) {
      account(address: $address) {
        positions {
          edges {
            node {
              valueUsd
            }
          }
        }
        pnlTimeseries(filter: { interval: MAX }) {
          edges {
            node {
              y
            }
          }
        }
      }
    }
    """
    addresses_to_try = [raw_address]
    if raw_address != raw_address.lower():
        addresses_to_try.append(raw_address.lower())

    for addr in addresses_to_try:
        payload = {"query": query, "variables": {"address": addr}}
        try:
            async with session.post(PREDICT_GRAPHQL_URL, json=payload, timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    data_obj = data.get("data") or {}
                    account_obj = data_obj.get("account") or {}
                    if account_obj:
                        positions_obj = account_obj.get("positions") or {}
                        edges = positions_obj.get("edges") or []
                        total_val = sum([float((e.get("node") or {}).get("valueUsd") or 0) for e in edges if isinstance(e, dict)])

                        pnl_obj = account_obj.get("pnlTimeseries") or {}
                        pnl_edges = pnl_obj.get("edges") or []
                        total_pnl = 0.0
                        if pnl_edges:
                            last_node = (pnl_edges[-1].get("node") or {})
                            total_pnl = float(last_node.get("y") or 0.0)
                        return total_val, total_pnl
        except Exception:
            pass
    return 0.0, 0.0

async def check_predict_whale_balance(session: aiohttp.ClientSession, whale: dict):
    raw_address = whale['address']
    address = raw_address.lower()
    nickname = whale.get('name', address[:8])

    usdt_bal = await fetch_usdt_balance_bsc(session, address)
    portfolio_val, pnl_usd = await fetch_predict_portfolio_and_pnl(session, raw_address)

    now = time.time()
    _balance_cache[address] = {
        "usdc_balance": max(usdt_bal, 0),
        "portfolio_value": max(portfolio_val, 0),
        "pnl_usd": pnl_usd,
        "last_updated": now,
        "nickname": nickname
    }

async def check_limitless_whale_balance(session: aiohttp.ClientSession, wallet: dict):
    raw_address = wallet['address']
    address = raw_address.lower()
    nickname = wallet.get('name', address[:8])

    usdc_bal, pnl_usd = await fetch_limitless_pnl_and_balance(session, raw_address)

    now = time.time()
    _balance_cache[address] = {
        "usdc_balance": max(usdc_bal, 0),
        "portfolio_value": 0.0,
        "pnl_usd": pnl_usd,
        "last_updated": now,
        "nickname": nickname
    }

def get_all_balances() -> dict:
    return {
        address: {
            "usdc_balance": info.get("usdc_balance", 0),
            "portfolio_value": info.get("portfolio_value", 0),
            "pnl_usd": info.get("pnl_usd", 0),
            "last_updated": info.get("last_updated", 0),
            "nickname": info.get("nickname", "")
        }
        for address, info in _balance_cache.items()
    }

async def balance_tracker_loop():
    logger.info("💰 Balance tracker loop started (Predict + Limitless Lifetime PnL)")
    connector = aiohttp.TCPConnector(limit=30, enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=12)
    headers = {"User-Agent": "Mozilla/5.0"}

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        while True:
            try:
                p_whales = await get_whales_cached()
                l_wallets = await get_limitless_wallets_db()

                tasks = []
                if p_whales:
                    tasks.extend([check_predict_whale_balance(session, w) for w in p_whales])
                if l_wallets:
                    tasks.extend([check_limitless_whale_balance(session, w) for w in l_wallets])

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                await asyncio.sleep(BALANCE_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Balance loop error: {e}")
                await asyncio.sleep(BALANCE_CHECK_INTERVAL)
