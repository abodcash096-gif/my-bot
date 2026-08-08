import os
import sqlite3
import random
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- الإعدادات ---
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 7255100997

def init_db():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    cursor = conn.cursor()
    # جدول المستخدمين: الأيدي، الرصيد، حالة التحقق، كود الكابتشا، أيدي المُحيل
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0, verified INTEGER DEFAULT 0, captcha_ans INTEGER, ref_by INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

init_db()

# --- دالة التحقق من الاشتراك الإجباري ---
async def check_sub(user_id, context):
    conn = sqlite3.connect('bot_data.db')
    channels = conn.execute("SELECT channel_username FROM channels").fetchall()
    conn.close()
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch[0], user_id=user_id)
            if member.status in ['left', 'kicked']: return False, ch[0]
        except: continue
    return True, None

# --- الأوامر ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك
    is_sub, ch = await check_sub(user_id, context)
    if not is_sub:
        await update.message.reply_text(f"⚠️ يرجى الاشتراك في القناة {ch} أولاً!")
        return

    # نظام الكابتشا
    num1, num2 = random.randint(1, 10), random.randint(1, 10)
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR REPLACE INTO users (user_id, captcha_ans, verified) VALUES (?, ?, 0)", (user_id, num1 + num2))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🤖 أثبت أنك إنسان، ناتج {num1} + {num2}؟")

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # --- الألعاب ---
    if data == "game_boxes":
        conn = sqlite3.connect('bot_data.db')
        balance = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
        if balance >= 5:
            win = random.choice([True, False])
            new_bal = balance + 10 if win else balance - 5
            conn.execute("UPDATE users SET balance = ? WHERE user_id=?", (new_bal, user_id))
            conn.commit()
            await query.message.edit_text(f"📦 النتيجة: {'🎉 ربحت 10' if win else '❌ خسرت 5'}.. رصيدك الحالي: {new_bal}")
        else:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
        conn.close()

    # --- لوحة الإدارة ---
    if user_id == ADMIN_ID:
        if data == "adm_reset":
            sqlite3.connect('bot_data.db').execute("UPDATE users SET balance = 0")
            await query.message.reply_text("🧹 تم تصفير جميع الأرصدة.")
        elif data == "adm_add_ch":
            context.user_data['waiting_ch'] = True
            await query.message.reply_text("أرسل معرف القناة (مثال: @ChannelName):")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    conn = sqlite3.connect('bot_data.db')

    # التحقق من الكابتشا
    user_data = conn.execute("SELECT captcha_ans, verified FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user_data and user_data[1] == 0:
        if text.isdigit() and int(text) == user_data[0]:
            conn.execute("UPDATE users SET verified=1 WHERE user_id=?", (user_id,))
            conn.commit()
            kb = [[InlineKeyboardButton("📦 لعبة الصناديق", callback_data="game_boxes")]]
            if user_id == ADMIN_ID:
                kb.append([InlineKeyboardButton("🧹 تصفير الأرصدة", callback_data="adm_reset"), InlineKeyboardButton("➕ إضافة قناة", callback_data="adm_add_ch")])
            await update.message.reply_text("✅ تم التحقق بنجاح! اختر من القائمة:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text("❌ إجابة خاطئة!")
    
    # إضافة قناة من الإدارة
    elif context.user_data.get('waiting_ch'):
        conn.execute("INSERT OR IGNORE INTO channels VALUES (?)", (text,))
        conn.commit()
        await update.message.reply_text("✅ تم إضافة القناة بنجاح.")
        context.user_data['waiting_ch'] = False
    
    conn.close()

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت يعمل بكامل طاقته...")
    app.run_polling()

if __name__ == '__main__':
    main()
