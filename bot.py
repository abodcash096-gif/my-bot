import os
import sqlite3
import random
import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- 1. سيرفر وهمي لإبقاء البوت نشطاً على Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active and running smooth!")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- 2. الإعدادات الأساسية وقاعدة البيانات ---
TOKEN = os.environ.get("BOT_TOKEN")
SUPER_ADMIN = 7255100997

def get_db_connection():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        phone TEXT,
        balance REAL DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        ref_by INTEGER,
        ref_count INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        last_daily_claim REAL DEFAULT 0
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
        channel_username TEXT PRIMARY KEY,
        channel_title TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdraw_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        method TEXT,
        account_info TEXT,
        status TEXT DEFAULT 'pending'
    )''')

    defaults = [
        ('ref_price', 5.0), 
        ('min_withdraw', 200.0), 
        ('game_cost', 5.0), 
        ('game_win_rate', 40.0),
        ('daily_bonus', 10.0),
        ('maintenance_mode', 0.0)
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (SUPER_ADMIN,))
    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة لقاعدة البيانات ---
def get_setting(key):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else 0.0

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = get_db_connection()
    res = conn.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return res is not None

def add_xp(user_id, amount):
    conn = get_db_connection()
    user = conn.execute("SELECT xp, level FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user:
        new_xp = user['xp'] + amount
        new_level = (new_xp // 100) + 1
        conn.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (new_xp, new_level, user_id))
        conn.commit()
    conn.close()

# --- دالة فحص الاشتراك بالنظام الجديد ---
async def check_sub(user_id, context):
    conn = get_db_connection()
    channels = conn.execute("SELECT channel_username, channel_title FROM channels").fetchall()
    conn.close()
    
    unsubbed = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch['channel_username'], user_id=user_id)
            if member.status in ['left', 'kicked']:
                unsubbed.append((ch['channel_username'], ch['channel_title'] or ch['channel_username']))
        except Exception:
            unsubbed.append((ch['channel_username'], ch['channel_title'] or ch['channel_username']))
    return unsubbed

async def enforce_channel_subscription(user_id, context, update_or_query):
    """فحص إجباري لاشتراك القنوات ويرسل القنوات غير المشترك بها فوراً"""
    if is_admin(user_id):
        return True
        
    unsub = await check_sub(user_id, context)
    if unsub:
        kb = []
        for ch_user, ch_title in unsub:
            clean_user = ch_user.replace('@', '')
            kb.append([InlineKeyboardButton(f"📢 اشترك في: {ch_title}", url=f"https://t.me/{clean_user}")])
        kb.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub_again")])
        
        msg_text = "⚠️ **يجب عليك الاشتراك في جميع القنوات لاستخدام البوت:**\nيرجى الاشتراك بالقنوات أدناه ثم الضغط على زر التحقق."
        
        if hasattr(update_or_query, 'edit_text'):
            try:
                await update_or_query.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except:
                await context.bot.send_message(chat_id=user_id, text=msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return False
    return True

# --- 3. أمر البداية /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**\n\nيرجى العودة لاحقاً!", parse_mode="Markdown")
        return

    conn = get_db_connection()
    user = conn.execute("SELECT user_id, is_banned, is_verified FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user['is_banned'] == 1:
        await update.message.reply_text("❌ حسابك محظور حالياً من استخدام الخدمة.")
        conn.close()
        return

    if not user:
        ref_id = None
        if context.args and context.args[0].isdigit():
            possible_ref = int(context.args[0])
            if possible_ref != user_id:
                ref_id = possible_ref
        conn.execute("INSERT INTO users (user_id, full_name, ref_by, balance) VALUES (?, ?, ?, 0)", (user_id, full_name, ref_id))
        conn.commit()
    
    conn.close()

    if not await enforce_channel_subscription(user_id, context, update.message):
        return

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✨ دخول البوت والشروط", callback_data="accept_terms")]])
    await update.message.reply_text(
        f"🌟 **أهلاً بك يا {full_name} في نظام الألعاب والكسب المطور!**\n\n"
        "🏛️ **الشروط والاحكام:**\n"
        "• يمنع الحسابات الوهمية والإحالات المزيفة.\n"
        "• الحظر النهائي وتصفير الرصيد سيكون عقوبة أي تلاعب.\n\n"
        "اضغط على الزر أدناه للبدء:",
        reply_markup=kb, parse_mode="Markdown"
    )

# --- 4. القائمة الرئيسية الاحترافية ---
async def main_menu(user_id, context, message_obj=None, text="🌟 القائمة الرئيسية:"):
    if not await enforce_channel_subscription(user_id, context, message_obj or user_id):
        return

    conn = get_db_connection()
    user = conn.execute("SELECT full_name, balance, level, xp FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return

    msg = (
        f"👑 **مرحباً بك:** {user['full_name']}\n"
        f"💰 **الرصيد الحالي:** `{user['balance']:.2f}` ليرة\n"
        f"🏅 **المستوى:** {user['level']} | **الخبرة (XP):** {user['xp']}/100\n"
        "--------------------------------------"
    )
    
    kb = [
        [InlineKeyboardButton("👤 حسابي", callback_data="my_account"), InlineKeyboardButton("🎁 المكافأة اليومية", callback_data="daily_bonus")],
        [InlineKeyboardButton("🎮 صالة الألعاب المباشرة 🔥", callback_data="games_menu"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("💳 سحب الرصيد", callback_data="withdraw"), InlineKeyboardButton("🎟️ تفعيل كود", callback_data="enter_code")],
        [InlineKeyboardButton("🛍️ طلب بوت خاص", callback_data="buy_bot"), InlineKeyboardButton("💬 الدعم الفني", callback_data="support_msg")],
        [InlineKeyboardButton("📢 القناة الرسمية", url="https://t.me/cashinsher")]
    ]
    
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("⚙️ لوحة الإدارة الذكية", callback_data="admin_panel")])
        
    markup = InlineKeyboardMarkup(kb)
    if message_obj and hasattr(message_obj, 'edit_text'):
        try:
            await message_obj.edit_text(f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
        except:
            await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")

# --- 5. معالج الأزرار التفاعلية ---
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await query.message.edit_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**\n\nيرجى العودة لاحقاً!", parse_mode="Markdown")
        return

    conn = get_db_connection()
    user = conn.execute("SELECT is_banned, is_verified, balance, ref_count, level, xp, last_daily_claim FROM users WHERE user_id=?", (user_id,)).fetchone()

    if user and user['is_banned'] == 1:
        await query.message.edit_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return

    # فحص القنوات الإجبارية لجميع الأزرار عدا زر إعادة التحقق
    if data != "check_sub_again":
        if not await enforce_channel_subscription(user_id, context, query.message):
            conn.close()
            return

    # زر التحقق من الاشتراك
    if data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.answer("❌ لم تقم بالاشتراك في جميع القنوات المطلوبة بعد!", show_alert=True)
            await enforce_channel_subscription(user_id, context, query.message)
        else:
            await query.message.edit_text("✅ **تم التحقق من الاشتراك بنجاح!**")
            await main_menu(user_id, context, query.message)

    # قبول الشروط
    elif data == "accept_terms":
        await main_menu(user_id, context, query.message)

    # العودة للرئيسية
    elif data == "back_main":
        await main_menu(user_id, context, query.message)

    # المكافأة اليومية
    elif data == "daily_bonus":
        current_time = time.time()
        last_claim = user['last_daily_claim']
        if current_time - last_claim >= 86400:
            bonus = get_setting('daily_bonus')
            conn.execute("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE user_id = ?", (bonus, current_time, user_id))
            conn.commit()
            add_xp(user_id, 10)
            await query.message.edit_text(f"🎁 **مبروك!** حصلت على مكافأتك اليومية بقيمة `{bonus}` ليرة و +10 XP!\nعد بعد 24 ساعة للمزيد.", parse_mode="Markdown")
        else:
            remaining = int((86400 - (current_time - last_claim)) // 3600)
            await query.answer(f"⏳ لقد أخذت مكافأتك اليوم! يرجى العودة بعد {remaining} ساعة.", show_alert=True)

    # حسابي ورابط الإحالة
    elif data == "my_account":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])
        await query.message.edit_text(
            f"👤 **بيانات حسابك الشخصي:**\n\n"
            f"🆔 **الأيدي:** `{user_id}`\n"
            f"💰 **الرصيد:** `{user['balance']:.2f}` ليرة\n"
            f"🏅 **المستوى:** {user['level']} (XP: {user['xp']}/100)\n"
            f"👥 **إجمالي الإحالات:** {user['ref_count']} إحالة", 
            reply_markup=kb, parse_mode="Markdown"
        )

    elif data == "my_ref":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])
        await query.message.edit_text(
            f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
            f"👥 **إحالاتك الناجحة:** {user['ref_count']}\n"
            f"💎 **ربح الإحالة:** {get_setting('ref_price')} ليرة لكل صديق يسجل ويؤكد حسابه!", 
            reply_markup=kb, parse_mode="Markdown"
        )

    # الدعم والطلبات والكود
    elif data == "buy_bot":
        context.user_data['state'] = 'waiting_bot_desc'
        await query.message.edit_text("📝 **يرجى إرسال تفاصيل ومواصفات البوت الذي ترغب بشرائه:**")

    elif data == "support_msg":
        context.user_data['state'] = 'waiting_support_msg'
        await query.message.edit_text("💬 **أرسل رسالتك أو استفسارك للدعم الفني الآن:**")

    elif data == "enter_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎟️ **أرسل كود الهدية للتحقق والتفعيل:**")

    # نظام السحب
    elif data == "withdraw":
        min_w = get_setting('min_withdraw')
        if user['balance'] < min_w:
            await query.message.edit_text(f"❌ **الحد الأدنى للسحب هو `{min_w}` ليرة.**\nرصيدك الحالي (`{user['balance']:.2f}`) غير كافٍ.", parse_mode="Markdown")
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("شام كاش 💸", callback_data="w_sham"), InlineKeyboardButton("سيريتل كاش 📱", callback_data="w_syriatel")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
            ])
            await query.message.edit_text("💳 **اختر طريقة السحب المناسبة لك:**", reply_markup=kb)

    elif data in ["w_sham", "w_syriatel"]:
        method = "شام كاش" if data == "w_sham" else "سيريتل كاش"
        context.user_data['withdraw_method'] = method
        context.user_data['state'] = 'waiting_withdraw_amount'
        await query.message.edit_text(f"💵 **أدخل المبلغ المراد سحبه الآن عبر ({method}):**\n• الحد الأدنى: `{get_setting('min_withdraw')}` ليرة\n• رصيدك المتاح: `{user['balance']:.2f}` ليرة", parse_mode="Markdown")

    # ==================== صالة الألعاب ====================
    elif data == "games_menu":
        cost = get_setting('game_cost')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎡 عجلة الحظ", callback_data="g_wheel"), InlineKeyboardButton("🎰 ماكينة السلوت", callback_data="g_slot")],
            [InlineKeyboardButton("🎲 النرد السحري", callback_data="g_dice")],
            [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main")]
        ])
        await query.message.edit_text(
            f"🎮 **صالة الألعاب المباشرة والتحديات:**\n\n"
            f"💵 **تكلفة الجولة:** `{cost}` ليرة.\n"
            f"🏆 كل جولة تمنحك نقاط خبرة XP لرفع مستواك!\n"
            f"اختر اللعبة وابدأ الحظ والتسلية:", 
            reply_markup=kb, parse_mode="Markdown"
        )

    # عجلة الحظ
    elif data == "g_wheel":
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ للعب!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        msg = await query.message.edit_text("🌀 **جاري تدوير العجلة...**")
        frames = ["🎡 [ 🎯 | 🎁 | ❌ ]", "🎡 [ ❌ | 🎯 | 🎁 ]", "🎡 [ 🎁 | ❌ | 🎯 ]", "🎡 [ 🔥 | 💎 | 🏆 ]"]
        for f in frames:
            await asyncio.sleep(0.3)
            try: await msg.edit_text(f)
            except: pass

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 لعب مرة أخرى", callback_data="g_wheel"), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await msg.edit_text(f"🎉 **فوز رائع!** توقفت العجلة على الجائزة ⭐\nربحت `{prize}` ليرة و +15 XP! 🎡", reply_markup=kb, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await msg.edit_text("❌ **حظاً أوفر!** توقفت العجلة على الخسارة. حصلت على +5 XP.", reply_markup=kb)

    # لعبة السلوت
    elif data == "g_slot":
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        await context.bot.send_dice(chat_id=user_id, emoji="🎰")
        await asyncio.sleep(2.5)

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 جولة جديدة", callback_data="g_slot"), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])
        if win:
            prize = cost * 2.5
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 20)
            await context.bot.send_message(chat_id=user_id, text=f"🎰 **ضربة حظ خارقة!** كسبت `{prize}` ليرة و +20 XP!", reply_markup=kb, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await context.bot.send_message(chat_id=user_id, text="❌ **لم تتطابق الرموز!** حاول مرة أخرى.", reply_markup=kb)

    # لعبة النرد
    elif data == "g_dice":
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        await context.bot.send_dice(chat_id=user_id, emoji="🎲")
        await asyncio.sleep(2.5)

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 رمي مجدداً", callback_data="g_dice"), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **النرد منحك الفوز!** كسبت `{prize}` ليرة!", reply_markup=kb, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await context.bot.send_message(chat_id=user_id, text="❌ **لم تنل الحظ هذه المرة!**", reply_markup=kb)

    # ==================== لوحة الأدمن والإدارة ====================
    elif data == "admin_panel" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="admin_add_ch"), InlineKeyboardButton("❌ حذف قناة", callback_data="admin_del_ch")],
            [InlineKeyboardButton("⚙️ الصيانة (تشغيل/إيقاف)", callback_data="admin_toggle_maint")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_main")]
        ])
        await query.message.edit_text("⚙️ **لوحة التحكم الخاصة بالإدارة:**", reply_markup=kb)

    elif data == "admin_add_ch" and is_admin(user_id):
        context.user_data['state'] = 'waiting_add_channel'
        await query.message.edit_text("📢 **أرسل معرّف القناة الجديدة أولاً (مثال: @mychannel):**")

    elif data == "admin_toggle_maint" and is_admin(user_id):
        curr = get_setting('maintenance_mode')
        new_val = 0.0 if curr == 1.0 else 1.0
        set_setting('maintenance_mode', new_val)
        status = "توقف الصيانة (البوت يعمل)" if new_val == 0.0 else "تفعيل الصيانة (البوت متوقف)"
        await query.answer(f"✅ تم {status}", show_alert=True)
        await main_menu(user_id, context, query.message)

    conn.close()

# --- 6. معالج الرسائل والمدخلات النصية ---
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get('state')

    # إدخال قناة جديدة من قبل الأدمن وإرسال تنبيه للمستخدمين للتحقق
    if state == 'waiting_add_channel' and is_admin(user_id):
        if text.startswith('@'):
            context.user_data['temp_ch_user'] = text
            context.user_data['state'] = 'waiting_add_channel_title'
            await update.message.reply_text("✨ ممتاز، الآن أرسل اسم القناة أو العنوان الذي سيظهر للأعضاء:")
        else:
            await update.message.reply_text("❌ يرجى إرسال المعرف يبدأ بـ @")
        return

    elif state == 'waiting_add_channel_title' and is_admin(user_id):
        ch_user = context.user_data.get('temp_ch_user')
        ch_title = text

        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO channels (channel_username, channel_title) VALUES (?, ?)", (ch_user, ch_title))
        
        # جلب كافة المستخدمين لإرسال إشعار بالقناة الجديدة
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text(f"✅ تم إضافة القناة ({ch_title}) بنجاح!\n⚡ جاري إرسال طلب التحقق لجميع مستخدمي البوت...")

        # إرسال طلب التحقق من الاشتراك لكل الأعضاء
        clean_user = ch_user.replace('@', '')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📢 اشترك في: {ch_title}", url=f"https://t.me/{clean_user}")],
            [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub_again")]
        ])
        
        broadcast_msg = f"🔔 **تحديث هام:** تمت إضافة قناة إجبارية جديدة!\nيرجى الاشتراك بها والضغط على زر التحقق لاستمرار عمل البوت."

        for u in users:
            uid = u['user_id']
            if not is_admin(uid):
                try:
                    await context.bot.send_message(chat_id=uid, text=broadcast_msg, reply_markup=kb, parse_mode="Markdown")
                except:
                    pass
        return

    # إرسال رسائل الدعم الفني
    elif state == 'waiting_support_msg':
        context.user_data['state'] = None
        await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💬 **رسالة دعم جديدة من:** `{user_id}`\n\n{text}", parse_mode="Markdown")
        await update.message.reply_text("✅ تم إرسال رسالتك إلى الدعم الفني بنجاح!")
        return

    # إدخال مبلغ السحب
    elif state == 'waiting_withdraw_amount':
        try:
            amount = float(text)
            min_w = get_setting('min_withdraw')
            conn = get_db_connection()
            user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            
            if amount < min_w or amount > user['balance']:
                await update.message.reply_text("❌ المبلغ غير صالح أو يتجاوز رصيدك الحالي!")
            else:
                context.user_data['withdraw_amt'] = amount
                context.user_data['state'] = 'waiting_withdraw_acc'
                await update.message.reply_text("📝 **أدخل الآن رقم الحساب / المحفظة لاستلام المبلغ:**")
            conn.close()
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح للمبلغ.")
        return

    elif state == 'waiting_withdraw_acc':
        amt = context.user_data.get('withdraw_amt')
        method = context.user_data.get('withdraw_method')
        
        conn = get_db_connection()
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, user_id))
        conn.execute("INSERT INTO withdraw_requests (user_id, amount, method, account_info) VALUES (?, ?, ?, ?)", (user_id, amt, method, text))
        conn.commit()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text("✅ **تم تقديم طلب السحب بنجاح!** سيتطابق مع المسؤولين قريباً.")
        await context.bot.send_message(
            chat_id=SUPER_ADMIN,
            text=f"🚨 **طلب سحب جديد:**\n• المستخدم: `{user_id}`\n• المبلغ: `{amt}`\n• الطريقة: {method}\n• الحساب: `{text}`",
            parse_mode="Markdown"
        )
        return

# --- 7. تشغيل البوت الرئيسية ---
def main():
    if not TOKEN:
        print("❌ BOT_TOKEN غير موجود في متغيرات البيئة!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🤖 البوت يعمل بنجاح الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
