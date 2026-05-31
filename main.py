import asyncio
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from tg_bot import router
from db_manager import init_db, save_price_history, get_all_tracked_items
from parser import fetch_current_listings, fetch_sales_history

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


async def market_watcher():
    """Фоновая задача: парсит рынок каждые 15 минут."""
    print("👀 Market Watcher запущен! Ждем отслеживаемых предметов...")
    while True:
        try:
            tracked_skins = await get_all_tracked_items()

            if tracked_skins:
                # Добавили вывод названий предметов в консоль
                skins_list = ", ".join(tracked_skins)
                print(f"🔄 Сбор данных по {len(tracked_skins)} предметам: [{skins_list}]")

                listings = await fetch_current_listings()
                history = await fetch_sales_history()

                if not listings or not history:
                    print("⚠️ Не удалось получить данные API (возможно 429). Ждем следующего цикла.")
                else:
                    saved_count = 0
                    for skin in tracked_skins:
                        if skin in listings and skin in history:
                            curr = listings[skin]
                            hist = history[skin]

                            await save_price_history(
                                hash_name=skin,
                                min_price=curr.get("min_price", 0.0),
                                quantity=curr.get("quantity", 0),
                                vol_24h=hist.get("last_24_hours", {}).get("volume", 0),
                                med_24h=hist.get("last_24_hours", {}).get("median", 0.0),
                                med_7d=hist.get("last_7_days", {}).get("median", 0.0)
                            )
                            saved_count += 1

                    if saved_count > 0:
                        print(f"✅ Данные по {saved_count} предметам успешно записаны в базу!")

        except Exception as e:
            print(f"❌ Ошибка в market_watcher: {e}")

        # Засыпаем на 15 минут (900 секунд) для обхода лимитов Skinport
        await asyncio.sleep(900)


async def main():
    if not BOT_TOKEN:
        raise ValueError("Токен бота не найден! Проверь файл .env")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print("🚀 Бот запущен и готов к работе!")
    asyncio.create_task(market_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен вручную.")