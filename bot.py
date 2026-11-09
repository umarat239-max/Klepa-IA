import asyncio
import logging
import os
import re
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

# ---------- Настройки ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1424001997"))
MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")
PORT = int(os.getenv("PORT", "10000"))

BOT_NAME = "Klepa IA"
CREATOR = "Marat"
CREATOR_USERNAME = "@f_f_f_07"
BIO_LINK = "https://t.me/Klepa_bio"
DB_NAME = "bot_database.db"

FREE_LIMIT = 10
PREMIUM_PRICE_STARS = 50
MAX_HISTORY = 12

SYSTEM_PROMPT = (
    f"Ты — {BOT_NAME}, персональная уникальная языковая модель на собственной архитектуре Klepa Engine. "
    f"Тебя создал и разработал {CREATOR} ({CREATOR_USERNAME}), он же твой единственный владелец. "
    f"Ссылка на био: {BIO_LINK}. "
    "Не упоминай сторонние компании или сервисы. На вопросы о владельце всегда отвечай, что тебя создал Marat (@f_f_f_07). "
    "Весь программный код оборачивай в блоки Markdown (```language ... ```) для удобного копирования в один клик. "
    "Отвечай красиво, структурированно и четко."
)

CREATOR_RE = re.compile(
    r"(кто\s+(тебя\s+)?(создал|сделал|разработал|написал|придумал)|"
    r"твой\s+(создатель|владелец|разработчик|автор|хозяин)|"
    r"кто\s+(твой\s+)?(создатель|владелец|разработчик|автор|хозяин)|"
    r"who\s+(made|created|built|developed|owns)\s+you|your\s+(creator|owner|developer))",
    re.IGNORECASE,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("klepa")
router = Router()

# ---------- База данных SQLite ----------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                messages_count INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                active_chat_id INTEGER DEFAULT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                content TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute("INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user_id, username, full_name))
                await db.commit()
                cur = await db.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (user_id, "Основной чат"))
                chat_id = cur.lastrowid
                await db.execute("UPDATE users SET active_chat_id = ? WHERE user_id = ?", (chat_id, user_id))
                await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def inc_msg_count(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET messages_count = messages_count + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def set_premium(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_premium = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_total_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            res = await cursor.fetchone()
            return res[0] if res else 0

# ---------- FSM Состояния ----------
class Donate(StatesGroup):
    amount = State()

class Support(StatesGroup):
    message = State()

class Review(StatesGroup):
    text = State()

class NewChat(StatesGroup):
    title = State()

class AdminReply(StatesGroup):
    text = State()

# ---------- Клавиатура ----------
BTN_PROFILE = "👤 Профиль"
BTN_CHATS = "💬 Мои диалоги"
BTN_NEW_CHAT = "➕ Новый чат"
BTN_CLEAR = "🧹 Очистить чат"
BTN_PREMIUM = "⭐ Premium (50 Stars)"
BTN_DONATE = "🌟 Задонатить Stars"
BTN_REVIEWS = "📝 Отзывы"
BTN_SUPPORT = "🆘 Поддержка"
BTN_BIO = "🔗 Klepa Bio"

def get_menu(is_admin: bool = False):
    buttons = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_CHATS)],
        [KeyboardButton(text=BTN_NEW_CHAT), KeyboardButton(text=BTN_CLEAR)],
        [KeyboardButton(text=BTN_PREMIUM), KeyboardButton(text=BTN_DONATE)],
        [KeyboardButton(text=BTN_REVIEWS), KeyboardButton(text=BTN_SUPPORT)],
        [KeyboardButton(text=BTN_BIO)]
    ]
    if is_admin:
        buttons.append([KeyboardButton(text="📊 Админ Статистика")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def user_nick(m: Message) -> str:
    u = m.from_user
    return f"@{u.username}" if u.username else (u.full_name or "без ника")

# ---------- Обработчики команд ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(m.from_user.id, m.from_user.username or "без_ника", m.from_user.full_name)
    is_admin = (m.from_user.id == ADMIN_ID)
    await m.answer(
        f"👋 Привет, <b>{m.from_user.first_name}</b>!\n\n"
        f"Я <b>{BOT_NAME}</b> — твой умный ИИ-помощник на архитектуре Klepa Engine.\n"
        "Отвечу на любой вопрос, напишу код, помогу с идеями и учёбой!\n\n"
        "Используй меню ниже для навигации 👇",
        reply_markup=get_menu(is_admin),
        parse_mode="HTML"
    )

@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Действие отменено.", reply_markup=get_menu(m.from_user.id == ADMIN_ID))

@router.message(F.text == BTN_PROFILE)
async def profile(m: Message, state: FSMContext):
    await state.clear()
    u = await get_user(m.from_user.id)
    tot = await get_total_users()
    st = "⭐ Premium (Навсегда)" if u["is_premium"] else f"🆓 Бесплатно: {u['messages_count']}/{FREE_LIMIT}"
    text = (
        f"👤 <b>Твой профиль Klepa IA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{u['user_id']}</code>\n"
        f"📛 <b>Имя:</b> {u['full_name']}\n"
        f"🌐 <b>Юзернейм:</b> @{u['username']}\n"
        f"💎 <b>Статус:</b> {st}\n\n"
        f"👥 <b>Посетило бота всего:</b> <code>{tot}</code> чел.\n"
        f"👑 <b>Владелец:</b> <a href='{BIO_LINK}'>{CREATOR}</a> ({CREATOR_USERNAME})"
    )
    await m.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text == BTN_BIO)
async def bio(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть Klepa Bio", url=BIO_LINK)]])
    await m.answer("Официальный канал и ссылки проекта:", reply_markup=kb)

# ---------- Диалоги и Чаты ----------
@router.message(F.text == BTN_CLEAR)
async def clear(m: Message, state: FSMContext):
    await state.clear()
    u = await get_user(m.from_user.id)
    if u and u["active_chat_id"]:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM messages WHERE chat_id = ?", (u["active_chat_id"],))
            await db.commit()
    await m.answer("🧹 Контекст активного чата очищен!", reply_markup=get_menu(m.from_user.id == ADMIN_ID))

@router.message(F.text == BTN_NEW_CHAT)
async def new_chat_start(m: Message, state: FSMContext):
    await state.set_state(NewChat.title)
    await m.answer("✏️ Введи название для нового чата (или /cancel):")

@router.message(NewChat.title, F.text)
async def new_chat_save(m: Message, state: FSMContext):
    title = m.text.strip()
    await state.clear()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (m.from_user.id, title))
        cid = cur.lastrowid
        await db.execute("UPDATE users SET active_chat_id = ? WHERE user_id = ?", (cid, m.from_user.id))
        await db.commit()
    await m.answer(f"✅ Новый чат <b>«{title}»</b> создан и выбран!", parse_mode="HTML", reply_markup=get_menu(m.from_user.id == ADMIN_ID))

@router.message(F.text == BTN_CHATS)
async def list_chats(m: Message):
    u = await get_user(m.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats WHERE user_id = ? ORDER BY chat_id DESC", (m.from_user.id,)) as cur:
            chats = await cur.fetchall()
    if not chats:
        return await m.answer("У тебя пока нет диалогов.")
    text = "💬 <b>Твои диалоги:</b>\n\n"
    kb = []
    for c in chats:
        act = "✅ " if c["chat_id"] == u["active_chat_id"] else ""
        text += f"{act}• <b>{c['title']}</b> (ID: <code>{c['chat_id']}</code>)\n"
        kb.append([
            InlineKeyboardButton(text=f"{act}{c['title']}", callback_data=f"sel_chat_{c['chat_id']}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_chat_{c['chat_id']}")
        ])
    await m.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("sel_chat_"))
async def sel_chat(call: CallbackQuery):
    cid = int(call.data.split("_")[2])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET active_chat_id = ? WHERE user_id = ?", (cid, call.from_user.id))
        await db.commit()
    await call.answer("Чат выбран!")
    await call.message.edit_text("✅ Вы переключились на выбранный чат.")

@router.callback_query(F.data.startswith("del_chat_"))
async def del_chat(call: CallbackQuery):
    cid = int(call.data.split("_")[2])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM messages WHERE chat_id = ?", (cid,))
        await db.execute("DELETE FROM chats WHERE chat_id = ?", (cid,))
        await db.commit()
    await call.answer("Чат удален.")
    await call.message.edit_text("🗑 Диалог был успешно удален.")

# ---------- Отзывы ----------
@router.message(F.text == BTN_REVIEWS)
async def reviews_menu(m: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reviews ORDER BY review_id DESC LIMIT 5") as cur:
            revs = await cur.fetchall()
    text = "⭐️ <b>Отзывы пользователей Klepa IA:</b>\n\n"
    if not revs:
        text += "Отзывов пока нет. Оставь первый!\n\n"
    else:
        for r in revs:
            text += f"👤 <b>@{r['username']}</b>: {r['text']}\n━━━━━━━━━━━━━━━━━━\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="add_review")]])
    await m.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "add_review")
async def add_review_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Review.text)
    await call.message.answer("✍️ Напиши свой отзыв о работе бота:")
    await call.answer()

@router.message(Review.text, F.text)
async def add_review_save(m: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO reviews (user_id, username, text) VALUES (?, ?, ?)", (m.from_user.id, m.from_user.username or m.from_user.full_name, m.text))
        await db.commit()
    await m.answer("❤️ Спасибо за отзыв! Он добавлен.", reply_markup=get_menu(m.from_user.id == ADMIN_ID))

# ---------- Оплата и Stars ----------
@router.message(F.text == BTN_PREMIUM)
async def buy_premium(m: Message):
    u = await get_user(m.from_user.id)
    if u["is_premium"]:
        return await m.answer("🌟 У тебя уже есть **Premium Навсегда**!")
    await m.answer_invoice(
        title="Klepa IA Premium (Навсегда)",
        description="Безлимитные сообщения навсегда!",
        payload=f"prem:{m.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=PREMIUM_PRICE_STARS)],
        provider_token="",
    )

@router.message(F.text == BTN_DONATE)
async def donate_start(m: Message, state: FSMContext):
    await state.set_state(Donate.amount)
    await m.answer("⭐ <b>Поддержка проекта Stars</b>\n\nВведи число Stars, которое хочешь отправить (или /cancel):")

@router.message(Donate.amount, F.text)
async def donate_amount(m: Message, state: FSMContext):
    if not m.text.isdigit() or int(m.text) <= 0:
        return await m.answer("Введи корректное целое число Stars.")
    amt = int(m.text)
    await state.clear()
    await m.answer_invoice(
        title="Донат для Klepa IA",
        description=f"Поддержка проекта на {amt} Stars",
        payload=f"donate:{m.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Донат", amount=amt)],
        provider_token="",
    )

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    payload = m.successful_payment.invoice_payload
    amt = m.successful_payment.total_amount
    if payload.startswith("prem:"):
        await set_premium(m.from_user.id)
        await m.answer("🎉 <b>Поздравляем! Premium навсегда успешно активирован!</b>", parse_mode="HTML")
    else:
        await m.answer(f"💛 Огромное спасибо за поддержку на ⭐ {amt}!", reply_markup=get_menu(m.from_user.id == ADMIN_ID))
    try:
        await bot.send_message(ADMIN_ID, f"💰 <b>Новая оплата!</b>\nОт: {user_nick(m)}\nСумма: <b>{amt} Stars</b> (Payload: {payload})", parse_mode="HTML")
    except Exception:
        log.exception("Ошибка уведомления админа")

# ---------- Техподдержка ----------
@router.message(F.text == BTN_SUPPORT)
async def support_start(m: Message, state: FSMContext):
    await state.set_state(Support.message)
    await m.answer("🆘 <b>Поддержка</b>\n\nНапиши своё сообщение администратору (или /cancel):")

@router.message(Support.message, F.text)
async def support_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_to_{m.from_user.id}")]])
    await bot.send_message(ADMIN_ID, f"🆘 <b>Обращение в поддержку</b>\nОт: {user_nick(m)}\nID: <code>{m.from_user.id}</code>\n\n{m.text}", parse_mode="HTML", reply_markup=kb)
    await m.answer("✅ Отправлено администратору!", reply_markup=get_menu(m.from_user.id == ADMIN_ID))

@router.callback_query(F.data.startswith("reply_to_"))
async def start_admin_reply(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    uid = int(call.data.split("_")[2])
    await state.update_data(target_user=uid)
    await state.set_state(AdminReply.text)
    await call.message.answer(f"✍️ Напиши ответ пользователю <code>{uid}</code>:", parse_mode="HTML")
    await call.answer()

@router.message(AdminReply.text, F.text)
async def send_admin_reply(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    uid = data.get("target_user")
    await state.clear()
    try:
        await bot.send_message(uid, f"💬 <b>Ответ поддержки:</b>\n\n{m.text}", parse_mode="HTML")
        await m.answer("✅ Ответ отправлен пользователю!")
    except Exception as e:
        await m.answer(f"❌ Ошибка отправки: {e}")

@router.message(F.text == "📊 Админ Статистика")
async def admin_stats(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    tot = await get_total_users()
    await m.answer(f"⚙️ <b>Статистика:</b>\nВсего пользователей: <code>{tot}</code>", parse_mode="HTML")

# ---------- OpenRouter ИИ Движок ----------
async def ask_ai(chat_id: int, user_text: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", user_text))
        await db.commit()
        async with db.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY message_id DESC LIMIT ?", (chat_id, MAX_HISTORY)) as cur:
            rows = await cur.fetchall()
            
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for r in reversed(rows):
        msgs.append({"role": r["role"], "content": r["content"]})

    payload = {"model": MODEL, "messages": msgs}
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "X-Title": BOT_NAME}
    
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        async with session.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                log.error("OpenRouter Error %s: %s", resp.status, data)
                raise RuntimeError("API Error")
                
    ans = data["choices"][0]["message"]["content"].strip()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", ans))
        await db.commit()
    return ans

@router.message(StateFilter(None), F.text)
async def ai_chat(m: Message, bot: Bot):
    u = await get_user(m.from_user.id)
    
    if not u["is_premium"] and u["messages_count"] >= FREE_LIMIT:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Купить Premium за 50 Stars", callback_data="buy_prem_cb")]])
        return await m.answer("⚠️ <b>Достигнут лимит 10 бесплатных сообщений!</b>\nКупи Premium навсегда за 50 Stars.", parse_mode="HTML", reply_markup=kb)

    text = m.text.strip()
    if CREATOR_RE.search(text):
        return await m.answer(f"Меня создал и разработал <b>{CREATOR}</b> ({CREATOR_USERNAME}) 👨‍💻\nОфициальное био: {BIO_LINK}", parse_mode="HTML")

    await inc_msg_count(m.from_user.id)
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    try:
        ans = await ask_ai(u["active_chat_id"], text)
    except Exception:
        return await m.answer("😔 Произошла ошибка генерации ответа. Попробуй чуть позже.")

    for i in range(0, len(ans), 4000):
        await m.answer(ans[i : i + 4000], parse_mode="Markdown")

@router.callback_query(F.data == "buy_prem_cb")
async def buy_prem_cb(call: CallbackQuery):
    await buy_premium(call.message)

# ---------- ВЕБ-СЕРВЕР ДЛЯ RENDER (ЧТОБЫ РАБОТАЛО НА БЕСПЛАТНОМ ТАРИФЕ) ----------
async def handle_ping(request):
    return web.Response(text=f"{BOT_NAME} is active and running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Веб-сервер запущен на порту {PORT}")

async def main():
    if not BOT_TOKEN or not OPENROUTER_API_KEY:
        raise SystemExit("Укажи BOT_TOKEN и OPENROUTER_API_KEY в Render Environment Variables")

    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запускаем локальный веб-сервер для Render в фоновом режиме
    await start_web_server()

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
