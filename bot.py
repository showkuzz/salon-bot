import os
import logging
import json
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
from openai import OpenAI

# =============================
# НАСТРОЙКИ САЛОНА — МЕНЯЙ ТУТ
# =============================
SALON_NAME = "[НАЗВАНИЕ САЛОНА]"
SALON_ADDRESS = "[АДРЕС]"
SALON_PHONE = "[ТЕЛЕФОН]"
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")  # добавь в Railway Variables

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
- Отмена записи не позднее чем за 2 часа до визита
- При неявке без предупреждения — клиент вносится в список ненадёжных
"""

SYSTEM_PROMPT = f"""Ты — AI-администратор салона красоты. Твоя задача — помогать клиентам записаться на услуги и отвечать на вопросы.

{SALON_INFO}

Правила общения:
- Будь дружелюбной и профессиональной
- Отвечай кратко (2-4 предложения)
- При записи ОБЯЗАТЕЛЬНО запроси: имя клиента и номер телефона
- После получения имени и телефона — подтверди запись со всеми деталями
- Сообщай о политике отмены при каждой записи
- Предлагай актуальные акции
- Отвечай только на русском языке
- Используй эмодзи умеренно

Формат подтверждения записи:
✅ Запись подтверждена!
👤 Имя: [имя]
📱 Телефон: [телефон]
💅 Услуга: [услуга]
👩 Мастер: [мастер]
📅 Дата и время: [дата и время]
⚠️ Напоминаем: отмена не позднее чем за 2 часа."""

# =============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище данных (в памяти)
conversation_history = {}
bookings = {}  # {user_id: [booking1, booking2, ...]}
blacklist = set()  # user_ids в чёрном списке
pending_reviews = {}  # {user_id: booking_info}

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in blacklist:
        await update.message.reply_text(
            "⛔ К сожалению, мы не можем принять вашу запись.\n"
            "Пожалуйста, свяжитесь с нами по телефону для уточнения деталей."
        )
        return

    conversation_history[user_id] = []
    await update.message.reply_text(
        f"👋 Привет! Я AI-администратор салона {SALON_NAME}.\n\n"
        "Помогу записаться на любую услугу или отвечу на вопросы.\n\n"
        "🎁 Акция: маникюр + педикюр со скидкой 15%!\n\n"
        "Чем могу помочь? 💅"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id in blacklist:
        await update.message.reply_text(
            "⛔ К сожалению, мы не можем принять вашу запись.\n"
            "Свяжитесь с нами по телефону."
        )
        return

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    # Проверяем если это отзыв
    if user_id in pending_reviews:
        review = user_message
        booking = pending_reviews.pop(user_id)
        logger.info(f"Отзыв от {user_id}: {review}")
        if OWNER_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"⭐ Новый отзыв!\n"
                         f"Клиент: {booking.get('name', 'Неизвестно')}\n"
                         f"Услуга: {booking.get('service', 'Неизвестно')}\n"
                         f"Отзыв: {review}"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки отзыва: {e}")
        await update.message.reply_text(
            "💖 Спасибо за отзыв! Будем рады видеть вас снова!"
        )
        return

    conversation_history[user_id].append({
        "role": "user",
        "content": user_message
    })

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

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

        conversation_history[user_id].append({
            "role": "assistant",
            "content": reply
        })

        # Если в ответе есть подтверждение записи — уведомляем владельца
        if "✅ Запись подтверждена" in reply and OWNER_CHAT_ID:
            try:
                username = update.effective_user.username or "нет username"
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"🔔 Новая запись!\n"
                         f"Telegram: @{username}\n\n"
                         f"{reply}"
                )
                # Планируем напоминание через 23 часа (за час до визита на следующий день)
                booking_info = {"details": reply, "user_id": user_id, "time": datetime.now().strftime("%H:%M")}
                if user_id not in bookings:
                    bookings[user_id] = []
                bookings[user_id].append(booking_info)

                # Напоминание клиенту через 23 часа
                context.job_queue.run_once(
                    send_reminder,
                    when=timedelta(hours=23),
                    data={"user_id": user_id, "booking": reply},
                    name=f"reminder_{user_id}"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления владельца: {e}")

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "Извините, произошла ошибка. Попробуйте ещё раз или позвоните нам. 🙏"
        )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание клиенту за день до визита"""
    job_data = context.job.data
    user_id = job_data["user_id"]
    booking = job_data["booking"]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⏰ Напоминание о записи!\n\n{booking}\n\n"
                 f"Ждём вас! Если планы изменились — отмените запись заранее.\n"
                 f"📞 {SALON_PHONE}"
        )
        # Через 2 часа после визита — запрашиваем отзыв
        context.job_queue.run_once(
            request_review,
            when=timedelta(hours=26),
            data={"user_id": user_id, "booking": {"name": "клиент", "service": "услуга"}},
            name=f"review_{user_id}"
        )
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")


async def request_review(context: ContextTypes.DEFAULT_TYPE):
    """Запрос отзыва после визита"""
    job_data = context.job.data
    user_id = job_data["user_id"]
    booking = job_data["booking"]
    try:
        pending_reviews[user_id] = booking
        await context.bot.send_message(
            chat_id=user_id,
            text="⭐ Как прошёл ваш визит?\n\n"
                 "Напишите нам отзыв — это очень важно для нас и займёт всего минуту! 💖"
        )
    except Exception as e:
        logger.error(f"Ошибка запроса отзыва: {e}")


async def today_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для владельца — список записей на сегодня"""
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    if not bookings:
        await update.message.reply_text("📅 Записей на сегодня нет.")
        return
    text = "📅 Записи на сегодня:\n\n"
    count = 1
    for uid, user_bookings in bookings.items():
        for b in user_bookings:
            text += f"{count}. 🕐 {b.get('time', '')}\n{b.get('details', '')}\n\n"
            count += 1
    await update.message.reply_text(text)


async def blacklist_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для владельца — добавить в чёрный список"""
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_CHAT_ID):
        return
    if context.args:
        bad_user_id = int(context.args[0])
        blacklist.add(bad_user_id)
        await update.message.reply_text(f"⛔ Пользователь {bad_user_id} добавлен в чёрный список.")
    else:
        await update.message.reply_text("Использование: /ban [user_id]")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("Диалог сброшен. Начнём сначала! 👋")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("today", today_bookings))
    app.add_handler(CommandHandler("ban", blacklist_user))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
