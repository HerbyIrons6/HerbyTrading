import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite

from db_manager import (
    DB_NAME, add_user, add_tracked_item,
    get_user_tracked_items, remove_tracked_item_by_id, clear_user_tracked_items,
    get_latest_item_stats, save_price_history
)
from parser import (
    fetch_current_listings, fetch_sales_history,
    fetch_steam_item_nameid, fetch_steam_order_histogram
)
from analytics import run_analysis, get_trade_advice

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id)
    await message.answer(
        "Аналитический бот запущен.\n\n"
        "Доступные команды вынесены в системное меню Telegram (кнопка слева от поля ввода)."
    )


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject):
    await add_user(message.from_user.id)

    if command.args is None:
        await message.answer("Укажите часть названия предмета. Пример: <code>/add p250 supernova</code>",
                             parse_mode="HTML")
        return

    # Нормализация: приводим к нижнему регистру и удаляем дефисы
    user_input = command.args.strip().lower().replace("-", "")
    keywords = user_input.split()

    wait_msg = await message.answer("Выполняется поиск...")

    listings = await fetch_current_listings()
    if not listings:
        await wait_msg.edit_text("Ошибка связи с API Skinport.")
        return

    all_skins = sorted(listings.keys())

    matches = []
    for idx, name in enumerate(all_skins):
        # Нормализуем название предмета перед проверкой
        name_normalized = name.lower().replace("-", "")
        if all(kw in name_normalized for kw in keywords):
            matches.append((idx, name))

    if not matches:
        await wait_msg.edit_text("Совпадений не найдено.")
        return

    if len(matches) > 15:
        await wait_msg.edit_text(f"Найдено {len(matches)} совпадений. Уточните запрос.")
        return

    builder = InlineKeyboardBuilder()
    for idx, name in matches:
        builder.add(InlineKeyboardButton(text=name, callback_data=f"save_{idx}"))

    builder.adjust(1)

    await wait_msg.delete()
    await message.answer(
        f"Найдено вариантов: {len(matches)}. Выберите предмет:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("save_"))
async def callback_save_item(callback: CallbackQuery):
    skin_idx = int(callback.data.split("_")[1])

    listings = await fetch_current_listings()
    if not listings:
        await callback.answer("Ошибка обновления данных", show_alert=True)
        return

    all_skins = sorted(listings.keys())

    if skin_idx >= len(all_skins):
        await callback.answer("Неверный индекс. Повторите поиск.", show_alert=True)
        return

    official_name = all_skins[skin_idx]

    await callback.message.edit_text(f"⏳ Получаю системный ID предмета из Steam...")
    steam_id = await fetch_steam_item_nameid(official_name)

    added = await add_tracked_item(callback.from_user.id, official_name, steam_id)

    if added:
        id_text = f" (Steam ID: {steam_id})" if steam_id else " (Steam ID скрыт)"
        await callback.answer("Предмет добавлен.")
        await callback.message.edit_text(
            f"✅ Предмет <code>{official_name}</code>{id_text} добавлен в систему мониторинга.\n"
            f"Статистика начнет собираться автоматически.",
            parse_mode="HTML"
        )

        async def fetch_initial_data():
            history = await fetch_sales_history(official_name)
            if official_name in listings and official_name in history:
                curr = listings[official_name]
                hist = history[official_name]
                await save_price_history(
                    hash_name=official_name,
                    min_price=curr.get("min_price", 0.0),
                    quantity=curr.get("quantity", 0),
                    vol_24h=hist.get("last_24_hours", {}).get("volume", 0),
                    med_24h=hist.get("last_24_hours", {}).get("median", 0.0),
                    med_7d=hist.get("last_7_days", {}).get("median", 0.0)
                )

        asyncio.create_task(fetch_initial_data())
    else:
        await callback.answer("Предмет уже в списке.", show_alert=True)
        await callback.message.edit_text(f"Предмет <code>{official_name}</code> уже отслеживается.", parse_mode="HTML")


@router.message(Command("list"))
async def cmd_list(message: Message):
    await add_user(message.from_user.id)
    items = await get_user_tracked_items(message.from_user.id)

    if not items:
        await message.answer("Список отслеживания пуст.")
        return

    await message.answer("Список отслеживаемых предметов (нажмите для удаления):")
    for item_id, hash_name in items:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="Удалить из базы", callback_data=f"del_{item_id}"))
        await message.answer(f"<code>{hash_name}</code>", parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_"))
async def callback_delete_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    deleted = await remove_tracked_item_by_id(item_id, user_id)
    if deleted:
        await callback.answer("Предмет удален")
        await callback.message.edit_text("Отслеживание предмета прекращено.")
    else:
        await callback.answer("Ошибка: предмет не найден", show_alert=True)


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_user_tracked_items(message.from_user.id)
    await message.answer("Список отслеживаемых предметов очищен.")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    items = await get_user_tracked_items(message.from_user.id)
    if not items:
        await message.answer("Для просмотра статистики необходимо добавить предметы (/add).")
        return

    builder = InlineKeyboardBuilder()
    for item_id, hash_name in items:
        builder.add(InlineKeyboardButton(text=hash_name, callback_data=f"stats_{item_id}"))
    builder.adjust(1)

    await message.answer("Выберите предмет для просмотра базовой статистики (Skinport):",
                         reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("stats_"))
async def callback_show_stats(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    items = await get_user_tracked_items(callback.from_user.id)

    hash_name = None
    for i_id, name in items:
        if i_id == item_id:
            hash_name = name
            break

    if not hash_name:
        await callback.answer("Предмет не найден.", show_alert=True)
        return

    stats = await get_latest_item_stats(hash_name)
    if not stats:
        await callback.answer("Данные еще не собраны. Повторите запрос позже.", show_alert=True)
        return

    msg = (
        f"📊 Базовая статистика: <code>{hash_name}</code>\n\n"
        f"Текущая минимальная цена: ${stats['min_price']}\n"
        f"Количество предложений: {stats['quantity']}\n"
        f"Объем торгов (24ч): {stats['volume_24h']}\n"
        f"Медианная цена (24ч): ${stats['median_24h']}\n"
        f"Медианная цена (7дн): ${stats['median_7d']}\n"
        f"Последнее обновление: {stats['timestamp'][11:19]}"
    )
    await callback.message.edit_text(msg, parse_mode="HTML")


@router.message(Command("check"))
async def cmd_check(message: Message):
    items = await get_user_tracked_items(message.from_user.id)
    if not items:
        await message.answer("Ваш список пуст. Сначала добавьте предметы через /add")
        return

    builder = InlineKeyboardBuilder()
    for item_id, hash_name in items:
        builder.add(InlineKeyboardButton(text=hash_name, callback_data=f"chk_{item_id}"))
    builder.adjust(1)

    await message.answer("🔎 Выберите предмет для полного математического анализа:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("chk_"))
async def callback_check_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT hash_name, item_nameid FROM Tracked_Items WHERE id = ?', (item_id,))
        row = await cursor.fetchone()

    if not row:
        await callback.answer("Предмет не найден в базе.", show_alert=True)
        return

    skin_name, steam_id = row
    await callback.message.edit_text(f"⏳ Анализирую <code>{skin_name}</code> (Skinport + Steam + Math)...",
                                     parse_mode="HTML")

    stats = await get_latest_item_stats(skin_name)
    if not stats:
        await callback.message.edit_text(
            "Недостаточно данных в базе. Бот еще не успел сделать первый снимок рынка. Подождите 15 минут.")
        return

    spread_text = "Нет данных Steam"
    spread_percent = None
    if steam_id:
        spread_info = await fetch_steam_order_histogram(steam_id)
        if spread_info:
            spread_percent = spread_info['spread_percent']
            spread_text = f"{spread_percent}%"
            if spread_percent <= 5.0:
                spread_text += " 🟢 (Ликвидный)"
            elif spread_percent > 15.0:
                spread_text += " 🔴 (Опасный спред)"

    signal = await run_analysis(skin_name)
    advice = get_trade_advice(signal, spread_percent)

    if signal:
        math_text = (
            f"✅ <b>Аномалия найдена</b>\n"
            f"├ Z-Score: {signal.z_score}\n"
            f"├ Оценка: {signal.base_score} балла(ов)\n"
            f"└ Дефицит лотов: -{signal.drain_percent}%"
        )
    else:
        math_text = "🟡 Предмет в норме (или накоплено менее 10 записей для анализа)."

    msg = (
        f"🔎 <b>РАДАР:</b> <code>{skin_name}</code>\n\n"
        f"💰 <b>Цены (Skinport):</b>\n"
        f"Текущая минимальная: <b>${stats['min_price']}</b>\n"
        f"Медиана за 7 дней: ${stats['median_7d']}\n"
        f"Доступно лотов: {stats['quantity']} шт.\n\n"
        f"📉 <b>Стакан (Steam):</b>\n"
        f"Спред: <b>{spread_text}</b>\n\n"
        f"🧠 <b>Математика:</b>\n"
        f"{math_text}\n\n"
        f"💡 <b>СОВЕТ БОТА:</b>\n"
        f"{advice}"
    )

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔄 Обновить", callback_data=f"chk_{item_id}"))

    await callback.message.edit_text(msg, parse_mode="HTML", reply_markup=builder.as_markup())


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        c1 = await db.execute('SELECT hash_name FROM Tracked_Items WHERE user_id = ?', (message.from_user.id,))
        tracked = await c1.fetchall()
        c2 = await db.execute('SELECT hash_name, min_price, timestamp FROM Price_History ORDER BY id DESC LIMIT 5')
        history = await c2.fetchall()

    msg = "Отладочная информация:\n\nПодписки:\n"
    if tracked:
        for t in tracked:
            msg += f"- {t[0]}\n"
    else:
        msg += "Пусто\n"

    msg += "\nПоследние записи (Price_History):\n"
    if history:
        for h in history:
            msg += f"{h[0][:15]}... | ${h[1]} | {h[2][11:19]}\n"
    else:
        msg += "Пусто\n"

    await message.answer(msg)