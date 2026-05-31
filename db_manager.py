import aiosqlite
import asyncio

DB_NAME = "trading_bot.db"

async def init_db():
    """
    Создает таблицы в SQLite, если они еще не существуют.
    """
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Таблица пользователей
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

        # 2. Таблица отслеживаемых предметов (Связь юзера и скина)
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

        # 3. Таблица истории (Сюда парсер будет складывать данные)
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

        # Создаем индексы для быстрого поиска
        await db.execute('CREATE INDEX IF NOT EXISTS idx_hash_name ON Price_History(hash_name)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON Price_History(timestamp)')

        await db.commit()
        print("📦 База данных SQLite успешно инициализирована!")


# Блок тестирования
if __name__ == "__main__":
    asyncio.run(init_db())