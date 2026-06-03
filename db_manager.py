import aiosqlite
import asyncio
import logging

DB_NAME = "trading_bot.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
                         CREATE TABLE IF NOT EXISTS Users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             created_at
                             TIMESTAMP
                             DEFAULT
                             CURRENT_TIMESTAMP
                         )
                         ''')
        # Создаем таблицу сразу с item_nameid
        await db.execute('''
                         CREATE TABLE IF NOT EXISTS Tracked_Items
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             user_id
                             INTEGER,
                             hash_name
                             TEXT,
                             item_nameid
                             INTEGER,
                             last_alert_time
                             TIMESTAMP
                             DEFAULT
                             0,
                             FOREIGN
                             KEY
                         (
                             user_id
                         ) REFERENCES Users
                         (
                             user_id
                         )
                             )
                         ''')

        # Миграция для обновления старой базы данных
        try:
            await db.execute('ALTER TABLE Tracked_Items ADD COLUMN item_nameid INTEGER')
        except aiosqlite.OperationalError:
            pass  # Столбец уже существует

        await db.execute('''
                         CREATE TABLE IF NOT EXISTS Price_History
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             hash_name
                             TEXT,
                             min_price
                             REAL,
                             quantity
                             INTEGER,
                             volume_24h
                             INTEGER,
                             median_24h
                             REAL,
                             median_7d
                             REAL,
                             timestamp
                             TIMESTAMP
                             DEFAULT
                             CURRENT_TIMESTAMP
                         )
                         ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_hash_name ON Price_History(hash_name)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON Price_History(timestamp)')
        await db.commit()
        logging.info("База данных SQLite успешно инициализирована.")


async def add_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR IGNORE INTO Users (user_id) VALUES (?)', (user_id,))
        await db.commit()


async def add_tracked_item(user_id: int, hash_name: str, item_nameid: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id FROM Tracked_Items WHERE user_id = ? AND hash_name = ?',
                                  (user_id, hash_name))
        exists = await cursor.fetchone()
        if not exists:
            await db.execute(
                'INSERT INTO Tracked_Items (user_id, hash_name, item_nameid) VALUES (?, ?, ?)',
                (user_id, hash_name, item_nameid)
            )
            await db.commit()
            return True
        return False


async def save_price_history(hash_name: str, min_price: float, quantity: int, vol_24h: int, med_24h: float,
                             med_7d: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
                         INSERT INTO Price_History
                             (hash_name, min_price, quantity, volume_24h, median_24h, median_7d)
                         VALUES (?, ?, ?, ?, ?, ?)
                         ''', (hash_name, min_price, quantity, vol_24h, med_24h, med_7d))
        await db.commit()


async def get_all_tracked_items() -> list[str]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT DISTINCT hash_name FROM Tracked_Items')
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_user_tracked_items(user_id: int) -> list[tuple[int, str]]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id, hash_name FROM Tracked_Items WHERE user_id = ?', (user_id,))
        return await cursor.fetchall()


async def remove_tracked_item_by_id(item_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('DELETE FROM Tracked_Items WHERE id = ? AND user_id = ?', (item_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def clear_user_tracked_items(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM Tracked_Items WHERE user_id = ?', (user_id,))
        await db.commit()


async def get_users_tracking_item(hash_name: str) -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT user_id FROM Tracked_Items WHERE hash_name = ?', (hash_name,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_latest_item_stats(hash_name: str) -> dict | None:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT * FROM Price_History 
            WHERE hash_name = ? 
            ORDER BY timestamp DESC LIMIT 1
        ''', (hash_name,))
        row = await cursor.fetchone()
        return dict(row) if row else None