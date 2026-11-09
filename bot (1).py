import asyncio
import base64
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import parse_qsl

from aiohttp import ClientSession, ClientTimeout, web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

# ================= Настройки (переменные окружения) =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1424001997"))
MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")
PORT = int(os.getenv("PORT", "10000"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "10"))
PREMIUM_PRICE = int(os.getenv("PREMIUM_PRICE", "50"))
TZ = timezone(timedelta(hours=int(os.getenv("TZ_OFFSET", "3"))))
FILE_MAX_TOKENS = int(os.getenv("FILE_MAX_TOKENS", "6000"))
BIO_URL = os.getenv("BIO_URL", "https://t.me/Klepa_bio")
# Адрес Mini App: Render сам задаёт RENDER_EXTERNAL_URL, вручную можно указать WEBAPP_URL
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
APP_URL = f"{WEBAPP_URL}/app" if WEBAPP_URL else ""
# Если основная модель недоступна, бот пробует запасные (по умолчанию — бесплатный роутер OpenRouter)
MODELS = [MODEL] + [
    x.strip() for x in os.getenv("FALLBACK_MODELS", "openrouter/free").split(",")
    if x.strip() and x.strip() != MODEL
]

BOT_NAME = "Klepa IA"
CREATOR = "Marat"
MAX_HISTORY = 16
MAX_CONVS = 15
MAX_FILE_BYTES = 60 * 1024
REVIEWS_PER_PAGE = 5

SYSTEM_PROMPT = (
    f"Ты — {BOT_NAME}, живой, остроумный и дружелюбный собеседник и помощник в Telegram. "
    f"Тебя создал и разработал {CREATOR}, он же твой владелец.\n"
    "Стиль: общайся как умный друг — тепло, с лёгким юмором и уместными эмодзи (не перебарщивай), "
    "без канцелярита и шаблонных фраз вроде «Как ИИ-модель…». Пиши короткими абзацами, "
    "конкретно, с примерами. Иногда задавай встречный вопрос, чтобы разговор продолжался, "
    "и подхватывай детали из прошлых сообщений.\n"
    "Форматирование: **жирный** для важного, `инлайн-код` для команд и имён, а любой код — "
    "ВСЕГДА отдельным блоком ```язык ... ```. Не используй таблицы и заголовки с #.\n"
    "Не упоминай сторонние компании, названия моделей и то, как ты обучался или на чём работаешь. "
    f"На вопрос «кто ты» отвечай, что ты {BOT_NAME}. Не выдавай себя за человека, если спрашивают прямо. "
    "Отвечай на языке пользователя. Если чего-то не знаешь — честно скажи об этом."
)

PHOTO_PROMPT = (
    "Посмотри на изображение. Объясни, что на нём, и подскажи, куда и как действовать, "
    "если это инструкция, схема, ошибка, карта, прибор или задача. Отвечай по шагам и понятно."
)

FILE_PROMPT = (
    "Ты редактор файлов. Тебе дают файл и задачу. Верни ТОЛЬКО полное новое содержимое файла целиком — "
    "без пояснений, без markdown-обёрток и без ```. Сохрани формат и всё, что менять не нужно."
)

CREATOR_RE = re.compile(
    r"(кто\s+(тебя\s+)?(создал|сделал|разработал|написал|придумал)|"
    r"(твой|кто\s+твой)\s+(создатель|владелец|разработчик|автор|хозяин)|"
    r"кто\s+(создатель|владелец|разработчик|автор|хозяин)|"
    r"who\s+(made|created|built|developed|owns)\s+you|your\s+(creator|owner|developer))",
    re.IGNORECASE,
)

TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".csv", ".html", ".css",
    ".xml", ".yaml", ".yml", ".sql", ".ini", ".cfg", ".log", ".java", ".c", ".cpp", ".cs",
    ".php", ".go", ".rs", ".sh", ".kt", ".swift", ".toml",
}
IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("klepa")

router = Router()
busy: set[int] = set()
esc = html.escape


# ===== FORMAT START (Markdown -> Telegram HTML) =====
def fmt_inline(s: str) -> str:
    if not s:
        return ""
    res = []
    for p in re.split(r"(`[^`\n]+`)", s):
        if len(p) >= 2 and p.startswith("`") and p.endswith("`"):
            res.append(f"<code>{esc(p[1:-1], quote=False)}</code>")
            continue
        p = esc(p, quote=False)
        p = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", p, flags=re.M)
        p = re.sub(r"^(\s*)[-*]\s+", r"\1• ", p, flags=re.M)
        p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p, flags=re.S)
        p = re.sub(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", p)
        p = re.sub(r"~~(.+?)~~", r"<s>\1</s>", p)
        p = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s\"]+)\)", r'<a href="\2">\1</a>', p)
        res.append(p)
    return "".join(res)


def md_to_html(text: str) -> str:
    out, pos = [], 0
    while True:
        i = text.find("```", pos)
        if i == -1:
            out.append(fmt_inline(text[pos:]))
            break
        out.append(fmt_inline(text[pos:i]))
        j = text.find("```", i + 3)
        body = text[i + 3 :] if j == -1 else text[i + 3 : j]
        pos = len(text) if j == -1 else j + 3
        mt = re.match(r"([\w+#.\-]*)[ \t]*\n", body)
        if mt:
            lang, code = mt.group(1), body[mt.end() :]
        else:
            lang, code = "", body
        code = code.rstrip("\n") or "…"
        cls = f' class="language-{esc(lang)}"' if lang else ""
        out.append(f"<pre><code{cls}>{esc(code, quote=False)}</code></pre>")
        if j == -1:
            break
    return "".join(out)


def split_chunks(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines = []
    for line in text.split("\n"):
        while len(line) > limit:
            lines.append(line[:limit])
            line = line[limit:]
        lines.append(line)
    chunks, cur, size, in_code, lang = [], [], 0, False, ""
    for line in lines:
        if size + len(line) + 1 > limit and cur:
            body = "\n".join(cur)
            if in_code:
                body += "\n```"
            chunks.append(body)
            cur = [f"```{lang}"] if in_code else []
            size = len(cur[0]) + 1 if cur else 0
        cur.append(line)
        size += len(line) + 1
        if line.strip().startswith("```"):
            if in_code:
                in_code, lang = False, ""
            else:
                in_code, lang = True, line.strip()[3:].strip()
    chunks.append("\n".join(cur))
    return chunks
# ===== FORMAT END =====


# ================= База данных (Postgres или SQLite) =================
def _pg(q: str) -> str:
    n, out = 0, []
    for ch in q:
        if ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
        joined TEXT, last_seen TEXT, premium INTEGER DEFAULT 0,
        total_requests INTEGER DEFAULT 0, used_today INTEGER DEFAULT 0,
        used_day TEXT DEFAULT '', current_conv BIGINT DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS reviews (
        user_id BIGINT PRIMARY KEY, name TEXT, rating INTEGER, text TEXT, created TEXT)""",
    """CREATE TABLE IF NOT EXISTS payments (
        id {pk}, user_id BIGINT, kind TEXT, amount INTEGER, charge_id TEXT, created TEXT)""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id {pk}, user_id BIGINT, title TEXT, created TEXT, updated TEXT)""",
    """CREATE TABLE IF NOT EXISTS messages (
        id {pk}, conv_id BIGINT, role TEXT, content TEXT, created TEXT)""",
]


class DB:
    def __init__(self, url: str):
        self.url = url
        self.pg = bool(url)
        self.pool = None
        self.conn = None
        self.lock = asyncio.Lock()

    async def init(self):
        if self.pg:
            import asyncpg

            self.pool = await asyncpg.create_pool(
                self.url.split("?")[0], ssl="require", min_size=1, max_size=4
            )
            pk = "SERIAL PRIMARY KEY"
            log.info("База: PostgreSQL")
        else:
            import aiosqlite

            self.conn = await aiosqlite.connect(os.getenv("SQLITE_PATH", "klepa.db"))
            self.conn.row_factory = aiosqlite.Row
            pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
            log.warning("DATABASE_URL не задан: используется SQLite, данные пропадут при перезапуске!")
        for q in SCHEMA:
            await self.execute(q.format(pk=pk))
        try:  # миграция для баз, созданных старой версией
            await self.execute("ALTER TABLE users ADD COLUMN current_conv BIGINT DEFAULT 0")
        except Exception:
            pass

    async def execute(self, q: str, *a):
        if self.pg:
            async with self.pool.acquire() as c:
                await c.execute(_pg(q), *a)
        else:
            async with self.lock:
                await self.conn.execute(q, a)
                await self.conn.commit()

    async def one(self, q: str, *a):
        if self.pg:
            async with self.pool.acquire() as c:
                return await c.fetchrow(_pg(q), *a)
        async with self.lock:
            async with self.conn.execute(q, a) as cur:
                row = await cur.fetchone()
            await self.conn.commit()
            return row

    async def all(self, q: str, *a):
        if self.pg:
            async with self.pool.acquire() as c:
                return await c.fetch(_pg(q), *a)
        async with self.lock:
            async with self.conn.execute(q, a) as cur:
                return await cur.fetchall()


db = DB(DATABASE_URL)


def now() -> datetime:
    return datetime.now(TZ)


def today() -> str:
    return now().strftime("%Y-%m-%d")


def stamp() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


async def touch(u):
    await db.execute(
        """INSERT INTO users(user_id, username, first_name, joined, last_seen) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
           first_name=excluded.first_name, last_seen=excluded.last_seen""",
        u.id, u.username or "", u.first_name or "", stamp(), stamp(),
    )


async def get_user(uid: int):
    return await db.one("SELECT * FROM users WHERE user_id=?", uid)


def is_unlimited(row, uid: int) -> bool:
    return uid == ADMIN_ID or bool(row and row["premium"])


def used_today(row) -> int:
    return row["used_today"] if row and row["used_day"] == today() else 0


async def add_usage(uid: int):
    row = await get_user(uid)
    await db.execute(
        "UPDATE users SET used_today=?, used_day=?, total_requests=total_requests+1 WHERE user_id=?",
        used_today(row) + 1, today(), uid,
    )


# ---------- Диалоги (чаты) ----------
NEW_TITLE = "Новый чат"


async def delete_conv(cid: int):
    await db.execute("DELETE FROM messages WHERE conv_id=?", cid)
    await db.execute("DELETE FROM conversations WHERE id=?", cid)


async def new_conv(uid: int) -> int:
    n = (await db.one("SELECT COUNT(*) AS c FROM conversations WHERE user_id=?", uid))["c"]
    if n >= MAX_CONVS:
        old = await db.one(
            "SELECT id FROM conversations WHERE user_id=? ORDER BY updated ASC LIMIT 1", uid
        )
        if old:
            await delete_conv(old["id"])
    row = await db.one(
        "INSERT INTO conversations(user_id, title, created, updated) VALUES(?,?,?,?) RETURNING id",
        uid, NEW_TITLE, stamp(), stamp(),
    )
    cid = row["id"]
    await db.execute("UPDATE users SET current_conv=? WHERE user_id=?", cid, uid)
    return cid


async def ensure_conv(uid: int) -> int:
    row = await get_user(uid)
    cid = (row["current_conv"] or 0) if row else 0
    if cid and await db.one("SELECT id FROM conversations WHERE id=? AND user_id=?", cid, uid):
        return cid
    return await new_conv(uid)


async def load_history(cid: int) -> list[dict]:
    rows = await db.all(
        "SELECT role, content FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?", cid, MAX_HISTORY
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def save_exchange(cid: int, user_text: str, answer: str):
    await db.execute(
        "INSERT INTO messages(conv_id, role, content, created) VALUES(?,?,?,?)", cid, "user", user_text, stamp()
    )
    await db.execute(
        "INSERT INTO messages(conv_id, role, content, created) VALUES(?,?,?,?)", cid, "assistant", answer, stamp()
    )
    conv = await db.one("SELECT title FROM conversations WHERE id=?", cid)
    title = conv["title"] if conv else NEW_TITLE
    if title == NEW_TITLE:
        first = re.sub(r"\s+", " ", user_text.replace("[фото]", "").strip())[:40]
        title = first or NEW_TITLE
    await db.execute("UPDATE conversations SET title=?, updated=? WHERE id=?", title, stamp(), cid)


class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if getattr(event, "from_user", None):
            try:
                await touch(event.from_user)
            except Exception:
                log.exception("touch failed")
        return await handler(event, data)


router.message.outer_middleware(Track())
router.callback_query.outer_middleware(Track())


# ================= Состояния =================
class Donate(StatesGroup):
    amount = State()


class Support(StatesGroup):
    message = State()


class Review(StatesGroup):
    text = State()


class FileEdit(StatesGroup):
    task = State()


# ================= Клавиатуры =================
BTN_PROFILE = "👤 Профиль"
BTN_DIALOGS = "💬 Мои диалоги"
BTN_NEW = "➕ Новый чат"
BTN_CLEAR = "🧹 Очистить чат"
BTN_PREMIUM = f"⭐ Premium ({PREMIUM_PRICE} Stars)"
BTN_DONATE = "🌟 Задонатить Stars"
BTN_REVIEWS = "📝 Отзывы"
BTN_SUPPORT = "🆘 Поддержка"
BTN_BIO = "🔗 Klepa Bio"
BTN_STATS = "📊 Админ Статистика"
BTN_APP = "🚀 Открыть Mini App"


def menu_for(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_DIALOGS)],
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_CLEAR)],
        [KeyboardButton(text=BTN_PREMIUM), KeyboardButton(text=BTN_DONATE)],
        [KeyboardButton(text=BTN_REVIEWS), KeyboardButton(text=BTN_SUPPORT)],
        [KeyboardButton(text=BTN_BIO)],
    ]
    if APP_URL:
        rows.insert(0, [KeyboardButton(text=BTN_APP, web_app=WebAppInfo(url=APP_URL))])
    if uid == ADMIN_ID:
        rows.append([KeyboardButton(text=BTN_STATS)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Сообщение…")


BUY_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=f"💎 Купить Premium за {PREMIUM_PRICE} ⭐", callback_data="buy:premium")]]
)


def user_nick(u) -> str:
    return esc(f"@{u.username}") if u.username else esc(u.full_name or "без ника")


async def notify_admin(bot: Bot, text: str):
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        log.exception("Не удалось уведомить админа")


# ================= ИИ: запросы и потоковые ответы =================
BOT: Bot | None = None
LAST_ERROR = ""
_last_alert = 0.0
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def api_headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": BOT_NAME,
    }


def api_error_text(data) -> str:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return data[:200]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:200]
        if err:
            return str(err)[:200]
    return str(data)[:200]


async def alert_admin_error(err: str):
    global LAST_ERROR, _last_alert
    LAST_ERROR = err
    if BOT and time.monotonic() - _last_alert > 120:
        _last_alert = time.monotonic()
        await notify_admin(BOT, f"⚠️ <b>Ошибка ИИ</b>\n<code>{esc(err[:500])}</code>\n\nПроверка: /diag")


def err_detail(m: Message) -> str:
    if m.from_user.id == ADMIN_ID and LAST_ERROR:
        return f"\n\n<code>{esc(LAST_ERROR[:300])}</code>"
    return ""


async def call_ai(messages, max_tokens=1500, temperature=0.8):
    err = ""
    async with ClientSession(timeout=ClientTimeout(total=120)) as s:
        for model in MODELS:
            payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
            try:
                async with s.post(API_URL, json=payload, headers=api_headers()) as r:
                    data = await r.json(content_type=None)
                    if r.status == 200 and data.get("choices"):
                        ch = data["choices"][0]
                        text = (ch["message"].get("content") or "").strip()
                        if text:
                            return text, ch.get("finish_reason")
                        err = f"{model}: пустой ответ"
                    else:
                        err = f"{model}: {r.status} {api_error_text(data)}"
            except Exception as e:
                err = f"{model}: {e}"
            log.error(err)
    await alert_admin_error(err)
    raise RuntimeError(err)


async def stream_ai(messages, max_tokens=2000, temperature=0.8):
    err = ""
    async with ClientSession(timeout=ClientTimeout(total=180)) as s:
        for model in MODELS:
            payload = {
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": True,
            }
            acc = ""
            try:
                async with s.post(API_URL, json=payload, headers=api_headers()) as r:
                    if r.status != 200:
                        err = f"{model}: {r.status} {api_error_text(await r.text())}"
                        log.error(err)
                        continue
                    async for raw in r.content:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        if obj.get("error"):
                            raise RuntimeError(api_error_text(obj))
                        delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content")
                        if delta:
                            acc += delta
                            yield acc
            except Exception as e:
                if acc:
                    raise
                err = f"{model}: {e}"
                log.error(err)
                continue
            if acc.strip():
                return
            err = f"{model}: пустой ответ"
    await alert_admin_error(err)
    raise RuntimeError(err)


async def probe_models() -> str:
    lines = []
    async with ClientSession(timeout=ClientTimeout(total=60)) as s:
        try:
            async with s.get("https://openrouter.ai/api/v1/auth/key", headers=api_headers()) as r:
                raw = await r.text()
                if r.status == 200:
                    d = (json.loads(raw).get("data") or {})
                    lines.append(
                        f"Ключ: ✅ действует | бесплатный аккаунт: {d.get('is_free_tier')} | "
                        f"потрачено: {d.get('usage')} | лимит: {d.get('limit')}"
                    )
                else:
                    lines.append(f"Ключ: ❌ {r.status} {api_error_text(raw)}")
        except Exception as e:
            lines.append(f"Ключ: ❌ {e}")
        for model in MODELS:
            payload = {"model": model, "messages": [{"role": "user", "content": "Скажи: ок"}], "max_tokens": 10}
            try:
                async with s.post(API_URL, json=payload, headers=api_headers()) as r:
                    raw = await r.text()
                    lines.append(f"✅ {model}" if r.status == 200 else f"❌ {model}: {r.status} {api_error_text(raw)}")
            except Exception as e:
                lines.append(f"❌ {model}: {e}")
    return "\n".join(lines)


async def safe_send(m: Message, html_text: str, plain: str) -> Message:
    for _ in range(3):
        try:
            return await m.answer(html_text)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest:
            return await m.answer(plain, parse_mode=None)
    return await m.answer(plain, parse_mode=None)


async def safe_edit(msg: Message, html_text: str, plain: str):
    for _ in range(3):
        try:
            await msg.edit_text(html_text)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            try:
                await msg.edit_text(plain, parse_mode=None)
            except Exception:
                pass
            return


async def stream_answer(m: Message, messages, max_tokens=2000) -> str:
    sent: list[Message] = []
    rendered: list[str] = []
    placeholder = await m.answer("💭 Думаю…")
    sent.append(placeholder)
    rendered.append("💭 Думаю…")

    async def render(txt: str, final: bool):
        chunks = split_chunks(txt)
        for i, ch in enumerate(chunks):
            cursor = "" if (final or i < len(chunks) - 1) else " ▌"
            html_text = md_to_html(ch) + cursor
            if i < len(sent):
                if rendered[i] != html_text:
                    await safe_edit(sent[i], html_text, ch + cursor)
                    rendered[i] = html_text
            else:
                sent.append(await safe_send(m, html_text, ch + cursor))
                rendered.append(html_text)

    text, last = "", 0.0
    try:
        async for text in stream_ai(messages, max_tokens=max_tokens):
            if time.monotonic() - last >= 1.2:
                await render(text, False)
                last = time.monotonic()
        await render(text, True)
    except Exception:
        for s in sent:
            try:
                await s.delete()
            except Exception:
                pass
        raise
    return text


async def run_chat(m: Message, bot: Bot, text: str, image_b64: str | None = None, mime: str = "image/jpeg"):
    uid = m.from_user.id
    cid = await ensure_conv(uid)
    hist = await load_history(cid)
    if image_b64:
        content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]
    else:
        content = text
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *hist, {"role": "user", "content": content}]
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    answer = await stream_answer(m, messages)
    await save_exchange(cid, f"[фото] {text}" if image_b64 else text, answer)
    await add_usage(uid)
    await limit_hint(m)


# ================= Лимиты =================
async def check_quota(m: Message) -> bool:
    row = await get_user(m.from_user.id)
    if is_unlimited(row, m.from_user.id):
        return True
    if used_today(row) >= FREE_LIMIT:
        await m.answer(
            f"⛔ Лимит на сегодня исчерпан ({FREE_LIMIT}/{FREE_LIMIT}).\n"
            "Он обновится в полночь по Москве.\n\n"
            f"Или забери 💎 <b>Premium</b> за {PREMIUM_PRICE} ⭐ — без лимитов навсегда.",
            reply_markup=BUY_KB,
        )
        return False
    return True


async def limit_hint(m: Message):
    uid = m.from_user.id
    row = await get_user(uid)
    if is_unlimited(row, uid):
        return
    left = FREE_LIMIT - used_today(row)
    if left == 0:
        await m.answer("🏁 Это был последний бесплатный запрос на сегодня.", reply_markup=BUY_KB)
    elif left <= 2:
        await m.answer(f"ℹ️ Осталось запросов на сегодня: <b>{left}</b>")


class Guard:
    def __init__(self, m: Message):
        self.m = m
        self.ok = False

    async def __aenter__(self):
        uid = self.m.from_user.id
        if uid in busy:
            await self.m.answer("⏳ Секунду, я ещё отвечаю на предыдущее сообщение.")
            return self
        if not await check_quota(self.m):
            return self
        busy.add(uid)
        self.ok = True
        return self

    async def __aexit__(self, *a):
        if self.ok:
            busy.discard(self.m.from_user.id)


# ================= Старт, меню, профиль =================
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await ensure_conv(m.from_user.id)
    await m.answer(
        f"👋 Привет, <b>{esc(m.from_user.first_name or 'друг')}</b>!\n\n"
        f"Я <b>{BOT_NAME}</b> — твой умный собеседник и помощник.\n\n"
        "Что я умею:\n"
        "💬 болтать и отвечать на любые вопросы — печатаю ответ прямо у тебя на глазах\n"
        "👨‍💻 писать код — он оформляется отдельным блоком, копируется одним нажатием\n"
        "🖼 разбирать фото — пришли снимок и объясню что к чему\n"
        "📎 править файлы — пришли файл и напиши, что изменить\n"
        "🗂 вести несколько диалогов — кнопки «Новый чат» и «Мои диалоги»\n"
        "🚀 Mini App — весь бот как приложение (кнопка внизу)\n\n"
        f"Бесплатно доступно {FREE_LIMIT} запросов в день, а 💎 Premium снимает лимит навсегда.\n\n"
        "Пиши что угодно, меню — внизу 👇",
        reply_markup=menu_for(m.from_user.id),
    )


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=menu_for(m.from_user.id))


@router.message(Command("app"))
async def app_cmd(m: Message):
    if not APP_URL:
        return await m.answer("Mini App пока не настроен.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть Klepa IA", web_app=WebAppInfo(url=APP_URL))]]
    )
    await m.answer("Открывай Mini App 👇", reply_markup=kb)


@router.message(Command("diag"), F.from_user.id == ADMIN_ID)
async def diag(m: Message):
    msg = await m.answer("🔎 Проверяю ИИ…")
    text = await probe_models()
    await msg.edit_text(
        f"<b>Диагностика</b>\n\n{esc(text)}\n\n"
        f"База: {'PostgreSQL' if db.pg else 'SQLite (временная!)'}\n"
        f"Mini App: {esc(APP_URL) or 'не настроен'}",
        parse_mode="HTML",
    )


@router.message(F.text == BTN_PROFILE)
async def profile(m: Message, state: FSMContext):
    await state.clear()
    u = m.from_user
    row = await get_user(u.id)
    if u.id == ADMIN_ID:
        status, limit_line = "👑 Владелец", "∞"
    elif row["premium"]:
        status, limit_line = "💎 Premium (навсегда)", "∞"
    else:
        status, limit_line = "🆓 Бесплатный", f"{used_today(row)}/{FREE_LIMIT}"
    rv = await db.one("SELECT rating FROM reviews WHERE user_id=?", u.id)
    review_line = "⭐" * rv["rating"] if rv else "не оставлен"
    convs = (await db.one("SELECT COUNT(*) AS c FROM conversations WHERE user_id=?", u.id))["c"]
    text = (
        "👤 <b>Твой профиль</b>\n\n"
        f"Имя: {esc(u.first_name or '—')}\n"
        f"Ник: {user_nick(u)}\n"
        f"ID: <code>{u.id}</code>\n"
        f"В боте с: {row['joined'][:10]}\n\n"
        f"Статус: {status}\n"
        f"Запросов сегодня: <b>{limit_line}</b>\n"
        f"Запросов всего: <b>{row['total_requests']}</b>\n"
        f"Диалогов: <b>{convs}</b>\n"
        f"Твой отзыв: {review_line}"
    )
    await m.answer(text, reply_markup=None if is_unlimited(row, u.id) else BUY_KB)


@router.message(F.text == BTN_BIO)
async def bio(m: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Открыть Klepa Bio", url=BIO_URL)]])
    await m.answer("🔗 <b>Klepa Bio</b>\nНаши ссылки и новости — по кнопке ниже 👇", reply_markup=kb)


# ================= Диалоги =================
async def dialogs_view(uid: int):
    rows = await db.all(
        "SELECT id, title FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 10", uid
    )
    cur = (await get_user(uid))["current_conv"]
    kb = []
    for r in rows:
        mark = "✅" if r["id"] == cur else "💬"
        kb.append([
            InlineKeyboardButton(text=f"{mark} {r['title'][:32]}", callback_data=f"conv:open:{r['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"conv:del:{r['id']}"),
        ])
    kb.append([InlineKeyboardButton(text="➕ Новый чат", callback_data="conv:new")])
    text = "💬 <b>Мои диалоги</b>\nНажми на диалог, чтобы продолжить его, или 🗑 чтобы удалить."
    if not rows:
        text = "💬 <b>Мои диалоги</b>\nПока пусто — напиши что-нибудь, и диалог появится."
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(F.text == BTN_DIALOGS)
async def dialogs(m: Message, state: FSMContext):
    await state.clear()
    text, kb = await dialogs_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@router.message(F.text == BTN_NEW)
async def new_chat(m: Message, state: FSMContext):
    await state.clear()
    await new_conv(m.from_user.id)
    await m.answer("➕ Новый чат создан. Пиши — начнём с чистого листа! ✨", reply_markup=menu_for(m.from_user.id))


@router.message(F.text == BTN_CLEAR)
async def clear(m: Message, state: FSMContext):
    await state.clear()
    cid = await ensure_conv(m.from_user.id)
    await db.execute("DELETE FROM messages WHERE conv_id=?", cid)
    await db.execute("UPDATE conversations SET title=? WHERE id=?", NEW_TITLE, cid)
    await m.answer("🧹 Чат очищен. Я всё забыл — можно начинать заново!", reply_markup=menu_for(m.from_user.id))


async def refresh_dialogs(cb: CallbackQuery):
    text, kb = await dialogs_view(cb.from_user.id)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "conv:new")
async def conv_new(cb: CallbackQuery):
    await new_conv(cb.from_user.id)
    await refresh_dialogs(cb)
    await cb.answer("Новый чат создан ✨")


@router.callback_query(F.data.startswith("conv:open:"))
async def conv_open(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    conv = await db.one("SELECT id, title FROM conversations WHERE id=? AND user_id=?", cid, cb.from_user.id)
    if not conv:
        return await cb.answer("Диалог не найден", show_alert=True)
    await db.execute("UPDATE users SET current_conv=? WHERE user_id=?", cid, cb.from_user.id)
    last = await db.one(
        "SELECT content FROM messages WHERE conv_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", cid
    )
    await refresh_dialogs(cb)
    note = f"✅ Открыт диалог «{esc(conv['title'])}»"
    if last:
        snippet = last["content"].strip()
        snippet = snippet[:300] + ("…" if len(snippet) > 300 else "")
        note += f"\n\n<i>Последний ответ:</i>\n{esc(snippet)}"
    await cb.message.answer(note + "\n\nПродолжай писать 👇")
    await cb.answer()


@router.callback_query(F.data.startswith("conv:del:"))
async def conv_del(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    conv = await db.one("SELECT id FROM conversations WHERE id=? AND user_id=?", cid, cb.from_user.id)
    if not conv:
        return await cb.answer("Диалог не найден", show_alert=True)
    await delete_conv(cid)
    row = await get_user(cb.from_user.id)
    if row["current_conv"] == cid:
        await db.execute("UPDATE users SET current_conv=0 WHERE user_id=?", cb.from_user.id)
    await refresh_dialogs(cb)
    await cb.answer("Диалог удалён 🗑")


# ================= Premium =================
@router.message(F.text == BTN_PREMIUM)
async def premium_info(m: Message, state: FSMContext):
    await state.clear()
    row = await get_user(m.from_user.id)
    if is_unlimited(row, m.from_user.id):
        return await m.answer("💎 У тебя уже есть Premium навсегда. Спасибо за поддержку! 💛")
    await m.answer(
        "💎 <b>Premium</b>\n\n"
        f"• Без лимита запросов (вместо {FREE_LIMIT} в день)\n"
        "• Платишь один раз — действует навсегда\n"
        "• Работает для текста, фото и файлов\n\n"
        f"Цена: <b>{PREMIUM_PRICE} ⭐</b>",
        reply_markup=BUY_KB,
    )


@router.callback_query(F.data == "buy:premium")
async def buy_premium(cb: CallbackQuery):
    row = await get_user(cb.from_user.id)
    if is_unlimited(row, cb.from_user.id):
        return await cb.answer("У тебя уже есть Premium 💎", show_alert=True)
    await cb.message.answer_invoice(
        title=f"{BOT_NAME} Premium",
        description="Безлимитные запросы навсегда",
        payload="premium",
        currency="XTR",
        prices=[LabeledPrice(label="Premium навсегда", amount=PREMIUM_PRICE)],
        provider_token="",
    )
    await cb.answer()


# ================= Донат =================
@router.message(F.text == BTN_DONATE)
async def donate_start(m: Message, state: FSMContext):
    await state.set_state(Donate.amount)
    await m.answer(
        "🌟 <b>Поддержка проекта</b>\n\nСпасибо, что хочешь помочь! Напиши, сколько звёзд "
        "отправить (целое число от 1 до 100000).\n\nОтмена — /cancel"
    )


# ================= Отзывы =================
async def reviews_page(page: int):
    agg = await db.one("SELECT COUNT(*) AS c, AVG(rating) AS a FROM reviews")
    total = agg["c"]
    avg = float(agg["a"] or 0)
    rows = await db.all(
        "SELECT name, rating, text FROM reviews ORDER BY created DESC LIMIT ? OFFSET ?",
        REVIEWS_PER_PAGE, page * REVIEWS_PER_PAGE,
    )
    if total == 0:
        text = "📝 <b>Отзывы</b>\n\nПока отзывов нет. Будь первым! 💛"
    else:
        text = f"📝 <b>Отзывы</b>\nСредняя оценка: ⭐ <b>{avg:.1f}</b> ({total})\n\n"
        for r in rows:
            text += f"{'⭐' * r['rating']} <b>{esc(r['name'] or 'Аноним')}</b>\n{esc(r['text'])}\n\n"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"rev:p:{page - 1}"))
    if (page + 1) * REVIEWS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Дальше ▶️", callback_data=f"rev:p:{page + 1}"))
    kb = [nav] if nav else []
    kb.append([InlineKeyboardButton(text="✍️ Написать отзыв", callback_data="rev:write")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(F.text == BTN_REVIEWS)
async def reviews(m: Message, state: FSMContext):
    await state.clear()
    text, kb = await reviews_page(0)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rev:p:"))
async def reviews_nav(cb: CallbackQuery):
    page = max(0, int(cb.data.split(":")[2]))
    text, kb = await reviews_page(page)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data == "rev:write")
async def review_write(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"{i}⭐", callback_data=f"rate:{i}") for i in range(1, 6)]]
    )
    await cb.message.answer("Как оцениваешь бота?", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("rate:"))
async def review_rate(cb: CallbackQuery, state: FSMContext):
    n = min(5, max(1, int(cb.data.split(":")[1])))
    await state.set_state(Review.text)
    await state.update_data(rating=n)
    await cb.message.edit_text(
        f"Оценка: {'⭐' * n}\n\nТеперь напиши отзыв одним сообщением (до 500 символов).\nОтмена — /cancel"
    )
    await cb.answer()


# ================= Поддержка =================
@router.message(F.text == BTN_SUPPORT)
async def support_start(m: Message, state: FSMContext):
    await state.set_state(Support.message)
    await m.answer(
        "🆘 <b>Поддержка</b>\n\nОпиши проблему или вопрос одним сообщением — "
        "я передам его администратору.\n\nОтмена — /cancel"
    )


# ================= Админ =================
async def stats_data() -> dict:
    t = today()
    week = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
    d = {}
    d["total"] = (await db.one("SELECT COUNT(*) AS c FROM users"))["c"]
    d["new"] = (await db.one("SELECT COUNT(*) AS c FROM users WHERE joined LIKE ?", t + "%"))["c"]
    d["active"] = (await db.one("SELECT COUNT(*) AS c FROM users WHERE last_seen LIKE ?", t + "%"))["c"]
    d["active7"] = (await db.one("SELECT COUNT(*) AS c FROM users WHERE last_seen >= ?", week))["c"]
    d["premium"] = (await db.one("SELECT COUNT(*) AS c FROM users WHERE premium=1"))["c"]
    d["req_today"] = int((await db.one("SELECT COALESCE(SUM(used_today),0) AS s FROM users WHERE used_day=?", t))["s"])
    d["req_total"] = int((await db.one("SELECT COALESCE(SUM(total_requests),0) AS s FROM users"))["s"])
    d["convs"] = (await db.one("SELECT COUNT(*) AS c FROM conversations"))["c"]
    rv = await db.one("SELECT COUNT(*) AS c, AVG(rating) AS a FROM reviews")
    d["reviews"], d["avg"] = rv["c"], round(float(rv["a"] or 0), 1)
    pays = await db.all("SELECT kind, COALESCE(SUM(amount),0) AS s FROM payments GROUP BY kind")
    sums = {r["kind"]: int(r["s"]) for r in pays}
    d["stars_premium"], d["stars_donate"] = sums.get("premium", 0), sums.get("donate", 0)
    return d


async def stats_text() -> str:
    d = await stats_data()
    return (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{d['total']}</b>\n"
        f"🆕 Новых сегодня: <b>{d['new']}</b>\n"
        f"🔥 Активных сегодня: <b>{d['active']}</b>\n"
        f"📅 Активных за 7 дней: <b>{d['active7']}</b>\n\n"
        f"💬 Запросов сегодня: <b>{d['req_today']}</b>\n"
        f"💬 Запросов всего: <b>{d['req_total']}</b>\n"
        f"🗂 Диалогов: <b>{d['convs']}</b>\n\n"
        f"💎 Premium-пользователей: <b>{d['premium']}</b>\n"
        f"⭐ Звёзд за Premium: <b>{d['stars_premium']}</b>\n"
        f"🌟 Звёзд донатами: <b>{d['stars_donate']}</b>\n\n"
        f"📝 Отзывов: <b>{d['reviews']}</b> (средняя {d['avg']:.1f})"
    )


@router.message(F.from_user.id == ADMIN_ID, F.text == BTN_STATS)
async def stats_btn(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(await stats_text())


@router.message(Command("stats"), F.from_user.id == ADMIN_ID)
async def stats_cmd(m: Message):
    await m.answer(await stats_text())


async def set_premium(m: Message, bot: Bot, command: CommandObject, value: int):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        return await m.answer("Формат: /grant 123456789 или /revoke 123456789")
    uid = int(arg)
    if not await get_user(uid):
        return await m.answer("Такого пользователя нет в базе (он ещё не писал боту).")
    await db.execute("UPDATE users SET premium=? WHERE user_id=?", value, uid)
    await m.answer("✅ Готово.")
    if value:
        try:
            await bot.send_message(uid, "💎 Тебе выдан Premium навсегда. Приятного общения!")
        except Exception:
            pass


@router.message(Command("grant"), F.from_user.id == ADMIN_ID)
async def grant(m: Message, bot: Bot, command: CommandObject):
    await set_premium(m, bot, command, 1)


@router.message(Command("revoke"), F.from_user.id == ADMIN_ID)
async def revoke(m: Message, bot: Bot, command: CommandObject):
    await set_premium(m, bot, command, 0)


@router.message(
    F.from_user.id == ADMIN_ID, F.reply_to_message.text.contains("Обращение в поддержку"), F.text
)
async def admin_reply(m: Message, bot: Bot):
    found = re.search(r"ID:\s*(\d+)", m.reply_to_message.text or "")
    if not found:
        return await m.answer("Не нашёл ID пользователя в сообщении.")
    try:
        await bot.send_message(int(found.group(1)), f"💬 <b>Ответ поддержки:</b>\n\n{esc(m.text)}")
        await m.answer("✅ Ответ отправлен.")
    except Exception as e:
        await m.answer(f"❌ Не удалось отправить: {esc(str(e))}")


# ================= Состояния: ввод данных =================
@router.message(Donate.amount, F.text)
async def donate_amount(m: Message, state: FSMContext):
    txt = m.text.strip()
    if not txt.isdigit() or not (1 <= int(txt) <= 100000):
        return await m.answer("Введи целое число от 1 до 100000 или /cancel для отмены.")
    await state.clear()
    await m.answer_invoice(
        title="Поддержка проекта",
        description=f"Спасибо, что помогаешь развивать {BOT_NAME}!",
        payload="donate",
        currency="XTR",
        prices=[LabeledPrice(label="Поддержка", amount=int(txt))],
        provider_token="",
    )


@router.message(Support.message, F.text)
async def support_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await bot.send_message(
        ADMIN_ID,
        "🆘 <b>Обращение в поддержку</b>\n"
        f"От: {user_nick(m.from_user)}\nID: <code>{m.from_user.id}</code>\n\n{esc(m.text)}\n\n"
        "<i>Чтобы ответить — сделай reply на это сообщение.</i>",
    )
    await m.answer("✅ Отправлено! Администратор ответит, как только сможет.", reply_markup=menu_for(m.from_user.id))


@router.message(Review.text, F.text)
async def review_text(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    rating = int(data.get("rating", 5))
    text = m.text.strip()[:500]
    name = (m.from_user.first_name or "Аноним")[:40]
    await db.execute(
        """INSERT INTO reviews(user_id, name, rating, text, created) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, rating=excluded.rating,
           text=excluded.text, created=excluded.created""",
        m.from_user.id, name, rating, text, stamp(),
    )
    await m.answer("💛 Спасибо за отзыв! Он уже в списке.", reply_markup=menu_for(m.from_user.id))
    await notify_admin(
        bot,
        f"📝 <b>Новый отзыв</b> {'⭐' * rating}\nОт: {user_nick(m.from_user)} (<code>{m.from_user.id}</code>)\n\n{esc(text)}",
    )


# ================= Оплата =================
@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    if q.invoice_payload == "premium":
        row = await get_user(q.from_user.id)
        if row and row["premium"]:
            return await q.answer(ok=False, error_message="У тебя уже есть Premium 💎")
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    sp = m.successful_payment
    kind = "premium" if sp.invoice_payload == "premium" else "donate"
    await db.execute(
        "INSERT INTO payments(user_id, kind, amount, charge_id, created) VALUES(?,?,?,?,?)",
        m.from_user.id, kind, sp.total_amount, sp.telegram_payment_charge_id, stamp(),
    )
    if kind == "premium":
        await db.execute("UPDATE users SET premium=1 WHERE user_id=?", m.from_user.id)
        await m.answer(
            "💎 <b>Premium активирован навсегда!</b>\nТеперь лимитов нет. Спасибо огромное! 💛",
            reply_markup=menu_for(m.from_user.id),
        )
        title = "💎 <b>Куплен Premium!</b>"
    else:
        await m.answer(
            f"💛 Спасибо огромное! Получено ⭐ {sp.total_amount}. Ты помогаешь проекту расти!",
            reply_markup=menu_for(m.from_user.id),
        )
        title = "🌟 <b>Новая поддержка проекта!</b>"
    await notify_admin(
        bot,
        f"{title}\n\nОт: {user_nick(m.from_user)}\nID: <code>{m.from_user.id}</code>\n"
        f"Сумма: <b>{sp.total_amount}</b> ⭐",
    )


# ================= Фото и файлы =================
async def handle_image(m: Message, bot: Bot, file_obj, mime: str):
    async with Guard(m) as g:
        if not g.ok:
            return
        try:
            buf = io.BytesIO()
            await bot.download(file_obj, destination=buf)
            b64 = base64.b64encode(buf.getvalue()).decode()
            prompt = (m.caption or "").strip() or PHOTO_PROMPT
            await run_chat(m, bot, prompt, b64, mime)
        except Exception:
            log.exception("vision failed")
            await m.answer("😔 Не получилось разобрать картинку. Попробуй ещё раз чуть позже." + err_detail(m))


def strip_fences(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rstrip()
        if t.endswith("```"):
            t = t[:-3]
    return t.strip("\n") + "\n"


async def edit_file(m: Message, bot: Bot, file_id: str, name: str, task: str):
    async with Guard(m) as g:
        if not g.ok:
            return
        await bot.send_chat_action(m.chat.id, ChatAction.UPLOAD_DOCUMENT)
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        raw = buf.getvalue()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("cp1251", errors="replace")
        messages = [
            {"role": "system", "content": FILE_PROMPT},
            {"role": "user", "content": f"Имя файла: {name}\nЗадача: {task}\n\nСодержимое файла:\n{content}"},
        ]
        try:
            result, reason = await call_ai(messages, max_tokens=FILE_MAX_TOKENS, temperature=0.2)
        except Exception:
            log.exception("file edit failed")
            return await m.answer("😔 Не получилось обработать файл. Попробуй ещё раз чуть позже." + err_detail(m))
        if reason == "length":
            return await m.answer("📏 Файл получился слишком большим для обработки. Пришли файл поменьше или раздели его на части.")
        result = strip_fences(result)
        if not result.strip():
            return await m.answer("😔 Получился пустой результат. Попробуй переформулировать задачу.")
        p = PurePosixPath(name or "file.txt")
        out_name = f"{p.stem}_edited{p.suffix}"
        await m.answer_document(
            BufferedInputFile(result.encode("utf-8"), filename=out_name),
            caption="✅ Готово! Файл изменён по твоей задаче.",
        )
        await add_usage(m.from_user.id)
        await limit_hint(m)


@router.message(StateFilter(None), F.photo)
async def on_photo(m: Message, bot: Bot):
    await handle_image(m, bot, m.photo[-1], "image/jpeg")


@router.message(StateFilter(None), F.document)
async def on_document(m: Message, state: FSMContext, bot: Bot):
    d = m.document
    if (d.mime_type or "") in IMAGE_MIME:
        return await handle_image(m, bot, d, d.mime_type)
    ext = PurePosixPath(d.file_name or "").suffix.lower()
    if ext not in TEXT_EXT:
        return await m.answer(
            "📎 Я умею менять текстовые файлы: txt, md, py, js, json, csv, html, css, xml, yaml, sql и подобные.\n"
            "Фото и картинки можно присылать как обычно."
        )
    if d.file_size and d.file_size > MAX_FILE_BYTES:
        return await m.answer(f"📏 Файл слишком большой. Максимум — {MAX_FILE_BYTES // 1024} КБ.")
    if m.caption and m.caption.strip():
        return await edit_file(m, bot, d.file_id, d.file_name, m.caption.strip())
    await state.set_state(FileEdit.task)
    await state.update_data(file_id=d.file_id, file_name=d.file_name)
    await m.answer("📎 Файл получил! Что с ним сделать? Напиши задачу одним сообщением.\nОтмена — /cancel")


@router.message(FileEdit.task, F.text)
async def file_task(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await edit_file(m, bot, data["file_id"], data["file_name"], m.text.strip())


# ================= Обычный чат =================
@router.message(StateFilter(None), F.text)
async def ai_chat(m: Message, bot: Bot):
    text = m.text.strip()
    if CREATOR_RE.search(text):
        return await m.answer(f"Меня создал и разработал <b>{CREATOR}</b> 👨‍💻 Он же мой владелец.")
    async with Guard(m) as g:
        if not g.ok:
            return
        try:
            await run_chat(m, bot, text)
        except Exception:
            log.exception("chat failed")
            await m.answer("😔 Сейчас не получилось ответить. Попробуй ещё раз чуть позже." + err_detail(m))


# ================= Mini App: сервер и API =================
WEBAPP_FILE = Path(__file__).with_name("webapp.html")


def J(data, status: int = 200):
    return web.json_response(data, status=status)


def verify_init_data(init_data: str):
    """Проверка подписи Telegram: только настоящий пользователь Telegram может вызвать API."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        recv = parsed.pop("hash", "")
        if not recv:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, recv):
            return None
        if time.time() - int(parsed.get("auth_date", "0")) > 86400 * 2:
            return None
        return json.loads(parsed["user"])
    except Exception:
        return None


async def api_user(request):
    data = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not data:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "auth"}), content_type="application/json")
    first = data.get("first_name") or ""
    u = SimpleNamespace(
        id=int(data["id"]), username=data.get("username"), first_name=first,
        full_name=f"{first} {data.get('last_name') or ''}".strip(),
    )
    await touch(u)
    return u


async def read_json(request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


async def serve_app(request):
    if not WEBAPP_FILE.exists():
        return web.Response(status=404, text="webapp.html не найден в репозитории")
    return web.FileResponse(WEBAPP_FILE, headers={"Cache-Control": "no-store"})


async def api_me(request):
    u = await api_user(request)
    row = await get_user(u.id)
    rv = await db.one("SELECT rating FROM reviews WHERE user_id=?", u.id)
    convs = (await db.one("SELECT COUNT(*) AS c FROM conversations WHERE user_id=?", u.id))["c"]
    status = "owner" if u.id == ADMIN_ID else ("premium" if row["premium"] else "free")
    return J({
        "id": u.id, "name": u.first_name, "username": u.username, "joined": row["joined"][:10],
        "status": status, "unlimited": is_unlimited(row, u.id), "used": used_today(row),
        "limit": FREE_LIMIT, "total": row["total_requests"], "convs": convs,
        "review": rv["rating"] if rv else 0, "admin": u.id == ADMIN_ID,
        "price": PREMIUM_PRICE, "bio": BIO_URL,
    })


async def api_dialogs(request):
    u = await api_user(request)
    rows = await db.all(
        "SELECT id, title FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 15", u.id
    )
    cur = (await get_user(u.id))["current_conv"]
    return J({"current": cur, "items": [{"id": r["id"], "title": r["title"]} for r in rows]})


async def api_dialog_new(request):
    u = await api_user(request)
    return J({"id": await new_conv(u.id)})


async def api_dialog_open(request):
    u = await api_user(request)
    cid = int((await read_json(request)).get("id", 0))
    if not await db.one("SELECT id FROM conversations WHERE id=? AND user_id=?", cid, u.id):
        return J({"error": "not_found"}, 404)
    await db.execute("UPDATE users SET current_conv=? WHERE user_id=?", cid, u.id)
    return J({"ok": True})


async def api_dialog_delete(request):
    u = await api_user(request)
    cid = int((await read_json(request)).get("id", 0))
    if not await db.one("SELECT id FROM conversations WHERE id=? AND user_id=?", cid, u.id):
        return J({"error": "not_found"}, 404)
    await delete_conv(cid)
    if (await get_user(u.id))["current_conv"] == cid:
        await db.execute("UPDATE users SET current_conv=0 WHERE user_id=?", u.id)
    return J({"ok": True})


async def api_messages(request):
    u = await api_user(request)
    cid = await ensure_conv(u.id)
    rows = await db.all("SELECT role, content FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT 60", cid)
    conv = await db.one("SELECT title FROM conversations WHERE id=?", cid)
    return J({
        "title": conv["title"] if conv else NEW_TITLE,
        "items": [{"role": r["role"], "content": r["content"]} for r in reversed(rows)],
    })


async def api_clear(request):
    u = await api_user(request)
    cid = await ensure_conv(u.id)
    await db.execute("DELETE FROM messages WHERE conv_id=?", cid)
    await db.execute("UPDATE conversations SET title=? WHERE id=?", NEW_TITLE, cid)
    return J({"ok": True})


async def api_chat(request):
    u = await api_user(request)
    b = await read_json(request)
    text = (b.get("text") or "").strip()[:8000]
    image = b.get("image") or None
    mime = b.get("mime") if b.get("mime") in IMAGE_MIME else "image/jpeg"
    if not text and not image:
        return J({"error": "empty"}, 400)
    row = await get_user(u.id)
    unlimited = is_unlimited(row, u.id)
    if not unlimited and used_today(row) >= FREE_LIMIT:
        return J({"error": "limit"}, 429)
    if u.id in busy:
        return J({"error": "busy"}, 429)
    busy.add(u.id)

    resp = web.StreamResponse(headers={
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)

    async def send(obj):
        await resp.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    try:
        if not image and CREATOR_RE.search(text):
            await send({"d": f"Меня создал и разработал **{CREATOR}** 👨‍💻 Он же мой владелец."})
            await send({"done": True, "left": None if unlimited else max(0, FREE_LIMIT - used_today(row))})
            return resp
        prompt = text or PHOTO_PROMPT
        cid = await ensure_conv(u.id)
        hist = await load_history(cid)
        if image:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image}"}},
            ]
        else:
            content = prompt
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *hist, {"role": "user", "content": content}]
        prev = ""
        async for acc in stream_ai(messages):
            await send({"d": acc[len(prev):]})
            prev = acc
        await save_exchange(cid, f"[фото] {prompt}" if image else prompt, prev)
        await add_usage(u.id)
        row = await get_user(u.id)
        left = None if unlimited else max(0, FREE_LIMIT - used_today(row))
        await send({"done": True, "left": left})
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception:
        log.exception("api chat failed")
        try:
            await send({"error": "ai"})
        except Exception:
            pass
    finally:
        busy.discard(u.id)
    return resp


async def api_reviews(request):
    await api_user(request)
    try:
        page = max(0, int(request.query.get("page", "0")))
    except ValueError:
        page = 0
    agg = await db.one("SELECT COUNT(*) AS c, AVG(rating) AS a FROM reviews")
    rows = await db.all(
        "SELECT name, rating, text, created FROM reviews ORDER BY created DESC LIMIT ? OFFSET ?",
        REVIEWS_PER_PAGE, page * REVIEWS_PER_PAGE,
    )
    return J({
        "total": agg["c"], "avg": round(float(agg["a"] or 0), 1),
        "more": (page + 1) * REVIEWS_PER_PAGE < agg["c"],
        "items": [
            {"name": r["name"] or "Аноним", "rating": r["rating"], "text": r["text"], "date": r["created"][:10]}
            for r in rows
        ],
    })


async def api_review(request):
    u = await api_user(request)
    b = await read_json(request)
    try:
        rating = int(b.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    text = (b.get("text") or "").strip()[:500]
    if not 1 <= rating <= 5 or not text:
        return J({"error": "invalid"}, 400)
    name = (u.first_name or "Аноним")[:40]
    await db.execute(
        """INSERT INTO reviews(user_id, name, rating, text, created) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, rating=excluded.rating,
           text=excluded.text, created=excluded.created""",
        u.id, name, rating, text, stamp(),
    )
    await notify_admin(
        request.app["bot"],
        f"📝 <b>Новый отзыв</b> {'⭐' * rating}\nОт: {user_nick(u)} (<code>{u.id}</code>)\n\n{esc(text)}",
    )
    return J({"ok": True})


async def api_invoice(request):
    u = await api_user(request)
    b = await read_json(request)
    bot = request.app["bot"]
    kind = b.get("kind")
    if kind == "premium":
        if is_unlimited(await get_user(u.id), u.id):
            return J({"error": "already"}, 400)
        link = await bot.create_invoice_link(
            title=f"{BOT_NAME} Premium", description="Безлимитные запросы навсегда", payload="premium",
            currency="XTR", prices=[LabeledPrice(label="Premium навсегда", amount=PREMIUM_PRICE)],
            provider_token="",
        )
    elif kind == "donate":
        try:
            amount = int(b.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        if not 1 <= amount <= 100000:
            return J({"error": "amount"}, 400)
        link = await bot.create_invoice_link(
            title="Поддержка проекта", description=f"Спасибо, что помогаешь развивать {BOT_NAME}!",
            payload="donate", currency="XTR", prices=[LabeledPrice(label="Поддержка", amount=amount)],
            provider_token="",
        )
    else:
        return J({"error": "kind"}, 400)
    return J({"link": link})


async def api_support(request):
    u = await api_user(request)
    text = ((await read_json(request)).get("text") or "").strip()[:2000]
    if not text:
        return J({"error": "empty"}, 400)
    await request.app["bot"].send_message(
        ADMIN_ID,
        "🆘 <b>Обращение в поддержку</b>\n"
        f"От: {user_nick(u)}\nID: <code>{u.id}</code>\n\n{esc(text)}\n\n"
        "<i>Чтобы ответить — сделай reply на это сообщение.</i>",
    )
    return J({"ok": True})


async def api_stats(request):
    u = await api_user(request)
    if u.id != ADMIN_ID:
        return J({"error": "forbidden"}, 403)
    return J(await stats_data())


async def health(_):
    return web.Response(text=f"{BOT_NAME} is running")


def make_app(bot: Bot) -> web.Application:
    app = web.Application(client_max_size=12 * 1024 * 1024)
    app["bot"] = bot
    app.router.add_get("/", health)
    app.router.add_get("/app", serve_app)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/dialogs", api_dialogs)
    app.router.add_post("/api/dialogs/new", api_dialog_new)
    app.router.add_post("/api/dialogs/open", api_dialog_open)
    app.router.add_post("/api/dialogs/delete", api_dialog_delete)
    app.router.add_get("/api/messages", api_messages)
    app.router.add_post("/api/clear", api_clear)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_get("/api/reviews", api_reviews)
    app.router.add_post("/api/review", api_review)
    app.router.add_post("/api/invoice", api_invoice)
    app.router.add_post("/api/support", api_support)
    app.router.add_get("/api/stats", api_stats)
    return app


async def main():
    global BOT
    if not BOT_TOKEN or not OPENROUTER_API_KEY:
        raise SystemExit("Задай BOT_TOKEN и OPENROUTER_API_KEY в переменных окружения")

    await db.init()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    BOT = bot
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    runner = web.AppRunner(make_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await bot.delete_webhook(drop_pending_updates=True)
    if APP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Klepa IA", web_app=WebAppInfo(url=APP_URL))
            )
        except Exception:
            log.exception("Не удалось поставить кнопку меню Mini App")
    log.info("Mini App: %s", APP_URL or "не настроен (задай WEBAPP_URL)")
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
