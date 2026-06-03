import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Убрали импорт DB_NAME и aiosqlite, теперь всё идет через db_manager
from db_manager import (
    add_user, add_tracked_item, get_user_tracked_items,
    remove_tracked_item_by_id, clear_user_tracked_items,
    get_latest_item_stats, save_price_history, get_item_nameid
)
from parser import (
    fetch_current_listings, fetch_sales_history,
    fetch_steam_item_nameid, fetch_steam_order_histogram
)
from analytics import run_analysis, get_trade_advice

router = Router()

# Кэш поиска для защиты от гонки данных (Race Condition)
# Формат: {user_id: ["AK-47 | Redline (Field-Tested)", "AWP | Asiimov (Field-Tested)", ...]}
SEARCH_CACHE = {}


class AddSkinState(StatesGroup):
    waiting_for_skin_name = State()


def get_main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🔎 Сканер (Математика + Steam)"))
    builder.add(KeyboardButton(text="➕ Добавить предмет"))
    builder.add(KeyboardButton(text="📋 Мои подписки"))
    builder.add(KeyboardButton(text="📊 Базовая стата (Skinport)"))
    builder.adjust(1, 2, 1)
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_cancel_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id)
    await message.answer(
        "👋 <b>Добро пожаловать в HerbyTrading!</b>\n\n"
        "Я — аналитический радар рынка CS2. Используйте меню ниже для навигации.",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )


@router.message(F.text == "❌ Отмена")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())


@router.message(F.text == "➕ Добавить предмет")
@router.message(Command("add"))
async def start_add_skin(message: Message, state: FSMContext, command: CommandObject = None):
    await add_user(message.from_user.id)

    if command and command.args:
        await process_skin_search(message, command.args)
        return

    await state.set_state(AddSkinState.waiting_for_skin_name)
    await message.answer(
        "Введите часть названия предмета (например, <code>ak47 vulcan</code> или <code>awp asiimov</code>):",
        parse_mode="HTML",
        reply_markup=get_cancel_menu()
    )


@router.message(AddSkinState.waiting_for_skin_name)
async def process_skin_name_input(message: Message, state: FSMContext):
    await state.clear()
    await process_skin_search(message, message.text)


async def process_skin_search(message: Message, search_query: str):
    user_input = search_query.strip().lower().replace("-", "")
    keywords = user_input.split()

    wait_msg = await message.answer("🔍 Выполняется поиск по базе Skinport...", reply_markup=get_main_menu())

    listings = await fetch_current_listings()
    if not listings:
        await wait_msg.edit_text("❌ Ошибка связи с API Skinport.")
        return

    all_skins = sorted(listings.keys())
    matches = []

    for name in all_skins:
        name_normalized = name.lower().replace("-", "")
        if all(kw in name_normalized for kw in keywords):
            matches.append(name)

    if not matches:
        await wait_msg.edit_text(f"Совпадений по запросу «{search_query}» не найдено.")
        return

    if len(matches) > 15:
        await wait_msg.edit_text(f"⚠️ Найдено слишком много совпадений ({len(matches)}). Пожалуйста, уточните запрос.")
        return

    # Сохраняем результаты в кэш юзера
    SEARCH_CACHE[message.from_user.id] = matches

    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(matches):
        builder.add(InlineKeyboardButton(text=name, callback_data=f"save_{idx}"))
    builder.adjust(1)

    await wait_msg.delete()
    await message.answer(
        f"✅ Найдено вариантов: {len(matches)}. Выберите точный предмет:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("save_"))
async def callback_save_item(callback: CallbackQuery):
    user_id = callback.from_user.id

    # Проверяем, есть ли кэш поиска для этого юзера
    if user_id not in SEARCH_CACHE:
        await callback.answer("Поиск устарел. Пожалуйста, введите запрос заново.", show_alert=True)
        return

    skin_idx = int(callback.data.split("_")[1])

    try:
        official_name = SEARCH_CACHE[user_id][skin_idx]
    except IndexError:
        await callback.answer("Ошибка индекса. Повторите поиск.", show_alert=True)
        return

    await callback.message.edit_text(f"⏳ Получаю системный ID предмета из Steam...")
    steam_id = await fetch_steam_item_nameid(official_name)

    added = await add_tracked_item(user_id, official_name, steam_id)

    if added:
        id_text = f" (Steam ID: {steam_id})" if steam_id else " (Steam ID скрыт)"
        await callback.answer("Предмет добавлен!")
        await callback.message.edit_text(
            f"✅ <b>{official_name}</b>{id_text} добавлен в систему мониторинга.\n"
            f"Бот начал сбор исторических данных.",
            parse_mode="HTML"
        )

        # Безопасный запуск фоновой задачи с обработкой ошибок
        async def fetch_initial_data_safe():
            try:
                listings = await fetch_current_listings()
                history = await fetch_sales_history()
                if listings and history and official_name in listings and official_name in history:
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
            except Exception as e:
                logging.error(f"Тихая ошибка при первичном сборе данных для {official_name}: {e}")

        asyncio.create_task(fetch_initial_data_safe())
    else:
        await callback.answer("Предмет уже в списке.", show_alert=True)
        await callback.message.edit_text(f"<b>{official_name}</b> уже отслеживается.", parse_mode="HTML")


@router.message(Command("list"))
@router.message(F.text == "📋 Мои подписки")
async def cmd_list(message: Message):
    await add_user(message.from_user.id)
    items = await get_user_tracked_items(message.from_user.id)

    if not items:
        await message.answer("Список отслеживания пуст.")
        return

    await message.answer("Ваши подписки (нажмите для удаления):")
    for item_id, hash_name in items:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="Удалить", callback_data=f"del_{item_id}"))
        await message.answer(f"<code>{hash_name}</code>", parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_"))
async def callback_delete_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    deleted = await remove_tracked_item_by_id(item_id, callback.from_user.id)
    if deleted:
        await callback.message.edit_text("Отслеживание предмета прекращено.")
    else:
        await callback.answer("Ошибка: предмет не найден", show_alert=True)


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_user_tracked_items(message.from_user.id)
    await message.answer("Список отслеживаемых предметов очищен.")


@router.message(Command("stats"))
@router.message(F.text == "📊 Базовая стата (Skinport)")
async def cmd_stats(message: Message):
    items = await get_user_tracked_items(message.from_user.id)
    if not items:
        await message.answer("Сначала добавьте предметы.")
        return

    builder = InlineKeyboardBuilder()
    for item_id, hash_name in items:
        builder.add(InlineKeyboardButton(text=hash_name, callback_data=f"stats_{item_id}"))
    builder.adjust(1)

    await message.answer("Выберите предмет:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("stats_"))
async def callback_show_stats(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    items = await get_user_tracked_items(callback.from_user.id)

    hash_name = next((name for i_id, name in items if i_id == item_id), None)
    if not hash_name:
        await callback.answer("Предмет не найден.", show_alert=True)
        return

    stats = await get_latest_item_stats(hash_name)
    if not stats:
        await callback.answer("Данные еще не собраны.", show_alert=True)
        return

    msg = (
        f"📊 <b>Статистика Skinport</b>\n"
        f"<code>{hash_name}</code>\n\n"
        f"Минимальная цена: <b>${stats['min_price']}</b>\n"
        f"Количество лотов: {stats['quantity']}\n"
        f"Объем (24ч): {stats['volume_24h']}\n"
        f"Медиана (24ч): ${stats['median_24h']}\n"
        f"Медиана (7дн): ${stats['median_7d']}"
    )
    await callback.message.edit_text(msg, parse_mode="HTML")


@router.message(Command("check"))
@router.message(F.text == "🔎 Сканер (Математика + Steam)")
async def cmd_check(message: Message):
    items = await get_user_tracked_items(message.from_user.id)
    if not items:
        await message.answer("Ваш список пуст.")
        return

    builder = InlineKeyboardBuilder()
    for item_id, hash_name in items:
        builder.add(InlineKeyboardButton(text=hash_name, callback_data=f"chk_{item_id}"))
    builder.adjust(1)

    await message.answer("🔎 Выберите предмет для оценки риска:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("chk_"))
async def callback_check_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])

    # Теперь мы не лазим в базу напрямую через aiosqlite, а используем API db_manager
    items = await get_user_tracked_items(callback.from_user.id)
    skin_name = next((name for i_id, name in items if i_id == item_id), None)

    if not skin_name:
        await callback.answer("Предмет не найден.")
        return

    await callback.message.edit_text(f"⏳ Считаю математику для <code>{skin_name}</code>...", parse_mode="HTML")

    steam_id = await get_item_nameid(skin_name)
    stats = await get_latest_item_stats(skin_name)

    if not stats:
        await callback.message.edit_text("Недостаточно данных. Подождите 15 минут.")
        return

    spread_text = "Нет данных Steam"
    spread_percent = None
    if steam_id:
        spread_info = await fetch_steam_order_histogram(steam_id)
        if spread_info:
            spread_percent = spread_info['spread_percent']
            spread_text = f"{spread_percent}%"
            if spread_percent <= 5.0:
                spread_text += " 🟢"
            elif spread_percent > 15.0:
                spread_text += " 🔴"

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
        f"💰 <b>Цены:</b>\n"
        f"Минимальная сейчас: <b>${stats['min_price']}</b>\n"
        f"Обычная медиана: ${stats['median_7d']}\n\n"
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