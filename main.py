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
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID"))
MONGODB_URL = os.getenv("MONGODB_URL")
# Auto-reply configuration
AUTO_REPLY_ENABLED = True
AUTO_REPLY_MESSAGE = "✅ Message received! Our team will reply in a few hours. Thank you! 🙏"

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
            logger.info(f"💾 Saved user {user_id} with topic {topic_id}")
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
            logger.error(f"Error logging message: {e}")
    
    def get_user_stats(self, user_id):
        """Get statistics for a specific user"""
        try:
            total_messages = self.messages.count_documents({"user_id": user_id})
            from_user = self.messages.count_documents({
                "user_id": user_id,
                "direction": "from_user"
            })
            to_user = self.messages.count_documents({
                "user_id": user_id,
                "direction": "to_user"
            })
            
            return {
                "total": total_messages,
                "from_user": from_user,
                "to_user": to_user
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return None
    
    def get_all_users(self):
        """Get all users from database"""
        try:
            return list(self.users.find({}, {"_id": 0}))
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
    
    def get_total_stats(self):
        """Get overall bot statistics"""
        try:
            total_users = self.users.count_documents({})
            total_messages = self.messages.count_documents({})
            
            return {
                "total_users": total_users,
                "total_messages": total_messages
            }
        except Exception as e:
            logger.error(f"Error getting total stats: {e}")
            return None

# Initialize database manager
try:
    db = DatabaseManager(MONGODB_URL)
except Exception as e:
    logger.error("Failed to initialize database. Exiting...")
    exit(1)

# ==================== SUPPORT BOT FUNCTIONS ====================

async def send_auto_reply(update: Update):
    """Send automatic acknowledgment to user"""
    if not AUTO_REPLY_ENABLED:
        return
    
    try:
        await update.message.reply_text(
            AUTO_REPLY_MESSAGE,
            disable_notification=True
        )
        logger.info(f"✅ Sent auto-reply to user {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Error sending auto-reply: {e}")

async def forward_to_support(user_id: str, chat_id: int, message_id: int, topic_id: int, context: ContextTypes.DEFAULT_TYPE, user_name: str, username: str):
    """Forward message to support group, recreating topic if deleted"""
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
            logger.warning(f"Topic {topic_id} was deleted for user {user_id}. Creating new topic...")
            db.users.delete_one({"user_id": user_id})
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
    """Get existing topic or create new one for user"""
    topic_id = db.get_user_topic(user_id)
    
    if not topic_id:
        try:
            topic = await context.bot.create_forum_topic(
                chat_id=SUPPORT_GROUP_ID,
                name=f"👤 {user_name[:20]}"
            )
            
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
                chat_id=SUPPORT_GROUP_ID,
                message_thread_id=topic_id,
                text=welcome_text,
                parse_mode='HTML',
                disable_notification=True
            )
            
            logger.info(f"✅ Created new topic for user {user_id} - {user_name}")
            
        except Exception as e:
            logger.error(f"Error creating topic: {e}")
            raise
    
    return topic_id

# ==================== USER MESSAGE HANDLERS ====================

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        db.log_message(user_id, "text", "from_user", update.message.text)
        logger.info(f"✅ Forwarded text message from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling text message: {e}")
        await update.message.reply_text(
            "❌ Sorry, there was an error processing your message. Please try again."
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        db.log_message(user_id, "photo", "from_user", update.message.caption)
        logger.info(f"✅ Forwarded photo from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await update.message.reply_text("❌ Error sending photo. Please try again.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        db.log_message(user_id, "video", "from_user", update.message.caption)
        logger.info(f"✅ Forwarded video from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling video: {e}")
        await update.message.reply_text("❌ Error sending video. Please try again.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        file_name = update.message.document.file_name if update.message.document else "file"
        db.log_message(user_id, "document", "from_user", file_name)
        logger.info(f"✅ Forwarded document from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        await update.message.reply_text("❌ Error sending file. Please try again.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await context.bot.forward_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            message_thread_id=topic_id
        )
        db.log_message(user_id, "voice", "from_user")
        logger.info(f"✅ Forwarded voice message from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling voice: {e}")
        await update.message.reply_text("❌ Error sending voice message. Please try again.")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle audio messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        db.log_message(user_id, "audio", "from_user")
        logger.info(f"✅ Forwarded audio from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling audio: {e}")
        await update.message.reply_text("❌ Error sending audio. Please try again.")

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sticker messages from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await forward_to_support(
            user_id, update.effective_chat.id, update.message.message_id,
            topic_id, context, user_name, username
        )
        db.log_message(user_id, "sticker", "from_user")
        logger.info(f"✅ Forwarded sticker from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling sticker: {e}")

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video notes (round videos) from users"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or "no_username"
    
    try:
        await send_auto_reply(update)
        
        topic_id = await get_or_create_topic(user_id, user_name, username, context)
        await context.bot.forward_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            message_thread_id=topic_id
        )
        db.log_message(user_id, "video_note", "from_user")
        logger.info(f"✅ Forwarded video note from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling video note: {e}")

# ==================== SUPPORT TEAM HANDLERS ====================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle replies from support team"""
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    
    if not update.message.message_thread_id:
        return
    
    topic_id = update.message.message_thread_id
    user_id = None
    
    user = db.users.find_one({"topic_id": topic_id})
    if user:
        user_id = user['user_id']
    
    if not user_id:
        logger.warning(f"No user found for topic {topic_id}")
        return
    
    try:
        message_type = "text"
        
        if update.message.text:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=update.message.text
            )
            message_type = "text"
        elif update.message.photo:
            await context.bot.send_photo(
                chat_id=int(user_id),
                photo=update.message.photo[-1].file_id,
                caption=update.message.caption
            )
            message_type = "photo"
        elif update.message.video:
            await context.bot.send_video(
                chat_id=int(user_id),
                video=update.message.video.file_id,
                caption=update.message.caption
            )
            message_type = "video"
        elif update.message.document:
            await context.bot.send_document(
                chat_id=int(user_id),
                document=update.message.document.file_id,
                caption=update.message.caption
            )
            message_type = "document"
        elif update.message.voice:
            await context.bot.send_voice(
                chat_id=int(user_id),
                voice=update.message.voice.file_id,
                caption=update.message.caption
            )
            message_type = "voice"
        elif update.message.audio:
            await context.bot.send_audio(
                chat_id=int(user_id),
                audio=update.message.audio.file_id,
                caption=update.message.caption
            )
            message_type = "audio"
        elif update.message.sticker:
            await context.bot.send_sticker(
                chat_id=int(user_id),
                sticker=update.message.sticker.file_id
            )
            message_type = "sticker"
        elif update.message.video_note:
            await context.bot.send_video_note(
                chat_id=int(user_id),
                video_note=update.message.video_note.file_id
            )
            message_type = "video_note"
        
        db.log_message(user_id, message_type, "to_user", update.message.text)
        logger.info(f"✅ Sent {message_type} reply to user {user_id}")
        
    except Exception as e:
        error_message = str(e).lower()
        
        if "blocked" in error_message or "bot was blocked" in error_message:
            error_details = (
                f"⚠️ <b>Cannot send message - User has blocked the bot</b>\n\n"
                f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
                f"📝 <b>User:</b> {user.get('user_name', 'Unknown')}\n"
                f"📱 <b>Username:</b> @{user.get('username', 'N/A')}\n\n"
                f"💡 <b>Note:</b> The user needs to unblock the bot and send /start again to receive messages."
            )
            logger.warning(f"⚠️ User {user_id} has blocked the bot")
        elif "chat not found" in error_message or "user not found" in error_message:
            error_details = (
                f"⚠️ <b>Cannot send message - User account not found</b>\n\n"
                f"👤 <b>User ID:</b> <code>{user_id}</code>\n\n"
                f"💡 <b>Possible reasons:</b>\n"
                f"• User deleted their Telegram account\n"
                f"• Invalid user ID\n"
                f"• User deactivated their account"
            )
            logger.warning(f"⚠️ User {user_id} not found (account may be deleted)")
        elif "bot can't initiate conversation" in error_message:
            error_details = (
                f"⚠️ <b>Cannot send message - Bot cannot initiate conversation</b>\n\n"
                f"👤 <b>User ID:</b> <code>{user_id}</code>\n\n"
                f"💡 <b>Solution:</b> The user needs to send /start to the bot first."
            )
            logger.warning(f"⚠️ Cannot initiate conversation with user {user_id}")
        elif "forbidden" in error_message:
            error_details = (
                f"⚠️ <b>Cannot send message - Access forbidden</b>\n\n"
                f"👤 <b>User ID:</b> <code>{user_id}</code>\n\n"
                f"💡 <b>Note:</b> User may have blocked the bot or privacy settings prevent messaging."
            )
            logger.warning(f"⚠️ Forbidden to send message to user {user_id}")
        else:
            error_details = (
                f"❌ <b>Failed to send message to user</b>\n\n"
                f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
                f"🔍 <b>Error:</b> {str(e)}\n\n"
                f"💡 <b>Note:</b> Please check the error details above."
            )
            logger.error(f"❌ Error sending reply to user {user_id}: {e}")
        
        try:
            await update.message.reply_text(
                error_details,
                message_thread_id=topic_id,
                parse_mode='HTML'
            )
        except Exception as notification_error:
            logger.error(f"Failed to send error notification: {notification_error}")

# ==================== COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if update.effective_chat.type != 'private':
        return
    
    welcome_message = (
        "👋 <b>Welcome to Support Bot!</b>\n\n"
        "Send me any message and our support team will respond soon.\n\n"
        "You can send:\n"
        "📝 Text messages\n"
        "📷 Photos\n"
        "🎥 Videos\n"
        "📄 Files/Documents\n"
        "🎵 Audio & Voice messages\n"
        "😊 Stickers\n\n"
        "⚡ <b>Quick Response:</b> You'll receive an instant confirmation when we get your message!\n\n"
        "Our team will reply as soon as possible!"
    )
    
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    if update.effective_chat.type != 'private':
        return
    
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
    """Handle /stats command - show bot statistics (admin only)"""
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    
    stats = db.get_total_stats()
    
    if stats:
        stats_message = (
            f"📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total Users: {stats['total_users']}\n"
            f"💬 Total Messages: {stats['total_messages']}\n"
            f"📁 Database: MongoDB\n"
            f"✅ Status: Active\n"
            f"🤖 Auto-Reply: {'Enabled' if AUTO_REPLY_ENABLED else 'Disabled'}"
        )
    else:
        stats_message = "❌ Error fetching statistics"
    
    await update.message.reply_text(stats_message, parse_mode='HTML')

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /userinfo command - show user statistics in their topic"""
    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return
    
    if not update.message.message_thread_id:
        await update.message.reply_text("Use this command inside a user's topic.")
        return
    
    topic_id = update.message.message_thread_id
    user = db.users.find_one({"topic_id": topic_id})
    
    if not user:
        await update.message.reply_text("❌ User not found for this topic.")
        return
    
    user_id = user['user_id']
    stats = db.get_user_stats(user_id)
    
    if stats:
        info_message = (
            f"📊 <b>User Information</b>\n\n"
            f"👤 Name: {user.get('user_name', 'N/A')}\n"
            f"📱 Username: @{user.get('username', 'N/A')}\n"
            f"🆔 User ID: <code>{user_id}</code>\n\n"
            f"📈 <b>Message Statistics:</b>\n"
            f"  ↗️ From User: {stats['from_user']}\n"
            f"  ↙️ To User: {stats['to_user']}\n"
            f"  📊 Total: {stats['total']}\n\n"
            f"🕐 First Contact: {user.get('created_at', 'N/A')}\n"
            f"🕐 Last Activity: {user.get('updated_at', 'N/A')}"
        )
    else:
        info_message = "❌ Error fetching user information"
    
    await update.message.reply_text(info_message, parse_mode='HTML', message_thread_id=topic_id)

# ==================== ERROR HANDLER ====================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception: {context.error}")

# ==================== MAIN FUNCTION ====================

def main():
    """Main function to run the support bot"""
    
    if SUPPORT_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Please set SUPPORT_BOT_TOKEN environment variable!")
        return
    
    if SUPPORT_GROUP_ID == -1001234567890:
        logger.error("Please set SUPPORT_GROUP_ID environment variable!")
        return
    
    logger.info("=" * 60)
    logger.info("🤖 STARTING SUPPORT BOT")
    logger.info("=" * 60)
    logger.info(f"✅ Bot Token: {SUPPORT_BOT_TOKEN[:10]}...")
    logger.info(f"✅ Support Group ID: {SUPPORT_GROUP_ID}")
    logger.info(f"✅ Auto-Reply: {'ENABLED' if AUTO_REPLY_ENABLED else 'DISABLED'}")
    logger.info(f"✅ Database: MongoDB")
    logger.info("=" * 60)
    
    # Create application
    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("userinfo", userinfo_command))
    
    # Add message handlers for private chats (user messages)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_text_message
    ))
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE,
        handle_photo
    ))
    app.add_handler(MessageHandler(
        filters.VIDEO & filters.ChatType.PRIVATE,
        handle_video
    ))
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.ChatType.PRIVATE,
        handle_document
    ))
    app.add_handler(MessageHandler(
        filters.VOICE & filters.ChatType.PRIVATE,
        handle_voice
    ))
    app.add_handler(MessageHandler(
        filters.AUDIO & filters.ChatType.PRIVATE,
        handle_audio
    ))
    app.add_handler(MessageHandler(
        filters.Sticker.ALL & filters.ChatType.PRIVATE,
        handle_sticker
    ))
    app.add_handler(MessageHandler(
        filters.VIDEO_NOTE & filters.ChatType.PRIVATE,
        handle_video_note
    ))
    
    # Add handler for support group replies
    app.add_handler(MessageHandler(
        filters.ChatType.SUPERGROUP & filters.Chat(chat_id=SUPPORT_GROUP_ID),
        handle_reply
    ))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    logger.info("✅ Support Bot is now running!")
    logger.info("📱 Ready to handle customer support messages")
    logger.info("=" * 60)
    
    # Start the bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':

    main()



