import aiosqlite
import os

# Check if Railway Volume is mounted at /data or custom DATABASE_PATH env var
if os.getenv("DATABASE_PATH"):
    DB_PATH = os.getenv("DATABASE_PATH")
elif os.path.exists("/data") and os.access("/data", os.W_OK):
    DB_PATH = "/data/balina.db"
else:
    DB_PATH = "data/balina.db"

INITIAL_WHALES = [
    ("0x17c99cd6ca9032910de5ccfa2a2febcc22319a86", "Predict Balina 1", None),
]

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS whales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'tracking'
            )
        """)
        try:
            await db.execute("ALTER TABLE whales ADD COLUMN chat_id TEXT")
        except aiosqlite.OperationalError:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_history (
                address TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                timestamp TEXT,
                PRIMARY KEY (address, tx_hash)
            )
        """)
        
        for address, name, chat_id in INITIAL_WHALES:
            await db.execute(
                "INSERT OR IGNORE INTO whales (address, name, chat_id) VALUES (?, ?, ?)", 
                (address.lower(), name, chat_id)
            )
            
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS orderbook_monitors (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market_id TEXT,
            min_shares REAL NOT NULL DEFAULT 2000,
            chat_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        
        await db.execute("""
        INSERT OR IGNORE INTO orderbook_monitors (id, name, market_id, min_shares, chat_id, status)
        VALUES ('default_auto', 'Bitcoin 5M Likidite Duvarı (Otomatik)', NULL, 2000, NULL, 'active')
        """)

        await db.commit()

async def get_whales():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM whales") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_whale(address: str, name: str, chat_id: str = None):
    address_lower = address.strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO whales (address, name, chat_id) VALUES (?, ?, ?)", (address_lower, name, chat_id))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def remove_whale(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE whales SET status = 'paused' WHERE LOWER(address) = LOWER(?)", (address,))
        await db.commit()
        return True

async def reactivate_whale(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE whales SET status = 'tracking' WHERE LOWER(address) = LOWER(?)", (address,))
        await db.commit()
        return True

async def delete_whale_permanently(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM whales WHERE LOWER(address) = LOWER(?)", (address,))
        await db.commit()
        return True

async def is_activity_seen(address: str, tx_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM activity_history WHERE LOWER(address) = LOWER(?) AND tx_hash = ?", (address, tx_hash)) as cursor:
            return await cursor.fetchone() is not None

async def record_activity(address: str, tx_hash: str, timestamp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO activity_history (address, tx_hash, timestamp) VALUES (?, ?, ?)", (address.lower(), tx_hash, timestamp))
        await db.commit()

import time as _time

_seen_cache: set = set()
_seen_cache_loaded: bool = False

_whales_cache: list = []
_whales_cache_ts: float = 0
_WHALES_CACHE_TTL = 30

async def load_seen_cache():
    global _seen_cache, _seen_cache_loaded
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT address, tx_hash FROM activity_history") as cursor:
            rows = await cursor.fetchall()
            _seen_cache = {(row[0].lower(), row[1]) for row in rows}
    _seen_cache_loaded = True
    return len(_seen_cache)

def is_activity_seen_fast(address: str, tx_hash: str) -> bool:
    return (address.lower(), tx_hash) in _seen_cache

def mark_activity_seen_fast(address: str, tx_hash: str):
    _seen_cache.add((address.lower(), tx_hash))

async def batch_record_activities(records: list):
    if not records:
        return
    formatted = [(r[0].lower(), r[1], r[2]) for r in records]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO activity_history (address, tx_hash, timestamp) VALUES (?, ?, ?)",
            formatted
        )
        await db.commit()

async def get_whales_cached():
    global _whales_cache, _whales_cache_ts
    now = _time.time()
    if not _whales_cache or (now - _whales_cache_ts) > _WHALES_CACHE_TTL:
        _whales_cache = await get_whales()
        _whales_cache_ts = now
    return _whales_cache

def invalidate_whales_cache():
    global _whales_cache_ts
    _whales_cache_ts = 0


async def get_orderbook_monitors_db():
    async with get_db() as db:
        async with db.execute("SELECT id, name, market_id, min_shares, chat_id, status, created_at FROM orderbook_monitors") as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "market_id": row[2],
                    "min_shares": float(row[3]),
                    "chat_id": row[4],
                    "status": row[5],
                    "created_at": row[6]
                }
                for row in rows
            ]

async def add_orderbook_monitor_db(name: str, market_id: str, min_shares: float, chat_id: str = None):
    import uuid
    monitor_id = str(uuid.uuid4())[:8]
    async with get_db() as db:
        await db.execute(
            "INSERT INTO orderbook_monitors (id, name, market_id, min_shares, chat_id, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (monitor_id, name, market_id or None, min_shares, chat_id or None)
        )
        await db.commit()
    return monitor_id

async def delete_orderbook_monitor_db(monitor_id: str):
    async with get_db() as db:
        await db.execute("DELETE FROM orderbook_monitors WHERE id = ?", (monitor_id,))
        await db.commit()
    return True
