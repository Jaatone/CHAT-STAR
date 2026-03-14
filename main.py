#!/usr/bin/env python3
"""
Support Bot - Full Support + Auto-Reply + Ban System (All Message Types)
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
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID"))
MONGODB_URL = os.getenv("MONGODB_URL")
AUTO_REPLY_ENABLED = True
AUTO_REPLY_MESSAGE = "✅ Message received! Our team will reply in a few hours. Thank you! 🙏"

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
class DatabaseManager:
    def __init__(self, mongodb_url):
        self.client = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)
        self.client.admin.command('ping')
        self.db = self.client['telegram_support_bot']
        self.users = self.db['users']
        self.messages = self.db['messages']
        self.users.create_index("user_id", unique=True)
        self.messages.create_index([("user_id", 1), ("timestamp", -1)])
        logger.info("✅ Connected to MongoDB")

    def get_user_topic(self, user_id):
        user = self.users.find_one({"user_id": user_id})
        return user['topic_id'] if user else None

    def save_user_topic(self, user_id, topic_id, user_name, username):
        self.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "topic_id": topic_id,
                "user_name": user_name,
                "username": username,
                "updated_at": datetime.utcnow()
            },
             "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True
        )

    def log_message(self, user_id, message_type, direction, content=None):
        self.messages.insert_one({
            "user_id": user_id,
            "message_type": message_type,
            "direction": direction,
            "content": content,
            "timestamp": datetime.utcnow()
        })

    def is_user_blocked(self, user_id):
        user = self.users.find_one({"user_id": user_id})
        return user.get("blocked", False) if user else False

    def set_user_block(self, user_id, status):
        self.users.update_one({"user_id": user_id}, {"$set": {"blocked": status}}, upsert=True)

db = DatabaseManager(MONGODB_URL)

# ==================== BOT FUNCTIONS ====================
async def send_auto_reply(update: Update):
    if AUTO_REPLY_ENABLED:
        try:
            await update.message.reply_text(AUTO_REPLY_MESSAGE, disable_notification=True)
        except Exception as e:
            logger.error(f"Auto-reply failed: {e}")

async def get_or_create_topic(user_id, user_name, username, context: ContextTypes.DEFAULT_TYPE):
    topic_id = db.get_user_topic(user_id)
    if topic_id:
        return topic_id
    topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=f"👤 {user_name[:20]}")
    topic_id = topic.message_thread_id
    db.save_user_topic(user_id, topic_id, user_name, username)
    welcome_text = (
        f"🆕 <b>New Conversation Started</b>\n\n"
        f"👤 <b>Name:</b> {user_name}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> {display_username}\n"
        f"🕐 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await context.bot.send_message(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id,
                                   text=welcome_text, parse_mode='HTML', disable_notification=True)
    return topic_id

async def forward_to_support(user_id, chat_id, message_id, topic_id, context, user_name, username):
    try:
        await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=chat_id,
                                          message_id=message_id, message_thread_id=topic_id)
    except Exception as e:
        if "thread not found" in str(e).lower():
            db.users.delete_one({"user_id": user_id})
            topic_id = await get_or_create_topic(user_id, user_name, username, context)
            await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=chat_id,
                                              message_id=message_id, message_thread_id=topic_id)
        else:
            raise
    return topic_id

# ==================== MESSAGE HANDLER (ALL TYPES) ====================
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id):
        await update.message.reply_text("❌ You are banned by admin.")
        return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username  # actual username ya None
display_username = f"@{username}" if username else "None"

    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id,
                                 topic_id, context, user_name, username)

        # Determine content for logging
        content = None
        if update.message.text:
            content = update.message.text
            mtype = "text"
        elif update.message.photo:
            content = update.message.caption
            mtype = "photo"
        elif update.message.video:
            content = update.message.caption
            mtype = "video"
        elif update.message.document:
            content = update.message.document.file_name
            mtype = "document"
        elif update.message.audio:
            mtype = "audio"
        elif update.message.voice:
            mtype = "voice"
        elif update.message.sticker:
            mtype = "sticker"
        elif update.message.video_note:
            mtype = "video_note"
        else:
            mtype = "other"

        db.log_message(user_id, mtype, "from_user", content)

    except Exception as e:
        logger.error(f"User message error: {e}")
        await update.message.reply_text("❌ Error processing your message.")

# ==================== SUPPORT REPLY ====================
async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    if not update.message.message_thread_id:
        return
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user:
        return
    user_id = user['user_id']
    if db.is_user_blocked(user_id):
        return

    try:
        # Handle different types
        if update.message.text:
            await context.bot.send_message(chat_id=int(user_id), text=update.message.text)
        elif update.message.photo:
            await context.bot.send_photo(chat_id=int(user_id),
                                         photo=update.message.photo[-1].file_id,
                                         caption=update.message.caption)
        elif update.message.video:
            await context.bot.send_video(chat_id=int(user_id),
                                         video=update.message.video.file_id,
                                         caption=update.message.caption)
        elif update.message.document:
            await context.bot.send_document(chat_id=int(user_id),
                                           document=update.message.document.file_id,
                                           caption=update.message.caption)
        elif update.message.audio:
            await context.bot.send_audio(chat_id=int(user_id),
                                         audio=update.message.audio.file_id,
                                         caption=update.message.caption)
        elif update.message.voice:
            await context.bot.send_voice(chat_id=int(user_id),
                                         voice=update.message.voice.file_id,
                                         caption=update.message.caption)
        elif update.message.sticker:
            await context.bot.send_sticker(chat_id=int(user_id),
                                           sticker=update.message.sticker.file_id)
        elif update.message.video_note:
            await context.bot.send_video_note(chat_id=int(user_id),
                                             video_note=update.message.video_note.file_id)

        db.log_message(user_id, "to_user", "to_user", update.message.text)

    except Exception as e:
        logger.warning(f"Cannot send message to user {user_id}: {e}")

# ==================== COMMANDS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Hello {user_name}! Send your message to our support team.",
        parse_mode='HTML'
    )

# ------------------- BAN / UNBAN -------------------
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    user_id = context.args[0]
    db.set_user_block(user_id, True)
    try:
        await context.bot.send_message(chat_id=int(user_id),
                                       text="❌ You are banned by admin. You cannot send messages.")
    except Exception as e:
        logger.warning(f"Cannot notify banned user {user_id}: {e}")
    await update.message.reply_text(f"🚫 User {user_id} banned.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    user_id = context.args[0]
    db.set_user_block(user_id, False)
    await update.message.reply_text(f"✅ User {user_id} unbanned.")

# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")

# ==================== MAIN ====================
def main():
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    # Message Handlers
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, handle_user_message))
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & filters.Chat(SUPPORT_GROUP_ID), handle_reply))

    # Error
    app.add_error_handler(error_handler)

    logger.info("✅ Support Bot Running!")
    app.run_polling()

# ==================== HEALTH SERVER ====================
health_app = Flask(__name__)
@health_app.route("/")
def health():
    return "OK", 200
Thread(target=lambda: health_app.run(host="0.0.0.0", port=8000)).start()

if __name__ == "__main__":
    main()