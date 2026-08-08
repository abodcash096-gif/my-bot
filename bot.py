import os
import sqlite3
import random
import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- 1. سيرفر الاستضافة الحية ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Engine is Active")

    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- 2. إعدادات قاعدة البيانات والإحداثيات ---
TOKEN = os.environ.get("BOT_TOKEN", "ضع_التوكن_هنا")
SUPER_ADMIN = 7255100997  # أيدي الآدمن الأساسي

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
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY, channel_title TEXT)''')
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
        ('min_withdraw', 100.0), 
        ('game_cost', 10.0), 
        ('game_win_rate', 40.0), # نسبة الفوز الخوارزمية
        ('daily_bonus', 10.0),
        ('welcome_bonus', 15.0), # البونص الترحيبي
        ('welcome_bonus_active', 1.0), # 1 مفعّل / 0 معطل
        ('maintenance_mode', 0.0)
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (SUPER_ADMIN,))
    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة ---
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

# --- 3. نظام التحقق الذكي من القنوات ---
async def check_sub(user_id, context):
    conn = get_db_connection()
    channels = conn.execute("SELECT channel_username, channel_title FROM channels").fetchall()
    conn.close()
    
    unsubbed = []
    for ch in channels:
        ch_user = ch['channel_username'] if ch['channel_username'].startswith('@') else '@' + ch['channel_username']
        try:
            member = await context.bot.get_chat_member(chat_id=ch_user, user_id=user_id)
            if member.status in ['left', 'kicked']:
                unsubbed.append((ch_user, ch['channel_title'] or ch_user))
        except Exception:
            unsubbed.append((ch_user, ch['channel_title'] or ch_user))
    return unsubbed

async def enforce_subscription(user_id, context, update_or_query):
    if is_admin(user_id):
        return True
    unsub = await check_sub(user_id, context)
    if unsub:
        kb = []
        for ch_user, ch_title in unsub:
            kb.append([InlineKeyboardButton(f"📢 اشترك في: {ch_title}", url=f"https://t.me/{ch_user.replace('@','')}")])
        kb.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الان", callback_data="check_sub_again")])
        
        msg_text = "⚠️ **عذراً عزيزي، يجب عليك الاشتراك بالقنوات التالية لاستخدام البوت:**"
        markup = InlineKeyboardMarkup(kb)
        if hasattr(update_or_query, 'edit_text'):
            await update_or_query.edit_text(msg_text, reply_markup=markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=msg_text, reply_markup=markup, parse_mode="Markdown")
        return False
    return True

# --- 4. أوامر البدء والقائمة الرئيسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة حالياً، حاول لاحقاً!**", parse_mode="Markdown")
        return

    conn = get_db_connection()
    user = conn.execute("SELECT user_id, is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user['is_banned'] == 1:
        await update.message.reply_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return

    if not user:
        ref_id = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user_id else None
        
        # إضافة البونص الترحيبي إذا كان مفضلاً
        welcome_bonus = get_setting('welcome_bonus') if get_setting('welcome_bonus_active') == 1.0 else 0.0
        
        conn.execute("INSERT INTO users (user_id, full_name, ref_by, balance) VALUES (?, ?, ?, ?)", 
                     (user_id, full_name, ref_id, welcome_bonus))
        conn.commit()

    conn.close()

    if not await enforce_subscription(user_id, context, update.message):
        return

    await main_menu(user_id, context, update.message)

async def main_menu(user_id, context, message_obj=None, text="🌟 **القائمة الرئيسية:**"):
    if not await enforce_subscription(user_id, context, message_obj or user_id):
        return

    conn = get_db_connection()
    user = conn.execute("SELECT full_name, balance, level, xp FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    msg = (
        f"👑 **مرحباً بك:** `{user['full_name']}`\n"
        f"💰 **رصيدك الحالي:** `{user['balance']:.2f}` ليرة\n"
        f"🏅 **المستوى:** {user['level']} | **XP:** {user['xp']}/100\n"
        "--------------------------------------"
    )
    
    kb = [
        [InlineKeyboardButton("🎮 صالة الألعاب الحية 🔥", callback_data="games_menu")],
        [InlineKeyboardButton("👤 حسابي", callback_data="my_account"), InlineKeyboardButton("🎁 المكافأة اليومية", callback_data="daily_bonus")],
        [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="my_ref"), InlineKeyboardButton("💳 سحب الرصيد", callback_data="withdraw_start")],
        [InlineKeyboardButton("🎟️ إدخال كود هدية", callback_data="enter_gift_code"), InlineKeyboardButton("🛍️ طلب شراء بوت", callback_data="buy_bot_request")],
        [InlineKeyboardButton("💬 الدعم الفني", callback_data="support_msg"), InlineKeyboardButton("👨‍💻 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("⚙️ لوحة التحكم للإدارة الفائقة ⚙️", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(kb)
    if message_obj and hasattr(message_obj, 'edit_text'):
        try: await message_obj.edit_text(f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
        except: await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")

# --- 5. صالة الألعاب التفاعلية المباشرة (أنيميشن متطور) ---
async def games_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    cost = get_setting('game_cost')
    win_rate = get_setting('game_win_rate')
    
    conn = get_db_connection()
    user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()

    if data == "games_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎡 عجلة الحظ السحرية", callback_data="play_wheel"), InlineKeyboardButton("🎰 ماكينة السلوت الحية", callback_data="play_slot")],
            [InlineKeyboardButton("🎲 النرد الملكي", callback_data="play_dice")],
            [InlineKeyboardButton("🔙 العودة للقائمة", callback_data="back_main")]
        ])
        await query.message.edit_text(
            f"🎰 **صالة الألعاب التفاعلية:**\n\n"
            f"🎯 سعر الجولة الواحدة: `{cost}` ليرة.\n"
            f"💰 اختر اللعبة للبدء بتشغيل الأنيميشن المباشر:",
            reply_markup=kb, parse_mode="Markdown"
        )
        conn.close()
        return

    if user['balance'] < cost:
        await query.answer("❌ رصيدك غير كافٍ للعب في هذه الجولة!", show_alert=True)
        conn.close()
        return

    # خصم تكلفة اللعب
    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
    conn.commit()

    is_win = random.randint(1, 100) <= win_rate
    kb_again = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 جولة أخرى", callback_data=data), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])

    # 1. أنيميشن عجلة الحظ
    if data == "play_wheel":
        msg = await query.message.edit_text("🌀 **جاري تدوير العجلة الحية...**")
        frames = ["🎡 [ 🎯 | 🎁 | ❌ ]", "🎡 [ ❌ | 🎯 | 🎁 ]", "🎡 [ 🎁 | ❌ | 🎯 ]", "🎡 [ 🔥 | ✨ | 💎 ]"]
        for f in frames:
            await asyncio.sleep(0.35)
            try: await msg.edit_text(f)
            except: pass
        
        if is_win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await msg.edit_text(f"🎉 **فوز ساحق!** توقفت العجلة عند الرمز الذهبي ✨\nربحت: `{prize}` ليرة و +15 XP!", reply_markup=kb_again, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await msg.edit_text("❌ **حظاً أوفر!** توقفت العجلة على الخسارة. حصلت على +5 XP.", reply_markup=kb_again)

    # 2. ماكينة السلوت
    elif data == "play_slot":
        await query.message.delete()
        dice_msg = await context.bot.send_dice(chat_id=user_id, emoji="🎰")
        await asyncio.sleep(2.5)
        
        if is_win:
            prize = cost * 2.5
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 20)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **مبروك الجائزة الكبرى!** 🎰\nربحت: `{prize}` ليرة و +20 XP!", reply_markup=kb_again, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await context.bot.send_message(chat_id=user_id, text="❌ **لم تتطابق الرموز.** حاول مرة أخرى!", reply_markup=kb_again)

    # 3. النرد الملكي
    elif data == "play_dice":
        await query.message.delete()
        await context.bot.send_dice(chat_id=user_id, emoji="🎲")
        await asyncio.sleep(2.5)
        
        if is_win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **رمية رابحة!** 🎲\nربحت: `{prize}` ليرة و +15 XP!", reply_markup=kb_again, parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await context.bot.send_message(chat_id=user_id, text="❌ **رمية غير حاسمة!** جرب حظك مجدداً.", reply_markup=kb_again)

    conn.close()

# --- 6. معالج الأزرار ولوحة الإدارة الفائقة ---
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.answer("❌ لم تقم بالاشتراك في كافة القنوات المطلوبة!", show_alert=True)
            return
        else:
            await query.message.edit_text("✅ **تم التأكد من جميع الاشتراكات بنجاح!**")
            await main_menu(user_id, context)
            return

    if not await enforce_subscription(user_id, context, query.message):
        return

    if data == "back_main":
        await main_menu(user_id, context, query.message)

    elif data == "my_account":
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])
        await query.message.edit_text(
            f"👤 **بيانات حسابك:**\n\n"
            f"🆔 الأيدي: `{user['user_id']}`\n"
            f"💰 الرصيد: `{user['balance']:.2f}` ليرة\n"
            f"🏅 المستوى: {user['level']} (XP: {user['xp']}/100)\n"
            f"👥 الإحالات الناجحة: {user['ref_count']}", 
            reply_markup=kb, parse_mode="Markdown"
        )

    elif data == "daily_bonus":
        conn = get_db_connection()
        user = conn.execute("SELECT last_daily_claim FROM users WHERE user_id=?", (user_id,)).fetchone()
        now = time.time()
        if now - user['last_daily_claim'] >= 86400:
            bonus = get_setting('daily_bonus')
            conn.execute("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE user_id = ?", (bonus, now, user_id))
            conn.commit()
            add_xp(user_id, 10)
            await query.message.edit_text(f"🎁 **تم استلام المكافأة اليومية بقيمة `{bonus}` ليرة!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة", callback_data="back_main")]]), parse_mode="Markdown")
        else:
            rem = int((86400 - (now - user['last_daily_claim'])) // 3600)
            await query.answer(f"⏳ عد بعد {rem} ساعة لاستلام المكافأة!", show_alert=True)
        conn.close()

    elif data == "my_ref":
        bot_uname = (await context.bot.get_me()).username
        conn = get_db_connection()
        user = conn.execute("SELECT ref_count FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        await query.message.edit_text(
            f"🔗 **رابط الإحالة المباشر:**\n`https://t.me/{bot_uname}?start={user_id}`\n\n"
            f"👥 عدد إحالاتك: {user['ref_count']}\n"
            f"💰 الربح عن كل إحالة: `{get_setting('ref_price')}` ليرة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]), parse_mode="Markdown"
        )

    # --- عمليات طلب السحب من العميل ---
    elif data == "withdraw_start":
        conn = get_db_connection()
        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        min_w = get_setting('min_withdraw')
        if user['balance'] < min_w:
            await query.answer(f"❌ رصيدك أقل من الحد الأدنى للسحب ({min_w} ليرة)", show_alert=True)
            return
        
        context.user_data['state'] = 'waiting_withdraw_amt'
        await query.message.edit_text(f"💵 **أدخل المبلغ الذي ترغب بسحبه الآن:**\n• الحد الأدنى: `{min_w}`\n• رصيدك المتاح: `{user['balance']:.2f}`", parse_mode="Markdown")

    elif data == "enter_gift_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎟️ **يرجى إرسال كود الهدية الآن لتفعيله:**")

    elif data == "buy_bot_request":
        context.user_data['state'] = 'waiting_bot_specs'
        await query.message.edit_text("📝 **يرجى كتابة مواصفات والشروط الكاملة للبوت الذي ترغب بشرائه:**")

    elif data == "support_msg":
        context.user_data['state'] = 'waiting_support_txt'
        await query.message.edit_text("💬 **أرسل رسالتك أو استفسارك لإدارة البوت الآن:**")

    # ==================== لوحة التحكم الإدارية المتقدمة ====================
    elif data == "admin_panel" and is_admin(user_id):
        kb = [
            [InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_broadcast"), InlineKeyboardButton("✉️ رسالة خاصة", callback_data="adm_private_msg")],
            [InlineKeyboardButton("📊 تفاصيل اللاعبين", callback_data="adm_stats"), InlineKeyboardButton("🎲 خوارزمية الربح", callback_data="adm_win_rate")],
            [InlineKeyboardButton("🎟️ إنشاء كود هدية", callback_data="adm_create_code"), InlineKeyboardButton("💥 تصفير الأرصدة", callback_data="adm_reset_bal")],
            [InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_welcome_bonus"), InlineKeyboardButton("⚙️ الصيانة", callback_data="adm_toggle_maint")]
        ]
        await query.message.edit_text("⚙️ **لوحة الإدارة العليا الذكية:**\nاختر الإجراء المناسب للتحكم الكامل:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data == "adm_stats" and is_admin(user_id):
        conn = get_db_connection()
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0
        total_refs = conn.execute("SELECT SUM(ref_count) FROM users").fetchone()[0] or 0
        conn.close()
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_panel")]])
        await query.message.edit_text(
            f"📊 **إحصائيات وتقارير البوت:**\n\n"
            f"👥 إجمالي المسجلين: `{total_users}`\n"
            f"💰 إجمالي أرصدة اللاعبين: `{total_bal:.2f}` ليرة\n"
            f"🔗 إجمالي الإحالات: `{total_refs}`", reply_markup=kb, parse_mode="Markdown"
        )

    elif data == "adm_win_rate" and is_admin(user_id):
        context.user_data['state'] = 'waiting_win_rate'
        await query.message.edit_text(f"🎲 **نسبة الفوز الحالية في الألعاب:** `{get_setting('game_win_rate')}%`\n\nأرسل النسبة الجديدة (من 1 إلى 100):", parse_mode="Markdown")

    elif data == "adm_broadcast" and is_admin(user_id):
        context.user_data['state'] = 'waiting_broadcast_msg'
        await query.message.edit_text("📢 **أرسل النص أو الرسالة المراد إرسالها لجميع المشتركين:**")

    elif data == "adm_private_msg" and is_admin(user_id):
        context.user_data['state'] = 'waiting_private_msg_id'
        await query.message.edit_text("✉️ **أرسل ID المستخدم المتبوع بالرسالة بفاصل مسافة (مثال: `12345678 مرحباً بك`):**", parse_mode="Markdown")

    elif data == "adm_create_code" and is_admin(user_id):
        context.user_data['state'] = 'waiting_gift_code_gen'
        await query.message.edit_text("🎟️ **أرسل الكود والمبلغ المخصص له بفاصل مسافة (مثال: `GIFT2026 50`):**", parse_mode="Markdown")

    elif data == "adm_reset_bal" and is_admin(user_id):
        conn = get_db_connection()
        conn.execute("UPDATE users SET balance = 0")
        conn.commit()
        conn.close()
        await query.answer("💥 تم تصفير كافة أرصدة اللاعبين بنجاح!", show_alert=True)

    elif data == "adm_welcome_bonus" and is_admin(user_id):
        current = get_setting('welcome_bonus_active')
        new_val = 0.0 if current == 1.0 else 1.0
        set_setting('welcome_bonus_active', new_val)
        status = "تفعيل 🟢" if new_val == 1.0 else "تعطيل 🔴"
        await query.answer(f"تم {status} البونص الترحيبي بنجاح!", show_alert=True)

    elif data == "adm_toggle_maint" and is_admin(user_id):
        cur = get_setting('maintenance_mode')
        set_setting('maintenance_mode', 0.0 if cur == 1.0 else 1.0)
        await query.answer("تم تغيير حالة الصيانة بنجاح!", show_alert=True)

    # --- أزرار الموافقة والرفض للسحب ---
    elif data.startswith("acc_w_") or data.startswith("ref_w_"):
        if not is_admin(user_id): return
        action, w_id = data.split("_")[0], data.split("_")[2]
        
        conn = get_db_connection()
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (w_id,)).fetchone()
        if req and req['status'] == 'pending':
            if action == "acc":
                conn.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (w_id,))
                conn.commit()
                await query.message.edit_text(f"{query.message.text}\n\n✅ **تمت الموافقة على الطلب.**")
                try: await context.bot.send_message(chat_id=req['user_id'], text=f"🎉 **تمت الموافقة على طلب سحب رصيدك بمبلغ `{req['amount']}` ليرة بنجاح!**", parse_mode="Markdown")
                except: pass
            else:
                conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (w_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (req['amount'], req['user_id']))
                conn.commit()
                await query.message.edit_text(f"{query.message.text}\n\n❌ **تم رفض الطلب وإعادة الرصيد.**")
                try: await context.bot.send_message(chat_id=req['user_id'], text=f"❌ **تم رفض طلب السحب الخاص بك وإعادة المبلغ لرصيدك.**", parse_mode="Markdown")
                except: pass
        conn.close()

# --- 7. معالج الرسائل النصية المدخلة من الخطوات التفاعلية ---
async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get('state')

    if not state:
        return

    conn = get_db_connection()

    # طلب السحب - مرحلة 1: المبلغ
    if state == 'waiting_withdraw_amt':
        try:
            amt = float(text)
            user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            min_w = get_setting('min_withdraw')
            if amt < min_w or amt > user['balance']:
                await update.message.reply_text("❌ **المبلغ غير متاح أو أقل من الحد الأدنى، أعد المحاولة:**")
                conn.close()
                return
            
            context.user_data['withdraw_amt'] = amt
            context.user_data['state'] = 'waiting_withdraw_acc'
            await update.message.reply_text("💳 **أدخل الآن تفاصيل طريقة الدفع ورقم الحساب (شام كاش / سيريتل كاش):**", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ يرجى كتابة رقم صحيحة!")

    # طلب السحب - مرحلة 2: الحساب
    elif state == 'waiting_withdraw_acc':
        amt = context.user_data.get('withdraw_amt')
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amt, user_id))
        
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, method, account_info) VALUES (?, ?, ?, ?)",
                       (user_id, amt, "كاش", text))
        w_id = cursor.lastrowid
        conn.commit()
        
        await update.message.reply_text("✅ **تم رفع طلب السحب الخاص بك بنجاح! وهو قيد المراجعة.**")
        
        # إرسال إشعار للإدارة
        adm_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"acc_w_{w_id}"), InlineKeyboardButton("❌ رفض", callback_data=f"ref_w_{w_id}")]
        ])
        for adm in conn.execute("SELECT user_id FROM admins").fetchall():
            try:
                await context.bot.send_message(
                    chat_id=adm['user_id'],
                    text=f"💳 **طلب سحب جديد #{w_id}:**\n\n🆔 المستخدم: `{user_id}`\n💵 المبلغ: `{amt}` ليرة\n📱 التفاصيل: `{text}`",
                    reply_markup=adm_kb, parse_mode="Markdown"
                )
            except: pass
        context.user_data['state'] = None

    # كود الهدية للعميل
    elif state == 'waiting_gift_code':
        code_data = conn.execute("SELECT amount FROM gift_codes WHERE code=?", (text,)).fetchone()
        if code_data:
            amt = code_data['amount']
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, user_id))
            conn.execute("DELETE FROM gift_codes WHERE code=?", (text,))
            conn.commit()
            await update.message.reply_text(f"🎉 **تم تفعيل الكود بنجاح! وحصلت على `{amt}` ليرة.**", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ **كود الهدية غير صحيح أو مستعمل!**")
        context.user_data['state'] = None

    # طلب شراء بوت
    elif state == 'waiting_bot_specs':
        await update.message.reply_text("✅ **تم إرسال طلب الشروط للمطور والمبرمج، سيتم التواصل معك فوراً!**")
        for adm in conn.execute("SELECT user_id FROM admins").fetchall():
            try: await context.bot.send_message(chat_id=adm['user_id'], text=f"🛍️ **طلب شراء بوت من (`{user_id}`):**\n\n{text}", parse_mode="Markdown")
            except: pass
        context.user_data['state'] = None

    # الدعم الفني
    elif state == 'waiting_support_txt':
        await update.message.reply_text("💬 **تم إرسال رسالتك لفريق الدعم.**")
        for adm in conn.execute("SELECT user_id FROM admins").fetchall():
            try: await context.bot.send_message(chat_id=adm['user_id'], text=f"📩 **رسالة دعم جديدة من (`{user_id}`):**\n\n{text}", parse_mode="Markdown")
            except: pass
        context.user_data['state'] = None

    # الأدمن: ضبط نسبة الفوز
    elif state == 'waiting_win_rate' and is_admin(user_id):
        try:
            val = float(text)
            set_setting('game_win_rate', val)
            await update.message.reply_text(f"✅ **تم تعديل نسبة الفوز الخوارزمية إلى `{val}%`**", parse_mode="Markdown")
        except: await update.message.reply_text("❌ أدخل قيمة صحيحة.")
        context.user_data['state'] = None

    # الأدمن: إرسال إذاعة جماعية
    elif state == 'waiting_broadcast_msg' and is_admin(user_id):
        users = conn.execute("SELECT user_id FROM users").fetchall()
        c = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=text, parse_mode="Markdown")
                c += 1
            except: pass
        await update.message.reply_text(f"📢 **تم إرسال الإذاعة بنجاح إلى {c} مستخدم.**")
        context.user_data['state'] = None

    # الأدمن: رسالة خاصة
    elif state == 'waiting_private_msg_id' and is_admin(user_id):
        try:
            parts = text.split(" ", 1)
            target_id, msg_txt = int(parts[0]), parts[1]
            await context.bot.send_message(chat_id=target_id, text=f"🔔 **رسالة خاصة من الإدارة:**\n\n{msg_txt}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم الإرسال بنجاح.")
        except: await update.message.reply_text("❌ الصيغة غير صحيحة، تأكد من الأيدي والرسالة.")
        context.user_data['state'] = None

    # الأدمن: إنشاء كود هدية
    elif state == 'waiting_gift_code_gen' and is_admin(user_id):
        try:
            code, amt = text.split(" ")
            conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount) VALUES (?, ?)", (code, float(amt)))
            conn.commit()
            await update.message.reply_text(f"🎟️ **تم إنشاء كود الهدية `{code}` بقيمة `{amt}` ليرة بنجاح!**", parse_mode="Markdown")
        except: await update.message.reply_text("❌ الصيغة غير صحيحة! أرسل بهذا الشكل: CODE 50")
        context.user_data['state'] = None

    conn.close()

# --- 8. تشغيل البوت ---
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(games_section, pattern="^(games_menu|play_wheel|play_slot|play_dice)$"))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    print("Bot is up and running...")
    app.run_polling()

if __name__ == '__main__':
    main()
