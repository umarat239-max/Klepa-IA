import aiosqlite

DB_NAME = "bot_database.db"

async def init_db():
    """Инициализация базы данных и создание всех необходимых таблиц"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей
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
        # Таблица диалогов (чатов)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)
        # Таблица истории сообщений внутри чатов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE
            )
        """)
        # Таблица отзывов пользователей
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
    """Получение пользователя или создание нового с дефолтным чатом"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                    (user_id, username, full_name)
                )
                await db.commit()
                
                # Создаем первый дефолтный чат для нового юзера
                cursor_chat = await db.execute(
                    "INSERT INTO chats (user_id, title) VALUES (?, ?)",
                    (user_id, "Основной чат")
                )
                chat_id = cursor_chat.lastrowid
                await db.execute(
                    "UPDATE users SET active_chat_id = ? WHERE user_id = ?",
                    (chat_id, user_id)
                )
                await db.commit()

async def get_user(user_id: int):
    """Получение данных пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def increment_message_count(user_id: int):
    """Увеличение счетчика отправленных сообщений"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET messages_count = messages_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def set_premium(user_id: int):
    """Выдача пожизненного премиум-статуса"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET is_premium = 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def get_total_users_count() -> int:
    """Общее количество уникальных пользователей в боте"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            res = await cursor.fetchone()
            return res[0] if res else 0

# Управление чатами
async def create_new_chat(user_id: int, title: str) -> int:
    """Создание нового диалога и переключение на него"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO chats (user_id, title) VALUES (?, ?)",
            (user_id, title)
        )
        chat_id = cursor.lastrowid
        await db.execute(
            "UPDATE users SET active_chat_id = ? WHERE user_id = ?",
            (chat_id, user_id)
        )
        await db.commit()
        return chat_id

async def get_user_chats(user_id: int):
    """Получение списка всех чатов пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats WHERE user_id = ? ORDER BY chat_id DESC", (user_id,)) as cursor:
            return await cursor.fetchall()

async def set_active_chat(user_id: int, chat_id: int):
    """Установка активного чата для пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET active_chat_id = ? WHERE user_id = ?", (chat_id, user_id))
        await db.commit()

async def delete_chat(chat_id: int):
    """Удаление чата и всей истории его сообщений"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def clear_chat_history(chat_id: int):
    """Очистка истории сообщений в конкретном чате"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        await db.commit()

# Управление отзывами
async def add_review(user_id: int, username: str, text: str):
    """Добавление отзыва в базу"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reviews (user_id, username, text) VALUES (?, ?, ?)",
            (user_id, username, text)
        )
        await db.commit()

async def get_latest_reviews(limit: int = 5):
    """Получение последних отзывов"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reviews ORDER BY review_id DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()
