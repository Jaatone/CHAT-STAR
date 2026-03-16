#!/usr/bin/env python3
""" Support Bot - Full Support + Auto-Reply + Ban System + Edit Message Support """

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
        # Edit feature के लिए admin_msg_id पर इंडेक्स
        self.messages.create_index("admin_msg_id") 
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

    def is_user_blocked(self, user_id):
        user = self.users.find_one({"user_id": user_id})
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
        f"🕐 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await context.bot.send_message(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, text=welcome_text, parse_mode='HTML')
    return topic_id

# ==================== SUPPORT REPLY (STABLE SEND & EDIT) ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # रिप्लाई और एडिट दोनों को हैंडल करना
    msg = update.message or update.edited_message
    
    if not msg or update.effective_chat.id != SUPPORT_GROUP_ID or not msg.message_thread_id:
        return
    
    if msg.from_user.is_bot:
        return

    topic_id = msg.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user:
        return
    
    user_id = user['user_id']

    # बैन चेक
    if db.is_user_blocked(user_id):
        if not update.edited_message:
            await msg.reply_text("⚠️ This user is banned. Unban them first.")
        return

    try:
        if update.edited_message:
            # ✅ EDIT LOGIC: पुराने मैसेज को ढूंढकर एडिट करना
            entry = db.messages.find_one({"admin_msg_id": msg.message_id})
            if entry and msg.text:
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=entry['user_side_msg_id'],
                    text=msg.text
                )
        else:
            # ✅ SEND LOGIC: नया मैसेज कॉपी करके भेजना
            sent_msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=SUPPORT_GROUP_ID,
                message_id=msg.message_id
            )
            # मैपिंग सेव करें ताकि एडिट काम कर सके
            db.messages.insert_one({
                "admin_msg_id": msg.message_id,
                "user_side_msg_id": sent_msg.message_id,
                "timestamp": datetime.utcnow()
            })
            
    except Forbidden:
        await msg.reply_text(f"❌ <b>FAILED:</b> User (<code>{user_id}</code>) has <b>blocked the bot</b>.", parse_mode='HTML')
    except BadRequest as e:
        if "chat not found" in str(e).lower():
            await msg.reply_text("❌ <b>FAILED:</b> User deleted the chat or account.")
        elif "message to edit not found" in str(e).lower():
            pass # एडिट के लिए मैसेज नहीं मिला
        else:
            await msg.reply_text(f"❌ <b>FAILED:</b> {e}")
    except Exception as e:
        logger.error(f"Reply error: {e}")

# ==================== USER MESSAGE ====================

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != 'private':
        return
    
    user_id = update.effective_user.id
    if db.is_user_blocked(user_id):
        return

    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, update.effective_user.first_name, update.effective_user.username, context)
        
        # फॉरवर्ड करना ताकि एडमिन को मैसेज मिले
        await context.bot.forward_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            message_thread_id=topic_id
        )
    except Exception as e:
        logger.error(f"User message error: {e}")

# ==================== COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👋 Hello {update.effective_user.first_name}! Send your message to our support team.")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user_id = None
    if context.args:
        user_id = int(context.args[0])
    elif update.message.reply_to_message:
        u = db.users.find_one({"topic_id": update.message.message_thread_id})
        if u: user_id = u['user_id']
    
    if user_id:
        db.set_user_block(user_id, True)
        await update.message.reply_text(f"🚫 User {user_id} banned.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user_id = int(context.args[0]) if context.args else None
    if user_id:
        db.set_user_block(user_id, False)
        await update.message.reply_text(f"✅ User {user_id} unbanned.")

# ==================== MAIN ====================

def main():
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, handle_user_message))

    # मैसेज भेजना और एडिट करना दोनों के लिए एक ही हैंडलर
    app.add_handler(MessageHandler(
        (filters.ChatType.SUPERGROUP & filters.Chat(SUPPORT_GROUP_ID) & ~filters.COMMAND),
        handle_reply
    ))

    logger.info("✅ Support Bot Running!")
    app.run_polling()

# ==================== KOYEB SERVICE ====================

health_app = Flask(__name__)
@health_app.route("/")
def health(): return "OK", 200

if __name__ == "__main__":
    Thread(target=lambda: health_app.run(host="0.0.0.0", port=8000), daemon=True).start()
    main()
    