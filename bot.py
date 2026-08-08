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
    """فحص إجباري دائم لاشتراك القنوات عند أي تفاعل من المستخدم"""
    if is_admin(user_id):
        return True
        
    unsub = await check_sub(user_id, context)
    if unsub:
        kb = []
        for ch_user, ch_title in unsub:
            kb.append([InlineKeyboardButton(f"📢 اشترك في: {ch_title}", url=f"https://t.me/{ch_user.replace('@','')}")])
        kb.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub_again")])
        
        msg_text = "⚠️ **يجب عليك الاشتراك في جميع القنوات لاستخدام البوت:**\nتمت إضافة قنوات جديدة أو أنك غير مشترك ببعضها."
        
        if hasattr(update_or_query, 'edit_text'):
            await update_or_query.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
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

    # فحص القنوات الإجبارية لجميع الأزرار
    if data != "check_sub_again":
        if not await enforce_channel_subscription(user_id, context, query.message):
            conn.close()
            return

    # زر التحقق من الاشتراك
    if data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.answer("❌ لم تقم بالاشتراك في جميع القنوات بعد!", show_alert=True)
        else:
            await query.message.edit_text("✅ **تم التحقق من الاشتراك بنجاح!**")
            if user and user['is_verified'] == 0:
                btn = KeyboardButton("📱 مشاركة رقم الهاتف للتأكيد", request_contact=True)
                await context.bot.send_message(
                    chat_id=user_id, 
                    text="📱 لتأكيد هويتك وأمان حسابك، اضغط على الزر أدناه لمشاركة رقمك:",
                    reply_markup=ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
                )
            else:
                await main_menu(user_id, context, query.message)

    # قبول الشروط
    elif data == "accept_terms":
        if user and user['is_verified'] == 0:
            btn = KeyboardButton("📱 مشاركة رقم الهاتف للتأكيد", request_contact=True)
            await context.bot.send_message(
                chat_id=user_id, 
                text="📱 لتأكيد هويتك، يرجى الضغط على الزر أدناه لمشاركة رقمك السوري:",
                reply_markup=ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
            )
        else:
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

    # خدمات الدعم والطلبات والسحب
    elif data == "buy_bot":
        context.user_data['state'] = 'waiting_bot_desc'
        await query.message.edit_text("📝 **يرجى إرسال تفاصيل ومواصفات البوت الذي ترغب بشرائه:**")

    elif data == "support_msg":
        context.user_data['state'] = 'waiting_support_msg'
        await query.message.edit_text("💬 **أرسل رسالتك أو استفسارك للدعم الفني الآن (يمكنك إرسال نص أو صورة):**")

    elif data == "enter_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎟️ **أرسل كود الهدية للتحقق والتفعيل:**")

    # نظام السحب المطور (طلب المبلغ أولاً)
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
            [InlineKeyboardButton("🎲 النرد السحري", callback_data="g_dice"), InlineKeyboardButton("🪙 ملك أم كتاب", callback_data="g_coin")],
            [InlineKeyboardButton("🔢 تخمين الرقم", callback_data="g_num")],
            [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main")]
        ])
        await query.message.edit_text(
            f"🎮 **صالة الألعاب المباشرة والتحديات:**\n\n"
            f"💵 **تكلفة الجولة:** `{cost}` ليرة.\n"
            f"🏆 كل جولة تمنحك نقاط خبرة XP لرفع مستواك!\n"
            f"اختر اللعبة وابدأ الحظ والتسلية:", 
            reply_markup=kb, parse_mode="Markdown"
        )

    # 1. عجلة الحظ
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
            await asyncio.sleep(0.4)
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

    # 2. لعبة السلوت
    elif data == "g_slot":
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        slot_msg = await context.bot.send_dice(chat_id=user_id, emoji="🎰")
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

    # 3. النرد
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
            await context.bot.send_message(chat_id=user_id, text="❌ **لم يوفق النرد!** حاول مرة أخرى.", reply_markup=kb)

    # 4. ملك أم كتاب
    elif data == "g_coin":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 ملك", callback_data="coin_king"), InlineKeyboardButton("📖 كتاب", callback_data="coin_book")],
            [InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]
        ])
        await query.message.edit_text("🪙 **اختر وجه العملة للرمي:**", reply_markup=kb)

    elif data in ["coin_king", "coin_book"]:
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        msg = await query.message.edit_text("🪙 **جاري رمي العملة في الهواء...**")
        for c in ["🪙 🟡", "🪙 ⚪", "🪙 🟡"]:
            await asyncio.sleep(0.3)
            try: await msg.edit_text(f"🪙 **في الهواء...** {c}")
            except: pass

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        choice = "ملك 👑" if data == "coin_king" else "كتاب 📖"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 رمي مجدداً", callback_data="g_coin"), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])
        
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await msg.edit_text(f"🎉 **تخمين صحيح!** ظهرت على ({choice}) وربحت `{prize}` ليرة!", reply_markup=kb, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await msg.edit_text(f"❌ **تخمين خاطئ!** سقطت العملة على الوجه المعاكس.", reply_markup=kb)

    # 5. تخمين الرقم
    elif data == "g_num":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1", callback_data="num_1"), InlineKeyboardButton("2", callback_data="num_2"), InlineKeyboardButton("3", callback_data="num_3")],
            [InlineKeyboardButton("4", callback_data="num_4"), InlineKeyboardButton("5", callback_data="num_5")],
            [InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]
        ])
        await query.message.edit_text("🔢 **خمن الرقم الذي سيفكر به البوت من (1 إلى 5):**", reply_markup=kb)

    elif data.startswith("num_"):
        cost = get_setting('game_cost')
        if user['balance'] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        guessed_num = data.split("_")[1]
        win = random.randint(1, 100) <= get_setting('game_win_rate')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تخمين جديد", callback_data="g_num"), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])

        if win:
            prize = cost * 2.5
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 20)
            await query.message.edit_text(f"🎉 **ذكاء رائع!** تخمينك للرقم ({guessed_num}) كان صحيحاً وحصلت على `{prize}` ليرة!", reply_markup=kb, parse_mode="Markdown")
        else:
            secret = random.choice([n for n in ["1","2","3","4","5"] if n != guessed_num])
            add_xp(user_id, 5)
            await query.message.edit_text(f"❌ **تخمين خاطئ!** الرقم الصحيح كان ({secret}).", reply_markup=kb)

    elif data == "back_main":
        await main_menu(user_id, context, query.message)

    # ==================== لوحة الإدارة ====================
    elif data == "admin_panel" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_users_menu"), InlineKeyboardButton("⚙️ ضبط النظام والأسعار", callback_data="adm_settings_menu")],
            [InlineKeyboardButton("📢 الإذاعة والقنوات", callback_data="adm_ch_bc_menu"), InlineKeyboardButton("🎟️ الأكواد والشحن", callback_data="adm_codes_menu")],
            [InlineKeyboardButton("🔙 العودة للمنيو الرئيسي", callback_data="back_main")]
        ])
        await query.message.edit_text("⚙️ **لوحة التحكم الاحترافية الشاملة:**\nاختر القسم المراد إدارته بنقرة واحدة:", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_users_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك حظر", callback_data="adm_unban")],
            [InlineKeyboardButton("🔍 تقرير عميل", callback_data="adm_user_info"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_user_count")],
            [InlineKeyboardButton("👑 ترقية أدمن", callback_data="adm_add_admin")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("👥 **قسم التحكم في الأعضاء والحسابات:**", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_settings_menu" and is_admin(user_id):
        ref_p = get_setting('ref_price')
        min_w = get_setting('min_withdraw')
        g_cost = get_setting('game_cost')
        w_rate = get_setting('game_win_rate')
        d_bonus = get_setting('daily_bonus')
        m_mode = get_setting('maintenance_mode')
        
        m_status = "⚠️ مفعل (البوت مغلق للعملاء)" if m_mode == 1.0 else "✅ معطل (البوت يعمل للجميع)"

        msg = (
            f"⚙️ **إعدادات البوت الحالية:**\n\n"
            f"• **حالة الصيانة:** {m_status}\n"
            f"• سعر الإحالة: `{ref_p}` ليرة\n"
            f"• الحد الأدنى للسحب: `{min_w}` ليرة\n"
            f"• سعر ضربة الألعاب: `{g_cost}` ليرة\n"
            f"• نسبة الربح في الألعاب: `{w_rate}%`\n"
            f"• المكافأة اليومية: `{d_bonus}` ليرة"
        )
        
        m_btn = InlineKeyboardButton("🟢 إلغاء وضع الصيانة", callback_data="disable_maintenance") if m_mode == 1.0 else InlineKeyboardButton("🛠️ تفعيل وضع الصيانة", callback_data="enable_maintenance")
        
        kb = InlineKeyboardMarkup([
            [m_btn],
            [InlineKeyboardButton("🎯 نسبة الربح بالأزرار", callback_data="adm_set_win_rate_menu")],
            [InlineKeyboardButton("💵 سعر الإحالة", callback_data="adm_set_ref_price"), InlineKeyboardButton("💳 حد السحب", callback_data="adm_set_min_w")],
            [InlineKeyboardButton("🎲 تكلفة اللعبة", callback_data="adm_set_game_cost"), InlineKeyboardButton("🎁 البونص اليومي", callback_data="adm_set_daily_bonus")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")

    elif data == "enable_maintenance" and is_admin(user_id):
        set_setting('maintenance_mode', 1.0)
        await query.answer("🛠️ تم تفعيل وضع الصيانة بنجاح.", show_alert=True)
        await main_menu(user_id, context, query.message)

    elif data == "disable_maintenance" and is_admin(user_id):
        set_setting('maintenance_mode', 0.0)
        await query.answer("✅ تم إلغاء وضع الصيانة.", show_alert=True)
        await main_menu(user_id, context, query.message)

    elif data == "adm_set_win_rate_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("10%", callback_data="set_wr_10"), InlineKeyboardButton("30%", callback_data="set_wr_30"), InlineKeyboardButton("50%", callback_data="set_wr_50")],
            [InlineKeyboardButton("70%", callback_data="set_wr_70"), InlineKeyboardButton("🔥 100% (فوز دائم)", callback_data="set_wr_100")],
            [InlineKeyboardButton("✏️ إدخال نسبة يدوياً", callback_data="adm_set_win_rate_manual")],
            [InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data="adm_settings_menu")]
        ])
        await query.message.edit_text("🎯 **تعديل نسبة الربح الحالية في الألعاب مباشرة:**", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("set_wr_") and is_admin(user_id):
        rate = float(data.split("_")[2])
        set_setting('game_win_rate', rate)
        await query.answer(f"✅ تم تعديل نسبة ربح الألعاب إلى {rate}%", show_alert=True)
        await main_menu(user_id, context, query.message)

    elif data == "adm_ch_bc_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="adm_add_ch"), InlineKeyboardButton("➖ حذف قناة", callback_data="adm_del_ch")],
            [InlineKeyboardButton("📢 إذاعة عامة لكل المشتركين", callback_data="adm_bc_all")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("📢 **قسم الإذاعة والقنوات الإجبارية:**", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_codes_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
            [InlineKeyboardButton("🎟️ إنشاء كود هدية", callback_data="adm_gen_code"), InlineKeyboardButton("🧹 تصفير كافة الأرصدة", callback_data="adm_reset_bal")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("🎟️ **قسم التحكم بالرصيد والكوبونات:**", reply_markup=kb, parse_mode="Markdown")

    # قبول / رفض السحب والرد
    elif data.startswith("approve_w_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
        if req and req['status'] == 'pending':
            conn.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
            conn.commit()
            await query.message.edit_text(f"✅ **تمت الموافقة على طلب السحب رقم #{req_id} بنجاح!**")
            try:
                await context.bot.send_message(chat_id=req['user_id'], text=f"🎉 **مبروك!** تم قبول طلب سحب رصيدك بمبلغ `{req['amount']}` ليرة وتحويله لحسابك بنجاح!", parse_mode="Markdown")
            except: pass
        else:
            await query.answer("⚠️ الطلب مُعالج مسبقاً!", show_alert=True)

    elif data.startswith("reject_w_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
        if req and req['status'] == 'pending':
            conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (req_id,))
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (req['amount'], req['user_id']))
            conn.commit()
            await query.message.edit_text(f"❌ **تم رفض طلب السحب رقم #{req_id} وإعادة المبلغ لرصيد المستخدم.**")
            try:
                await context.bot.send_message(chat_id=req['user_id'], text=f"❌ **تم رفض طلب السحب الخاص بك.** وتم إعادة مبلغ `{req['amount']}` ليرة إلى رصيدك.", parse_mode="Markdown")
            except: pass
        else:
            await query.answer("⚠️ الطلب مُعالج مسبقاً!", show_alert=True)

    elif data.startswith("reply_user_") and is_admin(user_id):
        target_user = int(data.split("_")[2])
        context.user_data['admin_reply_target'] = target_user
        await query.message.reply_text(f"💬 **أرسل الآن نص أو صورة الرد ليتم إرسالها للعميل (`{target_user}`):**", parse_mode="Markdown")

    elif is_admin(user_id):
        if data == "adm_user_count":
            cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            await query.answer(f"📊 عدد المشتركين الكلي: {cnt} مشترك", show_alert=True)
        elif data == "adm_reset_bal":
            conn.execute("UPDATE users SET balance=0")
            conn.commit()
            await query.message.edit_text("🧹 تم تصفير جميع أرصدة المستخدمين بنجاح.")
        elif data in ["adm_add_bal", "adm_sub_bal", "adm_ban", "adm_unban", "adm_user_info", 
                      "adm_set_ref_price", "adm_set_min_w", "adm_set_game_cost", "adm_set_daily_bonus",
                      "adm_set_win_rate_manual", "adm_add_ch", "adm_del_ch", "adm_add_admin", "adm_gen_code", "adm_bc_all"]:
            
            context.user_data['admin_action'] = data
            hints = {
                "adm_add_bal": "أرسل الأيدي والمبلغ (مثال: `123456789 50`)",
                "adm_sub_bal": "أرسل الأيدي والمبلغ المخصوم (مثال: `123456789 20`)",
                "adm_ban": "أرسل أيدي العميل للحظر",
                "adm_unban": "أرسل أيدي العميل لفك الحظر",
                "adm_user_info": "أرسل أيدي العميل لتأكيد تقرير حسابه",
                "adm_set_ref_price": "أرسل قيمة الإحالة الجديدة",
                "adm_set_min_w": "أرسل قيمة الحد الأدنى للسحب",
                "adm_set_game_cost": "أرسل سعر ضغطة اللعبة",
                "adm_set_daily_bonus": "أرسل قيمة البونص اليومي الجديد",
                "adm_set_win_rate_manual": "أرسل نسبة الربح من 1 إلى 100",
                "adm_add_ch": "أرسل معرف القناة واسم القناة معاً بمفصل | (مثال: `@mychannel | قناة المسابقات`)",
                "adm_del_ch": "أرسل معرف القناة المراد حذفها (مثال: `@mychannel`)",
                "adm_add_admin": "أرسل أيدي المستخدم لترقيته كـ أدمن",
                "adm_gen_code": "أرسل الكود والمبلغ (مثال: `BONUS 100`)",
                "adm_bc_all": "أرسل النص المراد إذاعته للجميع"
            }
            await query.message.edit_text(f"📥 **إدخال مطلوب:**\n{hints.get(data, 'أرسل المطلوب الآن:')}", parse_mode="Markdown")

    conn.close()

# --- 6. معالج الأرقام السورية والإحالة ---
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة حالياً.**", reply_markup=ReplyKeyboardRemove())
        return

    contact = update.message.contact
    if contact.user_id != user_id:
        await update.message.reply_text("❌ يرجى مشاركة جهة اتصالك الخاصة فقط.")
        return

    phone = contact.phone_number
    if not phone.startswith("+963") and not phone.startswith("963") and not phone.startswith("09"):
        await update.message.reply_text("❌ اعتذار، الخدمة تعمل حصراً على الأرقام السورية (+963).", reply_markup=ReplyKeyboardRemove())
        return

    if not await enforce_channel_subscription(user_id, context, update.message):
        return

    conn = get_db_connection()
    user = conn.execute("SELECT is_verified, ref_by FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user['is_verified'] == 0:
        conn.execute("UPDATE users SET phone=?, is_verified=1 WHERE user_id=?", (phone, user_id))
        
        ref_by = user['ref_by']
        if ref_by:
            ref_price = get_setting('ref_price')
            conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id=?", (ref_price, ref_by))
            conn.commit()
            add_xp(ref_by, 20)
            try:
                await context.bot.send_message(chat_id=ref_by, text=f"🎉 **إحالة جديدة ناجحة!** أكمل صديقك التسجيل والتحقق، وحصلت على `{ref_price}` ليرة و +20 XP!", parse_mode="Markdown")
            except: pass
            
        conn.commit()
        await update.message.reply_text("✅ تم تأكيد رقمك وتفعيل حسابك بنجاح!", reply_markup=ReplyKeyboardRemove())
        await main_menu(user_id, context)
    else:
        await update.message.reply_text("✅ حسابك مؤكد بالفعل!", reply_markup=ReplyKeyboardRemove())
        await main_menu(user_id, context)
        
    conn.close()

# --- 7. معالجة النصوص والسحب والإدارة ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    state = context.user_data.get('state')
    adm_action = context.user_data.get('admin_action')
    admin_reply_target = context.user_data.get('admin_reply_target')

    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**")
        return

    # فحص القنوات الإجبارية المستمر
    if not is_admin(user_id):
        if not await enforce_channel_subscription(user_id, context, update.message):
            return

    # رد الأدمن المباشر
    if is_admin(user_id) and admin_reply_target:
        try:
            if update.message.photo:
                photo = update.message.photo[-1].file_id
                await context.bot.send_photo(chat_id=admin_reply_target, photo=photo, caption=f"💬 **رد من الدعم الفني:**\n\n{update.message.caption or ''}")
            else:
                await context.bot.send_message(chat_id=admin_reply_target, text=f"💬 **رد من الدعم الفني:**\n\n{text}")
            await update.message.reply_text(f"✅ **تم إرسال الرد بنجاح للمستخدم `{admin_reply_target}`.**", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ تعذر إرسال الرد: {e}")
        context.user_data['admin_reply_target'] = None
        return

    if state == 'waiting_bot_desc':
        await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"🛍️ **طلب شراء بوت جديد:**\nالعميل: `{user_id}`\nالوصف:\n{text}", parse_mode="Markdown")
        await update.message.reply_text("✅ تم استلام طلبك، وسنتواصل معك بأقرب وقت.")
        context.user_data['state'] = None
        return

    if state == 'waiting_support_msg':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 رد على الرسالة", callback_data=f"reply_user_{user_id}")]])
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            await context.bot.send_photo(chat_id=SUPER_ADMIN, photo=photo, caption=f"💬 **دعم جديد من:** `{user_id}`\n{update.message.caption or ''}", reply_markup=kb, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💬 **دعم جديد من:** `{user_id}`\n\n{text}", reply_markup=kb, parse_mode="Markdown")
        await update.message.reply_text("✅ تم توصيل رسالتك لفريق الدعم وسيجيبك الأدمن قريباً.")
        context.user_data['state'] = None
        return

    if state == 'waiting_gift_code':
        conn = get_db_connection()
        code_data = conn.execute("SELECT amount FROM gift_codes WHERE code=?", (text.strip(),)).fetchone()
        if code_data:
            amount = code_data['amount']
            conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
            conn.execute("DELETE FROM gift_codes WHERE code=?", (text.strip(),))
            conn.commit()
            await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amount}` ليرة لرصيدك!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ الكود غير صحيح أو تم استخدامه مسبقاً.")
        conn.close()
        context.user_data['state'] = None
        return

    # ------------------ مراحل السحب المطور ------------------
    if state == 'waiting_withdraw_amount':
        try:
            req_amount = float(text.strip())
            conn = get_db_connection()
            user_bal = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()['balance']
            conn.close()
            min_w = get_setting('min_withdraw')

            if req_amount < min_w:
                await update.message.reply_text(f"❌ المبلغ المدخل أقل من الحد الأدنى للسحب (`{min_w}` ليرة).\nيرجى إدخال مبلغ صحيح:", parse_mode="Markdown")
                return

            if req_amount > user_bal:
                await update.message.reply_text(f"❌ رصيدك الحالي (`{user_bal:.2f}` ليرة) لا يكفي لسحب مبلغ `{req_amount}` ليرة.\nيرجى أدخل مبلغ أقل أو يساوي رصيدك:", parse_mode="Markdown")
                return

            context.user_data['withdraw_amount'] = req_amount
            context.user_data['state'] = 'waiting_withdraw_account'
            method = context.user_data.get('withdraw_method')
            await update.message.reply_text(f"✅ تم القبول. المبلغ المطلوب: `{req_amount}` ليرة.\n\n📲 **الآن أرسل رقم حسابك أو رقم المحفظة على ({method}):**", parse_mode="Markdown")

        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح فقط (مثال: `250`).")
        return

    if state == 'waiting_withdraw_account':
        account_info = text.strip()
        req_amount = context.user_data.get('withdraw_amount')
        method = context.user_data.get('withdraw_method')
        
        conn = get_db_connection()
        user_bal = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()['balance']

        if user_bal < req_amount:
            await update.message.reply_text("❌ حدث تغير في رصيدك وهو غير كافٍ الآن للعملية.")
            conn.close()
            context.user_data['state'] = None
            return

        # خصم المبلغ بالضبط من حساب المستخدم
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (req_amount, user_id))
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, method, account_info) VALUES (?, ?, ?, ?)", (user_id, req_amount, method, account_info))
        req_id = cursor.lastrowid
        conn.commit()
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"approve_w_{req_id}"), InlineKeyboardButton("❌ رفض الطلب", callback_data=f"reject_w_{req_id}")]
        ])

        await context.bot.send_message(
            chat_id=SUPER_ADMIN, 
            text=f"💳 **طلب سحب رصيد جديد (#{req_id}):**\n\n"
                 f"👤 **العميل:** `{user_id}`\n"
                 f"🌐 **الطريقة:** {method}\n"
                 f"💰 **المبلغ المطلوبة:** `{req_amount}` ليرة\n"
                 f"📝 **بيانات الحساب:** `{account_info}`", 
            reply_markup=kb, parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ **تم رفع طلب السحب بقيمة `{req_amount}` ليرة بنجاح.**\nسيتم مراجعته من الإدارة وإشعارك فور القبول أو الرفض.", parse_mode="Markdown")
        context.user_data['state'] = None
        return

    # أوامر الأدمن
    if is_admin(user_id) and adm_action:
        conn = get_db_connection()
        try:
            if adm_action == "adm_set_ref_price":
                set_setting('ref_price', float(text))
                await update.message.reply_text("✅ تم تحديث سعر الإحالة بنجاح.")

            elif adm_action == "adm_set_min_w":
                set_setting('min_withdraw', float(text))
                await update.message.reply_text("✅ تم تحديث حد السحب.")

            elif adm_action == "adm_set_game_cost":
                set_setting('game_cost', float(text))
                await update.message.reply_text("✅ تم تحديث سعر ضغطة اللعبة.")

            elif adm_action == "adm_set_daily_bonus":
                set_setting('daily_bonus', float(text))
                await update.message.reply_text("✅ تم تحديث قيمة البونص اليومي.")

            elif adm_action == "adm_set_win_rate_manual":
                set_setting('game_win_rate', float(text))
                await update.message.reply_text(f"✅ تم تحديث نسبة الربح إلى {text}%.")

            elif adm_action == "adm_add_bal":
                parts = text.split()
                target_id, amount = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target_id))
                conn.commit()
                await update.message.reply_text(f"💰 تم إضافة {amount} ليرة للمستخدم `{target_id}`.", parse_mode="Markdown")

            elif adm_action == "adm_sub_bal":
                parts = text.split()
                target_id, amount = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, target_id))
                conn.commit()
                await update.message.reply_text(f"📉 تم خصم {amount} ليرة من المستخدم `{target_id}`.", parse_mode="Markdown")

            elif adm_action == "adm_ban":
                conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"🚫 تم حظر المستخدم `{text}`.", parse_mode="Markdown")

            elif adm_action == "adm_unban":
                conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"✅ تم فك الحظر عن `{text}`.", parse_mode="Markdown")

            elif adm_action == "adm_user_info":
                u = conn.execute("SELECT full_name, phone, balance, ref_count, is_banned, level, xp FROM users WHERE user_id=?", (int(text),)).fetchone()
                if u:
                    status = "محظور 🚫" if u['is_banned'] == 1 else "نشط ✅"
                    await update.message.reply_text(
                        f"👤 **تقرير حساب `{text}`:**\n\n"
                        f"• الاسم: {u['full_name']}\n"
                        f"• الرقم: {u['phone'] or 'غير مسجل'}\n"
                        f"• الرصيد: `{u['balance']:.2f}` ليرة\n"
                        f"• المستوى: {u['level']} (XP: {u['xp']})\n"
                        f"• الإحالات: {u['ref_count']}\n"
                        f"• الحالة: {status}", parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text("❌ لم يتم العثور على هذا المستخدم.")

            elif adm_action == "adm_add_ch":
                if "|" in text:
                    ch_user, ch_title = text.split("|")[0].strip(), text.split("|")[1].strip()
                else:
                    ch_user, ch_title = text.strip(), text.strip()
                ch_user = ch_user if ch_user.startswith("@") else f"@{ch_user}"
                conn.execute("INSERT OR REPLACE INTO channels (channel_username, channel_title) VALUES (?, ?)", (ch_user, ch_title))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة القناة {ch_user} باسم ({ch_title}).")

            elif adm_action == "adm_del_ch":
                ch = text.strip() if text.strip().startswith("@") else f"@{text.strip()}"
                conn.execute("DELETE FROM channels WHERE channel_username=?", (ch,))
                conn.commit()
                await update.message.reply_text(f"🗑️ تم حذف القناة {ch}.")

            elif adm_action == "adm_add_admin":
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(text),))
                conn.commit()
                await update.message.reply_text(f"👑 تم إضافة الأدمن `{text}` بنجاح.", parse_mode="Markdown")

            elif adm_action == "adm_gen_code":
                parts = text.split()
                code, amount = parts[0], float(parts[1])
                conn.execute("INSERT INTO gift_codes (code, amount) VALUES (?, ?)", (code, amount))
                conn.commit()
                await update.message.reply_text(f"🎟️ تم إنشاء الكود `{code}` بقيمة {amount} ليرة.", parse_mode="Markdown")

            elif adm_action == "adm_bc_all":
                users = conn.execute("SELECT user_id FROM users").fetchall()
                sent = 0
                for u in users:
                    try:
                        await context.bot.send_message(chat_id=u['user_id'], text=text)
                        sent += 1
                        await asyncio.sleep(0.04)
                    except: pass
                await update.message.reply_text(f"📢 تم إرسال الإذاعة بنجاح إلى {sent} مشترك.")

        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ في الصيغة: {e}")

        context.user_data['admin_action'] = None
        conn.close()
        return

# --- 8. تشغيل البوت ---
def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN غير معرّف!")
        
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
