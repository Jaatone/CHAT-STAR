#!/usr/bin/env python3
""" Support Bot - Full Support + Auto-Reply + Ban System (All Message Types) """

from telegram import Update
from telegram.ext import ( Application, MessageHandler, CommandHandler, filters, ContextTypes )
from telegram.error import Forbidden, BadRequest
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
        # Fixed: Direct check for blocked status
        return user.get("blocked", False) if user else False

    def set_user_block(self, user_id, status):
        self.users.update_one(
            {"user_id": user_id},
            {"$set": {"blocked": status}},
            upsert=True
        )

db = DatabaseManager(MONGODB_URL)

# ==================== BOT FUNCTIONS ====================

async def send_auto_reply(update: Update):
    if AUTO_REPLY_ENABLED:
        try:
            await update.message.reply_text(
                AUTO_REPLY_MESSAGE,
                disable_notification=True
            )
        except Exception as e:
            logger.error(f"Auto-reply failed: {e}")

async def get_or_create_topic(user_id, user_name, username, context: ContextTypes.DEFAULT_TYPE):
    topic_id = db.get_user_topic(user_id)
    if topic_id:
        return topic_id

    topic = await context.bot.create_forum_topic(
        chat_id=SUPPORT_GROUP_ID,
        name=f"👤 {user_name[:20]}"
    )
    topic_id = topic.message_thread_id
    db.save_user_topic(user_id, topic_id, user_name, username)

    display_username = f"@{username}" if username else "No Username"
    welcome_text = (
        f"🆕 <b>New Conversation Started</b>\n\n"
        f"👤 <b>Name:</b> {user_name}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> {display_username}\n"
        f"🕐 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    await context.bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=welcome_text,
        parse_mode='HTML',
        disable_notification=True
    )
    return topic_id

async def forward_to_support(user_id, chat_id, message_id, topic_id, context, user_name, username):
    try:
        await context.bot.forward_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=chat_id,
            message_id=message_id,
            message_thread_id=topic_id
        )
    except Exception as e:
        if "thread not found" in str(e).lower():
            db.users.delete_one({"user_id": user_id})
            topic_id = await get_or_create_topic(user_id, user_name, username, context)
            await context.bot.forward_message(
                chat_id=SUPPORT_GROUP_ID,
                from_chat_id=chat_id,
                message_id=message_id,
                message_thread_id=topic_id
            )
        else:
            raise
    return topic_id

# ==================== USER MESSAGE ====================

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != 'private':
        return

    user_id = update.effective_user.id
    
    # ✅ Fixed: If user is blocked, stop processing immediately
    if db.is_user_blocked(user_id):
        await update.message.reply_text("❌ You are banned by admin. You cannot send messages.")
        return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username

    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
    except Exception as e:
        logger.error(f"User message error: {e}")

# ==================== SUPPORT REPLY ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    if not update.message or not update.message.message_thread_id:
        return

    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})

    if not user:
        return

    user_id = user['user_id']

    # ✅ Fixed: Check if admin is trying to reply to a banned user
    if db.is_user_blocked(user_id):
        await update.message.reply_text("⚠️ This user is banned. Unban them first to reply.")
        return

    try:
        # Using copy_message instead of send_message to support all types (media, sticker, etc.)
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=SUPPORT_GROUP_ID,
            message_id=update.message.message_id
        )
    except Forbidden:
        # ✅ Fixed: Reason when user blocks the bot
        await update.message.reply_text(f"❌ <b>FAILED:</b> User (<code>{user_id}</code>) has <b>blocked the bot</b>.", parse_mode='HTML')
    except BadRequest as e:
        # ✅ Fixed: Reason for deleted account or invalid ID
        await update.message.reply_text(f"❌ <b>FAILED:</b> Account deleted or invalid User ID.\nError: <code>{e}</code>", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"❌ <b>FAILED:</b> Unknown error: <code>{e}</code>", parse_mode='HTML')

# ==================== COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return
    user_name = update.effective_user.first_name
    await update.message.reply_text(f"👋 Hello {user_name}! Send your message to our support team.")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    
    user_id = None
    if context.args:
        user_id = int(context.args[0])
    elif update.message.reply_to_message:
        user_data = db.users.find_one({"topic_id": update.message.message_thread_id})
        if user_data: user_id = user_data['user_id']

    if not user_id:
        await update.message.reply_text("Usage: /ban <user_id> or reply to a message in the topic.")
        return

    db.set_user_block(user_id, True)
    try:
        await context.bot.send_message(chat_id=user_id, text="❌ You have been banned from using this support bot.")
    except:
        pass
    await update.message.reply_text(f"🚫 User {user_id} banned.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    
    user_id = None
    if context.args:
        user_id = int(context.args[0])
    elif update.message.reply_to_message:
        user_data = db.users.find_one({"topic_id": update.message.message_thread_id})
        if user_data: user_id = user_data['user_id']

    if not user_id:
        await update.message.reply_text("Usage: /unban <user_id> or reply to a message.")
        return

    db.set_user_block(user_id, False)
    await update.message.reply_text(f"✅ User {user_id} unbanned.")

# ==================== ERROR ====================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")

# ==================== MAIN ====================

def main():
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, handle_user_message))
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & filters.Chat(SUPPORT_GROUP_ID) & ~filters.COMMAND, handle_reply))

    app.add_error_handler(error_handler)
    logger.info("✅ Support Bot Running!")
    app.run_polling()

# ==================== KOYEB SERVICE ====================

health_app = Flask(__name__)
@health_app.route("/")
def health():
    return "OK", 200

def run_flask():
    health_app.run(host="0.0.0.0", port=8000)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    main()
    