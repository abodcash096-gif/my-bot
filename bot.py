import os
import sys
import sqlite3
import random
import string
import logging
import threading
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ----------------------------------------------------
# 1. إعدادات التسجيل والبيئة
# ----------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8842721926:AAFn7HGsi7MPsPO7KtN4Z9PE5lj-j6OOhvY")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "7255100997"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

# ----------------------------------------------------
# 2. إعداد قاعدة البيانات (SQLite)
# ----------------------------------------------------
DB_NAME = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            balance REAL DEFAULT 0.0,
            referred_by INTEGER,
            referrals_count INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            terms_accepted INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            captcha_answer INTEGER DEFAULT 0,
            step TEXT DEFAULT 'start',
            custom_boost REAL DEFAULT 0.0
        )
    ''')
    
    # التأكد من وجود عمود custom_boost للحسابات القديمة
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_boost REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    # جدول المسؤولين
    cursor.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
    
    # جدول قنوات الاشتراك الإجباري
    cursor.execute('CREATE TABLE IF NOT EXISTS compulsory_channels (channel_id TEXT PRIMARY KEY, channel_link TEXT)')
    
    # جدول الإعدادات العامة
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    
    # جدول أكواد الهدايا
    cursor.execute('CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, uses_left INTEGER)')
    
    # جدول سجلات العمليات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            amount REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول طلبات السحب
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            account_code TEXT,
            amount REAL,
            status TEXT DEFAULT 'pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # إدخال البيانات الافتراضية
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus', '100')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus_enabled', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_reward', '50')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', '1000')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('game_algorithm', 'normal')")

    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------------------------------------------
# 3. خادم Flask + محرك الخوارزمية الذكي
# ----------------------------------------------------
flask_app = Flask(__name__, template_folder="templates")
MULTIPLIERS = [3, 6, 9, 15, 20, 50, 100]

@flask_app.route("/")
def home():
    return "VIP Casino Engine Running Successfully!"

@flask_app.route("/games")
def games_page():
    return render_template("index.html", server_url=SERVER_URL)

@flask_app.route("/api/get_user_data", methods=["POST"])
def api_get_user():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400
    
    conn = get_db()
    user = conn.execute("SELECT user_id, full_name, balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({"error": "User not found"}), 404
        
    return jsonify({
        "user_id": user["user_id"],
        "name": user["full_name"],
        "balance": user["balance"]
    })

@flask_app.route("/api/play_game", methods=["POST"])
def api_play_game():
    data = request.json or {}
    user_id = data.get("user_id")
    bet = float(data.get("bet", 0))

    if not user_id or bet <= 0:
        return jsonify({"error": "بيانات الرهان غير صحيحة"}), 400

    conn = get_db()
    user = conn.execute("SELECT balance, is_banned, games_played, custom_boost FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user or user["is_banned"]:
        conn.close()
        return jsonify({"error": "الحساب محظور أو غير موجود"}), 403

    if user["balance"] < bet:
        conn.close()
        return jsonify({"error": "رصيدك غير كافٍ لتغطية الرهان"}), 400

    # خصم الرهان وزيادة عدد مرات اللعب
    new_balance = user["balance"] - bet
    conn.execute("UPDATE users SET balance = ?, games_played = games_played + 1 WHERE user_id = ?", (new_balance, user_id))

    # --------------------------------------------------------
    # محرك خوارزمية التحكم بالربح والخسارة
    # --------------------------------------------------------
    algo_setting = conn.execute("SELECT value FROM settings WHERE key = 'game_algorithm'").fetchone()
    mode = algo_setting["value"] if algo_setting else "normal"

    # النسب الأساسية العامة
    chances = {
        "loss": 0.05,      # 5% فوز
        "normal": 0.25,    # 25% فوز
        "medium": 0.45,    # 45% فوز
        "high": 0.65,      # 65% فوز
        "huge": 0.85       # 85% فوز
    }
    base_chance = chances.get(mode, 0.25)

    # دمج التعديل الخاص باللاعب (Custom Boost) إن وجد
    user_boost = user["custom_boost"] or 0.0
    final_win_chance = max(0.0, min(1.0, base_chance + (user_boost / 100.0)))

    # تحديد الفوز أو الخسارة
    win = random.random() < final_win_chance
    chosen_multiplier = 0
    target_index = -1

    if win:
        # أوزان ظهور المضاعفات عند الفوز (تفضيل الأرقام الصغرى لضمان سلامة الخزينة)
        weights = [50, 25, 12, 7, 4, 1.8, 0.2]
        target_index = random.choices(range(len(MULTIPLIERS)), weights=weights)[0]
        chosen_multiplier = MULTIPLIERS[target_index]
        win_amount = round(bet * chosen_multiplier, 2)
        new_balance += win_amount
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                     (user_id, f"ربح في لعبة العجلة (x{chosen_multiplier})", win_amount))
    else:
        win_amount = 0
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                     (user_id, f"خسارة في لعبة العجلة", -bet))

    conn.commit()
    conn.close()

    return jsonify({
        "win": win,
        "target_index": target_index,
        "multiplier": chosen_multiplier,
        "win_amount": win_amount,
        "new_balance": new_balance
    })

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ----------------------------------------------------
# 4. لوحات المفاتيح Inline Keyboards
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    keyboard = [
        [InlineKeyboardButton("🎮 صفحة الألعاب العالمية (Web App)", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("👤 حسابي ورصيدي", callback_data="btn_account"), InlineKeyboardButton("💸 سحب رصيدي", callback_data="btn_withdraw")],
        [InlineKeyboardButton("🔗 رابط إحالاتي", callback_data="btn_referral"), InlineKeyboardButton("🤖 شراء بوت", callback_data="btn_buy_bot")],
        [InlineKeyboardButton("💬 مراسلة الدعم", callback_data="btn_support"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="btn_gift")],
        [InlineKeyboardButton("📜 سجلاتي", callback_data="btn_logs"), InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if is_admin:
        keyboard.insert(1, [InlineKeyboardButton("⚙️ لوحة الإدارة الشاملة", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚙️ الخوارزمية العامة", callback_data="adm_algo"), InlineKeyboardButton("🎯 حظ لاعب معين", callback_data="adm_user_boost")],
        [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_make_gift"), InlineKeyboardButton("🔗 سعر الإحالة", callback_data="adm_set_ref")],
        [InlineKeyboardButton("💸 الحد الأدنى للسحب", callback_data="adm_set_min_w"), InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_set_welcome")],
        [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_info"), InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban")],
        [InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban"), InlineKeyboardButton("📢 رسالة جماعية (نص)", callback_data="adm_bc_txt")],
        [InlineKeyboardButton("📸 رسالة جماعية (صورة)", callback_data="adm_bc_img"), InlineKeyboardButton("📩 رسالة خاصة (نص)", callback_data="adm_pm_txt")],
        [InlineKeyboardButton("📸 رسالة خاصة (صورة)", callback_data="adm_pm_img"), InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats")],
        [InlineKeyboardButton("📜 سجلات العملاء", callback_data="adm_all_logs"), InlineKeyboardButton("📥 طلبات السحب", callback_data="adm_withdraws")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ----------------------------------------------------
# 5. التثبت وإرسال الإشعارات للإدارة
# ----------------------------------------------------
async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    conn = get_db()
    admins = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    for adm in admins:
        try:
            await context.bot.send_message(chat_id=adm["user_id"], text=text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            pass

# ----------------------------------------------------
# 6. معالجة الأوامر /start و /admin
# ----------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None
    conn.close()

    if is_admin:
        await update.message.reply_text("👮‍♂️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
    else:
        await update.message.reply_text("❌ عذراً، هذا الأمر مخصص للإدارة فقط.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None

    if u and u["is_banned"]:
        await update.message.reply_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return

    # تسجيل مستخدم جديد
    if not u:
        ref_id = None
        if context.args and context.args[0].isdigit():
            ref_id = int(context.args[0])
            if ref_id == user.id:
                ref_id = None
                
        num1, num2 = random.randint(1, 9), random.randint(1, 9)
        ans = num1 + num2
        
        conn.execute("INSERT INTO users (user_id, full_name, referred_by, captcha_answer, step) VALUES (?, ?, ?, ?, 'captcha')",
                     (user.id, user.full_name, ref_id, ans))
        conn.commit()
        conn.close()

        await notify_admins(context, f"🔔 **دخول مستخدم جديد:**\n👤 **الاسم:** {user.full_name}\n🆔 **المعرف:** `{user.id}`")

        await update.message.reply_text(
            f"👋 أهلاً بك يا {user.full_name} في البوت!\n\n"
            f"🛡️ للتأكد من أنك لست روبوت، يرجى كتابة الناتج:\n"
            f"❓ **{num1} + {num2} = ?**"
        )
        return

    conn.close()

    if not u["is_verified"]:
        if u["step"] == "captcha":
            await update.message.reply_text("⚠️ يرجى حَل كود الكابتشا أولاً بكتابة النتيجة.")
            return
        elif u["step"] == "phone":
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("📱 يرجى مشاركة رقمك السوري للتوثيق والبدء:", reply_markup=btn)
            return

    await send_main_dashboard(chat_id, user.id, user.full_name, is_admin, context)

async def send_main_dashboard(chat_id, user_id, full_name, is_admin, context):
    conn = get_db()
    u = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    bal = u["balance"] if u else 0.0
    text = (
        f"👑 **مرحباً بك في البوت الأقوى على التلغرام**\n\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🆔 **معرف الحساب (ID):** `{user_id}`\n"
        f"💰 **رصيدك الحالي:** `{bal:,.2f}` ليرة سورية جديدة\n\n"
        f"اختر من الأزرار أدناه للبدء:"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin))

# ----------------------------------------------------
# 7. توثيق رقم الهاتف والرموز الترحيبية والإحالات
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact
    
    if contact.user_id != user.id:
        await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي فقط.")
        return
        
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
    welcome_bonus = float(conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]) if welcome_enabled else 0.0
    ref_reward = float(conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"])

    new_bal = welcome_bonus
    conn.execute("UPDATE users SET phone = ?, is_verified = 1, balance = ?, step = 'main' WHERE user_id = ?", (contact.phone_number, new_bal, user.id))
    
    if u and u["referred_by"]:
        ref_user = conn.execute("SELECT balance, referrals_count FROM users WHERE user_id = ?", (u["referred_by"],)).fetchone()
        if ref_user:
            ref_new_bal = ref_user["balance"] + ref_reward
            ref_count = ref_user["referrals_count"] + 1
            conn.execute("UPDATE users SET balance = ?, referrals_count = ? WHERE user_id = ?", (ref_new_bal, ref_count, u["referred_by"]))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (u["referred_by"], f"مكافأة إحالة {user.id}", ref_reward))
            
            try:
                await context.bot.send_message(u["referred_by"], f"🎉 قام المستخدم {user.full_name} بالتسجيل والتوثيق عبر رابطك! حصلت على `{ref_reward}` ليرة جديدة.")
            except Exception: pass
            
            await notify_admins(context, f"🔗 **إشعار إحالة جديدة:**\nقام المستخدم `{u['referred_by']}` بإحالة المستخدم الجديد `{user.id}` ({user.full_name}).")

    conn.commit()
    conn.close()

    is_admin = (user.id == DEFAULT_ADMIN_ID)
    await update.message.reply_text(
        f"✅ تم تأكيد حسابك ورقم هاتفك بنجاح!\n"
        f"🎁 حصلت على بونص ترحيبي قدره `{welcome_bonus}` ليرة سورية جديدة.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin, context)

# ----------------------------------------------------
# 8. معالجة الرسائل النصية الموجهة
# ----------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""
    
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if not u or u["is_banned"]:
        conn.close()
        return

    step = u["step"]

    # حل الكابتشا
    if step == "captcha":
        if text.isdigit() and int(text) == u["captcha_answer"]:
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            conn.execute("UPDATE users SET step = 'phone' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ إجابة صحيحة! يرجى مشاركة رقم هاتفك للتأكيد:", reply_markup=btn)
        else:
            conn.close()
            await update.message.reply_text("❌ إجابة خاطئة! يرجى حساب الجمع بدقة ومحاولة كتابة الرقم مجدداً.")
        return

    # نظام السحب
    if step == "withdraw_step_code":
        context.user_data["withdraw_code"] = text
        conn.execute("UPDATE users SET step = 'withdraw_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم حفظ كود/رقم الحساب.\n\n✍️ **الآن يرجى إرسال المبلغ المراد سحبه (ليرة سورية جديدة):**")
        return

    if step == "withdraw_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ يرجى إدخال مبلغ مالي صحيح بكلمات أرقام فقط.")
            return

        min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])
        if amt < min_w:
            conn.close()
            await update.message.reply_text(f"❌ الحد الأدنى المسموح به للسحب هو `{min_w}` ليرة جديدة.")
            return

        if amt > u["balance"]:
            conn.close()
            await update.message.reply_text("❌ رصيدك الحالي لا يكفي لهذا المبلغ.")
            return

        method = context.user_data.get("withdraw_method", "غير محدد")
        acc_code = context.user_data.get("withdraw_code", "غير محدد")

        new_bal = u["balance"] - amt
        conn.execute("UPDATE users SET balance = ?, step = 'main' WHERE user_id = ?", (new_bal, user.id))
        cursor = conn.execute("INSERT INTO withdrawals (user_id, method, account_code, amount) VALUES (?, ?, ?, ?)",
                              (user.id, method, acc_code, amt))
        conn.commit()
        w_id = cursor.lastrowid
        conn.close()

        await update.message.reply_text("✅ تم تقديم طلب السحب بنجاح وهو قيد المراجعة والتدقيق الآن.")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد", callback_data=f"rej_w_{w_id}")]
        ])
        await notify_admins(context, 
            f"📥 **طلب سحب جديد (# {w_id}):**\n"
            f"👤 **اللاعب:** {user.full_name} (`{user.id}`)\n"
            f"💳 **الطريقة:** {method}\n"
            f"🔢 **الكود/الرقم:** `{acc_code}`\n"
            f"💰 **المبلغ:** `{amt:,.2f}` ليرة جديدة", reply_markup=kb)
        return

    # إدخال كود هدية
    if step == "enter_gift_code":
        gift = conn.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if not gift or gift["uses_left"] <= 0:
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("❌ الكود غير صحيح أو انتهت مرات استخدامه.")
            return

        amount = gift["amount"]
        new_bal = u["balance"] + amount
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, user.id))
        conn.execute("UPDATE gift_codes SET uses_left = uses_left - 1 WHERE code = ?", (text,))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, f"شحن كود هدية {text}", amount))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"🎉 مبروك! تم إضافة `{amount}` ليرة سورية جديدة إلى حسابك.")
        await notify_admins(context, f"🎁 **استخدام كود هدية:**\nقام المستخدم `{user.id}` ({user.full_name}) باستخدام الكود `{text}` ورابح `{amount}` ليرة جديدة.")
        return

    # مراسلة الدعم
    if step == "support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد فوري", callback_data=f"reply_sup_{user.id}")]])
        await notify_admins(context, f"📩 **رسالة دعم جديدة من:** {user.full_name} (`{user.id}`)\n\n💬 **النص:** {text}", reply_markup=kb)
        await update.message.reply_text("✅ تم إرسال رسالتك إلى فريق الدعم، سيصلك الرد هنا قريباً.")
        return

    # إدخالات لوحة الإدارة
    if step.startswith("adm_"):
        await handle_admin_text_inputs(update, context, u, step, text)
        return

    conn.close()

# ----------------------------------------------------
# 9. معالجة إدخالات الأدمن النصية
# ----------------------------------------------------
async def handle_admin_text_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_user, step, text):
    conn = get_db()
    
    if step == "adm_input_user_boost":
        try:
            parts = text.split()
            target_id = int(parts[0])
            boost_val = float(parts[1])
            conn.execute("UPDATE users SET custom_boost = ? WHERE user_id = ?", (boost_val, target_id))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
            conn.commit()
            await update.message.reply_text(f"🎯 تم ضبط التعديل الخاص بحظ المستخدم `{target_id}` على: `{boost_val:+}%` بنجاح!")
        except Exception as e:
            await update.message.reply_text("❌ صيغة غير صحيحة! يرجى كتابتها بالشكل: `ID Boost`\nمثال: `7255100997 20` لزيادة الحظ أو `7255100997 -30` لتخفيضه.")

    elif step == "adm_input_add_bal":
        parts = text.split()
        target_id, amt = int(parts[0]), float(parts[1])
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, target_id))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (target_id, "إضافة رصيد من الأدمن", amt))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم إضافة `{amt}` ليرة إلى حساب `{target_id}` بنجاح.")
        try: await context.bot.send_message(target_id, f"🎉 تم إضافة `{amt}` ليرة سورية جديدة إلى حسابك من الإدارة.")
        except Exception: pass

    elif step == "adm_input_sub_bal":
        parts = text.split()
        target_id, amt = int(parts[0]), float(parts[1])
        conn.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amt, target_id))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (target_id, "خصم رصيد من الأدمن", -amt))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم خصم `{amt}` ليرة من حساب `{target_id}` بنجاح.")

    elif step == "adm_input_make_gift":
        parts = text.split()
        amt, uses = float(parts[0]), int(parts[1])
        code = "GIFT-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        conn.execute("INSERT INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code, amt, uses))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"🎁 تم إنشاء كود الهدية:\n`{code}`\nالقيمة: `{amt}` | مرات الاستخدام: `{uses}`")

    elif step == "adm_input_set_ref":
        val = float(text)
        conn.execute("UPDATE settings SET value = ? WHERE key = 'referral_reward'", (str(val),))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم تعديل مكافأة الإحالة إلى `{val}` ليرة.")

    elif step == "adm_input_set_min_w":
        val = float(text)
        conn.execute("UPDATE settings SET value = ? WHERE key = 'min_withdraw'", (str(val),))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم تعديل الحد الأدنى للسحب إلى `{val}` ليرة.")

    elif step == "adm_input_welcome_amt":
        val = float(text)
        conn.execute("UPDATE settings SET value = ? WHERE key = 'welcome_bonus'", (str(val),))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى `{val}` ليرة.")

    elif step == "adm_input_user_info":
        target_id = int(text)
        usr = conn.execute("SELECT * FROM users WHERE user_id = ?", (target_id,)).fetchone()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        if not usr:
            await update.message.reply_text("❌ لم يتم العثور على مستخدم بهذا المعرف.")
        else:
            boost_val = usr['custom_boost'] or 0.0
            info = (
                f"🔍 **تفاصيل العميل المفصلة:**\n\n"
                f"👤 **الاسم:** {usr['full_name']}\n"
                f"🆔 **ID:** `{usr['user_id']}`\n"
                f"📱 **رقم الهاتف:** `{usr['phone']}`\n"
                f"💰 **الرصيد:** `{usr['balance']:,.2f}` ليرة\n"
                f"🎯 **تعديل الحظ الخاص:** `{boost_val:+}%`\n"
                f"👥 **عدد الإحالات:** `{usr['referrals_count']}`\n"
                f"🎰 **عدد الألعاب:** `{usr['games_played']}` مرة\n"
                f"🚫 **حالة الحظر:** {'محظور ❌' if usr['is_banned'] else 'نشط ✅'}"
            )
            await update.message.reply_text(info, parse_mode="Markdown")

    elif step == "adm_input_ban":
        target_id = int(text)
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"🚫 تم حظر المستخدم `{target_id}` بنجاح.")

    elif step == "adm_input_unban":
        target_id = int(text)
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم إلغاء حظر المستخدم `{target_id}` بنجاح.")

    elif step == "adm_input_bc_txt":
        users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        count = 0
        for u_item in users_list:
            try:
                await context.bot.send_message(u_item["user_id"], text, parse_mode="Markdown")
                count += 1
            except Exception: pass
        await update.message.reply_text(f"📢 تم إرسال الرسالة الجماعية لـ `{count}` مستخدم بنجاح.")

    elif step == "adm_input_pm_txt":
        parts = text.split("|")
        target_id, msg = int(parts[0].strip()), parts[1].strip()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        try:
            await context.bot.send_message(target_id, f"📩 **رسالة خاصة من الإدارة:**\n\n{msg}", parse_mode="Markdown")
            await update.message.reply_text(f"✅ تم إرسال الرسالة الخاصة للمستخدم `{target_id}`.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إرسال الرسالة: {e}")

    elif step == "adm_input_pm_img_id":
        context.user_data["target_pm_id"] = int(text)
        conn.execute("UPDATE users SET step = 'adm_input_pm_img_photo' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        await update.message.reply_text("📸 الآن يرجى إرسال **الصورة** المراد إرسالها للعميل:")

    elif step.startswith("reply_to_user_"):
        target_id = int(step.replace("reply_to_user_", ""))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (admin_user["user_id"],))
        conn.commit()
        try:
            await context.bot.send_message(target_id, f"💬 **رد الدعم الفني:**\n\n{text}")
            await update.message.reply_text("✅ تم إرسال الرد للعميل بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إرسال الرد: {e}")

    conn.close()

# ----------------------------------------------------
# 10. معالجة إرسال الصور
# ----------------------------------------------------
async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    u = conn.execute("SELECT step FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if not u:
        conn.close()
        return

    step = u["step"]
    photo_id = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    if step == "adm_input_bc_img":
        users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        count = 0
        for u_item in users_list:
            try:
                await context.bot.send_photo(u_item["user_id"], photo=photo_id, caption=caption, parse_mode="Markdown")
                count += 1
            except Exception: pass
        await update.message.reply_text(f"📸 تم إرسال الصورة الجماعية لـ `{count}` مستخدم بنجاح.")

    elif step == "adm_input_pm_img_photo":
        target_id = context.user_data.get("target_pm_id")
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        try:
            await context.bot.send_photo(target_id, photo=photo_id, caption=f"📩 **صورة من الإدارة:**\n\n{caption}", parse_mode="Markdown")
            await update.message.reply_text(f"✅ تم إرسال الصورة للعميل `{target_id}` بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الإرسال: {e}")

    conn.close()

# ----------------------------------------------------
# 11. معالجة الضغط على أزرار Callback Queries
# ----------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None

    if data == "open_admin_panel" and is_admin:
        await query.message.reply_text("👮‍♂️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())

    elif data == "back_to_main":
        await query.delete_message()
        await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)

    elif data == "btn_account":
        boost_val = u['custom_boost'] or 0.0
        text = (
            f"👤 **بيانات حسابك:**\n\n"
            f"🆔 **معرف الحساب:** `{u['user_id']}`\n"
            f"📱 **رقم الهاتف:** `{u['phone']}`\n"
            f"💰 **الرصيد الحالي:** `{u['balance']:,.2f}` ليرة جديدة\n"
            f"👥 **عدد الإحالات:** `{u['referrals_count']}`\n"
            f"🎰 **عدد الألعاب:** `{u['games_played']}` لعبة"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    elif data == "btn_withdraw":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_syriatel"), InlineKeyboardButton("💸 شام كاش", callback_data="w_sham")]
        ])
        await query.message.reply_text("اختر طريقة السحب المناسبة لك:", reply_markup=kb)

    elif data in ["w_syriatel", "w_sham"]:
        method_name = "سيريتل كاش" if data == "w_syriatel" else "شام كاش"
        context.user_data["withdraw_method"] = method_name
        conn.execute("UPDATE users SET step = 'withdraw_step_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text(f"✍️ اخترت السحب عبر **({method_name})**.\n\nيرجى إرسال **رقم أو كود الحساب** الخاص بك لاستقبال الأموال عليه:")

    elif data == "btn_referral":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        ref_reward = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]
        await query.message.reply_text(
            f"🔗 **رابط إحالتك الخاص:**\n`{ref_link}`\n\n"
            f"💰 احصل على `{ref_reward}` ليرة سورية جديدة لكل شخص يسجل ويؤكد برقم هاتفه عبر رابطك!"
        )

    elif data == "btn_buy_bot":
        await query.message.reply_text("🤖 لشراء بوت العاب كازينو أو تطوير تطبيق تواصل مع المبرمج المباشر: @lerafree")

    elif data == "btn_support":
        conn.execute("UPDATE users SET step = 'support_msg' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text("💬 اكتب رسالتك أو مشكلتك الآن وسوف تصل فورا لوحة الدعم الفني:")

    elif data == "btn_gift":
        conn.execute("UPDATE users SET step = 'enter_gift_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text("🎁 أدخل كود الهدية الآن:")

    elif data == "btn_logs":
        logs = conn.execute("SELECT action, amount, timestamp FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
        if not logs:
            await query.message.reply_text("📜 لا يوجد لديك سجلات عمليات سابقة حتى الآن.")
        else:
            log_text = "📜 **أخر 10 عمليات في حسابك:**\n\n"
            for lg in logs:
                log_text += f"▪️ {lg['action']} | `{lg['amount']}` ليرة | {lg['timestamp']}\n"
            await query.message.reply_text(log_text, parse_mode="Markdown")

    elif data.startswith("app_w_") or data.startswith("rej_w_"):
        if is_admin:
            w_id = int(data.split("_")[2])
            w_req = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
            if w_req:
                if data.startswith("app_w_"):
                    conn.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
                    conn.commit()
                    await query.edit_message_text(f"✅ تم قبول وسداد الطلب #{w_id} بنجاح.")
                    try: await context.bot.send_message(w_req["user_id"], f"🎉 تم قبول وسداد طلب سحب رصيدك قدره `{w_req['amount']}` ليرة سورية بنجاح!")
                    except Exception: pass
                else:
                    conn.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
                    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w_req["amount"], w_req["user_id"]))
                    conn.commit()
                    await query.edit_message_text(f"❌ تم رفض الطلب وإعادة المبلغ لحساب المستخدم #{w_id}.")
                    try: await context.bot.send_message(w_req["user_id"], f"❌ تم رفض طلب السحب وإعادة المبلغ `{w_req['amount']}` ليرة إلى حسابك.")
                    except Exception: pass

    elif data.startswith("reply_sup_") and is_admin:
        target_id = int(data.replace("reply_sup_", ""))
        conn.execute("UPDATE users SET step = ? WHERE user_id = ?", (f"reply_to_user_{target_id}", user.id))
        conn.commit()
        await query.message.reply_text(f"✍️ اكتب الرد الفوري الموجه للعميل `{target_id}` الآن:")

    elif data.startswith("adm_") and is_admin:
        await handle_admin_callbacks(query, context, user.id, data)

    conn.close()

async def handle_admin_callbacks(query, context, admin_id, data):
    conn = get_db()
    
    if data == "adm_user_boost":
        conn.execute("UPDATE users SET step = 'adm_input_user_boost' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text(
            "🎯 **التحكم بحظ لاعب معيّن:**\n\n"
            "أرسل المعرف والنسبة المراد إضافتها أو خصمها بأسلوب (ID Boost):\n\n"
            "• مثال لرفع نسبة حظ لاعب 30%:\n`7255100997 30`\n"
            "• مثال لتخفيض نسبة حظ لاعب 50%:\n`7255100997 -50`"
        )

    elif data == "adm_algo":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("خسارة عالية (5%)", callback_data="set_algo_loss"), InlineKeyboardButton("عادي (25%)", callback_data="set_algo_normal")],
            [InlineKeyboardButton("متوسط (45%)", callback_data="set_algo_medium"), InlineKeyboardButton("عالي (65%)", callback_data="set_algo_high")],
            [InlineKeyboardButton("كبير جداً (85%)", callback_data="set_algo_huge")]
        ])
        await query.message.reply_text("⚙️ **تعديل نسبة الفوز الإجمالية لخوارزمية الموقع:**", reply_markup=kb)

    elif data.startswith("set_algo_"):
        mode = data.replace("set_algo_", "")
        conn.execute("UPDATE settings SET value = ? WHERE key = 'game_algorithm'", (mode,))
        await query.message.reply_text(f"✅ تم ضبط الخوارزمية العامة بنجاح على الوضع: `{mode}`")

    elif data == "adm_add_bal":
        conn.execute("UPDATE users SET step = 'adm_input_add_bal' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("✍️ أرسل ID والمبلغ بأسلوب (ID Amount):\nمثال:\n7255100997 5000")

    elif data == "adm_sub_bal":
        conn.execute("UPDATE users SET step = 'adm_input_sub_bal' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("✍️ أرسل ID والمبلغ المراد خصمه بأسلوب (ID Amount):")

    elif data == "adm_make_gift":
        conn.execute("UPDATE users SET step = 'adm_input_make_gift' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("🎁 أرسل قيمة الكود وعدد الاستخدامات بأسلوب (Amount Uses):\nمثال:\n1000 20")

    elif data == "adm_set_ref":
        conn.execute("UPDATE users SET step = 'adm_input_set_ref' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("🔗 أرسل القيمة الجديدة لمكافأة الإحالة (ليرة جديدة):")

    elif data == "adm_set_min_w":
        conn.execute("UPDATE users SET step = 'adm_input_set_min_w' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("💸 أرسل حد السحب الأدنى الجديد (ليرة جديدة):")

    elif data == "adm_set_welcome":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تفعيل البونص", callback_data="toggle_welcome_1"), InlineKeyboardButton("❌ تعطيل البونص", callback_data="toggle_welcome_0")],
            [InlineKeyboardButton("✍️ تغيير قيمة البونص", callback_data="change_welcome_amt")]
        ])
        await query.message.reply_text("🎁 **إعدادات البونص الترحيبي:**", reply_markup=kb)

    elif data.startswith("toggle_welcome_"):
        val = data.split("_")[2]
        conn.execute("UPDATE settings SET value = ? WHERE key = 'welcome_bonus_enabled'", (val,))
        await query.message.reply_text("✅ تم تحديث حالة البونص الترحيبي بنجاح.")

    elif data == "change_welcome_amt":
        conn.execute("UPDATE users SET step = 'adm_input_welcome_amt' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("✍️ أدخل قيمة البونص الترحيبي الجديدة:")

    elif data == "adm_user_info":
        conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("🔍 أرسل ID العميل لاستعراض بياناته الكرتية وتفاصيل لعبه ورصيده:")

    elif data == "adm_ban":
        conn.execute("UPDATE users SET step = 'adm_input_ban' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("🚫 أرسل ID المستخدم المراد حظره:")

    elif data == "adm_unban":
        conn.execute("UPDATE users SET step = 'adm_input_unban' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("✅ أرسل ID المستخدم المراد إلغاء حظره:")

    elif data == "adm_bc_txt":
        conn.execute("UPDATE users SET step = 'adm_input_bc_txt' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("📢 أرسل النص المراد إرساله كرسالة جماعية لكافة المستخدمين:")

    elif data == "adm_bc_img":
        conn.execute("UPDATE users SET step = 'adm_input_bc_img' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("📸 أرسل **الصورة** المراد إرسالها لجميع المشتركين مع نص اختياري:")

    elif data == "adm_pm_txt":
        conn.execute("UPDATE users SET step = 'adm_input_pm_txt' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("📩 أرسل المعرف والنص بأسلوب (ID|المحتوى):\nمثال:\n7255100997|مرحبا بك")

    elif data == "adm_pm_img":
        conn.execute("UPDATE users SET step = 'adm_input_pm_img_id' WHERE user_id = ?", (admin_id,))
        await query.message.reply_text("✍️ أرسل **معرف الحساب (ID)** للعميل المراد إرسال الصورة له:")

    elif data == "adm_stats":
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_bal = conn.execute("SELECT SUM(balance) as s FROM users").fetchone()["s"] or 0
        total_games = conn.execute("SELECT SUM(games_played) as g FROM users").fetchone()["g"] or 0
        await query.message.reply_text(
            f"📊 **إحصائيات المنصة الشاملة:**\n\n"
            f"👥 **إجمالي اللاعبين:** `{total_users}`\n"
            f"💰 **إجمالي أرصدة الحسابات:** `{total_bal:,.2f}` ليرة جديدة\n"
            f"🎰 **إجمالي عدد مرات اللعب:** `{total_games}` لعبة"
        )

    elif data == "adm_all_logs":
        logs = conn.execute("SELECT user_id, action, amount, timestamp FROM logs ORDER BY id DESC LIMIT 15").fetchall()
        if not logs:
            await query.message.reply_text("📜 لا يوجد سجلات حركات سابقة.")
        else:
            log_text = "📜 **أحدث 15 حركة في البوت:**\n\n"
            for lg in logs:
                log_text += f"👤 `{lg['user_id']}` | {lg['action']} | `{lg['amount']}` ليرة | {lg['timestamp']}\n"
            await query.message.reply_text(log_text, parse_mode="Markdown")

    conn.commit()
    conn.close()

# ----------------------------------------------------
# 12. تشغيل التطبيق بالكامل
# ----------------------------------------------------
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # تسجيل الأوامر والروابط
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))  # إزالة خطأ NameError
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Ultimate Casino Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
