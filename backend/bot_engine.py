import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command

from database import (
    add_whale, add_limitless_wallet_db, get_whales,
    get_limitless_wallets_db, delete_whale_permanently, delete_limitless_wallet_db
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# ========================================== #
# TELEGRAM BOT INTERACTIVE COMMAND HANDLERS  #
# ========================================== #

@dp.message(Command("start", "help", "yardim"))
async def cmd_help(message: types.Message):
    msg = "🤖 <b>PREDICT & LIMITLESS TRACKER BOT KOMUTLARI</b>\n\n"
    msg += "🌀 <b>Limitless Balinası Ekleme:</b>\n"
    msg += "<code>/limitless 0xAdres Balinaİsmi</code>\n"
    msg += "<i>Örnek:</i> <code>/limitless 0x328c4072920e5e3f95911e887c077c23deb91901 Balina 1</code>\n\n"
    msg += "🟢 <b>Predict.fun Balinası Ekleme:</b>\n"
    msg += "<code>/predict 0xAdres Balinaİsmi</code>\n"
    msg += "<i>Örnek:</i> <code>/predict 0x17c99cd6ca9032910de5ccfa2a2febcc22319a86 PredictDev</code>\n\n"
    msg += "📋 <b>Takip Edilenleri Listeleme:</b>\n"
    msg += "<code>/liste</code>\n\n"
    msg += "🗑️ <b>Balina Silme:</b>\n"
    msg += "<code>/sil 0xAdres</code>"
    await message.answer(msg)

@dp.message(Command("limitless", "add_limitless"))
async def cmd_add_limitless(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("❌ <b>Hatalı Kullanım!</b>\nFormat: <code>/limitless 0xAdres Balinaİsmi</code>")
        return
    
    addr = parts[1].strip()
    name = parts[2].strip() if len(parts) > 2 else f"Limitless Balina ({addr[:6]})"
    
    if not addr.startswith("0x") or len(addr) < 10:
        await message.answer("❌ Geçerli bir 0x cüzdan adresi giriniz!")
        return
        
    try:
        await add_limitless_wallet_db(addr, name, str(message.chat.id))
        await message.answer(f"✅ <b>Limitless Balinası Takibe Alındı!</b>\n\n👤 <b>İsim:</b> {name}\n<code>{addr}</code>")
    except Exception as e:
        await message.answer(f"❌ Ekleme hatası: {e}")

@dp.message(Command("predict", "add_predict", "whale"))
async def cmd_add_predict(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("❌ <b>Hatalı Kullanım!</b>\nFormat: <code>/predict 0xAdres Balinaİsmi</code>")
        return
    
    addr = parts[1].strip()
    name = parts[2].strip() if len(parts) > 2 else f"Predict Balina ({addr[:6]})"
    
    if not addr.startswith("0x") or len(addr) < 10:
        await message.answer("❌ Geçerli bir 0x cüzdan adresi giriniz!")
        return
        
    try:
        success = await add_whale(addr, name, str(message.chat.id))
        if success:
            await message.answer(f"✅ <b>Predict.fun Balinası Takibe Alındı!</b>\n\n👤 <b>İsim:</b> {name}\n<code>{addr}</code>")
        else:
            await message.answer("⚠️ Bu Predict balinası zaten takibe alınmış!")
    except Exception as e:
        await message.answer(f"❌ Ekleme hatası: {e}")

@dp.message(Command("liste", "list"))
async def cmd_list(message: types.Message):
    try:
        p_whales = await get_whales()
        l_wallets = await get_limitless_wallets_db()
        
        text = "📋 <b>TAKİP EDİLEN BALİNALAR LİSTESİ</b>\n\n"
        
        text += "🟢 <b>PREDICT.FUN BALİNALARI:</b>\n"
        if p_whales:
            for w in p_whales:
                text += f"• <b>{w.get('name')}</b>: <code>{w.get('address')}</code>\n"
        else:
            text += "<i>Henüz Predict balinası eklenmemiş.</i>\n"
            
        text += "\n🌀 <b>LIMITLESS EXCHANGE BALİNALARI:</b>\n"
        if l_wallets:
            for w in l_wallets:
                text += f"• <b>{w.get('name')}</b>: <code>{w.get('address')}</code>\n"
        else:
            text += "<i>Henüz Limitless balinası eklenmemiş.</i>\n"
            
        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Liste alma hatası: {e}")

@dp.message(Command("sil", "del"))
async def cmd_delete(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ <b>Hatalı Kullanım!</b>\nFormat: <code>/sil 0xAdres</code>")
        return
    addr = parts[1].strip()
    try:
        await delete_whale_permanently(addr)
        await delete_limitless_wallet_db(addr)
        await message.answer(f"🗑️ <code>{addr}</code> adresi takiplerden silindi.")
    except Exception as e:
        await message.answer(f"❌ Silme hatası: {e}")

async def start_bot_polling():
    if not bot:
        logger.warning("Bot token not configured. Telegram bot polling disabled.")
        return
    logger.info("Starting Telegram Bot Polling...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Telegram Bot Polling error: {e}")
