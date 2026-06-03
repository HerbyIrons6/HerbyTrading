import asyncio
import os
import logging

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from tg_bot import router
from db_manager import init_db, save_price_history, get_all_tracked_items, get_users_tracking_item
from parser import fetch_current_listings, fetch_sales_history, fetch_steam_order_histogram
from analytics import run_analysis, get_trade_advice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="check", description="Ручной сканер предмета"),
        BotCommand(command="add", description="Добавить предмет для мониторинга"),
        BotCommand(command="stats", description="Просмотр текущей статистики"),
        BotCommand(command="list", description="Управление подписками"),
        BotCommand(command="clear", description="Очистить список подписок")
    ]
    await bot.set_my_commands(commands)


async def market_watcher(bot: Bot):
    logging.info("Market Watcher запущен.")
    while True:
        try:
            tracked_skins = await get_all_tracked_items()

            if tracked_skins:
                logging.info(f"Сбор данных по {len(tracked_skins)} предметам.")

                listings = await fetch_current_listings()
                history = await fetch_sales_history()

                if not listings or not history:
                    logging.warning("API Skinport не ответил. Ожидание.")
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

                            signal = await run_analysis(skin)

                            if signal:
                                async with aiosqlite.connect("trading_bot.db") as db:
                                    cursor = await db.execute(
                                        'SELECT item_nameid FROM Tracked_Items WHERE hash_name = ? LIMIT 1', (skin,))
                                    row = await cursor.fetchone()
                                    steam_id = row[0] if row else None

                                spread_info = None
                                spread_percent = None
                                spread_text = "Нет данных Steam"

                                if steam_id:
                                    spread_info = await fetch_steam_order_histogram(steam_id)

                                final_score = signal.base_score
                                is_pump = False

                                if spread_info:
                                    spread_percent = spread_info['spread_percent']
                                    spread_text = f"{spread_percent}%"
                                    if spread_percent > 15.0:
                                        is_pump = True
                                        logging.warning(f"Сигнал отменен! Спред > 15%: {skin}")
                                    elif spread_percent <= 5.0:
                                        final_score += 1
                                        spread_text += " 🟢"

                                if not is_pump:
                                    rank = "WEAK 🟡"
                                    if final_score == 3:
                                        rank = "MEDIUM 🟠"
                                    elif final_score >= 4:
                                        rank = "STRONG 🔴"

                                    advice = get_trade_advice(signal, spread_percent)

                                    users_to_notify = await get_users_tracking_item(skin)
                                    for uid in users_to_notify:
                                        try:
                                            msg = (
                                                f"🚨 <b>РАДАР АНОМАЛИЙ</b> | Уровень: <b>{rank}</b>\n\n"
                                                f"🔹 <b>{signal.skin}</b>\n"
                                                f"Купить сейчас: <b>${signal.current_price}</b>\n"
                                                f"Обычная цена: ${signal.median_price}\n\n"
                                                f"📊 <b>Анализ:</b>\n"
                                                f"• Спред (ликвидность): {spread_text}\n"
                                                f"• Дефицит лотов: -{signal.drain_percent}%\n\n"
                                                f"💡 <b>СОВЕТ БОТА:</b>\n"
                                                f"{advice}"
                                            )
                                            await bot.send_message(uid, msg, parse_mode="HTML")
                                        except Exception as e:
                                            logging.error(f"Ошибка отправки {uid}: {e}")

                    if saved_count > 0:
                        logging.info(f"Данные по {saved_count} предметам успешно сохранены.")
        except Exception as e:
            logging.error(f"Ошибка в market_watcher: {e}")

        await asyncio.sleep(900)


async def main():
    if not BOT_TOKEN:
        raise ValueError("Токен бота не найден.")
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await setup_bot_commands(bot)
    logging.info("Бот запущен.")
    asyncio.create_task(market_watcher(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")