import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

async def send_notification(message: str, chat_id: str = None):
    target = chat_id if chat_id else TARGET_CHAT_ID
    if not target or not bot:
        print(f"[BOT] Message (not sent, missing bot token/chat_id): {message[:80]}...")
        return
    try:
        await bot.send_message(chat_id=target, text=message, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error sending message to Telegram: {e}")
