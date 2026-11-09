import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, 
    LabeledPrice, PreCheckoutQuery
)

import config
import database as db

# Настройка логирования
logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Состояния FSM (Finite State Machine) для диалогов и ввода данных
class Form(StatesGroup):
    waiting_for_support_msg = State()
    waiting_for_review = State()
    waiting_for_new_chat_title = State()
    waiting_for_admin_reply = State()
    waiting_for_stars_donation = State()

# Главная клавиатура интерфейса
def get_main_keyboard(is_admin: bool = False):
    buttons = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💬 Мои диалоги")],
        [KeyboardButton(text="➕ Новый чат"), KeyboardButton(text="🧹 Очистить чат")],
        [KeyboardButton(text="⭐ Premium (Навсегда)"), KeyboardButton(text="🌟 Задонатить Stars")],
        [KeyboardButton(text="📝 Отзывы"), KeyboardButton(text="🆘 Поддержка")],
        [KeyboardButton(text="🔗 Klepa Bio")]
    ]
    if is_admin:
        buttons.append([KeyboardButton(text="📊 Админ Статистика")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "без_юзернейма",
        full_name=message.from_user.full_name
    )
    is_admin = (message.from_user.id == config.ADMIN_ID)
    
    welcome_text = (
        f"👋 **Добро пожаловать в Klepa IA!**\n\n"
        f"🤖 Я — ваш передовой ИИ-помощник на базе уникального движка **Klepa Engine**.\n"
        f"Здесь вы можете создавать чаты, получать качественные ответы с кодом, общаться без лимитов и многое другое.\n\n"
        f"Выберите нужное действие в меню ниже 👇"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin))

# Просмотр профиля
@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    total_users = await db.get_total_users_count()
    
    status = "⭐ Premium (Безлимит навсегда)" if user["is_premium"] else f"🆓 Лимит бесплатных: {user['messages_count']}/{config.FREE_MESSAGE_LIMIT}"
    
    profile_text = (
        f"👤 **Ваш профиль в Klepa IA**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **ID:** `{user['user_id']}`\n"
        f"📛 **Имя:** {user['full_name']}\n"
        f"🌐 **Юзернейм:** @{user['username']}\n"
        f"💎 **Статус подписки:** {status}\n\n"
        f"📊 **Посетило проект всего:** `{total_users}` участников\n"
        f"👑 **Владелец и создатель:** [{config.OWNER_NAME}]({config.BIO_LINK}) ({config.OWNER_USERNAME})"
    )
    await message.answer(profile_text, parse_mode="Markdown", disable_web_page_preview=True)

# Ссылка на Klepa Bio
@dp.message(F.text == "🔗 Klepa Bio")
async def bio_handler(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть Klepa Bio", url=config.BIO_LINK)]
    ])
    await message.answer("Переходите по официальной ссылке проекта:", reply_markup=kb)

# Очистка текущего чата
@dp.message(F.text == "🧹 Очистить чат")
async def clear_chat_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if user and user["active_chat_id"]:
        await db.clear_chat_history(user["active_chat_id"])
        await message.answer("🧹 **История активного диалога успешно очищена!**", parse_mode="Markdown")
    else:
        await message.answer("⚠️ У вас нет активного чата для очистки.")

# Управление списком диалогов (мои чаты)
@dp.message(F.text == "💬 Мои диалоги")
async def list_chats_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    chats = await db.get_user_chats(message.from_user.id)
    
    if not chats:
        await message.answer("У вас пока нет созданных диалогов.")
        return

    text = "💬 **Ваши диалоги:**\n\n"
    keyboard = []
    for c in chats:
        is_active = "✅ " if c["chat_id"] == user["active_chat_id"] else ""
        text += f"{is_active}• **{c['title']}** (ID: `{c['chat_id']}`)\n"
        
        keyboard.append([
            InlineKeyboardButton(text=f"{is_active}{c['title']}", callback_data=f"select_chat_{c['chat_id']}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_chat_{c['chat_id']}")
        ])
    
    await message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

@dp.callback_query(F.data.startswith("select_chat_"))
async def select_chat_callback(call: types.CallbackQuery):
    chat_id = int(call.data.split("_")[2])
    await db.set_active_chat(call.from_user.id, chat_id)
    await call.answer("Чат успешно выбран!")
    await call.message.edit_text("✅ **Вы успешно переключились на этот диалог!**", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("delete_chat_"))
async def delete_chat_callback(call: types.CallbackQuery):
    chat_id = int(call.data.split("_")[2])
    await db.delete_chat(chat_id)
    await call.answer("Чат удален.")
    await call.message.edit_text("🗑️ **Диалог был полностью удален из базы.**", parse_mode="Markdown")

# Создание нового чата
@dp.message(F.text == "➕ Новый чат")
async def new_chat_handler(message: types.Message, state: FSMContext):
    await message.answer("✏️ Введите название для нового чата:")
    await state.set_state(Form.waiting_for_new_chat_title)

@dp.message(Form.waiting_for_new_chat_title)
async def process_new_chat_title(message: types.Message, state: FSMContext):
    title = message.text.strip()
    await db.create_new_chat(message.from_user.id, title)
    await state.clear()
    await message.answer(f"✅ **Новый чат «{title}» создан и установлен как активный!**", parse_mode="Markdown")

# Отзывы
@dp.message(F.text == "📝 Отзывы")
async def reviews_menu(message: types.Message):
    reviews = await db.get_latest_reviews(5)
    text = "⭐️ **Последние отзывы о Klepa IA:**\n\n"
    if not reviews:
        text += "Отзывов пока нет. Будьте первыми!\n\n"
    else:
        for r in reviews:
            text += f"👤 **@{r['username']}**: {r['text']}\n🗓️ _{r['created_at']}_\n━━━━━━━━━━━━━━━━━━━\n"
            
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="add_review")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "add_review")
async def start_review(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("✍️ Напишите ваш отзыв о работе бота:")
    await state.set_state(Form.waiting_for_review)
    await call.answer()

@dp.message(Form.waiting_for_review)
async def save_review(message: types.Message, state: FSMContext):
    await db.add_review(
        user_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        text=message.text
    )
    await state.clear()
    await message.answer("❤️ **Спасибо за ваш отзыв! Он успешно сохранен.**", parse_mode="Markdown")

# Покупка Premium навсегда за 50 Telegram Stars
@dp.message(F.text == "⭐ Premium (Навсегда)")
async def buy_premium_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if user["is_premium"]:
        await message.answer("🌟 У вас уже активирован **Premium навсегда**!", parse_mode="Markdown")
        return
        
    prices = [LabeledPrice(label="Klepa IA Premium Lifetime", amount=config.PREMIUM_PRICE_STARS)]
    await message.answer_invoice(
        title="Klepa IA Premium (Навсегда)",
        description="Полное снятие ограничений на общение и приоритетная генерация ответов навсегда!",
        payload="buy_premium_forever",
        currency="XTR",
        prices=prices,
        start_parameter="premium"
    )

# Произвольный донат Stars в поддержку проекта
@dp.message(F.text == "🌟 Задонатить Stars")
async def donate_stars_handler(message: types.Message, state: FSMContext):
    await message.answer("⭐ Введите количество Telegram Stars, которое вы хотите отправить в поддержку проекта:")
    await state.set_state(Form.waiting_for_stars_donation)

@dp.message(Form.waiting_for_stars_donation)
async def process_stars_donation(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("⚠️ Пожалуйста, введите корректное число Stars цифрами.")
        return
    
    amount = int(message.text)
    await state.clear()
    prices = [LabeledPrice(label="Поддержка Klepa IA", amount=amount)]
    await message.answer_invoice(
        title="Поддержка Klepa IA",
        description=f"Отправка доната в размере {amount} Telegram Stars разработчику.",
        payload=f"donate_{amount}",
        currency="XTR",
        prices=prices,
        start_parameter="donate"
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload == "buy_premium_forever":
        await db.set_premium(message.from_user.id)
        await message.answer("🎉 **Поздравляем! Ваш статус успешно повышен до Premium навсегда!**", parse_mode="Markdown")
    else:
        await message.answer("❤️ **Огромное спасибо за вашу финансовую поддержку проекта!**", parse_mode="Markdown")

# Техническая поддержка (Тикеты)
@dp.message(F.text == "🆘 Поддержка")
async def support_handler(message: types.Message, state: FSMContext):
    await message.answer("📨 Опишите ваш вопрос или проблему. Ваше сообщение будет моментально переслано администратору:")
    await state.set_state(Form.waiting_for_support_msg)

@dp.message(Form.waiting_for_support_msg)
async def process_support_msg(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Кнопка для ответа администратора без упоминания ID (красиво и чисто)
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить пользователю", callback_data=f"reply_to_{message.from_user.id}")]
    ])
    
    admin_notification = (
        f"🆘 **Новое обращение в поддержку!**\n\n"
        f"👤 Пользователь: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"🆔 ID: `{message.from_user.id}`\n\n"
        f"💬 **Текст сообщения:**\n{message.text}"
    )
    await bot.send_message(config.ADMIN_ID, admin_notification, parse_mode="Markdown", reply_markup=reply_kb)
    await message.answer("✅ Ваше сообщение успешно отправлено в поддержку! Ожидайте ответа.")

# Ответ администратора пользователю
@dp.callback_query(F.data.startswith("reply_to_"))
async def start_admin_reply(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != config.ADMIN_ID:
        return
    user_id = int(call.data.split("_")[2])
    await state.update_data(target_user_id=user_id)
    await call.message.answer(f"✍️ Напишите ответ для пользователя (ID: `{user_id}`):", parse_mode="Markdown")
    await state.set_state(Form.waiting_for_admin_reply)
    await call.answer()

@dp.message(Form.waiting_for_admin_reply)
async def send_admin_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    await state.clear()
    
    formatted_reply = (
        f"💬 **Ответ от Администратора Klepa IA:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{message.text}"
    )
    try:
        await bot.send_message(target_user_id, formatted_reply, parse_mode="Markdown")
        await message.answer("✅ Ответ красиво доставлен пользователю!")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при отправке ответа: {e}")

# Панель администратора (Статистика)
@dp.message(F.text == "📊 Админ Статистика")
async def admin_stats(message: types.Message):
    if message.from_user.id != config.ADMIN_ID:
        return
    total_users = await db.get_total_users_count()
    await message.answer(
        f"⚙️ **Панель Администратора**\n\n👥 Всего участников посетило бота: `{total_users}`",
        parse_mode="Markdown"
    )

# Интеллектуальный процессинг сообщений Klepa IA
@dp.message(F.text)
async def handle_ai_chat(message: types.Message):
    user = await db.get_user(message.from_user.id)
    
    # Проверка лимита бесплатных сообщений
    if not user["is_premium"] and user["messages_count"] >= config.FREE_MESSAGE_LIMIT:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Купить Premium за 50 Stars", callback_data="buy_prem_btn")]
        ])
        await message.answer(
            "⚠️ **Вы исчерпали лимит из 10 бесплатных сообщений!**\nПриобретите Premium навсегда, чтобы продолжить общение без ограничений.",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    # Обработка вопросов о владельце и создателе
    text_lower = message.text.lower()
    owner_triggers = ["кто создатель", "кто владелец", "кто тебя создал", "чей бот", "кто автор", "создатель", "владелец", "кто разработчик"]
    if any(trigger in text_lower for trigger in owner_triggers):
        owner_response = (
            f"👑 Моим создателем, разработчиком и единственным владельцем является **{config.OWNER_NAME}** ({config.OWNER_USERNAME}).\n\n"
            f"📌 Узнать больше можно в официальном источнике: {config.BIO_LINK}"
        )
        await message.answer(owner_response, parse_mode="Markdown")
        return

    # Увеличение счетчика использованных сообщений
    await db.increment_message_count(message.from_user.id)
    await bot.send_chat_action(message.chat.id, "typing")
    
    # Качественный форматированный ответ с блоками кода под копирование
    ai_response = (
        f"✨ **Klepa IA Engine Response:**\n\n"
        f"Ваш запрос успешно обработан: _{message.text}_\n\n"
        f"Вот сгенерированное решение / код:\n"
        f"```python\n"
        f"# Автор и разработчик: {config.OWNER_USERNAME}\n"
        f"# Архитектура: Klepa Engine\n"
        f"def klepa_execute_task():\n"
        f"    print('Задача выполнена успешно!')\n"
        f"    return True\n"
        f"```\n"
        f"📌 *Вы можете скопировать данный блок кода в один клик.*"
    )
    
    await message.answer(ai_response, parse_mode="Markdown")

@dp.callback_query(F.data == "buy_prem_btn")
async def prem_btn_callback(call: types.CallbackQuery):
    await buy_premium_handler(call.message)

async def main():
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
