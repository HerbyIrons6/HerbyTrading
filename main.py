import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from db_manager import (
    init_db, save_price_history, get_all_tracked_items,
    get_users_tracking_item, get_item_nameid,
    get_last_alert_time, update_last_alert_time, cleanup_old_history # <--- ДОБАВИЛИ ЭТО
)

from parser import fetch_current_listings, fetch_sales_history, fetch_steam_order_histogram
from analytics import run_analysis, get_trade_advice
from tg_bot import router

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
        BotCommand(command="list", description="Управление подписками"),
        BotCommand(command="stats", description="Просмотр текущей статистики"),
        BotCommand(command="clear", description="Очистить список подписок")
    ]
    await bot.set_my_commands(commands)


async def send_notification(bot: Bot, user_id: int, text: str):
    """Безопасная отправка сообщения одному пользователю"""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка отправки пользователю {user_id}: {e}")


async def market_watcher(bot: Bot):
    logging.info("Market Watcher запущен.")

    # Переменная для таймера чистки базы
    last_cleanup_time = datetime.now(timezone.utc)

    while True:
        try:
            # --- БЛОК АВТОЧИСТКИ БАЗЫ ДАННЫХ ---
            now = datetime.now(timezone.utc)
            if (now - last_cleanup_time).total_seconds() > 86400:  # 86400 секунд = 24 часа
                deleted_rows = await cleanup_old_history(days=45)
                logging.info(f"🧹 Плановая очистка БД: удалено {deleted_rows} устаревших записей.")
                last_cleanup_time = now

            tracked_skins = await get_all_tracked_items()

            if tracked_skins:
                logging.info(f"Сбор данных по {len(tracked_skins)} предметам.")

                # 1. КЭШИРОВАНИЕ ЗАПРОСОВ:
                # Делаем вызов к Skinport только ОДИН раз за цикл, а не для каждого предмета!
                listings = await fetch_current_listings()
                history = await fetch_sales_history()

                if not listings or not history:
                    logging.warning("API Skinport не ответил. Ожидание следующего цикла.")
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
                                # 2. АНТИ-СПАМ СИСТЕМА (Кулдаун 4 часа)
                                last_alert = await get_last_alert_time(skin)
                                if last_alert:
                                    try:
                                        # SQLite сохраняет время в UTC
                                        last_alert_dt = datetime.strptime(last_alert, '%Y-%m-%d %H:%M:%S')
                                        if datetime.now(timezone.utc).replace(tzinfo=None) - last_alert_dt < timedelta(hours=4):
                                            logging.info(f"Алерт для {skin} пропущен (кулдаун 4 часа).")
                                            continue
                                    except Exception as e:
                                        logging.error(f"Ошибка парсинга даты алерта: {e}")

                                # 3. ИЗБАВЛЕНИЕ ОТ СЫРОГО SQL
                                steam_id = await get_item_nameid(skin)

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

                                    users_to_notify = await get_users_tracking_item(skin)
                                    if users_to_notify:
                                        # 4. ПАРАЛЛЕЛЬНАЯ ОТПРАВКА (asyncio.gather)
                                        # Вместо того чтобы ждать отправки каждому юзеру по очереди, бьем залпом
                                        tasks = [send_notification(bot, uid, msg) for uid in users_to_notify]
                                        await asyncio.gather(*tasks, return_exceptions=True)

                                        # ЗАПИСЫВАЕМ ВРЕМЯ УВЕДОМЛЕНИЯ (включаем кулдаун)
                                        await update_last_alert_time(skin)

                    if saved_count > 0:
                        logging.info(f"Данные по {saved_count} предметам успешно сохранены.")
        except Exception as e:
            logging.error(f"Ошибка в market_watcher: {e}")

        await asyncio.sleep(900)


async def main():
    if not BOT_TOKEN:
        raise ValueError("Токен бота не найден. Проверьте файл .env")
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await setup_bot_commands(bot)
    logging.info("Бот запущен.")

    # Запускаем фон
    asyncio.create_task(market_watcher(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")