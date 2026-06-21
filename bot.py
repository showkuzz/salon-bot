import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# =============================
# НАСТРОЙКИ САЛОНА — МЕНЯЙ ТУТ
# =============================
SALON_INFO = """
Название салона: [НАЗВАНИЕ САЛОНА]
Адрес: [АДРЕС]
Телефон: [ТЕЛЕФОН]
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
"""

SYSTEM_PROMPT = f"""Ты — AI-администратор салона красоты. Твоя задача — помогать клиентам записаться на услуги и отвечать на вопросы.

{SALON_INFO}

Правила:
- Будь дружелюбной и профессиональной
- Отвечай кратко (2-4 предложения)
- Если клиент хочет записаться — уточни услугу, мастера и время
- Предлагай свободные слоты
- После записи — подтверди все детали
- Отвечай только на русском языке
- Используй эмодзи умеренно"""

# =============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

conversation_history = {}

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text(
        "👋 Привет! Я AI-администратор салона красоты.\n\n"
        "Помогу записаться на любую услугу или отвечу на вопросы.\n\n"
        "Чем могу помочь? 💅"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

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

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "Извините, произошла ошибка. Попробуйте ещё раз или позвоните нам. 🙏"
        )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("Диалог сброшен. Начнём сначала! 👋")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
