#!/usr/bin/env python3
"""
Support Bot - Customer Support with Forum Topics + Auto-Reply
Handles customer support messages and forwards them to support team
"""

from telegram import Update, Bot
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
from pymongo.errors import ConnectionFailure, OperationFailure
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1003803623115")) 
MONGODB_URL = os.getenv("MONGODB_URL")

# Auto-reply configuration
AUTO_REPLY_ENABLED = True
AUTO_REPLY_MESSAGE = (
    "✅ आपका मैसेज मिल गया है। / Message Received.\n\n"
    "👨‍💻 एडमिन के ऑनलाइन आने पर आपको जवाब दिया जाएगा।\n"
    "Admin will respond once they are online.\n\n"
    "🚫 कृपया बॉट को ब्लॉक न करें, आपको जवाब मिल जाएगा।\n"
    "Please do not block the bot, you will receive a reply.\n\n"
    "🙏 Thanks for your patience! ❤️"
)


# ==================== MONGODB SETUP ====================

class DatabaseManager:
    """Manage MongoDB connections and operations"""
    
    def __init__(self, mongodb_url):
        try:
            self.client = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            logger.info("✅ Connected to MongoDB successfully!")
            
            self.db = self.client['telegram_support_bot']
            self.users = self.db['users']
            self.messages = self.db['messages']
            
            self.users.create_index("user_id", unique=True)
            self.messages.create_index([("user_id", 1), ("timestamp", -1)])
            
        except ConnectionFailure as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ MongoDB initialization error: {e}")
            raise
    
    def get_user_topic(self, user_id):
        user = self.users.find_one({"user_id": str(user_id)})
        return user['topic_id'] if user else None
    
    def save_user_topic(self, user_id, topic_id, user_name, username):
        try:
            self.users.update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "user_id": str(user_id),
                        "topic_id": topic_id,
                        "user_name": user_name,
                        "username": username,
                        "updated_at": datetime.utcnow()
                    },
                    "$setOnInsert": {
                        "created_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error saving user topic: {e}")
            return False
    
    def log_message(self, user_id, message_type, direction, content=None):
        try:
            self.messages.insert_one({
                "user_id": str(user_id),
                "message_type": message_type,
                "direction": direction,
                "content": content,
                "timestamp": datetime.utcnow()
            })
        except Exception as e:
            logger.error(f"Error logging message: {e}")
    
    def get_user_stats(self, user_id):
        try:
            total_messages = self.messages.count_documents({"user_id": str(user_id)})
            from_user = self.messages.count_documents({"user_id": str(user_id), "direction": "from_user"})
            to_user = self.messages.count_documents({"user_id": str(user_id), "direction": "to_user"})
            
            return {
                "total": total_messages,
                "from_user": from_user,
                "to_user": to_user
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return None

    # Fix: Strictly enforcing string matching for robust block checking
    def is_user_blocked(self, user_id):
        user = self.users.find_one({"user_id": str(user_id)})
        if user and user.get("blocked", False):
            return True
        return False

    def set_user_block(self, user_id, status):
        self.users.update_one(
            {"user_id": str(user_id)},
            {"$set": {"blocked": status}}
        )
    
    def get_total_stats(self):
        try:
            total_users = self.users.count_documents({})
            total_messages = self.messages.count_documents({})
            return {"total_users": total_users, "total_messages": total_messages}
        except Exception as e:
            logger.error(f"Error getting total stats: {e}")
            return None

# Initialize database manager
try:
    if MONGODB_URL:
        db = DatabaseManager(MONGODB_URL)
    else:
        logger.error("MONGODB_URL environment variable is not set!")
        exit(1)
except Exception as e:
    logger.error("Failed to initialize database. Exiting...")
    exit(1)

# ==================== SUPPORT BOT FUNCTIONS ====================

async def send_auto_reply(update: Update):
    if not AUTO_REPLY_ENABLED:
        return
    try:
        await update.message.reply_text(AUTO_REPLY_MESSAGE, disable_notification=True)
    except Exception as e:
        logger.error(f"Error sending auto-reply: {e}")

async def forward_to_support(user_id: str, chat_id: int, message_id: int, topic_id: int, context: ContextTypes.DEFAULT_TYPE, user_name: str, username: str):
    try:
        await context.bot.forward_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=chat_id,
            message_id=message_id,
            message_thread_id=topic_id
        )
        return topic_id
    except Exception as e:
        if "thread not found" in str(e).lower() or "message thread not found" in str(e).lower():
            db.users.delete_one({"user_id": str(user_id)})
            new_topic_id = await get_or_create_topic(user_id, user_name, username, context)
            await context.bot.forward_message(
                chat_id=SUPPORT_GROUP_ID,
                from_chat_id=chat_id,
                message_id=message_id,
                message_thread_id=new_topic_id
            )
            return new_topic_id
        else:
            raise

async def get_or_create_topic(user_id: str, user_name: str, username: str, context: ContextTypes.DEFAULT_TYPE):
    topic_id = db.get_user_topic(user_id)
    if not topic_id:
        try:
            topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=f"👤 {user_name[:20]}")
            topic_id = topic.message_thread_id
            db.save_user_topic(user_id, topic_id, user_name, username)
            
            welcome_text = (
                f"🆕 <b>New Conversation Started</b>\n\n"
                f"👤 <b>Name:</b> {user_name}\n"
                f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                f"📱 <b>Username:</b> @{username if username else 'None'}\n"
                f"🕐 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await context.bot.send_message(
                chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, text=welcome_text, parse_mode='HTML', disable_notification=True
            )
        except Exception as e:
            logger.error(f"Error creating topic: {e}")
            raise
    return topic_id

# ==================== USER MESSAGE HANDLERS ====================

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    
    # Silent Ban Check
    if db.is_user_blocked(user_id):
        return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, "text", "from_user", update.message.text)
    except Exception as e:
        await update.message.reply_text("❌ Sorry, there was an error processing your message.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, "photo", "from_user", update.message.caption)
    except Exception:
        pass

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, "video", "from_user", update.message.caption)
    except Exception:
        pass

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        file_name = update.message.document.file_name if update.message.document else "file"
        db.log_message(user_id, "document", "from_user", file_name)
    except Exception:
        pass

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id, message_thread_id=topic_id)
        db.log_message(user_id, "voice", "from_user")
    except Exception:
        pass

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, "audio", "from_user")
    except Exception:
        pass

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, "sticker", "from_user")
    except Exception:
        pass

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id, message_thread_id=topic_id)
        db.log_message(user_id, "video_note", "from_user")
    except Exception:
        pass

# ==================== SUPPORT TEAM HANDLERS ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID or not update.message.message_thread_id:
        return
    
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user: return
    user_id = user['user_id']
    
    try:
        message_type = "text"
        if update.message.text:
            await context.bot.send_message(chat_id=int(user_id), text=update.message.text)
        elif update.message.photo:
            await context.bot.send_photo(chat_id=int(user_id), photo=update.message.photo[-1].file_id, caption=update.message.caption)
            message_type = "photo"
        elif update.message.video:
            await context.bot.send_video(chat_id=int(user_id), video=update.message.video.file_id, caption=update.message.caption)
            message_type = "video"
        elif update.message.document:
            await context.bot.send_document(chat_id=int(user_id), document=update.message.document.file_id, caption=update.message.caption)
            message_type = "document"
        elif update.message.voice:
            await context.bot.send_voice(chat_id=int(user_id), voice=update.message.voice.file_id, caption=update.message.caption)
            message_type = "voice"
        
        db.log_message(user_id, message_type, "to_user", update.message.text)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send reply: User might have blocked the bot.", message_thread_id=topic_id)

# ==================== COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return # Ignore banned users

    user_name = update.effective_user.first_name
    welcome_message = (
        f"👋 <b>Hello {user_name}!</b>\n\n"
        "📩 Send your message and our team will respond as soon as possible.\n\n"
        "⚠️ Important:\n"
        "• Please do not send 'Hi' or 'Hello' messages.\n"
        "• Directly send your query or issue.\n"
        "• Write your batch name correctly.\n"
        "• Do not block the bot, otherwise you will not receive our reply.\n\n"
        "🙏 Thank you."
    )
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id): return # Ignore banned users

    help_message = (
        "ℹ️ <b>How to use this bot:</b>\n\n"
        "1️⃣ Just send your message/question\n"
        "2️⃣ You'll get instant confirmation ✅\n"
        "3️⃣ Our support team will see it\n"
        "4️⃣ You'll receive a reply here\n\n"
        "💬 All message types are supported!"
    )
    await update.message.reply_text(help_message, parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    stats = db.get_total_stats()
    if stats:
        stats_message = (f"📊 <b>Bot Statistics</b>\n\n👥 Total Users: {stats['total_users']}\n💬 Total Messages: {stats['total_messages']}")
        await update.message.reply_text(stats_message, parse_mode='HTML')

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID or not update.message.message_thread_id: return
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user:
        await update.message.reply_text("❌ User not found for this topic.")
        return
    user_id = user['user_id']
    stats = db.get_user_stats(user_id)
    if stats:
        info_message = f"📊 <b>User Information</b>\n\n🆔 User ID: <code>{user_id}</code>\nTotal Messages: {stats['total']}"
        await update.message.reply_text(info_message, parse_mode='HTML', message_thread_id=topic_id)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user:
        await update.message.reply_text("❌ User not found.")
        return
    db.set_user_block(user["user_id"], True)
    await update.message.reply_text("🚫 User banned. Ab inka koi message is group me nahi aayega.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user:
        await update.message.reply_text("❌ User not found.")
        return
    db.set_user_block(user["user_id"], False)
    await update.message.reply_text("✅ User unbanned.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    if not update.message.reply_to_message: return
    msg = update.message.reply_to_message
    try:
        await msg.delete()
        if msg.forward_from and msg.forward_from.id:
            await context.bot.delete_message(chat_id=msg.forward_from.id, message_id=msg.forward_from_message_id)
        await update.message.delete()
    except Exception:
        await update.message.reply_text("❌ Failed to delete message.")

# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")

# ==================== MAIN FUNCTION ====================
def main():
    if not SUPPORT_BOT_TOKEN or not SUPPORT_GROUP_ID: return
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("userinfo", userinfo_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("delete", delete_command))
    
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, handle_document))
    app.add_handler(MessageHandler(filters.VOICE & filters.ChatType.PRIVATE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO & filters.ChatType.PRIVATE, handle_audio))
    app.add_handler(MessageHandler(filters.Sticker.ALL & filters.ChatType.PRIVATE, handle_sticker))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE & filters.ChatType.PRIVATE, handle_video_note))
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & filters.Chat(chat_id=SUPPORT_GROUP_ID), handle_reply))
    
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== HEALTH CHECK SERVER ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive and running on Koyeb!")
        
    def log_message(self, format, *args): pass

def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == '__main__':
    server_thread = threading.Thread(target=run_health_server)
    server_thread.daemon = True
    server_thread.start()
    main()
