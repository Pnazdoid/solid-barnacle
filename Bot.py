import logging
import os
import sqlite3
from datetime import datetime, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Update,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

LOG_LEVEL = logging.INFO
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN") or ""
DB_PATH = os.path.join(os.path.dirname(__file__), "bookings.db")

ALL_SLOTS = ["10:00", "12:00", "14:00", "16:00", "18:00"]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def add_booking(user_id: int, username: str, date: str, time: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bookings (user_id, username, date, time, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, date, time, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_booking(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, date, time FROM bookings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_free_slots(date_iso: str) -> list:
    """Возвращает список свободных слотов на дату."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT time FROM bookings WHERE date = ?", (date_iso,))
    taken = {row[0] for row in cur.fetchall()}
    conn.close()
    return [s for s in ALL_SLOTS if s not in taken]


def slot_taken(date: str, time: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM bookings WHERE date = ? AND time = ?", (date, time))
    found = cur.fetchone() is not None
    conn.close()
    return found


def start(update: Update, context: CallbackContext):
    kb = [
        [KeyboardButton("Записаться")],
        [KeyboardButton("Моя запись")],
        [KeyboardButton("Отменить запись")],
    ]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    update.message.reply_text(
        "Привет! Я бот для записи на пирсинг. Выберите действие:", reply_markup=markup
    )


def build_dates_inline(days=14):
    buttons = []
    today = datetime.now().date()
    for i in range(days):
        d = today + timedelta(days=i)
        label = d.strftime("%d.%m (%a)")
        cb = f"date:{d.isoformat()}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])
    return InlineKeyboardMarkup(buttons)


def build_times_inline(date_iso: str):
    """Строит клавиатуру только из свободных слотов."""
    free_slots = get_free_slots(date_iso)
    if not free_slots:
        return None  # нет свободных слотов
    buttons = []
    for t in free_slots:
        cb = f"time:{date_iso}|{t}"
        buttons.append([InlineKeyboardButton(t, callback_data=cb)])
    # Кнопка «Назад» к выбору даты
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:dates")])
    return InlineKeyboardMarkup(buttons)


def text_message(update: Update, context: CallbackContext):
    text = update.message.text
    user = update.message.from_user

    if text == "Записаться":
        update.message.reply_text("Выберите дату:", reply_markup=build_dates_inline())

    elif text == "Моя запись":
        row = get_booking(user.id)
        if row:
            _, date, time = row
            update.message.reply_text(f"📌 Ваша запись: {date} в {time}")
        else:
            update.message.reply_text("У вас нет записи.")

    elif text == "Отменить запись":
        row = get_booking(user.id)
        if row:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("DELETE FROM bookings WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            update.message.reply_text("✅ Ваша запись отменена.")
        else:
            update.message.reply_text("У вас нет записи, которую можно отменить.")

    else:
        update.message.reply_text("Не понимаю. Используйте кнопки меню.")


def callback_query(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    user = query.from_user
    query.answer()

    if data == "back:dates":
        # Редактируем текущее сообщение — возвращаем выбор даты
        query.edit_message_text("Выберите дату:", reply_markup=build_dates_inline())

    elif data.startswith("date:"):
        date_iso = data.split("date:", 1)[1]
        context.user_data["selected_date"] = date_iso

        markup = build_times_inline(date_iso)
        if markup is None:
            # Все слоты заняты — редактируем сообщение
            query.edit_message_text(
                f"😔 На {date_iso} нет свободных слотов.\n\nВыберите другую дату:",
                reply_markup=build_dates_inline(),
            )
        else:
            # Редактируем сообщение — показываем свободное время
            query.edit_message_text(
                f"📅 Дата: *{date_iso}*\n\nВыберите удобное время:",
                parse_mode="Markdown",
                reply_markup=markup,
            )

    elif data.startswith("time:"):
        payload = data.split("time:", 1)[1]
        date_iso, time_slot = payload.split("|")

        existing = get_booking(user.id)
        if existing:
            query.edit_message_text(
                "⚠️ У вас уже есть запись. Сначала отмените её через кнопку «Отменить запись»."
            )
            return

        if slot_taken(date_iso, time_slot):
            # Слот успели занять — обновить список
            markup = build_times_inline(date_iso)
            if markup is None:
                query.edit_message_text(
                    f"😔 На {date_iso} больше нет свободных слотов.\n\nВыберите другую дату:",
                    reply_markup=build_dates_inline(),
                )
            else:
                query.edit_message_text(
                    f"⚠️ Это время уже занято. Выберите другой слот на *{date_iso}*:",
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
            return

        add_booking(user.id, user.username or "", date_iso, time_slot)

        # Редактируем сообщение — убираем клавиатуру, показываем подтверждение
        query.edit_message_text(
            f"✅ *Запись подтверждена!*\n\n"
            f"📅 Дата: {date_iso}\n"
            f"🕐 Время: {time_slot}\n\n"
            f"Ждём вас! Чтобы отменить — используйте кнопку «Отменить запись».",
            parse_mode="Markdown",
        )


def mybooking_command(update: Update, context: CallbackContext):
    user = update.message.from_user
    row = get_booking(user.id)
    if row:
        _, date, time = row
        update.message.reply_text(f"📌 Ваша запись: {date} в {time}")
    else:
        update.message.reply_text("У вас нет записи.")


def cancel_command(update: Update, context: CallbackContext):
    user = update.message.from_user
    row = get_booking(user.id)
    if row:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM bookings WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        update.message.reply_text("✅ Ваша запись отменена.")
    else:
        update.message.reply_text("У вас нет записи.")


def main():
    init_db()
    request_kwargs = {"read_timeout": 30, "connect_timeout": 10}
    updater = Updater(TOKEN, use_context=True, request_kwargs=request_kwargs)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("mybooking", mybooking_command))
    dp.add_handler(CommandHandler("cancel", cancel_command))
    dp.add_handler(CallbackQueryHandler(callback_query))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_message))

    logger.info("Starting bot")
    try:
        updater.start_polling()
        updater.idle()
    except Exception as e:
        logger.exception("Bot stopped with exception: %s", e)
        raise


if __name__ == "__main__":
    main()
