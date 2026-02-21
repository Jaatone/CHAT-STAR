#!/usr/bin/env python3
"""
Delete Bot - Bulk Message Deletion
Handles bulk message deletion in groups, supergroups, and channels
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
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
DELETE_BOT_TOKEN = os.getenv("DELETE_BOT_TOKEN", "8317686483:AAHH9z7a_FkkvQwHnmbHtBwrR0KixcGE5uQ")

# Dictionary to track active deletion operations per chat
# Format: {chat_id: {'active': bool, 'status_message': Message}}
active_deletions = {}

# ==================== COMMAND HANDLERS ====================

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /stop command to cancel ongoing deletion
    """
    logger.info(f"[DELETE BOT] /stop command received")
    
    # Get the message object (works for both regular messages and channel posts)
    message = update.message or update.channel_post
    if not message:
        logger.error("[DELETE BOT] No message or channel_post found")
        return
    
    chat_id = update.effective_chat.id
    
    # Check if there's an active deletion
    if chat_id not in active_deletions or not active_deletions[chat_id].get('active'):
        await message.reply_text(
            "ℹ️ No active deletion operation to stop.",
            parse_mode='HTML'
        )
        return
    
    # Check if user is admin (same logic as del_command)
    is_admin = False
    
    if update.effective_chat.type == 'channel':
        is_admin = True
        logger.info("[DELETE BOT] Channel detected - assuming admin")
    elif update.effective_user:
        try:
            chat_member = await context.bot.get_chat_member(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id
            )
            
            if chat_member.status in ['creator', 'administrator']:
                is_admin = True
                logger.info(f"[DELETE BOT] User {update.effective_user.id} is admin")
            else:
                logger.warning(f"[DELETE BOT] User {update.effective_user.id} is not admin")
        except Exception as e:
            logger.error(f"[DELETE BOT] Error checking admin status: {e}")
    
    if not is_admin:
        await message.reply_text(
            "❌ Only administrators can stop deletion operations."
        )
        return
    
    # Mark deletion as stopped
    active_deletions[chat_id]['active'] = False
    
    await message.reply_text(
        "🛑 <b>Deletion Stopped!</b>\n\n"
        "The ongoing deletion operation has been cancelled.",
        parse_mode='HTML'
    )
    
    logger.info(f"[DELETE BOT] Deletion stopped for chat {chat_id}")

async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /del command for bulk message deletion
    Works in groups, supergroups, and channels
    """
    
    logger.info(f"[DELETE BOT] /del command received")
    
    # Get the message object (works for both regular messages and channel posts)
    message = update.message or update.channel_post
    if not message:
        logger.error("[DELETE BOT] No message or channel_post found")
        return
    
    chat_id = update.effective_chat.id
    
    # Check if there's already an active deletion
    if chat_id in active_deletions and active_deletions[chat_id].get('active'):
        await message.reply_text(
            "⚠️ <b>Deletion Already in Progress!</b>\n\n"
            "Please wait for the current operation to complete or use <code>/stop</code> to cancel it.",
            parse_mode='HTML'
        )
        return
    
    # Allow in groups, supergroups, and channels only
    allowed_types = ['group', 'supergroup', 'channel']
    
    if update.effective_chat.type not in allowed_types:
        logger.warning(f"[DELETE BOT] Wrong chat type: {update.effective_chat.type}")
        try:
            await message.reply_text(
                "❌ This command only works in groups/channels.\n\n"
                "💡 Add me to your channel/group as an admin to use this command there."
            )
        except Exception as e:
            logger.error(f"[DELETE BOT] Error sending error message: {e}")
        return
    
    logger.info(f"[DELETE BOT] ✅ Chat type check passed: {update.effective_chat.type}")
    
    # For channels, there's no effective_user
    is_admin = False
    
    if update.effective_chat.type == 'channel':
        # In channels, messages are sent by the channel itself
        # We trust that only admins can send messages in the channel
        is_admin = True
        logger.info("[DELETE BOT] Channel detected - assuming admin")
    elif update.effective_user:
        # For groups/supergroups, check if user is admin
        try:
            chat_member = await context.bot.get_chat_member(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id
            )
            
            if chat_member.status in ['creator', 'administrator']:
                is_admin = True
                logger.info(f"[DELETE BOT] User {update.effective_user.id} is admin")
            else:
                logger.warning(f"[DELETE BOT] User {update.effective_user.id} is not admin")
        except Exception as e:
            logger.error(f"[DELETE BOT] Error checking admin status: {e}")
    
    if not is_admin:
        await message.reply_text(
            "❌ Only administrators can use this command."
        )
        return
    
    logger.info("[DELETE BOT] ✅ Admin check passed")
    
    # Parse arguments
    # For MessageHandler (channels), context.args is None, so parse from message text
    if context.args is None:
        # Extract arguments from message text
        text = message.text or ""
        parts = text.split()
        if len(parts) == 3 and parts[0] == '/del':
            context.args = [parts[1], parts[2]]
        else:
            context.args = []
    
    if len(context.args) != 2:
        logger.warning(f"[DELETE BOT] Invalid arguments: {context.args}")
        await message.reply_text(
            "❌ <b>Invalid format!</b>\n\n"
            "<b>Usage:</b> <code>/del &lt;start_id&gt; &lt;end_id&gt;</code>\n\n"
            "<b>Example:</b> <code>/del 100 200</code>\n\n"
            "This will delete messages from ID 100 to 200 (inclusive).",
            parse_mode='HTML'
        )
        return
    
    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])
        logger.info(f"[DELETE BOT] Parsed IDs - Start: {start_id}, End: {end_id}")
    except ValueError:
        logger.error("[DELETE BOT] Invalid number format")
        await message.reply_text(
            "❌ Both start and end must be valid numbers!\n\n"
            "<b>Example:</b> <code>/del 100 200</code>",
            parse_mode='HTML'
        )
        return
    
    # Validate range
    if start_id > end_id:
        logger.warning(f"[DELETE BOT] Start ID > End ID")
        await message.reply_text(
            "❌ Start ID must be less than or equal to End ID!"
        )
        return
    
    if end_id - start_id > 1000:
        logger.warning(f"[DELETE BOT] Range too large")
        await message.reply_text(
            "⚠️ You can only delete up to 1000 messages at once.\n"
            "Please use a smaller range."
        )
        return
    
    logger.info("[DELETE BOT] ✅ All validations passed. Starting deletion...")
    
    # Confirm deletion
    total_messages = end_id - start_id + 1
    
    try:
        status_message = await message.reply_text(
            f"🗑️ <b>Starting Bulk Deletion</b>\n\n"
            f"📊 Range: {start_id} → {end_id}\n"
            f"📝 Total Messages: {total_messages}\n"
            f"⏳ Status: Processing...\n\n"
            f"💡 Use <code>/stop</code> to cancel",
            parse_mode='HTML'
        )
        logger.info("[DELETE BOT] Status message sent")
    except Exception as e:
        logger.error(f"[DELETE BOT] Error sending status message: {e}")
        return
    
    # Mark deletion as active
    active_deletions[chat_id] = {
        'active': True,
        'status_message': status_message
    }
    
    # Start deletion
    deleted_count = 0
    failed_count = 0
    
    batch_size = 10
    current_batch = []
    
    logger.info(f"[DELETE BOT] Starting bulk deletion in chat {chat_id}: {start_id} to {end_id}")
    
    for msg_id in range(start_id, end_id + 1):
        # Check if deletion was stopped
        if not active_deletions.get(chat_id, {}).get('active'):
            logger.info(f"[DELETE BOT] Deletion stopped by user in chat {chat_id}")
            try:
                await status_message.edit_text(
                    f"🛑 <b>Bulk Deletion Stopped!</b>\n\n"
                    f"📊 Range: {start_id} → {end_id}\n"
                    f"📝 Total Messages: {total_messages}\n"
                    f"✅ Deleted: {deleted_count}\n"
                    f"❌ Failed: {failed_count}\n"
                    f"⏸️ Stopped at message: {msg_id}\n"
                    f"📈 Completed: {int((deleted_count + failed_count) / total_messages * 100)}%",
                    parse_mode='HTML'
                )
            except:
                pass
            
            # Clean up
            if chat_id in active_deletions:
                del active_deletions[chat_id]
            
            return
        
        current_batch.append(msg_id)
        
        if len(current_batch) >= batch_size or msg_id == end_id:
            try:
                for batch_msg_id in current_batch:
                    try:
                        await context.bot.delete_message(
                            chat_id=chat_id,
                            message_id=batch_msg_id
                        )
                        deleted_count += 1
                    except Exception as e:
                        failed_count += 1
                        logger.debug(f"[DELETE BOT] Failed to delete message {batch_msg_id}: {e}")
                
                # Update status every 50 messages
                if deleted_count % 50 == 0:
                    try:
                        await status_message.edit_text(
                            f"🗑️ <b>Bulk Deletion in Progress</b>\n\n"
                            f"📊 Range: {start_id} → {end_id}\n"
                            f"✅ Deleted: {deleted_count}\n"
                            f"❌ Failed: {failed_count}\n"
                            f"⏳ Progress: {int((deleted_count + failed_count) / total_messages * 100)}%\n\n"
                            f"💡 Use <code>/stop</code> to cancel",
                            parse_mode='HTML'
                        )
                    except:
                        pass
                
                current_batch = []
                await asyncio.sleep(0.1)  # Small delay to avoid rate limits
                
            except Exception as e:
                logger.error(f"[DELETE BOT] Error deleting batch: {e}")
                failed_count += len(current_batch)
                current_batch = []
    
    # Final status
    try:
        await status_message.edit_text(
            f"✅ <b>Bulk Deletion Completed!</b>\n\n"
            f"📊 Range: {start_id} → {end_id}\n"
            f"📝 Total Messages: {total_messages}\n"
            f"✅ Successfully Deleted: {deleted_count}\n"
            f"❌ Failed: {failed_count}\n"
            f"📈 Success Rate: {int(deleted_count / total_messages * 100) if total_messages > 0 else 0}%",
            parse_mode='HTML'
        )
        logger.info("[DELETE BOT] Final status sent")
    except Exception as e:
        logger.error(f"[DELETE BOT] Error updating final status: {e}")
    
    logger.info(
        f"[DELETE BOT] Bulk deletion completed in chat {chat_id}: "
        f"{deleted_count} deleted, {failed_count} failed"
    )
    
    # Clean up active deletion tracking
    if chat_id in active_deletions:
        del active_deletions[chat_id]

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    message = update.message or update.channel_post
    if not message:
        return
    
    welcome_message = (
        "🗑️ <b>Bulk Delete Bot</b>\n\n"
        "I can help you delete multiple messages at once in groups/channels.\n\n"
        "<b>Commands:</b>\n"
        "🗑️ <code>/del &lt;start_id&gt; &lt;end_id&gt;</code> - Delete messages\n"
        "🛑 <code>/stop</code> - Cancel ongoing deletion\n"
        "ℹ️ <code>/help</code> - Show help\n\n"
        "<b>Example:</b>\n"
        "<code>/del 100 200</code>\n\n"
        "⚠️ <b>Requirements:</b>\n"
        "• You must be an admin (or it's a channel)\n"
        "• Bot must be an admin\n"
        "• Bot needs delete permission\n\n"
        "💡 <b>Tip:</b> Add me to your group/channel and make me an admin!"
    )
    
    await message.reply_text(welcome_message, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    message = update.message or update.channel_post
    if not message:
        return
    
    help_message = (
        "ℹ️ <b>Bulk Delete Bot - Help</b>\n\n"
        "<b>Commands:</b>\n"
        "🗑️ <code>/del &lt;start&gt; &lt;end&gt;</code> - Delete messages\n"
        "🛑 <code>/stop</code> - Stop ongoing deletion\n"
        "ℹ️ <code>/help</code> - Show this help\n\n"
        "<b>Examples:</b>\n"
        "• <code>/del 100 200</code> - Delete messages 100-200\n"
        "• <code>/del 500 510</code> - Delete messages 500-510\n"
        "• <code>/stop</code> - Cancel current deletion\n\n"
        "<b>Limits:</b>\n"
        "• Maximum 1000 messages per command\n"
        "• Admin only command (or channel posts)\n"
        "• Only one deletion per chat at a time\n\n"
        "<b>How to get message IDs:</b>\n"
        "• Right-click on message → Copy Message Link\n"
        "• The number at the end is the message ID\n\n"
        "<b>Supported Chat Types:</b>\n"
        "• Groups ✅\n"
        "• Supergroups ✅\n"
        "• Channels ✅"
    )
    
    await message.reply_text(help_message, parse_mode='HTML')

# ==================== ERROR HANDLER ====================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"[DELETE BOT] Exception: {context.error}")

# ==================== MAIN FUNCTION ====================

def main():
    """Main function to run the delete bot"""
    
    if DELETE_BOT_TOKEN == "YOUR_DELETE_BOT_TOKEN_HERE":
        logger.error("Please set DELETE_BOT_TOKEN environment variable!")
        return
    
    logger.info("=" * 60)
    logger.info("🗑️ STARTING DELETE BOT")
    logger.info("=" * 60)
    logger.info(f"✅ Bot Token: {DELETE_BOT_TOKEN[:10]}...")
    logger.info("=" * 60)
    
    # Create application
    app = Application.builder().token(DELETE_BOT_TOKEN).build()
    
    # Add handlers for BOTH regular messages AND channel posts
    # For groups and supergroups
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # For channels - handle channel posts with MessageHandler
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.Regex(r'^/del(\s+\d+\s+\d+)?'),
        del_command
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.Regex(r'^/stop'),
        stop_command
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.Regex(r'^/start'),
        start_command
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.Regex(r'^/help'),
        help_command
    ))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    logger.info("✅ Delete Bot is now running!")
    logger.info("🗑️ Ready to handle bulk deletions in groups/channels")
    logger.info("🛑 /stop command available to cancel operations")
    logger.info("=" * 60)
    
    # Start the bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()