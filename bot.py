import os
import telebot
from telebot import types
from flask import Flask, request
from libsql_client import create_client

# 1. БАЗОВЫЕ НАСТРОЙКИ
def get_env(name):
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Переменная окружения {name} обязательна")
    return value

TOKEN = get_env("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(get_env("ADMIN_ID"))
WEBHOOK_URL = get_env("WEBHOOK_URL")
SQL_URL = get_env("SQL_URL")
SQL_TOKEN = get_env("SQL_TOKEN")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# 2. ПОДКЛЮЧЕНИЕ К SQL
def db():
    if not hasattr(db, "_cl"):
        db._cl = create_client(url=SQL_URL, auth_token=SQL_TOKEN)
    return db._cl

def init_db():
    d = db()
    d.execute("""
        CREATE TABLE IF NOT EXISTS trainers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)

# 3. ИНТЕРНАЛЬНЫЕ СОСТОЯНИЯ
user_states = {}   # Для FSM выбора тренера и чатов
user_forms = {}
admin_chat = {}    # user_id: True -- активные чаты с админом

# 4. АДМИН-ПАНЕЛЬ
@bot.message_handler(func=lambda m: m.text == "Edit")
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "❌ Доступ только для администратора")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Добавить пользователя", "➖ Удалить пользователя")
    markup.add("📋 Все тренеры", "⬅️ Выйти")
    bot.send_message(message.chat.id, "⚙️ Админ-панель", reply_markup=markup)
    user_states[message.chat.id] = "admin_panel"

@bot.message_handler(func=lambda m: m.text == "➕ Добавить пользователя")
def admin_add_trainer_ask_username(message):
    if message.from_user.id != ADMIN_ID: return
    user_states[message.chat.id] = "add_trainer/username"
    user_forms[message.chat.id] = {}
    msg = "Введите @username тренера (начиная с @):"
    bot.send_message(message.chat.id, msg)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id)=='add_trainer/username')
def admin_add_trainer_username(message):
    username = message.text.strip()
    if not username.startswith("@"):
        bot.send_message(message.chat.id, "❗️Username должен начинаться с @")
        return
    user_forms[message.chat.id]["username"] = username
    user_states[message.chat.id] = "add_trainer/name"
    bot.send_message(message.chat.id, "Введите имя тренера:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id)=='add_trainer/name')
def admin_add_trainer_name(message):
    user_forms[message.chat.id]["name"] = message.text.strip()
    user_states[message.chat.id] = "add_trainer/description"
    bot.send_message(message.chat.id, "Введите описание, опыт, основные достижения:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id)=='add_trainer/description')
def admin_add_trainer_desc(message):
    form = user_forms[message.chat.id]
    form["description"] = message.text.strip()
    try:
        d = db()
        d.execute(
            "INSERT INTO trainers (username, name, description) VALUES (?, ?, ?)",
            [form["username"], form["name"], form["description"]]
        )
        bot.send_message(message.chat.id, f"✅ Тренер {form['name']} добавлен!")
    except Exception as e:
        msg = str(e)
        if "UNIQUE constraint failed" in msg or "unique" in msg.lower():
            bot.send_message(message.chat.id, "❗️Тренер с таким username уже есть.")
        else:
            bot.send_message(message.chat.id, "❗️Ошибка: " + msg)
    user_states.pop(message.chat.id, None)
    user_forms.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "➖ Удалить пользователя")
def admin_delete_trainer_list(message):
    if message.from_user.id != ADMIN_ID: return
    d = db()
    trainers = d.execute("SELECT id, name FROM trainers ORDER BY name").rows
    if not trainers:
        bot.send_message(message.chat.id, "Список тренеров пуст.")
        return
    markup = types.InlineKeyboardMarkup()
    for t_id, name in trainers:
        markup.add(types.InlineKeyboardButton(f"❌ {name}", callback_data=f"deltr_{t_id}"))
    bot.send_message(message.chat.id, "Выберите тренера для удаления:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("deltr_"))
def admin_delete_trainer_confirm(call):
    t_id = call.data.split("_")[1]
    d = db()
    t = d.execute("SELECT name FROM trainers WHERE id=?", [t_id]).rows
    if t:
        d.execute("DELETE FROM trainers WHERE id=?", [t_id])
        bot.edit_message_text(f"Тренер {t[0][0]} удалён.", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Не найден.", show_alert=True)

@bot.message_handler(func=lambda m: m.text == "📋 Все тренеры")
def admin_list_trainers(message):
    if message.from_user.id != ADMIN_ID: return
    d = db()
    trainers = d.execute("SELECT name, username, description FROM trainers ORDER BY name").rows
    if not trainers:
        bot.send_message(message.chat.id, "Список пуст.")
        return
    text = "\n".join([f"{i+1}. {n} ({u}) – {desc}" for i, (n, u, desc) in enumerate(trainers)])
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "⬅️ Выйти")
def admin_panel_exit(message):
    if user_states.get(message.chat.id) == "admin_panel":
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Выбрать тренера", "Связаться с администратором")
        bot.send_message(message.chat.id, "Главное меню", reply_markup=markup)
        user_states[message.chat.id] = "main_menu"

# 5. ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ
@bot.message_handler(commands=['start'])
def user_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Выбрать тренера", "Связаться с администратором")
    bot.send_message(message.chat.id, "♟️ Добро пожаловать в шахматную школу!\nВыберите действие:", reply_markup=markup)
    user_states[message.chat.id] = "main_menu"

@bot.message_handler(func=lambda m: m.text == "Выбрать тренера")
def user_pick_trainer_start(message):
    user_states[message.chat.id] = "pick_phone"
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📱 Отправить телефон", request_contact=True))
    markup.add("Отмена")
    bot.send_message(message.chat.id, "Отправьте ваш номер телефона:", reply_markup=markup)

@bot.message_handler(content_types=['contact'])
def user_pick_trainer_phone(message):
    if user_states.get(message.chat.id) != "pick_phone":
        return
    user_forms[message.chat.id] = {"phone": message.contact.phone_number}
    user_states[message.chat.id] = "pick_name"
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отмена")
    bot.send_message(message.chat.id, "Введите ваше имя:", reply_markup=markup)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "pick_name")
def user_pick_trainer_name(message):
    if message.text == "Отмена":
        user_forms.pop(message.chat.id, None)
        user_states[message.chat.id] = "main_menu"
        user_start(message)
        return
    user_forms[message.chat.id]["name"] = message.text.strip()
    user_states[message.chat.id] = "pick_level"
    bot.send_message(message.chat.id, "Опишите ваш уровень (например: Начинающий, Средний, Продвинутый):")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "pick_level")
def user_pick_trainer_level(message):
    if message.text == "Отмена":
        user_forms.pop(message.chat.id, None)
        user_states[message.chat.id] = "main_menu"
        user_start(message)
        return
    user_forms[message.chat.id]["level"] = message.text.strip()
    # список тренеров
    d = db()
    trainers = d.execute("SELECT id, name FROM trainers ORDER BY name").rows
    if not trainers:
        bot.send_message(message.chat.id, "Нет доступных тренеров. Попробуйте позже.")
        user_states[message.chat.id] = "main_menu"
        user_start(message)
        return
    markup = types.InlineKeyboardMarkup()
    for t_id, name in trainers:
        markup.add(types.InlineKeyboardButton(f"👨‍🏫 {name}", callback_data=f"chtr_{t_id}"))
    bot.send_message(message.chat.id, "Выберите вашего тренера:", reply_markup=markup)
    user_states[message.chat.id] = "pick_trainer"

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("chtr_"))
def user_pick_trainer_send_to_trainer(call):
    t_id = call.data.split("_")[1]
    d = db()
    trainer = d.execute("SELECT username, name FROM trainers WHERE id=?", [t_id]).rows
    form = user_forms.get(call.message.chat.id)
    if not trainer or not form:
        bot.answer_callback_query(call.id, "Ошибка, попробуйте заново", show_alert=True)
        return
    username, t_name = trainer[0]
    # Отправляем тренеру!
    try:
        text = (f"🎯 Новая заявка!\n\n"
                f"👤 Имя: {form['name']}\n"
                f"📱 Телефон: {form['phone']}\n"
                f"♟️ Уровень: {form['level']}\n")
        bot.send_message(username, text)
        bot.answer_callback_query(call.id, "Заявка отправлена!", show_alert=False)
    except Exception:
        bot.send_message(call.message.chat.id, "⚠️ Не удалось отправить тренеру (он должен начать чат с ботом).")
    bot.edit_message_text(
        f"✅ Заявка отправлена тренеру {t_name}!\nОн свяжется с вами в ближайшее время.",
        call.message.chat.id,
        call.message.message_id
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Выбрать тренера", "Связаться с администратором")
    bot.send_message(call.message.chat.id, "Что дальше?", reply_markup=markup)
    user_states[call.message.chat.id] = "main_menu"
    user_forms.pop(call.message.chat.id, None)

# 6. ЧАТ С АДМИНИСТРАТОРОМ
@bot.message_handler(func=lambda m: m.text == "Связаться с администратором")
def user_contact_admin_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("❌ Завершить чат")
    bot.send_message(message.chat.id, "Ожидайте ответа администратора.", reply_markup=markup)
    user_states[message.chat.id] = "wait_admin"
    # Администратору сообщение:
    admin_markup = types.InlineKeyboardMarkup()
    admin_markup.add(types.InlineKeyboardButton("✅ Принять чат", callback_data=f"accchat_{message.chat.id}"))
    admin_markup.add(types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rejchat_{message.chat.id}"))
    bot.send_message(ADMIN_ID,
        f"💬 Запрос на чат: пользователь {message.chat.id}",
        reply_markup=admin_markup)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("accchat_"))
def admin_chat_accept(call):
    user_id = int(call.data.split("_")[1])
    admin_chat[user_id] = True
    user_states[user_id] = "chat_with_admin"
    bot.send_message(user_id, "✅ Админ в чате! Можете общаться.\nНажмите ❌ Завершить чат для выхода.")

    bot.edit_message_text("Чат с пользователем начат.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("rejchat_"))
def admin_chat_reject(call):
    user_id = int(call.data.split("_")[1])
    bot.send_message(user_id, "❌ Ваш чат с админом отклонён.")
    user_states[user_id] = "main_menu"
    user_start(types.SimpleNamespace(chat=types.SimpleNamespace(id=user_id)))
    bot.edit_message_text("Чат отклонён.", call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "chat_with_admin")
def relay_to_admin(message):
    if message.text == "❌ Завершить чат":
        admin_chat.pop(message.chat.id, None)
        user_states[message.chat.id] = "main_menu"
        user_start(message)
        bot.send_message(ADMIN_ID, f"Пользователь {message.chat.id} завершил чат.")
        return
    bot.send_message(ADMIN_ID, f"👤 {message.chat.id}: {message.text}")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and any(admin_chat.values()))
def relay_from_admin(message):
    # Отсылает только в активный юзер-чат
    for user_id in list(admin_chat.keys()):
        bot.send_message(user_id, f"Админ: {message.text}")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.text == "❌ Завершить чат")
def admin_chat_end(message):
    for user_id in list(admin_chat.keys()):
        bot.send_message(user_id, "Чат с администратором завершён.")
        user_states[user_id] = "main_menu"
        admin_chat.pop(user_id, None)
        user_start(types.SimpleNamespace(chat=types.SimpleNamespace(id=user_id)))
    bot.send_message(ADMIN_ID, "Чат(ы) завершены.")

# 7. FLASK (Render webhooks)
@app.route('/webhook', methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.get_json(force=True))
    bot.process_new_updates([update])
    return '', 200

@app.route("/health", methods=['GET'])
def health(): return "OK", 200

# 8. ЗАПУСК
if __name__ == "__main__":
    print("Запуск Chess bot...")
    init_db()
    try: bot.remove_webhook()
    except: pass
    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
