import aiosqlite
import logging
from datetime import datetime

DB_NAME = "trading_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY,
                user_id INTEGER UNIQUE,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Tracked_Items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                hash_name TEXT,
                item_nameid INTEGER,
                last_alert_time TIMESTAMP,
                UNIQUE(user_id, hash_name)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Price_History (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_name TEXT,
                min_price REAL,
                quantity INTEGER,
                volume_24h INTEGER,
                med_24h REAL,
                med_7d REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def add_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR IGNORE INTO Users (user_id) VALUES (?)', (user_id,))
        await db.commit()

async def add_tracked_item(user_id: int, hash_name: str, item_nameid: int = None) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                'INSERT INTO Tracked_Items (user_id, hash_name, item_nameid) VALUES (?, ?, ?)',
                (user_id, hash_name, item_nameid)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def get_user_tracked_items(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id, hash_name FROM Tracked_Items WHERE user_id = ?', (user_id,))
        return await cursor.fetchall()

async def get_all_tracked_items():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT DISTINCT hash_name FROM Tracked_Items')
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_users_tracking_item(hash_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT user_id FROM Tracked_Items WHERE hash_name = ?', (hash_name,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def remove_tracked_item_by_id(item_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('DELETE FROM Tracked_Items WHERE id = ? AND user_id = ?', (item_id, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def clear_user_tracked_items(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM Tracked_Items WHERE user_id = ?', (user_id,))
        await db.commit()

async def save_price_history(hash_name: str, min_price: float, quantity: int, vol_24h: int, med_24h: float, med_7d: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO Price_History (hash_name, min_price, quantity, volume_24h, med_24h, med_7d)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (hash_name, min_price, quantity, vol_24h, med_24h, med_7d))
        await db.commit()

async def get_latest_item_stats(hash_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT min_price, quantity, volume_24h, med_24h, med_7d, timestamp 
            FROM Price_History WHERE hash_name = ? ORDER BY timestamp DESC LIMIT 1
        ''', (hash_name,))
        row = await cursor.fetchone()
        if row:
            return {
                "min_price": row[0],
                "quantity": row[1],
                "volume_24h": row[2],
                "median_24h": row[3],
                "median_7d": row[4],
                "timestamp": row[5]
            }
        return None

# ==========================================
# НОВЫЕ ФУНКЦИИ (ИСПРАВЛЕНИЕ БАГОВ ЭТАПА 1)
# ==========================================

async def get_item_nameid(hash_name: str) -> int | None:
    """Безопасно достает Steam ID предмета из базы."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT item_nameid FROM Tracked_Items WHERE hash_name = ? AND item_nameid IS NOT NULL LIMIT 1', (hash_name,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def update_last_alert_time(hash_name: str):
    """Обновляет время последнего уведомления по скину (защита от спама)."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем для всех юзеров, отслеживающих этот скин
        await db.execute('UPDATE Tracked_Items SET last_alert_time = CURRENT_TIMESTAMP WHERE hash_name = ?', (hash_name,))
        await db.commit()

async def get_last_alert_time(hash_name: str):
    """Получает время последнего уведомления в виде строки."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT last_alert_time FROM Tracked_Items WHERE hash_name = ? AND last_alert_time IS NOT NULL LIMIT 1', (hash_name,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def cleanup_old_history(days: int = 45):
        """
        Удаляет исторические записи старше указанного количества дней.
        Предотвращает бесконечное разрастание базы данных.
        """
        async with aiosqlite.connect(DB_NAME) as db:
            # SQLite функция datetime('now', '-45 days')
            cursor = await db.execute('''
                                      DELETE
                                      FROM Price_History
                                      WHERE timestamp <= datetime('now', ?)
                                      ''', (f'-{days} days',))
            deleted_count = cursor.rowcount
            await db.commit()
            return deleted_count