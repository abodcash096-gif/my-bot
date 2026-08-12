import os
import sys
import sqlite3
import random
import string
import logging
import threading
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
    filters,
    ConversationHandler
)

# ----------------------------------------------------
# 1. إعدادات التسجيل والبيئة
# ----------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# إدخال توكن البوت ومعرف المدير الرئيسي هنا
BOT_TOKEN = os.getenv("BOT_TOKEN", "8842721926:AAFn7HGsi7MPsPO7KtN4Z9PE5lj-j6OOhvY")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "7255100997")) # استبدل بـ ID حسابك
SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")

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
            is_verified INTEGER DEFAULT 0,
            terms_accepted INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            captcha_answer INTEGER DEFAULT 0,
            step TEXT DEFAULT 'start'
        )
    ''')
    
    # جدول المسؤولين (Admins)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    
    # جدول قنوات الاشتراك الإجباري
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS compulsory_channels (
            channel_id TEXT PRIMARY KEY,
            channel_link TEXT
        )
    ''')
    
    # جدول الإعدادات العامة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # جدول أكواد الهدايا
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            uses_left INTEGER
        )
    ''')
    
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
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_reward', '50')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', '1000')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('game_algorithm', 'normal')") # loss, normal, medium, high, huge

    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------------------------------------------
# 3. خادم Flask الوهمي + تطبيق الويب للألعاب
# ----------------------------------------------------
flask_app = Flask(__name__, template_folder="templates")

@flask_app.route("/")
def home():
    return "Bot Server & WebApp API is Running Successfully!"

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
    game_type = data.get("game_type")

    if not user_id or bet <= 0:
        return jsonify({"error": "Invalid request"}), 400

    conn = get_db()
    user = conn.execute("SELECT balance, is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user or user["is_banned"]:
        conn.close()
        return jsonify({"error": "Unauthorized or Banned"}), 403

    if user["balance"] < bet:
        conn.close()
        return jsonify({"error": "رصيدك غير كافٍ"}), 400

    # خصم الرهان مبدئياً
    new_balance = user["balance"] - bet
    conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    # جلب خوارزمية الربح المحسوبة من لوحة الإدارة
    algo_setting = conn.execute("SELECT value FROM settings WHERE key = 'game_algorithm'").fetchone()
    mode = algo_setting["value"] if algo_setting else "normal"

    # تحديد الاحتمالية بناءً على وضع الإدارة
    win = False
    multiplier = 0.0

    if mode == "loss":
        win_chance = 0.05
    elif mode == "normal":
        win_chance = 0.40
    elif mode == "medium":
        win_chance = 0.55
    elif mode == "high":
        win_chance = 0.70
    elif mode == "huge":
        win_chance = 0.85
    else:
        win_chance = 0.40

    if random.random() < win_chance:
        win = True
        # مضاعفات سريعة وحقيقية بين 1.1x و 100x
        if mode == "loss":
            multiplier = round(random.uniform(1.05, 1.3), 2)
        elif mode == "normal":
            multiplier = round(random.uniform(1.2, 2.5), 2)
        elif mode == "medium":
            multiplier = round(random.uniform(2.0, 5.0), 2)
        elif mode == "high":
            multiplier = round(random.uniform(5.0, 20.0), 2)
        elif mode == "huge":
            multiplier = round(random.uniform(20.0, 100.0), 2)

    win_amount = 0
    if win:
        win_amount = round(bet * multiplier, 2)
        new_balance += win_amount
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                     (user_id, f"ربح في لعبة {game_type} (x{multiplier})", win_amount))
    else:
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                     (user_id, f"خسارة في لعبة {game_type}", -bet))

    conn.commit()
    conn.close()

    return jsonify({
        "win": win,
        "multiplier": multiplier,
        "win_amount": win_amount,
        "new_balance": new_balance
    })

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ----------------------------------------------------
# 4. لوحات المفاتيح Inline Keyboards للبوت
# ----------------------------------------------------
def main_menu_keyboard():
    games_url = f"{SERVER_URL}/games"
    keyboard = [
        [InlineKeyboardButton("🎮 صفحة الألعاب العالمية (Web App)", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("👤 حسابي ورصيدي", callback_data="btn_account"), InlineKeyboardButton("💸 سحب رصيدي", callback_data="btn_withdraw")],
        [InlineKeyboardButton("🔗 رابط إحالاتي", callback_data="btn_referral"), InlineKeyboardButton("🤖 شراء بوت", callback_data="btn_buy_bot")],
        [InlineKeyboardButton("💬 مراسلة الدعم", callback_data="btn_support"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="btn_gift")],
        [InlineKeyboardButton("📜 سجلاتي", callback_data="btn_logs"), InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🔍 تفاصيل لاعب", callback_data="adm_user_info"), InlineKeyboardButton("📊 عدد وأرصدة اللاعبين", callback_data="adm_stats")],
        [InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_make_gift"), InlineKeyboardButton("👮‍♂️ إضافة أدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban")],
        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_broadcast"), InlineKeyboardButton("📩 رسالة خاصة", callback_data="adm_pm")],
        [InlineKeyboardButton("📥 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("💬 رسائل الدعم", callback_data="adm_support_tickets")],
        [InlineKeyboardButton("⚙️ خوارزمية الربح", callback_data="adm_algo"), InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_welcome")],
        [InlineKeyboardButton("📢 إدارية القنوات", callback_data="adm_channels"), InlineKeyboardButton("⚙️ إعدادات الإحالة والسحب", callback_data="adm_config")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ----------------------------------------------------
# 5. دوال التعامل مع العميل ورسائل البدء
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    # فحص الحظر
    if u and u["is_banned"]:
        await update.message.reply_text("❌ حسابك محظور من استخدام البوت بسبب مخالفة الشروط.")
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
        
        conn.execute("""
            INSERT INTO users (user_id, full_name, referred_by, captcha_answer, step)
            VALUES (?, ?, ?, ?, 'captcha')
        """, (user.id, user.full_name, ref_id, ans))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"👋 أهلاً بك يا {user.full_name} في أضخم بوت كازينو وسحب أرباح!\n\n"
            f"🛡️ للتأكد من أنك لست روبوت، يرجى إجابة السؤال التالي:\n"
            f"❓ كم حاصل جمع: **{num1} + {num2}** ؟"
        )
        return

    conn.close()
    
    # متابعة خطوات التحقق
    if not u["is_verified"]:
        if u["step"] == "captcha":
            await update.message.reply_text("⚠️ يرجى حل كود الكابتشا أولاً عن طريق كتابة النتيجة بنص.")
            return
        elif u["step"] == "phone":
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم السوري للتأكيد", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("📱 يرجى الضغط على الزر أدناه لمشاركة رقمك للتأكيد:", reply_markup=btn)
            return

    # التحقق من قنوات الاشتراك الإجباري
    if not await check_compulsory_channels(user.id, context):
        await send_compulsory_channels_msg(chat_id, context)
        return

    # التحقق من قبول الشروط
    if not u["terms_accepted"]:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ أوافق على الشروط والتعليمات", callback_data="accept_terms")]])
        await update.message.reply_text(
            "📜 **شروط الاستخدام:**\n\n"
            "1. يتعهد المستخدم بعدم استخدام أي ثغرة أو احتيال.\n"
            "2. في حال اكتشاف أي عمل احتيالي، سيتم تجميد الرصيد وحظر الحساب فوراً ولن يتم الدفع.\n"
            "3. جميع التعاملات بالعملة السورية الجديدة.\n\n"
            "اضغط على الزر أدناه للموافقة والبدء:",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    # عرض القائمة الرئيسية
    await send_main_dashboard(chat_id, user.id, user.full_name, context)

async def send_main_dashboard(chat_id, user_id, full_name, context):
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
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ----------------------------------------------------
# 6. فحص قنوات الاشتراك الإجباري
# ----------------------------------------------------
async def check_compulsory_channels(user_id, context):
    conn = get_db()
    channels = conn.execute("SELECT channel_id FROM compulsory_channels").fetchall()
    conn.close()
    
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            pass
    return True

async def send_compulsory_channels_msg(chat_id, context):
    conn = get_db()
    channels = conn.execute("SELECT channel_id, channel_link FROM compulsory_channels").fetchall()
    conn.close()
    
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton("📢 اضغط هنا للإشتراك بالقناة", url=ch["channel_link"])])
    
    buttons.append([InlineKeyboardButton("🔄 تم الاشتراك، تحقق الآن", callback_data="check_channels")])
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ **عذراً عزيزي، يجب عليك الاشتراك في قنوات البوت أولاً لاستخدامه:**",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

# ----------------------------------------------------
# 7. معالجة النصوص والتحقق والدعم
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

    # كابتشا
    if step == "captcha":
        if text.isdigit() and int(text) == u["captcha_answer"]:
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم السوري للتأكيد", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            conn.execute("UPDATE users SET step = 'phone' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ كابتشا صحيحة! الأن يرجى مشاركة رقمك السوري للتوثيق:", reply_markup=btn)
        else:
            conn.close()
            await update.message.reply_text("❌ إجابة خاطئة! حاول مرة أخرى واحسب الناتج بدقة.")
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
        
        # إشعار المدير
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        for adm in admins:
            try:
                await context.bot.send_message(adm["user_id"], f"🔔 **إشعار كود هدية:**\nقام المستخدم `{user.id}` ({user.full_name}) باستخدام الكود `{text}` وحصل على `{amount}` ليرة جديدة.")
            except Exception: pass

        conn.close()
        await update.message.reply_text(f"🎉 مبروك! تم إضافة `{amount}` ليرة سورية جديدة إلى حسابك.")
        return

    # طلب سحب رصيد - مرحلة إدخال الكود والمبلغ
    if step.startswith("withdraw_"):
        method = "شام كاش" if "sham" in step else "سيريتل كاش"
        parts = text.split("\n")
        if len(parts) < 2:
            await update.message.reply_text("⚠️ يرجى إرسال الكود/الرقم والمبلغ في سطرين مختلفين.\nمثال:\n09xxxxxxx\n5000")
            conn.close()
            return

        acc_code = parts[0].strip()
        try:
            amt = float(parts[1].strip())
        except ValueError:
            await update.message.reply_text("❌ المبلغ المدخل غير صحيح.")
            conn.close()
            return

        min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])

        if amt < min_w:
            await update.message.reply_text(f"❌ الحد الأدنى للسحب هو `{min_w}` ليرة سورية جديدة.")
            conn.close()
            return

        if amt > u["balance"]:
            await update.message.reply_text("❌ رصيدك الحالي لا يكفي لهذا المبلغ.")
            conn.close()
            return

        # خصم الرصيد بانتظار الموافقة
        new_bal = u["balance"] - amt
        conn.execute("UPDATE users SET balance = ?, step = 'main' WHERE user_id = ?", (new_bal, user.id))
        cursor = conn.execute("INSERT INTO withdrawals (user_id, method, account_code, amount) VALUES (?, ?, ?, ?)",
                              (user.id, method, acc_code, amt))
        conn.commit()
        w_id = cursor.lastrowid
        conn.close()

        # إرسال إشعار للإدارة
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد", callback_data=f"rej_w_{w_id}")]
        ])
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        for adm in admins:
            try:
                await context.bot.send_message(
                    adm["user_id"],
                    f"📥 **طلب سحب جديد (#{w_id}):**\n"
                    f"👤 **اللاعب:** {user.full_name} (`{user.id}`)\n"
                    f"💳 **الطريقة:** {method}\n"
                    f"🔢 **رقم/كود الحساب:** `{acc_code}`\n"
                    f"💰 **المبلغ:** `{amt}` ليرة جديدة",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
            except Exception: pass

        await update.message.reply_text("✅ تم تقديم طلب السحب بنجاح وهو قيد المراجعة الآن من الإدارة.")
        return

    # شراء بوت
    if step == "buy_bot_desc":
        approx_price = random.randint(10, 50) * 100 # يبدأ من 1000 ليرة
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👍 موافقة وشراء", callback_data="confirm_buy_bot"), InlineKeyboardButton("👎 إلغاء", callback_data="cancel_buy_bot")]
        ])
        await update.message.reply_text(
            f"🤖 **السعر التقريبي للبوت المطلوبة مواصفاته:**\n\n"
            f"💰 `{approx_price}` ليرة سورية جديدة.\n\n"
            f"هل ترغب بالموافقة وطلب البوت رسمياً؟",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    # مراسلة الدعم
    if step == "support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        for adm in admins:
            try:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد فوري", callback_data=f"reply_sup_{user.id}")]])
                await context.bot.send_message(
                    adm["user_id"],
                    f"📩 **رسالة دعم جديدة من:** {user.full_name} (`{user.id}`)\n\n💬 **النص:** {text}",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
            except Exception: pass

        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم، سيصلك الرد هنا فورا.")
        return

    # إدخالات لوحة الإدارة
    if step.startswith("adm_input_"):
        await handle_admin_inputs(update, context, u, step, text)
        return

    conn.close()

# ----------------------------------------------------
# 8. مشاركة رقم الهاتف للتوثيق
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact
    
    if contact.user_id != user.id:
        await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي فقط.")
        return
        
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    welcome_bonus = float(conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"])
    ref_reward = float(conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"])

    # تحديث بيانات التوثيق والبونص الترحيبي
    new_bal = welcome_bonus
    conn.execute("UPDATE users SET phone = ?, is_verified = 1, balance = ?, step = 'main' WHERE user_id = ?", (contact.phone_number, new_bal, user.id))
    
    # منح مكافأة الإحالة إن وجدت
    if u and u["referred_by"]:
        ref_user = conn.execute("SELECT balance, referrals_count FROM users WHERE user_id = ?", (u["referred_by"],)).fetchone()
        if ref_user:
            ref_new_bal = ref_user["balance"] + ref_reward
            ref_count = ref_user["referrals_count"] + 1
            conn.execute("UPDATE users SET balance = ?, referrals_count = ? WHERE user_id = ?", (ref_new_bal, ref_count, u["referred_by"]))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (u["referred_by"], f"مكافأة إحالة {user.id}", ref_reward))
            
            # إشعار المحيل والإدارة
            try:
                await context.bot.send_message(u["referred_by"], f"🎉 قام المستخدم {user.full_name} باجتياز التوثيق عبر رابطك! حصلت على `{ref_reward}` ليرة جديدة.")
            except Exception: pass
            
            admins = conn.execute("SELECT user_id FROM admins").fetchall()
            for adm in admins:
                try:
                    await context.bot.send_message(adm["user_id"], f"🔔 **إشعار إحالة جديدة:**\nقام `{u['referred_by']}` بإنشاء إحالة ناجحة لـ `{user.id}`.")
                except Exception: pass

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ تم تأكيد حسابك ورقم هاتفك بنجاح!\n"
        f"🎁 تم منحك بونص ترحيبي قدره `{welcome_bonus}` ليرة سورية جديدة.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    
    await start_command(update, context)

# ----------------------------------------------------
# 9. معالجة الضغط على أزرار Inline Callbacks
# ----------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()

    if data == "accept_terms":
        conn.execute("UPDATE users SET terms_accepted = 1 WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.delete_message()
        await send_main_dashboard(query.message.chat_id, user.id, user.full_name, context)
        return

    if data == "check_channels":
        if await check_compulsory_channels(user.id, context):
            await query.delete_message()
            await start_command(update, context)
        else:
            await query.message.reply_text("❌ لم تقم بالإشتراك في جميع القنوات بعد.")
        conn.close()
        return

    # زر حسابي ورصيدي
    if data == "btn_account":
        text = (
            f"👤 **بيانات حسابي:**\n\n"
            f"🆔 **معرف الحساب:** `{u['user_id']}`\n"
            f"📱 **رقم الهاتف:** `{u['phone']}`\n"
            f"💰 **الرصيد الحالي:** `{u['balance']:,.2f}` ليرة جديدة\n"
            f"👥 **عدد إحالاتك:** `{u['referrals_count']}`"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    # زر سحب رصيدي
    elif data == "btn_withdraw":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 شام كاش", callback_data="w_sham"), InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_syriatel")]
        ])
        await query.message.reply_text("اختر طريقة السحب المناسبة لك:", reply_markup=kb)

    elif data in ["w_sham", "w_syriatel"]:
        method = "withdraw_sham" if data == "w_sham" else "withdraw_syriatel"
        conn.execute("UPDATE users SET step = ? WHERE user_id = ?", (method, user.id))
        conn.commit()
        await query.message.reply_text(
            "✍️ يرجى إرسال **رقم الحساب/الكود** والمبلغ المراد سحبه في **سطرين**:\n\n"
            "سطر 1: رقم الحساب / الكود\n"
            "سطر 2: المبلغ المطلوب"
        )

    # رابط الإحالة
    elif data == "btn_referral":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        ref_reward = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]
        await query.message.reply_text(
            f"🔗 **رابط إحالتك الخاص:**\n`{ref_link}`\n\n"
            f"💰 احصل على `{ref_reward}` ليرة سورية جديدة لكل صديق يسجل ويؤكد رقم هاتفه عبر رابطك!"
        )

    # شراء بوت
    elif data == "btn_buy_bot":
        conn.execute("UPDATE users SET step = 'buy_bot_desc' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text("🤖 اكتب التفاصيل والميزات التي تريدها في البوت الخا ص بك ونوع استضافته:")

    elif data == "confirm_buy_bot":
        await query.message.reply_text("✅ تم استلام طلبك! سيتواصل معك قسم البرمجة فوراً لتسليم البوت.")

    # مراسلة الدعم
    elif data == "btn_support":
        conn.execute("UPDATE users SET step = 'support_msg' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text("💬 اكتب رسالتك أو مشكلتك الآن وسوف تصل مباشرة للوحة الإدارة:")

    # إدخال كود هدية
    elif data == "btn_gift":
        conn.execute("UPDATE users SET step = 'enter_gift_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        await query.message.reply_text("🎁 أدخل كود الهدية الآن:")

    # سجلاتي
    elif data == "btn_logs":
        logs = conn.execute("SELECT action, amount, timestamp FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
        if not logs:
            await query.message.reply_text("📜 لا يوجد لديك سجلات عمليات سابقة حتى الآن.")
        else:
            log_text = "📜 **أخر 10 عمليات في حسابك:**\n\n"
            for lg in logs:
                log_text += f"▪️ {lg['action']} | `{lg['amount']}` ليرة | {lg['timestamp']}\n"
            await query.message.reply_text(log_text, parse_mode="Markdown")

    # لوحة الإدارة وأزرار السحب
    elif data.startswith("app_w_") or data.startswith("rej_w_"):
        w_id = int(data.split("_")[2])
        w_req = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
        if w_req:
            if data.startswith("app_w_"):
                conn.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
                conn.commit()
                await query.edit_message_text(f"✅ تم تنفيذ وتأكيد السحب بنجاح للطلب #{w_id}")
                try: await context.bot.send_message(w_req["user_id"], f"🎉 تم قبول وسداد طلب سحب رصيدك قدره `{w_req['amount']}` ليرة سورية بنجاح!")
                except Exception: pass
            else:
                # إعادة الرصيد للمستخدم
                conn.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w_req["amount"], w_req["user_id"]))
                conn.commit()
                await query.edit_message_text(f"❌ تم رفض الطلب وإعادة المبلغ للرصيد للطلب #{w_id}")
                try: await context.bot.send_message(w_req["user_id"], f"❌ تم رفض طلب سحب رصيدك وإعادة المبلغ `{w_req['amount']}` إلى حسابك.")
                except Exception: pass

    # معالجات أزرار التحكم الإدارية
    elif data.startswith("adm_"):
        await handle_admin_callbacks(update, context, u, data)

    conn.close()

# ----------------------------------------------------
# 10. وظائف التحكم الكامل للوحة الإدارة
# ----------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    is_adm = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    if not is_adm:
        await update.message.reply_text("❌ أنت لست مسؤولاً في البوت.")
        return
        
    await update.message.reply_text("👮‍♂️ **أهلاً بك في لوحة التحكم الإدارية الكاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())

async def handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, user, data):
    query = update.callback_query
    conn = get_db()
    
    if data == "adm_add_bal":
        conn.execute("UPDATE users SET step = 'adm_input_add_bal' WHERE user_id = ?", (user["user_id"],))
        await query.message.reply_text("✍️ أرسل ايدي الحساب والمبلغ بأسلوب (ID Amount):\nمثال:\n123456789 5000")
    
    elif data == "adm_sub_bal":
        conn.execute("UPDATE users SET step = 'adm_input_sub_bal' WHERE user_id = ?", (user["user_id"],))
        await query.message.reply_text("✍️ أرسل ايدي الحساب والمبلغ المراد خصمه (ID Amount):")
        
    elif data == "adm_user_info":
        conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (user["user_id"],))
        await query.message.reply_text("🔍 أرسل ايدي الحساب للاستعلام عن كامل بياناته:")

    elif data == "adm_stats":
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_bal = conn.execute("SELECT SUM(balance) as s FROM users").fetchone()["s"] or 0
        await query.message.reply_text(f"📊 **إحصائيات البوت:**\n\n👥 عدد اللاعبين: `{total_users}`\n💰 إجمالي أرصدة اللاعبين: `{total_bal:,.2f}` ليرة جديدة")

    elif data == "adm_make_gift":
        conn.execute("UPDATE users SET step = 'adm_input_make_gift' WHERE user_id = ?", (user["user_id"],))
        await query.message.reply_text("🎁 أرسل قيمة الكود وعدد مرات الاستخدام بأسلوب (Amount Uses):\nمثال:\n1000 50")

    elif data == "adm_algo":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("خسارة (5%)", callback_data="set_algo_loss"), InlineKeyboardButton("ربح عادي (40%)", callback_data="set_algo_normal")],
            [InlineKeyboardButton("ربح متوسط (55%)", callback_data="set_algo_medium"), InlineKeyboardButton("ربح عالي (70%)", callback_data="set_algo_high")],
            [InlineKeyboardButton("ربح كبير (85%)", callback_data="set_algo_huge")]
        ])
        await query.message.reply_text("⚙️ **التحكم بنسب خوارزمية الربح لألعاب الكازينو:**", reply_markup=kb)

    elif data.startswith("set_algo_"):
        mode = data.replace("set_algo_", "")
        conn.execute("UPDATE settings SET value = ? WHERE key = 'game_algorithm'", (mode,))
        await query.message.reply_text(f"✅ تم تعديل نمط الخوارزمية بنجاح إلى: `{mode}`")

    conn.commit()
    conn.close()

async def handle_admin_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE, user, step, text):
    conn = get_db()
    
    if step == "adm_input_add_bal":
        parts = text.split()
        target_id, amt = int(parts[0]), float(parts[1])
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, target_id))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"✅ تم إضافة `{amt}` ليرة سورية للرصيد بنجاح.")

    elif step == "adm_input_make_gift":
        parts = text.split()
        amt, uses = float(parts[0]), int(parts[1])
        code = "GIFT-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        conn.execute("INSERT INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code, amt, uses))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user["user_id"],))
        conn.commit()
        await update.message.reply_text(f"🎁 تم إنشاء الكود بنجاح:\n`{code}`\nالقيمة: `{amt}` | الاستخدامات: `{uses}`")

    conn.close()

# ----------------------------------------------------
# 11. تشغيل التطبيق بالكامل
# ----------------------------------------------------
def main():
    # تشغيل خادم WebApp
    threading.Thread(target=run_flask, daemon=True).start()

    # بناء البوت
    app = Application.builder().token(BOT_TOKEN).build()

    # الأوامر الرئيسية
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))

    # معالجات الاتصالات والأجوبة
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
