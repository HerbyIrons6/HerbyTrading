from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite

# Импортируем наши функции и имя базы данных для дебага
from db_manager import (
    DB_NAME, add_user, add_tracked_item,
    get_user_tracked_items, remove_tracked_item_by_id, clear_user_tracked_items
)
from parser import fetch_current_listings

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id)
    await message.answer(
        "Привет! Я аналитический бот рынка CS2 📈\n\n"
        "🤖 <b>Доступные команды:</b>\n"
        "📝 /add <code>[название]</code> — умный поиск предмета\n"
        "📋 /list — управление текущими подписками\n"
        "🧹 /clear — удалить все свои треки\n"
        "🛠 /debug — проверить статус записи в БД",
        parse_mode="HTML"
    )


# --- УМНЫЙ ПОИСК И ВЫБОР КАЧЕСТВА ---
@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject):
    await add_user(message.from_user.id)

    if command.args is None:
        await message.answer(
            "Укажи часть названия предмета.\nПример: <code>/add p250 supernova</code> или <code>/add redline</code>",
            parse_mode="HTML"
        )
        return

    user_input = command.args.strip().lower()
    keywords = user_input.split()

    wait_msg = await message.answer("🔍 Ищу подходящие варианты на Skinport...")

    listings = await fetch_current_listings()
    if not listings:
        await wait_msg.edit_text("❌ Ошибка связи со Skinport. Попробуй позже (возможно, сработал лимит запросов).")
        return

    all_skins = sorted(listings.keys())

    matches = []
    for idx, name in enumerate(all_skins):
        if all(kw in name.lower() for kw in keywords):
            matches.append((idx, name))

    if not matches:
        await wait_msg.edit_text(
            f"❌ Ничего не найдено по запросу «<code>{command.args}</code>».\n"
            f"Попробуй ввести более короткое или общее название.",
            parse_mode="HTML"
        )
        return

    if len(matches) > 15:
        await wait_msg.edit_text(
            f"⚠️ Найдено слишком много совпадений (<b>{len(matches)}</b>).\n"
            f"Пожалуйста, уточни запрос (например, добавь модель оружия или качество).",
            parse_mode="HTML"
        )
        return

    builder = InlineKeyboardBuilder()
    for idx, name in matches:
        builder.add(InlineKeyboardButton(text=name, callback_data=f"save_{idx}"))

    builder.adjust(1)

    await wait_msg.delete()
    await message.answer(
        f"🎯 Найдено вариантов: <b>{len(matches)}</b>.\nВыбери точный предмет для мониторинга:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
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
        await callback.answer("Ошибка: индекс устарел. Повтори поиск.", show_alert=True)
        return

    official_name = all_skins[skin_idx]

    added = await add_tracked_item(callback.from_user.id, official_name)

    if added:
        await callback.answer("Предмет добавлен!")
        await callback.message.edit_text(
            f"✅ Предмет <code>{official_name}</code> успешно добавлен в мониторинг.\n"
            f"Статистика начнет собираться автоматически.",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Уже отслеживается", show_alert=True)
        await callback.message.edit_text(f"⚠️ Предмет <code>{official_name}</code> уже был в твоем списке.")


# --- УПРАВЛЕНИЕ ТЕКУЩИМ СПИСКОМ ---
@router.message(Command("list"))
async def cmd_list(message: Message):
    await add_user(message.from_user.id)
    items = await get_user_tracked_items(message.from_user.id)

    if not items:
        await message.answer("📋 Твой список отслеживания пуст. Добавь что-то с помощью /add")
        return

    await message.answer("📋 <b>Твой список отслеживания:</b>\nНажми кнопку под предметом, чтобы удалить его:",
                         parse_mode="HTML")

    for item_id, hash_name in items:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text=f"❌ Удалить из базы", callback_data=f"del_{item_id}"))
        await message.answer(f"📦 <b>{hash_name}</b>", parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_"))
async def callback_delete_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    deleted = await remove_tracked_item_by_id(item_id, user_id)

    if deleted:
        await callback.answer("Предмет удален")
        await callback.message.edit_text("✨ Отслеживание этого предмета прекращено.")
    else:
        await callback.answer("Ошибка: предмет не найден", show_alert=True)


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_user_tracked_items(message.from_user.id)
    await message.answer("🧹 Все твои отслеживаемые предметы успешно удалены.")


# --- ИНСТРУМЕНТ ДЛЯ ТЕСТИРОВАНИЯ (РЕНТГЕН БАЗЫ) ---
@router.message(Command("debug"))
async def cmd_debug(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        c1 = await db.execute('SELECT hash_name FROM Tracked_Items WHERE user_id = ?', (message.from_user.id,))
        tracked = await c1.fetchall()

        c2 = await db.execute('SELECT hash_name, min_price, timestamp FROM Price_History ORDER BY id DESC LIMIT 5')
        history = await c2.fetchall()

    msg = "🛠 <b>ОТЛАДОЧНАЯ ИНФОРМАЦИЯ</b>\n\n"

    msg += "<b>1. Сейчас в твоих подписках:</b>\n"
    if tracked:
        for t in tracked:
            msg += f"- <code>{t[0]}</code>\n"
    else:
        msg += "Пусто\n"

    msg += "\n<b>2. Последние 5 записей в истории:</b>\n"
    if history:
        for h in history:
            msg += f"📦 {h[0][:15]}... | 💰 ${h[1]} | 🕒 {h[2][11:19]}\n"
    else:
        msg += "История пока пуста\n"

    await message.answer(msg, parse_mode="HTML")