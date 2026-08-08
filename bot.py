import os
import sqlite3
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_ID = 123456789  # استبدل هذا الأيدي بأيدي حسابك الحقيقي

def init_db():
    conn = sqlite3.connect('bot_full_database.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY, 
                        balance REAL DEFAULT 0, 
                        spins INTEGER DEFAULT 3, 
                        referred_by INTEGER, 
                        is_banned INTEGER DEFAULT 0
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS wheel_probs (prize INTEGER PRIMARY KEY, weight INTEGER)''')
    cursor.execute("SELECT COUNT(*) FROM wheel_probs")
    if cursor.fetchone()[0] == 0:
        default_probs = {0: 40, 5: 25, 10: 15, 15: 10, 25: 5, 50: 3, 100: 1.5, 200: 0.5}
        for p, w in default_probs.items():
            cursor.execute("INSERT INTO wheel_probs (prize, weight) VALUES (?, ?)", (p, int(w * 10)))
    conn.commit()
    conn.close()

init_db()

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    ban = cursor.fetchone()
    if ban and ban[0] == 1:
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت.")
        conn.close()
        return

    if context.args:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:
                cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                if not cursor.fetchone():
                    cursor.execute("INSERT OR IGNORE INTO users (user_id, referred_by, spins) VALUES (?, ?, 3)", (user_id, referrer_id))
                    conn.commit()
                    cursor.execute("UPDATE users SET spins = spins + 1 WHERE user_id = ?", (referrer_id,))
                    conn.commit()
                    try:
                        await context.bot.send_message(referrer_id, "🎉 انضم شخص جديد عبر رابط إحالتك وحصلت على لفة مجانية!")
                    except Exception:
                        pass
        except ValueError:
            pass

    cursor.execute("INSERT OR IGNORE INTO users (user_id, spins) VALUES (?, 3)", (user_id,))
    conn.commit()
    
    cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("💰 رصيدي", callback_data="my_balance"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="ref_link")],
        [InlineKeyboardButton("💳 طلب سحب", callback_data="withdraw"), InlineKeyboardButton("🛠️ الدعم", callback_data="support")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 لوحة الإدارة", callback_data="admin_panel")])

    await update.message.reply_text(f"مرحباً بك يا عبود في بوتك المجاني السريع 🚀\n\nرصيدك: {data[0]} نقطة | اللفات المتاحة: {data[1]}", reply_markup=InlineKeyboardMarkup(keyboard))

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "my_balance":
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        await query.answer(f"💰 رصيدك: {res[0]} نقطة\n🎡 اللفات: {res[1]}", show_alert=True)
    elif data == "ref_link":
        ref_url = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.message.reply_text(f"🔗 رابط إحالتك الشخصي:\n{ref_url}\n\nشارك الرابط وكل شخص يدخل تكسب لفة مجانية!")
    elif data == "withdraw":
        await query.message.reply_text("💳 أرسل عنوان محفظتك أو وسيلة السحب مع المبلغ ليتم مراجعته.")
    elif data == "support":
        await query.message.reply_text("🛠️ للإبلاغ عن مشكلة، تواصل مع الإدارة.")

if __name__ == '__main__':
    if TOKEN:
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(buttons_handler))
        if __name__ == '__main__':
    if TOKEN:
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(buttons_handler))
        print("Bot is starting via Polling...")
        
        # استبدل application.run_polling() بهذا السطر الآمن لإنشاء حلقة عمل (Event Loop) يدوية:
        import asyncio
        asyncio.run(application.run_polling())
        
