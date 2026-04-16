import telebot
from telebot import types
import os
import logging
from flask import Flask, request
import time
import requests

# =========================
# 📝 ЛОГИРОВАНИЕ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# 🔐 НАЛАШТУВАННЯ
# =========================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ТВІЙ_ТОКЕН_БОТА")
ADMIN_ID = int(os.getenv("ADMIN_ID", "887078537"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://78655.onrender.com")

TURSO_URL = os.getenv("TURSO_URL", "https://1qaz2wsx-yhbvgt65.aws-eu-west-1.turso.io")
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJleHAiOjE4MDc4NjA1NDEsImlhdCI6MTc3NjMyNDU0MSwiaWQiOiIwMTlkOTUyZC03YjAxLTc3N2QtYjE4NS03MDEzY2JjOWYwMDkiLCJyaWQiOiI3NmJlZDlhMy01Zjk1LTQ0OGYtYThkYi1kZTY2OTNmNjcwZTAifQ.fN9MZ5inviHOnUNqhrW20hbt1oUmHS6E2auA_grZ6pcv02NvEKEmrI5Ms_oSnwbBM1nTsR-TmE7SSIrB4utKDw")

MAX_DB_RETRIES = 3
DB_RETRY_DELAY = 2

# =========================
# 📊 СТАНИ КОРИСТУВАЧІВ
# =========================
user_states = {}
user_form = {}
trainer_data = {}
admin_chats = {}

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# =========================
# 🗄️ ПІДКЛЮЧЕННЯ ДО БД (TURSO)
# =========================

class TursoClient:
    """Синхронный клиент для Turso БД через REST API"""
    
    def __init__(self, url: str, auth_token: str):
        if url.startswith("libsql://"):
            url = url.replace("libsql://", "https://", 1)
        
        self.url = url.rstrip("/")
        self.auth_token = auth_token
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }
    
    def execute(self, query: str):
        """Выполнить SQL запрос БЕЗ параметров"""
        try:
            payload = {
                "requests": [
                    {
                        "type": "execute",
                        "stmt": {
                            "sql": query
                        }
                    }
                ]
            }
            
            url = f"{self.url}/v2/pipeline"
            logger.debug(f"📡 Отправка запроса: {query}")
            
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code != 200:
                error_msg = f"DB Error (status {response.status_code}): {response.text}"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            
            result = response.json()
            logger.debug(f"DB Response: {result}")
            
            if result.get("results"):
                result_data = result["results"][0]
                
                if result_data.get("response"):
                    response_data = result_data["response"]
                    if response_data.get("rows"):
                        # ✅ ИСПРАВЛЕНИЕ: Правильная парсинг структуры Turso
                        return QueryResult(response_data["rows"])
                
                return QueryResult([])
            
            return QueryResult([])
        except Exception as e:
            logger.error(f"❌ Ошибка запроса: {e}")
            raise

class QueryResult:
    """Результат запроса к БД"""
    def __init__(self, rows):
        self.rows = []
        
        if not rows:
            return
        
        # ✅ ИСПРАВЛЕНИЕ: Парсим структуру Turso
        # Turso возвращает: [{"values": [1, "ivan", ...]}, ...]
        if isinstance(rows, list) and len(rows) > 0:
            if isinstance(rows[0], dict) and "values" in rows[0]:
                # Структура Turso - извлекаем values
                self.rows = [tuple(row["values"]) for row in rows]
                logger.debug(f"📦 Парсинг Turso: {len(self.rows)} строк")
            elif isinstance(rows[0], (list, tuple)):
                # Уже готовый список кортежей
                self.rows = [tuple(row) if not isinstance(row, tuple) else row for row in rows]
                logger.debug(f"📦 Готовый формат: {len(self.rows)} строк")
            else:
                # Неизвестный формат, оставляем как есть
                self.rows = rows
                logger.warning(f"⚠️ Неизвестный формат: {type(rows[0])}")

client = None
db_initialized = False

def init_client():
    """Ініціалізація клієнта Turso"""
    global client
    try:
        logger.info(f"🔗 Підключення до: {TURSO_URL}")
        
        if not TURSO_URL or not TURSO_TOKEN:
            logger.error("❌ TURSO_URL або TURSO_TOKEN не встановлені!")
            return False
        
        client = TursoClient(url=TURSO_URL, auth_token=TURSO_TOKEN)
        
        try:
            result = client.execute("SELECT 1")
            logger.info("✅ Підключення успішне")
            return True
        except Exception as test_error:
            logger.error(f"❌ Тест не вдалось: {test_error}")
            client = None
            return False
            
    except Exception as e:
        logger.error(f"❌ Помилка клієнта: {e}")
        client = None
        return False

def get_db_client(retry_count=0):
    """Отримати клієнта БД з повторними спробами"""
    global client
    
    try:
        if client is None:
            if retry_count < MAX_DB_RETRIES:
                logger.warning(f"⚠️ Спроба {retry_count + 1}...")
                time.sleep(DB_RETRY_DELAY)
                if init_client():
                    return client
                else:
                    return get_db_client(retry_count + 1)
            else:
                logger.error(f"❌ Не вдалось після {MAX_DB_RETRIES} спроб")
                return None
        
        try:
            client.execute("SELECT 1")
            return client
        except Exception as e:
            logger.warning(f"⚠️ З'єднання втрачено: {e}")
            client = None
            return get_db_client(retry_count)
            
    except Exception as e:
        logger.error(f"❌ Помилка DB клієнта: {e}")
        return None

def init_db():
    """Ініціалізація таблиць БД"""
    global db_initialized
    try:
        db = get_db_client()
        if db is None:
            logger.error("❌ Не вдалось підключитися")
            return False
        
        logger.info("📋 Створення таблиці trainers...")
        
        try:
            db.execute("SELECT COUNT(*) FROM trainers")
            logger.info("✅ Таблиця існує")
        except:
            db.execute("""
                CREATE TABLE trainers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("✅ Таблиця створена")
        
        db_initialized = True
        logger.info("✅ БД ініціалізована")
        return True
        
    except Exception as e:
        logger.error(f"❌ Помилка БД: {e}")
        db_initialized = False
        return False

def escape_sql(text: str) -> str:
    """Экранировать одиночные кавычки"""
    return str(text).replace("'", "''")

# =========================
# 🏁 СТАРТ БОТА
# =========================

@bot.message_handler(commands=['start'])
def start(message):
    """Головне меню"""
    try:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Вибрати тренера", "Зв'язатися з адміністратором")
        
        bot.send_message(
            message.chat.id,
            "♟️ Ласкаво просимо до шахматної школи!\nВиберіть дію:",
            reply_markup=markup
        )
        user_states[message.chat.id] = "main_menu"
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# =========================
# 👨‍💼 АДМІН-ПАНЕЛЬ
# =========================

@bot.message_handler(func=lambda message: message.text == "Edit")
def admin_panel(message):
    """Адмін-панель"""
    try:
        if message.from_user.id != ADMIN_ID:
            bot.send_message(message.chat.id, "❌ Немає доступу")
            return
        
        user_states[message.chat.id] = "admin_panel"
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("➕ Додати тренера", "➖ Видалити тренера")
        markup.add("📋 Список тренерів")
        
        bot.send_message(message.chat.id, "👨‍💼 Адміністраторська панель:", reply_markup=markup)
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# ===== ДОДАВАННЯ ТРЕНЕРА =====

@bot.message_handler(func=lambda message: message.text == "➕ Додати тренера")
def add_trainer_start(message):
    """Додавання тренера"""
    try:
        if message.from_user.id != ADMIN_ID:
            return
        
        user_states[message.chat.id] = "waiting_trainer_username"
        bot.send_message(message.chat.id, "Введи @username тренера:\n(Приклад: @chess_coach_ivan)")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == "waiting_trainer_username")
def get_trainer_username(message):
    """Username тренера"""
    try:
        username = message.text.strip()
        
        if not username.startswith("@"):
            bot.send_message(message.chat.id, "❌ Має починатися з @\nПопробуй:")
            return
        
        clean_username = username[1:]
        trainer_data[message.chat.id] = {"username": clean_username, "display_username": username}
        user_states[message.chat.id] = "waiting_trainer_name"
        bot.send_message(message.chat.id, "Введи ім'я тренера:")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == "waiting_trainer_name")
def get_trainer_name(message):
    """Ім'я тренера"""
    try:
        trainer_data[message.chat.id]["name"] = message.text
        user_states[message.chat.id] = "waiting_trainer_description"
        bot.send_message(message.chat.id, "Введи опис тренера:")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == "waiting_trainer_description")
def get_trainer_description(message):
    """Опис та збереження"""
    try:
        trainer_data[message.chat.id]["description"] = message.text
        data = trainer_data[message.chat.id]
        
        db = get_db_client()
        if db is None:
            bot.send_message(message.chat.id, "❌ Помилка БД")
            user_states.pop(message.chat.id, None)
            trainer_data.pop(message.chat.id, None)
            return
        
        try:
            # ✅ SQL БЕЗ параметров
            username_escaped = escape_sql(data["username"])
            name_escaped = escape_sql(data["name"])
            desc_escaped = escape_sql(data["description"])
            
            query = f"""INSERT INTO trainers (username, name, description) 
            VALUES ('{username_escaped}', '{name_escaped}', '{desc_escaped}')"""
            
            logger.info(f"📤 SQL: {query}")
            db.execute(query)
            
            logger.info(f"✅ Тренер: {data['name']} ({data['display_username']})")
            bot.send_message(message.chat.id, f"✅ Тренер {data['name']} додан!")
            
        except Exception as db_error:
            error_str = str(db_error).lower()
            logger.error(f"❌ Помилка: {db_error}")
            
            if "unique" in error_str or "constraint" in error_str:
                bot.send_message(message.chat.id, f"❌ {data['display_username']} уже існує")
            else:
                bot.send_message(message.chat.id, f"❌ Помилка: {db_error}")
        
        user_states.pop(message.chat.id, None)
        trainer_data.pop(message.chat.id, None)
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# ===== ВИДАЛЕННЯ ТРЕНЕРА =====

@bot.message_handler(func=lambda message: message.text == "➖ Видалити тренера")
def delete_trainer_start(message):
    """Список для видалення"""
    try:
        if message.from_user.id != ADMIN_ID:
            return
        
        db = get_db_client()
        if db is None:
            bot.send_message(message.chat.id, "❌ Помилка БД")
            return
        
        result = db.execute("SELECT id, name FROM trainers ORDER BY name")
        trainers = result.rows if result.rows else []
        
        if not trainers:
            bot.send_message(message.chat.id, "📭 Список порожній")
            return
        
        markup = types.InlineKeyboardMarkup()
        
        for trainer in trainers:
            trainer_id = trainer[0]
            name = trainer[1]
            btn = types.InlineKeyboardButton(text=f"❌ {name}", callback_data=f"delete_trainer_{trainer_id}")
            markup.add(btn)
        
        bot.send_message(message.chat.id, "Вибери тренера:", reply_markup=markup)
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_trainer_"))
def delete_trainer_confirm(call):
    """Видалення"""
    try:
        if call.from_user.id != ADMIN_ID:
            return
        
        trainer_id = call.data.split("_")[2]
        
        db = get_db_client()
        if db is None:
            bot.answer_callback_query(call.id, "❌ Помилка", show_alert=True)
            return
        
        result = db.execute(f"SELECT name FROM trainers WHERE id = {trainer_id}")
        trainer = result.rows[0] if result.rows else None
        
        if not trainer:
            bot.answer_callback_query(call.id, "❌ Не знайдений", show_alert=True)
            return
        
        db.execute(f"DELETE FROM trainers WHERE id = {trainer_id}")
        
        logger.info(f"✅ Видалений: {trainer[0]}")
        bot.answer_callback_query(call.id, "✅ Видалено!", show_alert=False)
        bot.edit_message_text(f"✅ Видалений '{trainer[0]}'", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# ===== СПИСОК ТРЕНЕРІВ =====

@bot.message_handler(func=lambda message: message.text == "📋 Список тренерів")
def list_trainers(message):
    """Список"""
    try:
        if message.from_user.id != ADMIN_ID:
            return
        
        db = get_db_client()
        if db is None:
            bot.send_message(message.chat.id, "❌ Помилка БД")
            return
        
        result = db.execute("SELECT id, name, username, description FROM trainers ORDER BY name")
        trainers = result.rows if result.rows else []
        
        if not trainers:
            bot.send_message(message.chat.id, "📭 Список порожній")
            return
        
        text = "📋 **Список тренерів:**\n\n"
        for idx, trainer in enumerate(trainers, 1):
            name = trainer[1]
            username = trainer[2]
            desc = trainer[3] or "Без опису"
            text += f"{idx}. **{name}** (@{username})\n_{desc}_\n\n"
        
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# =========================
# 👤 ВИБІР ТРЕНЕРА
# =========================

@bot.message_handler(func=lambda message: message.text == "Вибрати тренера")
def choose_trainer_start(message):
    """Вибір тренера"""
    try:
        user_states[message.chat.id] = "waiting_phone"
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        btn = types.KeyboardButton("📱 Надіслати номер", request_contact=True)
        markup.add(btn)
        
        bot.send_message(message.chat.id, "Поділись номером:", reply_markup=markup)
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(content_types=['contact'])
def get_phone(message):
    """Номер"""
    try:
        if user_states.get(message.chat.id) != "waiting_phone":
            return
        
        user_form[message.chat.id] = {"phone": message.contact.phone_number}
        user_states[message.chat.id] = "waiting_user_name"
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("◀️ Скасувати")
        
        bot.send_message(message.chat.id, "Введи ім'я:", reply_markup=markup)
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == "waiting_user_name")
def get_user_name(message):
    """Ім'я користувача"""
    try:
        if message.text == "◀️ Скасувати":
            cancel_selection(message)
            return
        
        user_form[message.chat.id]["name"] = message.text
        user_states[message.chat.id] = "waiting_level"
        
        bot.send_message(message.chat.id, "Твій рівень гри:")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == "waiting_level")
def get_level(message):
    """Рівень гри"""
    try:
        if message.text == "◀️ Скасувати":
            cancel_selection(message)
            return
        
        user_form[message.chat.id]["level"] = message.text
        
        db = get_db_client()
        if db is None:
            bot.send_message(message.chat.id, "❌ Помилка БД")
            cancel_selection(message)
            return
        
        result = db.execute("SELECT id, name, description FROM trainers ORDER BY name")
        trainers = result.rows if result.rows else []
        
        if not trainers:
            bot.send_message(message.chat.id, "❌ Немає тренерів")
            cancel_selection(message)
            return
        
        markup = types.InlineKeyboardMarkup()
        
        for trainer in trainers:
            trainer_id = trainer[0]
            name = trainer[1]
            btn = types.InlineKeyboardButton(text=f"👨‍🏫 {name}", callback_data=f"choose_trainer_{trainer_id}")
            markup.add(btn)
        
        bot.send_message(message.chat.id, "Вибери тренера:", reply_markup=markup)
        user_states[message.chat.id] = "trainer_selected"
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")
        cancel_selection(message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("choose_trainer_"))
def send_request_to_trainer(call):
    """Заявка тренеру"""
    try:
        trainer_id = call.data.split("_")[2]
        
        db = get_db_client()
        if db is None:
            bot.answer_callback_query(call.id, "❌ Помилка", show_alert=True)
            return
        
        result = db.execute(f"SELECT username, name FROM trainers WHERE id = {trainer_id}")
        trainer = result.rows[0] if result.rows else None
        
        if not trainer:
            bot.answer_callback_query(call.id, "❌ Не знайдений", show_alert=True)
            return
        
        username, trainer_name = trainer
        username_with_at = f"@{username}"
        data = user_form.get(call.message.chat.id)
        
        if not data:
            bot.answer_callback_query(call.id, "❌ Помилка", show_alert=True)
            return
        
        notification_text = f"""🎯 **Нова заявка!**

👤 Ім'я: {data['name']}
📱 Телефон: {data['phone']}
♟️ Рівень: {data['level']}"""
        
        try:
            bot.send_message(username_with_at, notification_text, parse_mode="Markdown")
            logger.info(f"✅ Заявка надіслана {trainer_name}")
            bot.answer_callback_query(call.id, "✅ Надіслано!", show_alert=False)
        except Exception as send_error:
            logger.warning(f"⚠️ Помилка: {send_error}")
            bot.send_message(call.message.chat.id, "⚠️ Не вдалось надіслати")
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Вибрати тренера", "Зв'язатися з адміністратором")
        
        bot.edit_message_text(f"✅ Заявка надіслана {trainer_name}!", call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Що далі?", reply_markup=markup)
        
        user_states.pop(call.message.chat.id, None)
        user_form.pop(call.message.chat.id, None)
        
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

def cancel_selection(message):
    """Скасування"""
    try:
        user_states.pop(message.chat.id, None)
        user_form.pop(message.chat.id, None)
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Вибрати тренера", "Зв'язатися з адміністратором")
        
        bot.send_message(message.chat.id, "Скасовано. Меню:", reply_markup=markup)
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# =========================
# 💬 ЧАТ З АДМІНІСТРАТОРОМ
# =========================

@bot.message_handler(func=lambda message: message.text == "Зв'язатися з адміністратором")
def contact_admin_start(message):
    """Запит на чат"""
    try:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🛑 Завершити чат")
        
        bot.send_message(message.chat.id, "⏳ Очікуємо адміністратора...", reply_markup=markup)
        user_states[message.chat.id] = "waiting_admin_response"
        
        admin_markup = types.InlineKeyboardMarkup()
        admin_markup.add(types.InlineKeyboardButton("✅ Прийняти", callback_data=f"accept_chat_{message.chat.id}"))
        admin_markup.add(types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_chat_{message.chat.id}"))
        
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.chat.id}"
        
        bot.send_message(ADMIN_ID, f"📞 Запит: {user_info}\nІм'я: {message.from_user.first_name}", reply_markup=admin_markup)
        logger.info(f"📞 Запит: {user_info}")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("accept_chat_"))
def accept_chat(call):
    """Прийняти чат"""
    try:
        user_id = int(call.data.split("_")[2])
        
        if user_id in admin_chats:
            bot.answer_callback_query(call.id, "⚠️ Вже активний", show_alert=True)
            return
        
        admin_chats[user_id] = call.from_user.id
        user_states[user_id] = "in_admin_chat"
        
        bot.edit_message_text("✅ Прийнято", call.message.chat.id, call.message.message_id)
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🛑 Завершити чат")
        
        bot.send_message(user_id, "✅ Адміністратор прийняв!", reply_markup=markup)
        logger.info(f"✅ Чат: {user_id}")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_chat_"))
def reject_chat(call):
    """Відхилити чат"""
    try:
        user_id = int(call.data.split("_")[2])
        bot.edit_message_text("❌ Відхилено", call.message.chat.id, call.message.message_id)
        user_states[user_id] = "main_menu"
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Вибрати тренера", "Зв'язатися з адміністратором")
        
        bot.send_message(user_id, "❌ Відхилено", reply_markup=markup)
        logger.info(f"❌ Чат: {user_id}")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: message.text == "🛑 Завершити чат")
def end_chat(message):
    """Завершити чат"""
    try:
        if message.chat.id in admin_chats:
            admin_id = admin_chats[message.chat.id]
            bot.send_message(message.chat.id, "👋 Спасибі!")
            try:
                bot.send_message(admin_id, f"👤 Користувач завершив")
            except:
                pass
            admin_chats.pop(message.chat.id, None)
        elif message.from_user.id == ADMIN_ID:
            user_id = None
            for uid, aid in admin_chats.items():
                if aid == message.from_user.id:
                    user_id = uid
                    break
            
            if user_id:
                try:
                    bot.send_message(user_id, "👋 Адміністратор завершив")
                except:
                    pass
                admin_chats.pop(user_id, None)
        
        user_states[message.chat.id] = "main_menu"
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Вибрати тренера", "Зв'язатися з адміністратором")
        bot.send_message(message.chat.id, "Меню:", reply_markup=markup)
        logger.info(f"👋 Завершено: {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: message.chat.id in admin_chats and user_states.get(message.chat.id) == "in_admin_chat")
def relay_user_message(message):
    """Від користувача до адміна"""
    try:
        if message.text == "🛑 Завершити чат":
            end_chat(message)
            return
        
        admin_id = admin_chats[message.chat.id]
        try:
            bot.send_message(admin_id, f"💬 {message.text}")
        except:
            pass
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID)
def relay_admin_message(message):
    """Від адміна до користувача"""
    try:
        if message.text == "🛑 Завершити чат":
            end_chat(message)
            return
        
        user_id = None
        for uid, aid in admin_chats.items():
            if aid == message.from_user.id:
                user_id = uid
                break
        
        if not user_id:
            bot.send_message(message.chat.id, "❌ Нема чату")
            return
        
        try:
            bot.send_message(user_id, f"💬 Адміністратор: {message.text}")
        except:
            bot.send_message(message.chat.id, "❌ Помилка")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")

# =========================
# 🌐 FLASK ENDPOINTS
# =========================

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook"""
    try:
        json_data = request.get_json()
        update = telebot.types.Update.de_json(json_data)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"❌ Webhook: {e}")
    return '', 200

@app.route('/health', methods=['GET'])
def health():
    """Health"""
    try:
        db = get_db_client()
        return 'OK' if db else 'ERROR', 200 if db else 500
    except:
        return 'ERROR', 500

# =========================
# 🚀 ЗАПУСК
# =========================

if __name__ == "__main__":
    logger.info("🚀 Запуск...")
    
    if not init_db():
        logger.error("❌ Помилка БД")
    
    try:
        bot.remove_webhook()
        logger.info("✅ Webhook видалено")
    except:
        pass
    
    webhook_url = f"{WEBHOOK_URL}/webhook"
    try:
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Webhook: {e}")
    
    port = int(os.getenv("PORT", 5000))
    logger.info(f"🌐 Запуск на {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
