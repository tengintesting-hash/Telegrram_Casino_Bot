import os

import psycopg2
import requests
from psycopg2.extras import RealDictCursor
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ChatJoinRequestHandler, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL")


def get_db():
    return psycopg2.connect(DATABASE_URL)


def fetch_all(query, params=None):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or {})
            return cur.fetchall()


def fetch_one(query, params=None):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or {})
            return cur.fetchone()


def execute(query, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
        conn.commit()


def ensure_user(user, referred_by=None):
    existing = fetch_one("SELECT * FROM users WHERE telegram_id = %(telegram_id)s", {"telegram_id": user.id})
    if not existing:
        execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name, referred_by)
            VALUES (%(telegram_id)s, %(username)s, %(first_name)s, %(last_name)s, %(referred_by)s)
            """,
            {
                "telegram_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "referred_by": referred_by,
            },
        )
        if referred_by:
            execute(
                """
                UPDATE users SET tokens = tokens + 1000 WHERE telegram_id = %(referrer)s
                """,
                {"referrer": referred_by},
            )
            execute(
                """
                INSERT INTO token_history (user_id, change_amount, reason)
                VALUES (%(referrer)s, 1000, %(reason)s)
                """,
                {"referrer": referred_by, "reason": f"Referral bonus for {user.id}"},
            )


def get_mandatory_channels():
    return fetch_all("SELECT * FROM mandatory_channels ORDER BY id")


def check_subscription(user_id: int):
    channels = get_mandatory_channels()
    missing = []
    for channel in channels:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
            params={"chat_id": channel["channel_id"], "user_id": user_id},
            timeout=10,
        )
        data = response.json()
        if not data.get("ok"):
            missing.append(channel)
            continue
        status = data["result"]["status"]
        if status in {"left", "kicked"}:
            missing.append(channel)
    return missing


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                referred_by = int(parts[1].replace("ref_", ""))
            except ValueError:
                referred_by = None
    ensure_user(user, referred_by=referred_by)
    missing = check_subscription(user.id)
    if missing:
        buttons = []
        for channel in missing:
            username = channel["channel_username"]
            link = f"https://t.me/{username}" if username else f"https://t.me/c/{channel['channel_id']}"
            buttons.append([InlineKeyboardButton(channel["channel_title"] or str(channel["channel_id"]), url=link)])
        await update.message.reply_text(
            "Please subscribe to the mandatory channels before continuing:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Next", callback_data="next")]])
    await update.message.reply_text("Welcome to the project!", reply_markup=keyboard)


async def next_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    description = (
        "This project combines a Telegram Bot and WebApp for tasks, tokens, and community updates."
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open Project", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await query.message.reply_text(description, reply_markup=keyboard)


async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    channels = get_mandatory_channels()
    channel_ids = {channel["channel_id"] for channel in channels}
    if join_request.chat.id in channel_ids:
        await join_request.approve()


def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(next_step, pattern="^next$"))
    application.add_handler(ChatJoinRequestHandler(approve_join_request))
    application.run_polling()


if __name__ == "__main__":
    main()
