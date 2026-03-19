#!/usr/bin/env python3
"""
Support Bot - Full Support + Auto-Reply + Ban System + Koyeb Health Server
"""

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes
)
import os
import logging
from datetime import datetime
from pymongo import MongoClient
from flask import Flask
from threading import Thread

# ==================== CONFIG ====================

SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "YOUR_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-100XXXXXXXXXX"))
MONGODB_URL = os.getenv("MONGODB_URL", "YOUR_MONGO_URL")

AUTO_REPLY_ENABLED = True
AUTO_REPLY_MESSAGE = "✅ Message received! Team will reply soon."

ADMIN_IDS = [8383703664]  # 👈 apna ID daalo

# ==================== LOG ====================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DB ====================

client = MongoClient(MONGODB_URL)
db = client['telegram_support_bot']
users = db['users']

# ==================== BAN SYSTEM ====================

def is_banned(user_id):
    user = users.find_one({"user_id": user_id})
    return user.get("banned", False) if user else False

def ban_user(user_id):
    users.update_one({"user_id": user_id}, {"$set": {"banned": True}}, upsert=True)

def unban_user(user_id):
    users.update_one({"user_id": user_id}, {"$set": {"banned": False}}, upsert=True)

# ==================== WEB SERVER (KOYEB) ====================

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot is running 🚀"

def run_web():
    port = int(os.environ.get("PORT", 8000))
    web_app.run(host="0.0.0.0", port=port)

# ==================== AUTO REPLY ====================

async def send_auto_reply(update: Update):
    if AUTO_REPLY_ENABLED:
        await update.message.reply_text(AUTO_REPLY_MESSAGE)

# ==================== TOPIC ====================

async def get_or_create_topic(user_id, name, username, context):
    user = users.find_one({"user_id": user_id})

    if user and "topic_id" in user:
        return user["topic_id"]

    topic = await context.bot.create_forum_topic(
        chat_id=SUPPORT_GROUP_ID,
        name=f"👤 {name}"
    )

    topic_id = topic.message_thread_id

    users.update_one(
        {"user_id": user_id},
        {"$set": {"topic_id": topic_id, "username": username}},
        upsert=True
    )

    return topic_id

# ==================== USER MESSAGE ====================

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    user_id = str(update.effective_user.id)

    # 🚫 BAN CHECK
    if is_banned(user_id):
        logger.info(f"🚫 Banned user {user_id}")
        return

    name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"

    await send_auto_reply(update)

    topic_id = await get_or_create_topic(user_id, name, username, context)

    await context.bot.forward_message(
        chat_id=SUPPORT_GROUP_ID,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
        message_thread_id=topic_id
    )

# ==================== SUPPORT REPLY ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return

    topic_id = update.message.message_thread_id
    user = users.find_one({"topic_id": topic_id})

    if not user:
        return

    user_id = int(user["user_id"])

    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=SUPPORT_GROUP_ID,
            message_id=update.message.message_id
        )
    except:
        pass

# ==================== COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    name = update.effective_user.first_name or "User"

    text = (
        f"✨ <b>Welcome {name}!</b> ✨\n\n"
        "🚀 <b>Premium Support System</b>\n\n"
        "💬 Send anything:\n"
        "📝 Text\n📷 Photo\n🎥 Video\n📄 File\n🎧 Audio\n😊 Sticker\n\n"
        "⚡ Instant confirmation मिलेगा\n"
        "👨‍💻 Team जल्दी reply करेगी\n\n"
        "🛡 24/7 Support Active\n\n"
        "📩 Message bhejo 👇"
    )

    await update.message.reply_text(text, parse_mode='HTML')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    topic_id = update.message.message_thread_id
    user = users.find_one({"topic_id": topic_id})

    if not user:
        await update.message.reply_text("❌ User not found")
        return

    ban_user(user["user_id"])
    await update.message.reply_text("🚫 User Banned")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    topic_id = update.message.message_thread_id
    user = users.find_one({"topic_id": topic_id})

    if not user:
        await update.message.reply_text("❌ User not found")
        return

    unban_user(user["user_id"])
    await update.message.reply_text("✅ User Unbanned")

# ==================== MAIN ====================

def main():
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, handle_all))

    app.add_handler(MessageHandler(
        filters.ChatType.SUPERGROUP & filters.Chat(chat_id=SUPPORT_GROUP_ID),
        handle_reply
    ))

    # 🌐 START KOYEB SERVER
    web_thread = Thread(target=run_web)
    web_thread.start()

    logger.info("🚀 Bot + Web Server Started")

    app.run_polling()

if __name__ == "__main__":
    main()