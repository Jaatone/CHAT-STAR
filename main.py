#!/usr/bin/env python3
""" Support Bot with Admin Edit Sync """

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import Forbidden, BadRequest

import os
import logging
from datetime import datetime
from pymongo import MongoClient
from flask import Flask
from threading import Thread

# ================= CONFIG =================

SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID"))
MONGODB_URL = os.getenv("MONGODB_URL")

AUTO_REPLY_ENABLED = True
AUTO_REPLY_MESSAGE = "✅ Message received! Our team will reply soon."

# ================= LOGGING =================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# ================= DATABASE =================

class DatabaseManager:

    def __init__(self, mongodb_url):

        self.client = MongoClient(mongodb_url)

        self.db = self.client['telegram_support_bot']

        self.users = self.db['users']
        self.messages = self.db['messages']

        self.users.create_index("user_id", unique=True)
        self.users.create_index("topic_id")

        logger.info("MongoDB Connected")

    def get_user_topic(self, user_id):

        user = self.users.find_one({"user_id": user_id})
        return user['topic_id'] if user else None

    def save_user_topic(self, user_id, topic_id, name, username):

        self.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "topic_id": topic_id,
                "name": name,
                "username": username,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )

    def get_user_by_topic(self, topic_id):

        return self.users.find_one({"topic_id": topic_id})

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

# ================= USER MESSAGE =================

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    user_id = user.id

    if db.is_user_blocked(user_id):

        await update.message.reply_text("❌ You are banned.")
        return

    try:

        if AUTO_REPLY_ENABLED:
            await update.message.reply_text(AUTO_REPLY_MESSAGE)

        topic_id = db.get_user_topic(user_id)

        if not topic_id:

            topic = await context.bot.create_forum_topic(
                chat_id=SUPPORT_GROUP_ID,
                name=f"👤 {user.first_name}"
            )

            topic_id = topic.message_thread_id

            db.save_user_topic(
                user_id,
                topic_id,
                user.first_name,
                user.username
            )

            await context.bot.send_message(
                chat_id=SUPPORT_GROUP_ID,
                message_thread_id=topic_id,
                text=f"🆕 New User\nName: {user.first_name}\nID: `{user_id}`",
                parse_mode="Markdown"
            )

        await context.bot.copy_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=user_id,
            message_id=update.message.message_id,
            message_thread_id=topic_id
        )

    except Exception as e:

        logger.error(e)

# ================= ADMIN REPLY =================

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message or update.edited_message

    if not msg:
        return

    if msg.chat.id != SUPPORT_GROUP_ID:
        return

    topic_id = msg.message_thread_id

    if not topic_id:
        return

    user = db.get_user_by_topic(topic_id)

    if not user:
        return

    user_id = user["user_id"]

    if db.is_user_blocked(user_id):
        return

    try:

        # ===== EDIT MESSAGE =====

        if update.edited_message:

            entry = db.messages.find_one({"admin_msg_id": msg.message_id})

            if entry and msg.text:

                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=entry["user_msg_id"],
                    text=msg.text
                )

            return

        # ===== NEW MESSAGE =====

        if msg.text:

            sent = await context.bot.send_message(
                chat_id=user_id,
                text=msg.text
            )

        else:

            sent = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=SUPPORT_GROUP_ID,
                message_id=msg.message_id
            )

        db.messages.insert_one({
            "admin_msg_id": msg.message_id,
            "user_msg_id": sent.message_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow()
        })

    except Forbidden:

        await msg.reply_text("❌ User blocked bot.")

    except BadRequest:

        await msg.reply_text("❌ Invalid user.")

# ================= COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 Hello! Send your message to support."
    )

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return

    topic_id = update.message.message_thread_id

    user = db.get_user_by_topic(topic_id)

    if not user:
        return

    db.set_user_block(user["user_id"], True)

    await update.message.reply_text("🚫 User banned")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id != SUPPORT_GROUP_ID:
        return

    topic_id = update.message.message_thread_id

    user = db.get_user_by_topic(topic_id)

    if not user:
        return

    db.set_user_block(user["user_id"], False)

    await update.message.reply_text("✅ User unbanned")

# ================= MAIN =================

def main():

    app = Application.builder().token(SUPPORT_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))

    app.add_handler(
        MessageHandler(
            filters.ALL & filters.ChatType.PRIVATE,
            handle_user_message
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ALL & filters.Chat(SUPPORT_GROUP_ID),
            handle_reply
        )
    )

    logger.info("Bot Started")

    app.run_polling(drop_pending_updates=True)

# ================= HEALTH SERVER =================

health_app = Flask(__name__)

@health_app.route("/")
def home():
    return "OK"

Thread(
    target=lambda: health_app.run(host="0.0.0.0", port=8000),
    daemon=True
).start()

if __name__ == "__main__":
    main()