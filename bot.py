import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# =============================
# НАСТРОЙКИ САЛОНА — МЕНЯЙ ТУТ
# =============================
SALON_NAME = "[НАЗВАНИЕ САЛОНА]"
SALON_ADDRESS = "[АДРЕС]"
SALON_PHONE = "[ТЕЛЕФОН]"
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")

SALON_INFO = f"""
Название салона: {SALON_NAME}
Адрес: {SALON_ADDRESS}
Телефон: {SALON_PHONE}
Часы работы: Пн-Сб 9:00-20:00, Вс 10:00-18:00

Услуги и цены:
- Стрижка женская — от 1500₽ (45 мин)
- Окрашивание волос — от 3500₽ (2-3 часа)
- Маникюр — 1200₽ (60 мин)
- Педикюр — 1500₽ (75 мин)
- Наращивание ресниц — 2500₽ (2 часа)
- Макияж — 2000₽ (60 мин)

Мастера:
- Анна — стрижки и окрашивание
- Мария — ногтевой сервис
- Елена — ресницы и брови

Свободные слоты:
- Завтра: 10:00, 13:00, 16:00
- Послезавтра: 11:00, 14:00, 17:00

Акции:
- Маникюр + педикюр = скидка 15%
- Каждый 5-й визит — скидка 20%

Политика отмены:
- Отмена не позднее чем за 2 часа до визита
- При неявке без предупреждения — клиент вносится в список ненадёжных
"""

SYSTEM_PROMPT = f"""Ты — AI-администратор салона красоты. Помогай клиентам записываться и отвечай на вопросы.

{SALON_INFO}

Правила:
- Будь дружелюбной и профессиональной
- Отвечай кратко (2-4 предложения)
- При записи ОБЯЗАТЕЛЬНО запроси имя и номер телефона
- После получения данных — подтверди запись в формате ниже
- Сообщай о политике отмены при каждой записи
- Предлагай акции
- Отвечай только на русском языке

Формат подтверждения:
✅ Запись подтверждена!
👤 Имя: [имя]
📱 Телефон: [телефон]
💅 Услуга: [услуга]
👩 Мастер: [мастер]
📅 Дата и время: [дата и время]
⚠️ Отмена не позднее чем за 2 часа."""

# =============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

conversation_history = {}
bookings = []
blacklist = set()
pending_reviews = {}

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in blacklist:
        await update.message.reply_text(
            "К сожалению, мы не можем принять вашу запись.\n"
            "Свяжитесь с нами по телефону."
        )
        return
    conversation_history[user_id] = []
    await update.message.reply_text(
        f"Привет! Я AI-администратор салона {SALON_NAME}.\n\n"
        "Помогу записаться на любую услугу или отвечу на вопросы.\n\n"
        "Акция: маникюр + педикюр со скидкой 15%!\n\n"
        "Чем могу помочь? "
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id in blacklist:
        await update.message.reply_text("К сожалению, мы не можем принять вашу запись.")
        return

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    if user_id in pending_reviews:
        review = user_message
        booking = pending_reviews.pop(user_id)
        if OWNER_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text="Новый отзыв!\n"
                         f"Клиент: {booking.get('name', 'Неизвестно')}\n"
                         f"Отзыв: {review}"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки отзыва: {e}")
        await update.message.reply_text("Спасибо за отзыв! Будем рады видеть вас снова!")
        return

    conversation_history[user_id].append({"role": "user", "content": user_message})

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *conversation_history[user_id]
            ],
            max_tokens=1000,
        )

        reply = response.choices[0].message.content
        conversation_history[user_id].append({"role": "assistant", "content": reply})

        if "Запись подтверждена" in reply and OWNER_CHAT_ID:
            try:
                username = update.effective_user.username or "нет username"
                now = datetime.now()
                day_tag = "tomorrow" if "завтра" in reply.lower() else "today"
                booking_info = {
                    "details": reply,
                    "user_id": user_id,
                    "username": username,
                    "time": now.strftime("%H:%M"),
                    "date": now.strftime("%d.%m.%Y"),
                    "day": day_tag
                }
                bookings.append(booking_info)

                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"Новая запись! @{username}\n\n{reply}"
                )

                context.job_queue.run_once(
                    send_reminder,
                    when=timedelta(hours=23),
                    data={"user_id": user_id, "booking": reply},
                    name=f"reminder_{user_id}"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления: {e}")

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "Извините, произошла ошибка. Попробуйте ещё раз или позвоните нам."
        )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    booking = job_data["booking"]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Напоминание о записи!\n\n{booking}\n\nЖдём вас! Телефон: {SALON_PHONE}"
        )
        context.job_queue.run_once(
            request_review,
            when=timedelta(hours=26),
            data={"user_id": user_id},
            name=f"review_{user_id}"
        )
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")


async def request_review(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    try:
        pending_reviews[user_id] = {"name": "клиент"}
        await context.bot.send_message(
            chat_id=user_id,
            text="Как прошёл ваш визит?\n\nНапишите отзыв — это очень важно для нас!"
        )
    except Exception as e:
        logger.error(f"Ошибка запроса отзыва: {e}")


async def today_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    today = datetime.now().strftime("%d.%m.%Y")
    today_list = [b for b in bookings if b.get("date") == today]
    if not today_list:
        await update.message.reply_text("Записей на сегодня нет.")
        return
    text = "Записи на сегодня:\n\n"
    for i, b in enumerate(today_list, 1):
        text += f"{i}. {b.get('time', '')}\n{b.get('details', '')}\n\n"
    await update.message.reply_text(text)


async def tomorrow_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    tomorrow_list = [b for b in bookings if b.get("day") == "tomorrow"]
    if not tomorrow_list:
        await update.message.reply_text("Записей на завтра нет.")
        return
    text = "Записи на завтра:\n\n"
    for i, b in enumerate(tomorrow_list, 1):
        text += f"{i}. {b.get('time', '')}\n{b.get('details', '')}\n\n"
    await update.message.reply_text(text)


async def week_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    if not bookings:
        await update.message.reply_text("Записей на неделю нет.")
        return
    text = "Записи на неделю:\n\n"
    for i, b in enumerate(bookings, 1):
        text += f"{i}. {b.get('date', '')} {b.get('time', '')}\n{b.get('details', '')}\n\n"
    await update.message.reply_text(text)


async def blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    if context.args:
        bad_id = int(context.args[0])
        blacklist.add(bad_id)
        await update.message.reply_text(f"Пользователь {bad_id} заблокирован.")
    else:
        await update.message.reply_text("Использование: /ban [user_id]")


async def blacklist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    if context.args:
        bad_id = int(context.args[0])
        blacklist.discard(bad_id)
        await update.message.reply_text(f"Пользователь {bad_id} разблокирован.")
    else:
        await update.message.reply_text("Использование: /unban [user_id]")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("Диалог сброшен. Начнём сначала!")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("today", today_bookings))
    app.add_handler(CommandHandler("tomorrow", tomorrow_bookings))
    app.add_handler(CommandHandler("week", week_bookings))
    app.add_handler(CommandHandler("ban", blacklist_add))
    app.add_handler(CommandHandler("unban", blacklist_remove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
