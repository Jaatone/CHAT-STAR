#!/usr/bin/env python3
"""
Support Bot - Customer Support with Forum Topics + Auto-Reply + Advanced Features
Ready for Koyeb Deployment
"""

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import os
import logging
from datetime import datetime
import time
import pytz
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
import asyncio
from flask import Flask
from threading import Thread

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1")) # Default to -1 if not set
MONGODB_URL = os.getenv("MONGODB_URL")

# Auto-reply configuration
AUTO_REPLY_ENABLED = True
BUSINESS_HOURS_START = 10 # 10 AM
BUSINESS_HOURS_END = 18   # 6 PM
TIMEZONE = pytz.timezone('Asia/Kolkata') # Indian Standard Time

WORKING_HOURS_MESSAGE = "✅ आपका मैसेज मिल गया है! हमारी टीम जल्द ही आपको रिप्लाई करेगी। धन्यवाद! 🙏"
OFF_HOURS_MESSAGE = "🌙 अभी हमारे काम करने का समय समाप्त हो गया है। हमारी टीम सुबह 10 बजे से शाम 6 बजे के बीच उपलब्ध होती है। हम वापस आते ही आपकी सहायता करेंगे! 🙏"

# Anti-Spam Configuration
user_cooldowns = {}
SPAM_LIMIT_SECONDS = 3

# ==================== MONGODB SETUP ====================

class DatabaseManager:
    """Manage MongoDB connections and operations"""
    
    def __init__(self, mongodb_url):
        """Initialize MongoDB connection"""
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
        """Get topic ID for a user"""
        user = self.users.find_one({"user_id": user_id})
        return user['topic_id'] if user else None
    
    def save_user_topic(self, user_id, topic_id, user_name, username):
        """Save or update user topic mapping"""
        try:
            self.users.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "user_id": user_id,
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
        """Log message to database for analytics"""
        try:
            self.messages.insert_one({
                "user_id": user_id,
                "message_type": message_type,
                "direction": direction,
                "content": content,
                "timestamp": datetime.utcnow()
            })
        except Exception as e:
            pass
    
    def get_user_stats(self, user_id):
        """Get statistics for a specific user"""
        try:
            total_messages = self.messages.count_documents({"user_id": user_id})
            from_user = self.messages.count_documents({"user_id": user_id, "direction": "from_user"})
            to_user = self.messages.count_documents({"user_id": user_id, "direction": "to_user"})
            return {"total": total_messages, "from_user": from_user, "to_user": to_user}
        except:
            return None
    
    def get_all_users(self):
        """Get all users from database"""
        try:
            return list(self.users.find({}, {"_id": 0}))
        except Exception as e:
            return []

    def is_user_blocked(self, user_id):
        user = self.users.find_one({"user_id": user_id})
        return user.get("blocked", False) if user else False

    def set_user_block(self, user_id, status):
        self.users.update_one({"user_id": user_id}, {"$set": {"blocked": status}})
    
    def get_total_stats(self):
        """Get overall bot statistics"""
        try:
            return {
                "total_users": self.users.count_documents({}),
                "total_messages": self.messages.count_documents({})
            }
        except:
            return None

# Initialize database manager
try:
    db = DatabaseManager(MONGODB_URL)
except Exception as e:
    logger.error("Failed to initialize database. Exiting...")
    exit(1)

# ==================== HELPER FUNCTIONS ====================

def check_spam(user_id):
    """Check if user is sending messages too fast"""
    now = time.time()
    if user_id in user_cooldowns:
        if now - user_cooldowns[user_id] < SPAM_LIMIT_SECONDS:
            return True
    user_cooldowns[user_id] = now
    return False

def get_auto_reply_message():
    """Determine message based on business hours"""
    now_ist = datetime.now(TIMEZONE)
    if BUSINESS_HOURS_START <= now_ist.hour < BUSINESS_HOURS_END:
        return WORKING_HOURS_MESSAGE
    return OFF_HOURS_MESSAGE

async def send_auto_reply(update: Update):
    """Send automatic acknowledgment to user"""
    if not AUTO_REPLY_ENABLED: return
    try:
        reply_msg = get_auto_reply_message()
        await update.message.reply_text(reply_msg, disable_notification=True)
    except Exception as e:
        logger.error(f"Error sending auto-reply: {e}")

async def get_or_create_topic(user_id: str, user_name: str, username: str, context: ContextTypes.DEFAULT_TYPE):
    """Get existing topic or create new one for user"""
    topic_id = db.get_user_topic(user_id)
    if not topic_id:
        try:
            topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=f"👤 {user_name[:20]}")
            topic_id = topic.message_thread_id
            db.save_user_topic(user_id, topic_id, user_name, username)
            
            welcome_text = (
                f"🆕 <b>नयी चैट शुरू हुई</b>\n\n"
                f"👤 <b>नाम:</b> {user_name}\n"
                f"🆔 <b>यूज़र ID:</b> <code>{user_id}</code>\n"
                f"📱 <b>यूज़रनेम:</b> @{username if username else 'None'}"
            )
            await context.bot.send_message(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, text=welcome_text, parse_mode='HTML')
        except Exception as e:
            raise e
    return topic_id

async def forward_to_support(user_id: str, chat_id: int, message_id: int, topic_id: int, context: ContextTypes.DEFAULT_TYPE, user_name: str, username: str):
    """Forward message to support group, recreating topic if deleted"""
    try:
        await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=chat_id, message_id=message_id, message_thread_id=topic_id)
    except Exception as e:
        if "thread not found" in str(e).lower() or "message thread not found" in str(e).lower():
            db.users.delete_one({"user_id": user_id})
            new_topic_id = await get_or_create_topic(user_id, user_name, username, context)
            await context.bot.forward_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=chat_id, message_id=message_id, message_thread_id=new_topic_id)
        else:
            raise

# ==================== USER MESSAGE HANDLERS ====================

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_type: str):
    if update.effective_chat.type != 'private': return
    
    user_id = str(update.effective_user.id)
    if db.is_user_blocked(user_id):
        return # Ignore silently or send simple message
        
    if check_spam(user_id):
        await update.message.reply_text("⚠️ कृपया धीरे-धीरे मैसेज भेजें। (Spam Protection)")
        return

    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        
        # Determine content for logging
        content = ""
        if msg_type == "text": content = update.message.text
        elif msg_type in ["photo", "video", "document"]: 
            content = update.message.caption or (update.message.document.file_name if msg_type == "document" else "")

        await forward_to_support(user_id, update.effective_chat.id, update.message.message_id, topic_id, context, user_name, username)
        db.log_message(user_id, msg_type, "from_user", content)
    except Exception as e:
        logger.error(f"Error handling {msg_type}: {e}")
        await update.message.reply_text("❌ कुछ तकनीकी खराबी आ गयी है। कृपया बाद में प्रयास करें।")

# Wrappers for specific message types
async def handle_text_message(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "text")
async def handle_photo(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "photo")
async def handle_video(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "video")
async def handle_document(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "document")
async def handle_voice(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "voice")
async def handle_sticker(u: Update, c: ContextTypes.DEFAULT_TYPE): await handle_user_message(u, c, "sticker")

# ==================== SUPPORT TEAM HANDLERS ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    if not update.message.message_thread_id: return
    
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    if not user: return
    user_id = user['user_id']
    
    try:
        if update.message.text:
            await context.bot.send_message(chat_id=int(user_id), text=update.message.text)
            db.log_message(user_id, "text", "to_user", update.message.text)
        elif update.message.photo:
            await context.bot.send_photo(chat_id=int(user_id), photo=update.message.photo[-1].file_id, caption=update.message.caption)
            db.log_message(user_id, "photo", "to_user")
        elif update.message.document:
            await context.bot.send_document(chat_id=int(user_id), document=update.message.document.file_id, caption=update.message.caption)
            db.log_message(user_id, "document", "to_user")
    except Exception as e:
        await update.message.reply_text(f"❌ मैसेज भेजने में समस्या: यूज़र ने बॉट ब्लॉक कर दिया है या अकाउंट डिलीट कर दिया है।\nError: {e}", message_thread_id=topic_id)

# ==================== ADVANCED COMMANDS ====================

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users (Admin only)"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    if len(context.args) == 0:
        await update.message.reply_text("❌ कृपया मैसेज लिखें। उपयोग: `/broadcast आपका मैसेज`", parse_mode='Markdown')
        return

    broadcast_text = " ".join(context.args)
    users = db.get_all_users()
    
    await update.message.reply_text(f"⏳ ब्रॉडकास्ट शुरू किया जा रहा है... कुल यूज़र्स: {len(users)}")
    
    success_count = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=int(u['user_id']), text=f"📢 <b>सूचना:</b>\n\n{broadcast_text}", parse_mode='HTML')
            success_count += 1
            await asyncio.sleep(0.05) # Prevent flood limits
        except:
            pass
            
    await update.message.reply_text(f"✅ ब्रॉडकास्ट पूरा हुआ! {success_count} यूज़र्स को मैसेज भेजा गया।")

async def close_ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close topic and ask for rating"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return

    user = db.users.find_one({"topic_id": topic_id})
    if not user: return
    
    # Send rating request to user
    keyboard = [
        [InlineKeyboardButton("⭐⭐⭐⭐⭐ (बेहतरीन)", callback_data="rate_5")],
        [InlineKeyboardButton("⭐⭐⭐ (ठीक-ठाक)", callback_data="rate_3")],
        [InlineKeyboardButton("⭐ (खराब)", callback_data="rate_1")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=int(user['user_id']), 
            text="✅ आपकी समस्या का समाधान कर दिया गया है। कृपया हमारी सहायता को रेट करें:", 
            reply_markup=reply_markup
        )
        
        # Try to close the forum topic (bot needs right permissions)
        try:
            await context.bot.close_forum_topic(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id)
        except:
            pass # Ignore if bot lacks permissions to close topics
            
        await update.message.reply_text("✅ टिकट बंद कर दिया गया है और यूज़र को रेटिंग के लिए मैसेज भेज दिया गया है।", message_thread_id=topic_id)
    except:
        await update.message.reply_text("❌ यूज़र को मैसेज नहीं भेजा जा सका।", message_thread_id=topic_id)

async def faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick reply predefined answers"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return
    
    user = db.users.find_one({"topic_id": topic_id})
    if not user: return

    # You can add more FAQs here
    faq_text = (
        "💡 <b>अक्सर पूछे जाने वाले सवाल (FAQ):</b>\n\n"
        "हमारी सर्विस से जुड़ी सभी जानकारी के लिए कृपया हमारी वेबसाइट देखें।\n"
        "अगर आपका कोई और सवाल है तो कृपया हमें बताएँ।"
    )
    
    try:
        await context.bot.send_message(chat_id=int(user['user_id']), text=faq_text, parse_mode='HTML')
        await update.message.reply_text("✅ FAQ यूज़र को भेज दिया गया है।", message_thread_id=topic_id)
    except:
        await update.message.reply_text("❌ भेजने में त्रुटि।", message_thread_id=topic_id)

async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle rating button press"""
    query = update.callback_query
    await query.answer()
    
    rating = query.data.split('_')[1]
    user_name = update.effective_user.first_name
    
    # Edit user message
    await query.edit_message_text(text=f"🙏 धन्यवाद! आपने हमें {rating} स्टार दिए हैं। आपकी प्रतिक्रिया हमारे लिए महत्वपूर्ण है।")
    
    # Notify support group
    topic_id = db.get_user_topic(str(update.effective_user.id))
    if topic_id:
        try:
            await context.bot.send_message(
                chat_id=SUPPORT_GROUP_ID, 
                message_thread_id=topic_id, 
                text=f"📊 <b>फीडबैक प्राप्त हुआ:</b>\nयूज़र ने {rating} स्टार की रेटिंग दी है।",
                parse_mode='HTML'
            )
        except: pass

# Standard Commands
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    welcome_message = f"👋 <b>नमस्ते {update.effective_user.first_name}!</b>\n\n📩 अपना मैसेज भेजें और हमारी टीम जल्द ही आपसे संपर्क करेगी।"
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    stats = db.get_total_stats()
    await update.message.reply_text(f"📊 कुल यूज़र्स: {stats['total_users']}\n💬 कुल मैसेजेस: {stats['total_messages']}", parse_mode='HTML')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user = db.users.find_one({"topic_id": update.message.message_thread_id})
    if user:
        db.set_user_block(user["user_id"], True)
        await update.message.reply_text("🚫 यूज़र को बैन कर दिया गया है।")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user = db.users.find_one({"topic_id": update.message.message_thread_id})
    if user:
        db.set_user_block(user["user_id"], False)
        await update.message.reply_text("✅ यूज़र को अनबैन कर दिया गया है।")

# ==================== MAIN FUNCTION ====================

def main():
    if not SUPPORT_BOT_TOKEN or not MONGODB_URL:
        logger.error("Missing Environment Variables!")
        return
        
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("close", close_ticket_command))
    app.add_handler(CommandHandler("faq", faq_command))
    
    # Callback (for rating)
    app.add_handler(CallbackQueryHandler(rating_callback, pattern='^rate_'))
    
    # User Messages
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, handle_document))
    app.add_handler(MessageHandler(filters.VOICE & filters.ChatType.PRIVATE, handle_voice))
    app.add_handler(MessageHandler(filters.Sticker.ALL & filters.ChatType.PRIVATE, handle_sticker))
    
    # Support Reply
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & filters.Chat(chat_id=SUPPORT_GROUP_ID), handle_reply))
    
    logger.info("✅ Bot is running with Advanced Features!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== HEALTH CHECK SERVER (FOR KOYEB) ====================

health_app = Flask(__name__)

@health_app.route("/")
def health():
    return "Bot is running perfectly!", 200

def run_health_server():
    # Get port from environment variables, fallback to 8000
    port = int(os.environ.get("PORT", 8000))
    health_app.run(host="0.0.0.0", port=port)

# Run flask server in background thread so bot can run on main thread
Thread(target=run_health_server, daemon=True).start()

if __name__ == '__main__':
    main()
    