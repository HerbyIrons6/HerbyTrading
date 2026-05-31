import aiosqlite
import asyncio

DB_NAME = "trading_bot.db"

async def init_db():
    """Создает таблицы в SQLite, если они еще не существуют."""
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Таблица пользователей
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                user_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. Таблица отслеживаемых предметов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Tracked_Items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                hash_name TEXT,
                last_alert_time TIMESTAMP DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES Users (user_id)
            )
        ''')

        # 3. Таблица истории
        await db.execute('''
            CREATE TABLE IF NOT EXISTS Price_History (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_name TEXT,
                min_price REAL,
                quantity INTEGER,
                volume_24h INTEGER,
                median_24h REAL,
                median_7d REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Индексы
        await db.execute('CREATE INDEX IF NOT EXISTS idx_hash_name ON Price_History(hash_name)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON Price_History(timestamp)')

        await db.commit()
        print("📦 База данных SQLite успешно инициализирована!")

async def add_user(user_id: int):
    """Регистрирует нового пользователя."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR IGNORE INTO Users (user_id) VALUES (?)', (user_id,))
        await db.commit()

async def add_tracked_item(user_id: int, hash_name: str):
    """Связывает пользователя с предметом для отслеживания."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id FROM Tracked_Items WHERE user_id = ? AND hash_name = ?', (user_id, hash_name))
        exists = await cursor.fetchone()
        if not exists:
            await db.execute('INSERT INTO Tracked_Items (user_id, hash_name) VALUES (?, ?)', (user_id, hash_name))
            await db.commit()
            return True
        return False

async def save_price_history(hash_name: str, min_price: float, quantity: int, vol_24h: int, med_24h: float, med_7d: float):
    """Записывает срез рынка по конкретному предмету в базу."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO Price_History
                (hash_name, min_price, quantity, volume_24h, median_24h, median_7d)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (hash_name, min_price, quantity, vol_24h, med_24h, med_7d))
        await db.commit()

async def get_all_tracked_items() -> list[str]:
    """Возвращает уникальный список всех отслеживаемых предметов."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT DISTINCT hash_name FROM Tracked_Items')
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_user_tracked_items(user_id: int) -> list[tuple[int, str]]:
    """Возвращает список кортежей (id_записи, имя_скина) конкретного пользователя."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id, hash_name FROM Tracked_Items WHERE user_id = ?', (user_id,))
        return await cursor.fetchall()

async def remove_tracked_item_by_id(item_id: int, user_id: int) -> bool:
    """Удаляет предмет из отслеживания по ID записи (с проверкой владельца)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('DELETE FROM Tracked_Items WHERE id = ? AND user_id = ?', (item_id, user_id))
        await db.commit()
        return cursor.rowcount > 0  # Вернет True, если строка реально удалилась

async def clear_user_tracked_items(user_id: int):
    """Полностью очищает список отслеживаемых предметов конкретного пользователя."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM Tracked_Items WHERE user_id = ?', (user_id,))
        await db.commit()

if __name__ == "__main__":
    asyncio.run(init_db())