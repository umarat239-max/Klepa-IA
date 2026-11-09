import asyncio
import logging
import os
import re

from aiohttp import ClientSession, ClientTimeout, web
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
)

# ---------- Настройки (берутся из переменных окружения) ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1424001997"))
MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")
PORT = int(os.getenv("PORT", "10000"))

BOT_NAME = "Klepa IA"
CREATOR = "Marat"
MAX_HISTORY = 12

SYSTEM_PROMPT = (
    f"Ты — {BOT_NAME}, умный и дружелюбный ассистент в Telegram. "
    f"Тебя создал и разработал {CREATOR}, он же твой владелец. "
    "Не упоминай названия сторонних компаний, моделей и сервисов, на которых ты работаешь, "
    "и не рассказывай, как ты обучался. Если спрашивают, кто ты — ты Klepa IA. "
    "Никогда не выдавай себя за человека. "
    "Отвечай на языке пользователя, понятно, по делу и дружелюбно."
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
history: dict[int, list[dict]] = {}


class Donate(StatesGroup):
    amount = State()


class Support(StatesGroup):
    message = State()


# ---------- Клавиатура ----------
BTN_PROFILE = "👤 Профиль"
BTN_DONATE = "⭐ Поддержать проект"
BTN_SUPPORT = "🆘 Поддержка"
BTN_CLEAR = "🗑 Очистить диалог"

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_CLEAR)],
        [KeyboardButton(text=BTN_DONATE), KeyboardButton(text=BTN_SUPPORT)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Напишите сообщение…",
)


def user_nick(m: Message) -> str:
    u = m.from_user
    return f"@{u.username}" if u.username else (u.full_name or "без ника")


# ---------- Команды и меню ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        f"👋 Привет, <b>{m.from_user.first_name}</b>!\n\n"
        f"Я <b>{BOT_NAME}</b> — твой умный помощник. "
        "Спрашивай что угодно: отвечу, помогу с текстом, идеями, учёбой и не только.\n\n"
        "Меню внизу — для быстрых действий 👇",
        reply_markup=MENU,
    )


@router.message(F.text == BTN_PROFILE)
async def profile(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "👤 <b>Твой профиль</b>\n\n"
        f"Ник: {user_nick(m)}\n"
        f"ID: <code>{m.from_user.id}</code>",
        reply_markup=MENU,
    )


@router.message(F.text == BTN_CLEAR)
async def clear(m: Message, state: FSMContext):
    await state.clear()
    history.pop(m.from_user.id, None)
    await m.answer("🧹 Диалог очищен. Начнём заново!", reply_markup=MENU)


# ---------- Поддержка проекта звёздами ----------
@router.message(F.text == BTN_DONATE)
async def donate_start(m: Message, state: FSMContext):
    await state.set_state(Donate.amount)
    await m.answer(
        "⭐ <b>Поддержка проекта</b>\n\n"
        "Спасибо, что хочешь помочь! Напиши, сколько звёзд хочешь отправить "
        "(целое число от 1 до 100000).\n\nОтмена — /cancel"
    )


@router.message(Donate.amount, F.text)
async def donate_amount(m: Message, state: FSMContext):
    txt = m.text.strip()
    if not txt.isdigit() or not (1 <= int(txt) <= 100000):
        await m.answer("Введи целое число от 1 до 100000 или /cancel для отмены.")
        return
    amount = int(txt)
    await state.clear()
    await m.answer_invoice(
        title="Поддержка проекта",
        description=f"Спасибо, что помогаешь развивать {BOT_NAME}!",
        payload=f"donate:{m.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Поддержка", amount=amount)],
        provider_token="",
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    amount = m.successful_payment.total_amount
    await m.answer(f"💛 Спасибо огромное! Получено ⭐ {amount}. Ты помогаешь проекту расти!", reply_markup=MENU)
    try:
        await bot.send_message(
            ADMIN_ID,
            "⭐ <b>Новая поддержка!</b>\n\n"
            f"От: {user_nick(m)}\nID: <code>{m.from_user.id}</code>\nСумма: <b>{amount}</b> ⭐",
        )
    except Exception:
        log.exception("Не удалось уведомить админа")


# ---------- Поддержка пользователей ----------
@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=MENU)


@router.message(F.text == BTN_SUPPORT)
async def support_start(m: Message, state: FSMContext):
    await state.set_state(Support.message)
    await m.answer(
        "🆘 <b>Поддержка</b>\n\nОпиши свою проблему или вопрос одним сообщением — "
        "я передам его администратору.\n\nОтмена — /cancel"
    )


@router.message(Support.message, F.text)
async def support_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await bot.send_message(
        ADMIN_ID,
        "🆘 <b>Обращение в поддержку</b>\n"
        f"От: {user_nick(m)}\nID: <code>{m.from_user.id}</code>\n\n{m.text}\n\n"
        "<i>Чтобы ответить — сделай reply на это сообщение.</i>",
    )
    await m.answer("✅ Отправлено! Администратор ответит как только сможет.", reply_markup=MENU)


@router.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_reply(m: Message, bot: Bot):
    src = m.reply_to_message.text or ""
    found = re.search(r"ID:\s*(\d+)", src)
    if not found or "Обращение в поддержку" not in src:
        return await ai_chat(m, bot)
    try:
        await bot.send_message(int(found.group(1)), f"💬 <b>Ответ поддержки:</b>\n\n{m.text}")
        await m.answer("✅ Ответ отправлен.")
    except Exception as e:
        await m.answer(f"❌ Не удалось отправить: {e}")


# ---------- Общение с ИИ ----------
async def ask_ai(user_id: int, text: str) -> str:
    msgs = history.setdefault(user_id, [])
    msgs.append({"role": "user", "content": text})
    del msgs[:-MAX_HISTORY]

    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *msgs],
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": BOT_NAME,
    }
    async with ClientSession(timeout=ClientTimeout(total=90)) as s:
        async with s.post(
            "https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers
        ) as r:
            data = await r.json()
            if r.status != 200:
                log.error("OpenRouter %s: %s", r.status, data)
                msgs.pop()
                raise RuntimeError("api error")
    answer = data["choices"][0]["message"]["content"].strip()
    msgs.append({"role": "assistant", "content": answer})
    del msgs[:-MAX_HISTORY]
    return answer


@router.message(StateFilter(None), F.text)
async def ai_chat(m: Message, bot: Bot):
    text = m.text.strip()
    if CREATOR_RE.search(text):
        return await m.answer(f"Меня создал и разработал <b>{CREATOR}</b> 👨‍💻 Он же мой владелец.")

    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        answer = await ask_ai(m.from_user.id, text)
    except Exception:
        return await m.answer("😔 Сейчас не получилось ответить. Попробуй ещё раз чуть позже.")

    for i in range(0, len(answer), 4000):
        await m.answer(answer[i : i + 4000], parse_mode=None)


# ---------- Веб-сервер для Render ----------
async def health(_):
    return web.Response(text=f"{BOT_NAME} is running")


async def main():
    if not BOT_TOKEN or not OPENROUTER_API_KEY:
        raise SystemExit("Задай BOT_TOKEN и OPENROUTER_API_KEY в переменных окружения")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
