import os
import telebot
import google.generativeai as genai
import psycopg2
from psycopg2 import sql

# --- НАСТРОЙКА ---
# Получаем секретные ключи из переменных окружения на Render
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # ИЗМЕНЕНО: Используем ключ Gemini
DATABASE_URL = os.getenv("DATABASE_URL")

# Проверяем, что все ключи доступны
if not all([TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, DATABASE_URL]):
    raise ValueError("Один или несколько секретных ключей не установлены в переменных окружения.")

# Инициализация Gemini AI и Telegram Bot
genai.configure(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
model = genai.GenerativeModel('gemini-pro')

# --- РАБОТА С БАЗОЙ ДАННЫХ (без изменений) ---

def get_db_connection():
    """Устанавливает соединение с базой данных PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

def init_db():
    """Создает необходимые таблицы в базе данных, если они еще не существуют."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name VARCHAR(255),
                    username VARCHAR(255),
                    registration_date TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    role VARCHAR(50),
                    content TEXT,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
            """)
        conn.commit()
        print("База данных успешно инициализирована.")
    except Exception as e:
        print(f"Ошибка при инициализации таблиц: {e}")
    finally:
        if conn:
            conn.close()


def add_user_to_db(message):
    """Добавляет или обновляет информацию о пользователе в базе данных."""
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username

    conn = get_db_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, first_name, username)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET first_name = EXCLUDED.first_name, username = EXCLUDED.username;
            """, (user_id, first_name, username))
        conn.commit()
    except Exception as e:
        print(f"Ошибка при добавлении пользователя {user_id}: {e}")
    finally:
        if conn: conn.close()


def add_message_to_history(user_id, role, content):
    """Сохраняет сообщение в историю чата в базе данных."""
    # Для Gemini роль ассистента - 'model'
    role_to_save = 'model' if role == 'assistant' else role
    conn = get_db_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_history (user_id, role, content)
                VALUES (%s, %s, %s);
            """, (user_id, role_to_save, content))
        conn.commit()
    except Exception as e:
        print(f"Ошибка при сохранении сообщения для пользователя {user_id}: {e}")
    finally:
        if conn: conn.close()

def get_user_history(user_id, limit=20):
    """Получает последние сообщения пользователя из базы данных для Gemini."""
    conn = get_db_connection()
    if not conn: return []
    history = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content FROM (
                    SELECT role, content, timestamp
                    FROM chat_history
                    WHERE user_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                ) AS recent_history
                ORDER BY timestamp ASC;
            """, (user_id, limit))
            # Форматируем историю для Gemini
            for role, content in cur.fetchall():
                history.append({"role": role, "parts": [content]})
    except Exception as e:
        print(f"Ошибка при получении истории для пользователя {user_id}: {e}")
    finally:
        if conn: conn.close()
    return history


# --- ОБРАБОТЧИКИ TELEGRAM ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Обработчик команды /start."""
    add_user_to_db(message)
    welcome_text = (
        "Привет! Я Bronhitik, ваш личный помощник на базе Gemini.\n\n"
        "Задайте мне любой вопрос, и я постараюсь на него ответить."
    )
    bot.reply_to(message, welcome_text)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработчик всех текстовых сообщений."""
    user_id = message.from_user.id
    user_text = message.text

    add_user_to_db(message)
    add_message_to_history(user_id, "user", user_text)
    
    conversation_history = get_user_history(user_id)
    
    try:
        # Начинаем чат с моделью Gemini с полной историей
        chat = model.start_chat(history=conversation_history)
        # Отправляем последнее сообщение пользователя
        response = chat.send_message(user_text)
        
        ai_response = response.text

        # Сохраняем ответ ассистента в БД
        add_message_to_history(user_id, "model", ai_response)

        bot.reply_to(message, ai_response)

    except Exception as e:
        print(f"Ошибка при обращении к Gemini API: {e}")
        bot.reply_to(message, "К сожалению, произошла ошибка. Попробуйте еще раз позже.")


# --- ЗАПУСК БОТА ---
if __name__ == '__main__':
    print("Инициализация базы данных...")
    init_db()
    print("Запуск Gemini бота...")
    bot.polling(none_stop=True)
