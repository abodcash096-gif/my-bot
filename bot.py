import os
import sys
import sqlite3
import random
import string
import logging
import threading
import re
import hmac
import hashlib
from urllib.parse import parse_qsl
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

# تم إزالة التوكين الحقيقي لحمايته، يفضل ضبطه عبر متغيرات البيئة Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "7255100997"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

ALLOWED_STRIKE_PRICES = [3, 6, 9, 12, 15, 20, 50, 100]
MULTIPLIERS = [2, 3, 5, 10, 15, 20, 50, 100]
MULTIPLIER_WEIGHTS = [45, 25, 15, 8, 4, 2, 0.8, 0.2]

ALLOWED_GAMES = [
    'wheel', 'aviator', 'mines', 'slots', 'chests', 
    'dice', 'coinflip', 'cards', 'thimbles', 'roulette'
]

# ----------------------------------------------------
# 2. إعداد قاعدة البيانات (SQLite) مع وضع WAL
# ----------------------------------------------------
DB_NAME = "bot_database.db"

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    # تفعيل وضع WAL لمنع مشكلة قفل قاعدة البيانات أثناء تزامن Flask وتليجرام
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
    
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
    
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_boost REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    cursor.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, uses_left INTEGER)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_title TEXT,
            channel_link TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            amount REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
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

    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus', '100')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus_enabled', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_reward', '50')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', '1000')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('game_algorithm', '35')")

    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 3. التحقق أمنياً من بيانات Telegram WebApp
# ----------------------------------------------------
def verify_telegram_webapp_data(init_data: str, token: str) -> bool:
    if not init_data:
        return False
    try:
        parsed_data = dict(parse_qsl(init_data))
        hash_check = parsed_data.pop('hash', '')
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calculated_hash == hash_check
    except Exception:
        return False

# ----------------------------------------------------
# 4. فحص الاشتراك الإجباري بالقنوات
# ----------------------------------------------------
async def check_user_channels_subscription(bot, user_id: int) -> tuple[bool, list]:
    conn = get_db()
    channels = conn.execute("SELECT channel_id, channel_title, channel_link FROM channels").fetchall()
    conn.close()

    if not channels:
        return True, []

    unsubscribed = []
    for ch in channels:
        ch_id = ch["channel_id"]
        try:
            member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                unsubscribed.append(ch)
        except Exception as e:
            logger.warning(f"Error checking channel {ch_id} for user {user_id}: {e}")
            unsubscribed.append(ch)

    if unsubscribed:
        return False, unsubscribed
    return True, []

def build_sub_keyboard(unsubscribed_channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in unsubscribed_channels:
        title = ch["channel_title"] or "القناة المطلوب الاشتراك بها"
        url = ch["channel_link"]
        keyboard.append([InlineKeyboardButton(f"📢 {title}", url=url)])
    
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الان", callback_data="check_subscription_status")])
    return InlineKeyboardMarkup(keyboard)

# ----------------------------------------------------
# 5. خادم Flask API والألعاب الـ 10
# ----------------------------------------------------
flask_app = Flask(__name__, template_folder="templates")

@flask_app.route("/")
def home():
    return "1xBet Style VIP Casino Engine Running!"

@flask_app.route("/games")
def games_page():
    return render_template("index.html", server_url=SERVER_URL)

@flask_app.route("/api/get_user_data", methods=["POST"])
def api_get_user():
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        init_data = data.get("init_data", "")

        # تحقق أمني اختياري في البيئة الإنتاجية
        if init_data and not verify_telegram_webapp_data(init_data, BOT_TOKEN):
            return jsonify({"error": "فشل التحقق الأمني من الجلسة"}), 403

        if not user_id:
            return jsonify({"error": "معرف المستخدم مفقود"}), 400
        
        conn = get_db()
        user = conn.execute("SELECT user_id, full_name, balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        
        if not user:
            return jsonify({"error": "الحساب غير موجود"}), 404
            
        return jsonify({
            "user_id": user["user_id"],
            "name": user["full_name"],
            "balance": user["balance"]
        })
    except Exception as e:
        logger.error(f"Error in api_get_user: {e}")
        return jsonify({"error": "حدث خطأ داخلي في السيرفر"}), 500

@flask_app.route("/api/play_game", methods=["POST"])
def api_play_game():
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        bet = float(data.get("bet", 0))
        game_type = data.get("game", "wheel")
        init_data = data.get("init_data", "")

        if init_data and not verify_telegram_webapp_data(init_data, BOT_TOKEN):
            return jsonify({"error": "فشل التحقق الأمني من الجلسة"}), 403

        if not user_id or bet <= 0:
            return jsonify({"error": "بيانات الرهان غير صحيحة"}), 400

        if int(bet) not in ALLOWED_STRIKE_PRICES:
            return jsonify({"error": "سعر الضربة المختار غير مسموح به"}), 400

        if game_type not in ALLOWED_GAMES:
            return jsonify({"error": "نوع اللعبة غير مدعوم"}), 400

        conn = get_db()
        user = conn.execute("SELECT balance, is_banned, games_played, custom_boost FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        if not user or user["is_banned"]:
            conn.close()
            return jsonify({"error": "الحساب محظور أو غير موجود"}), 403

        if user["balance"] < bet:
            conn.close()
            return jsonify({"error": "رصيدك غير كافٍ لتغطية هذه الضربة"}), 400

        new_balance = user["balance"] - bet
        conn.execute("UPDATE users SET balance = ?, games_played = games_played + 1 WHERE user_id = ?", (new_balance, user_id))

        algo_setting = conn.execute("SELECT value FROM settings WHERE key = 'game_algorithm'").fetchone()
        algo_val = algo_setting["value"] if algo_setting else "35"

        preset_rates = {
            "forced_loss": 0.0,
            "very_low": 0.10,
            "low": 0.20,
            "normal": 0.35,
            "balanced": 0.50,
            "high": 0.70,
            "forced_win": 1.00
        }

        if algo_val in preset_rates:
            base_chance = preset_rates[algo_val]
        else:
            try:
                base_chance = float(algo_val) / 100.0
            except ValueError:
                base_chance = 0.35

        user_boost = user["custom_boost"] or 0.0
        final_win_chance = max(0.0, min(1.0, base_chance + (user_boost / 100.0)))

        is_win = random.random() < final_win_chance
        chosen_multiplier = 0
        win_amount = 0

        if is_win:
            chosen_multiplier = random.choices(MULTIPLIERS, weights=MULTIPLIER_WEIGHTS)[0]
            win_amount = round(bet * chosen_multiplier, 2)
            new_balance += win_amount
            
            conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                         (user_id, f"فوز في {game_type} (x{chosen_multiplier})", win_amount))
        else:
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                         (user_id, f"خسارة ضربة في {game_type}", -bet))

        conn.commit()
        conn.close()

        return jsonify({
            "win": is_win,
            "multiplier": chosen_multiplier if is_win else 0,
            "win_amount": win_amount,
            "new_balance": new_balance,
            "game": game_type
        })
    except Exception as e:
        logger.error(f"Error in api_play_game: {e}")
        return jsonify({"error": f"خطأ في السيرفر: {str(e)}"}), 500

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ----------------------------------------------------
# 6. لوحات التحكم والأوامر (Telegram Engine)
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    keyboard = [
        [InlineKeyboardButton("🔥 صالة ألعاب 1xBet VIP (10 ألعاب)", web_app=WebAppInfo(url=games_url))],
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
        [InlineKeyboardButton("⚙️ خوارزمية أرباح البوت (RTP)", callback_data="adm_algo"), InlineKeyboardButton("🎯 حظ لاعب معين", callback_data="adm_user_boost")],
        [InlineKeyboardButton("📢 إعداد قنوات الاشتراك الإجباري", callback_data="adm_channels_menu")],
        [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_make_gift"), InlineKeyboardButton("🔗 سعر الإحالة", callback_data="adm_set_ref")],
        [InlineKeyboardButton("💸 الحد الأدنى للسحب", callback_data="adm_set_min_w"), InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_set_welcome")],
        [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_info"), InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban")],
        [InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban"), InlineKeyboardButton("📢 رسالة جماعية (نص)", callback_data="adm_bc_txt")],
        [InlineKeyboardButton("📸 رسالة جماعية (صورة)", callback_data="adm_bc_img"), InlineKeyboardButton("📩 رسالة خاصة (نص)", callback_data="adm_pm_txt")],
        [InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats"), InlineKeyboardButton("📜 سجلات العملاء", callback_data="adm_all_logs")],
        [InlineKeyboardButton("📥 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    conn = get_db()
    admins = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    for adm in admins:
        try:
            await context.bot.send_message(chat_id=adm["user_id"], text=text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            pass

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

    is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
    if not is_subscribed:
        await update.message.reply_text(
            "⚠️ **عذراً عزيزي العميل!**\n\n"
            "يرجى الاشتراك بالقنوات التالية لاستخدام البوت والألعاب المتاحة:",
            reply_markup=build_sub_keyboard(unsubscribed),
            parse_mode="Markdown"
        )
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None

    if u and u["is_banned"]:
        await update.message.reply_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return

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
            f"👋 أهلاً بك يا {user.full_name} في كازينو VIP!\n\n"
            f"🛡️ للتأكد من أنك لست روبوت، يرجى كتابة الناتج:\n"
            f"❓ **{num1} + {num2} = ?**"
        )
        return

    conn.close()

    if not u["is_verified"]:
        if u["step"] == "captcha":
            await update.message.reply_text("⚠️ يرجى حل كود الكابتشا أولاً بكتابة النتيجة.")
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
        f"👑 **مرحباً بك في كازينو VIP العالمي (10 ألعاب 1xBet)**\n\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🆔 **معرف الحساب (ID):** `{user_id}`\n"
        f"💰 **رصيدك الحالي:** `{bal:,.2f}` ليرة سورية جديدة\n\n"
        f"اضغط على زر (صالة ألعاب 1xBet VIP) للبدء باللعب المباشر:"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin))

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
# 7. معالج الصور (للإذاعة الجماعية بالصور)
# ----------------------------------------------------
async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    u = conn.execute("SELECT step FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if u and u["step"] == "adm_input_bc_img":
        photo_file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        
        count = 0
        for u_item in users_list:
            try:
                await context.bot.send_photo(chat_id=u_item["user_id"], photo=photo_file_id, caption=caption, parse_mode="Markdown")
                count += 1
            except Exception:
                pass
        await update.message.reply_text(f"📸 تم إرسال الصورة الجماعية لـ `{count}` مستخدم بنجاح.")
    
    conn.close()

# ----------------------------------------------------
# 8. معالجة الرسائل النصية والمدخلات
# ----------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""
    
    is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
    if not is_subscribed:
        await update.message.reply_text(
            "⚠️ **عذراً عزيزي العميل!**\n\nيرجى الاشتراك بالقنوات التالية أولاً لتتمكن من استخدام البوت:",
            reply_markup=build_sub_keyboard(unsubscribed),
            parse_mode="Markdown"
        )
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if not u or u["is_banned"]:
        conn.close()
        return

    step = u["step"]

    if step == "captcha":
        if text.isdigit() and int(text) == u["captcha_answer"]:
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            conn.execute("UPDATE users SET step = 'phone' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ إجابة صحيحة! يرجى مشاركة رقم هاتفك للتأكيد:", reply_markup=btn)
        else:
            conn.close()
            await update.message.reply_text("❌ إجابة خاطئة! يرجى كتابة الرقم المطلوب بدقة.")
        return

    if step == "withdraw_step_code":
        context.user_data["withdraw_code"] = text
        conn.execute("UPDATE users SET step = 'withdraw_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم حفظ كود/رقم الحساب.\n\n✍️ **أدخل المبلغ المراد سحبه (ليرة جديدة):**")
        return

    if step == "withdraw_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ يرجى إدخال مبلغ مالي صحيح بالأرقام فقط.")
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
            f"🔢 **الكود / الرقم:** `{acc_code}`\n"
            f"💰 **المبلغ:** `{amt}` ليرة جديدة", reply_markup=kb)
        return

    if step == "input_gift_code":
        g = conn.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if not g or g["uses_left"] <= 0:
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("❌ الكود غير صحيح أو انتهت عدد مرات استخدامه.")
            return

        amt = g["amount"]
        new_b = u["balance"] + amt
        uses = g["uses_left"] - 1

        conn.execute("UPDATE users SET balance = ?, step = 'main' WHERE user_id = ?", (new_b, user.id))
        if uses > 0:
            conn.execute("UPDATE gift_codes SET uses_left = ? WHERE code = ?", (uses, text))
        else:
            conn.execute("DELETE FROM gift_codes WHERE code = ?", (text,))
        
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, f"استخدام كود هدية {text}", amt))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` ليرة جديدة إلى رصيدك!")
        return

    # خطوة رسالة الدعم
    if step == "input_support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم، وسيتم الرد عليك قريباً.")
        await notify_admins(context, f"💬 **رسالة دعم جديدة من:** {user.full_name} (`{user.id}`)\n\nالرسالة:\n{text}")
        return

    # الأوامر والخطوات الإدارية
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None
    if is_admin:
        if step == "adm_input_add_ch_id":
            context.user_data["new_ch_id"] = text
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_title' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل اسم القناة الذي سيظهر للعميل:**")
            return

        if step == "adm_input_add_ch_title":
            context.user_data["new_ch_title"] = text
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_link' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل رابط القناة (رابط الدعوة/العام):**")
            return

        if step == "adm_input_add_ch_link":
            ch_id = context.user_data.get("new_ch_id")
            ch_title = context.user_data.get("new_ch_title")
            ch_link = text

            conn.execute("INSERT OR REPLACE INTO channels (channel_id, channel_title, channel_link) VALUES (?, ?, ?)",
                         (ch_id, ch_title, ch_link))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إضافة القناة ({ch_title}) بنجاح لقائمة الإجباريات!")
            return

        if step == "adm_input_user_boost_id":
            context.user_data["boost_user_id"] = text
            conn.execute("UPDATE users SET step = 'adm_input_user_boost_val' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل نسبة تعديل الحظ (مثال: 20 لزيادة الحظ 20%، أو -30 لإنقاص الحظ 30%):**")
            return

        if step == "adm_input_user_boost_val":
            try:
                target_id = int(context.user_data.get("boost_user_id"))
                boost_val = float(text)
                
                conn.execute("UPDATE users SET custom_boost = ? WHERE user_id = ?", (boost_val, target_id))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم ضبط تعديل الحظ للمستخدم `{target_id}` بنسبة `{boost_val}%` بنجاح.")
            except Exception as e:
                conn.close()
                await update.message.reply_text(f"❌ حدث خطأ في البيانات المدخلة: {e}")
            return

        if step == "adm_input_add_bal":
            try:
                parts = text.split()
                target_id, amt = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, target_id))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (target_id, "إضافة رصيد من الإدارة", amt))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إضافة `{amt}` ليرة لرصيد المستخدم `{target_id}`.")
                try: await context.bot.send_message(target_id, f"🎁 تم إضافة `{amt}` ليرة سورية لرصيدك من قبل الإدارة!")
                except: pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. يرجى كتابة ID ثم مسافة ثم المبلغ (مثال: `7255100997 500`).")
            return

        if step == "adm_input_sub_bal":
            try:
                parts = text.split()
                target_id, amt = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amt, target_id))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (target_id, "خصم رصيد من الإدارة", -amt))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم خصم `{amt}` ليرة من رصيد المستخدم `{target_id}`.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. اكتب ID ثم مسافة ثم المبلغ.")
            return

        if step == "adm_input_make_gift":
            try:
                parts = text.split()
                code_str, amt, uses = parts[0], float(parts[1]), int(parts[2])
                conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code_str, amt, uses))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"🎁 تم إنتاج الكود: `{code_str}` بقيمة `{amt}` وبعدد مرات استخدام: `{uses}`.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. اكتب: الكود المبلغ عدد_المرات (مثال: `VIP100 100 10`).")
            return

        if step == "adm_input_set_ref":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_reward', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل مكافأة الإحالة إلى `{val}` ليرة سورية.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى أدخال رقم صحيح فقط.")
            return

        if step == "adm_input_set_min_w":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('min_withdraw', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل حد السحب الأدنى إلى `{val}` ليرة سورية.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
            return

        if step == "adm_input_set_welcome":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('welcome_bonus', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى `{val}` ليرة سورية.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى أدخال رقم صحيح.")
            return

        if step == "adm_input_user_info":
            try:
                tid = int(text)
                info = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
                if not info:
                    await update.message.reply_text("❌ هذا المستخدم غير موجود في قاعدة البيانات.")
                else:
                    await update.message.reply_text(
                        f"👤 **معلومات العميل:**\n"
                        f"🆔 **ID:** `{info['user_id']}`\n"
                        f"✏️ **الاسم:** {info['full_name']}\n"
                        f"📱 **الهاتف:** `{info['phone']}`\n"
                        f"💰 **الرصيد:** `{info['balance']}` ليرة\n"
                        f"👥 **عدد الإحالات:** `{info['referrals_count']}`\n"
                        f"🎮 **عدد الضربات/الألعاب:** `{info['games_played']}`\n"
                        f"🎯 **تعديل الحظ الخاص:** `{info['custom_boost']}%`\n"
                        f"🚫 **الحالة:** {'محظور' if info['is_banned'] else 'نشط'}"
                    )
            except Exception:
                await update.message.reply_text("❌ يرجى أدخال ID بصيغة أرقام فقط.")
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

        if step == "adm_input_ban":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET is_banned = 1, step = 'main' WHERE user_id = ?", (tid,))
                conn.commit()
                await update.message.reply_text(f"🚫 تم حظر المستخدم `{tid}` بنجاح.")
            except Exception:
                await update.message.reply_text("❌ أدخل ID صحيح.")
            conn.close()
            return

        if step == "adm_input_unban":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET is_banned = 0, step = 'main' WHERE user_id = ?", (tid,))
                conn.commit()
                await update.message.reply_text(f"✅ تم فك الحظر عن المستخدم `{tid}`.")
            except Exception:
                await update.message.reply_text("❌ أدخل ID صحيح.")
            conn.close()
            return

        if step == "adm_input_bc_txt":
            users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            
            count = 0
            for u_item in users_list:
                try:
                    await context.bot.send_message(chat_id=u_item["user_id"], text=text, parse_mode="Markdown")
                    count += 1
                except Exception: pass
            await update.message.reply_text(f"📢 تم إرسال الإذاعة النصية لـ `{count}` مستخدم.")
            return

        if step == "adm_input_pm_txt":
            try:
                parts = text.split(" ", 1)
                tid, msg_content = int(parts[0]), parts[1]
                await context.bot.send_message(chat_id=tid, text=f"💬 **رسالة خاصة من الإدارة:**\n\n{msg_content}", parse_mode="Markdown")
                await update.message.reply_text(f"✅ تم إرسال الرسالة للمستخدم `{tid}` بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطأ في الإرسال: {e}")
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

    conn.close()

# ----------------------------------------------------
# 9. معالجة النقرات (Callback Queries)
# ----------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data

    if data == "check_subscription_status":
        is_sub, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
        if is_sub:
            await query.message.delete()
            await query.message.reply_text("✅ شكرًا لاشتراكك بالقنوات! يمكنك الآن استخدام البوت بشكل كامل.")
            conn = get_db()
            is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None
            conn.close()
            await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
        else:
            await query.message.edit_text(
                "⚠️ **لم تقم بالاشتراك بكافة القنوات المطلوب الاشتراك بها بعد!**\n\nيرجى الاشتراك أولاً:",
                reply_markup=build_sub_keyboard(unsubscribed),
                parse_mode="Markdown"
            )
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None

    if not u or u["is_banned"]:
        conn.close()
        return

    if data == "back_to_main":
        conn.close()
        await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
        return

    if data == "btn_account":
        msg = (
            f"👤 **بيانات حسابك الشخصي:**\n\n"
            f"✏️ **الاسم:** {u['full_name']}\n"
            f"🆔 **معرف الحساب (ID):** `{u['user_id']}`\n"
            f"📱 **رقم الهاتف:** `{u['phone'] or 'غير مرتيط'}`\n"
            f"💰 **الرصيد المتاح:** `{u['balance']:,.2f}` ليرة جديدة\n"
            f"👥 **عدد إحالاتك الناجحة:** `{u['referrals_count']}`\n"
            f"🎮 **عدد الضربات الملعبوبة:** `{u['games_played']}`"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        conn.close()
        return

    if data == "btn_withdraw":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش (Syriatel Cash)", callback_data="w_meth_Syriatel Cash")],
            [InlineKeyboardButton("📱 إم تي إن كاش (MTN Cash)", callback_data="w_meth_MTN Cash")],
            [InlineKeyboardButton("💳 شام كاش / كارت مصرفي", callback_data="w_meth_Bank Cham Cash")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ])
        min_w = conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"]
        await query.message.edit_text(
            f"💸 **قسم سحب الأرباح والعمولات:**\n\n"
            f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` ليرة جديدة\n"
            f"⚠️ **الحد الأدنى للسحب:** `{min_w}` ليرة جديدة\n\n"
            f"يرجى اختيار وسيلة السحب المناسبة لك:",
            parse_mode="Markdown",
            reply_markup=kb
        )
        conn.close()
        return

    if data.startswith("w_meth_"):
        method = data.replace("w_meth_", "")
        context.user_data["withdraw_method"] = method
        conn.execute("UPDATE users SET step = 'withdraw_step_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text(f"✍️ **طريقة السحب المختارة:** {method}\n\nيرجى كتابة رقم الحساب أو كود المحفظة للتحويل إليه:")
        return

    if data == "btn_referral":
        ref_reward = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        
        msg = (
            f"🔗 **نظام الإحالة ودعوة الأصدقاء:**\n\n"
            f"احصل على `{ref_reward}` ليرة سورية جديدة فوراً عن كل صديق يقوم بالتسجيل وتوثيق حسابه عبر رابطك الخاص!\n\n"
            f"👥 **عدد إحالاتك الحالية:** `{u['referrals_count']}`\n"
            f"🔗 **رابط الدعوة الخاص بك:**\n`{ref_link}`"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        conn.close()
        return

    if data == "btn_gift":
        conn.execute("UPDATE users SET step = 'input_gift_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("🎁 **أدخل كود الهدية أو الرمز الترويجي الآن:**")
        return

    if data == "btn_logs":
        logs = conn.execute("SELECT action, amount, timestamp FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
        conn.close()
        
        if not logs:
            txt = "📜 لا توجد أي سجلات حركة مسبقة في حسابك."
        else:
            txt = "📜 **آخر 10 عمليات في حسابك:**\n\n"
            for lg in logs:
                txt += f"• `{lg['timestamp']}` | {lg['action']} | `{lg['amount']}` ليرة\n"
                
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "btn_support":
        conn.execute("UPDATE users SET step = 'input_support_msg' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("💬 **اكتب رسالتك الآن وسيقوم فريق الدعم الفني بالرد عليك في أسرع وقت:**")
        return

    if data == "btn_buy_bot":
        conn.close()
        msg = "🤖 **لشراء بوت كازينو أو تطوير خدمات خاصة بك، يرجى التواصل مع المبرمج:**\n\n📢 **قناة المبرمج:** @lerafree"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        return

    # ------------------ لوحة الأدمن (Call Actions) ------------------
    if is_admin:
        if data == "open_admin_panel":
            conn.close()
            await query.message.edit_text("⚙️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_algo":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 خسارة حتمية (0%)", callback_data="set_algo_forced_loss"), InlineKeyboardButton("📉 منخفضة جداً (10%)", callback_data="set_algo_very_low")],
                [InlineKeyboardButton("📉 منخفضة (20%)", callback_data="set_algo_low"), InlineKeyboardButton("⚖️ متوسطة متوازنة (35%)", callback_data="set_algo_normal")],
                [InlineKeyboardButton("📈 مرتفعة (50%)", callback_data="set_algo_balanced"), InlineKeyboardButton("🔥 عالية جداً (70%)", callback_data="set_algo_high")],
                [InlineKeyboardButton("💎 فوز حتمي (100%)", callback_data="set_algo_forced_win")],
                [InlineKeyboardButton("🔙 رجوع للوحة", callback_data="open_admin_panel")]
            ])
            cur_a = conn.execute("SELECT value FROM settings WHERE key='game_algorithm'").fetchone()["value"]
            await query.message.edit_text(f"⚙️ **ضبط خوارزمية الربح والخسارة (RTP العامة):**\n\nالضبط الحالي: `{cur_a}`\n\nاختر الإعداد الجديد:", parse_mode="Markdown", reply_markup=kb)
            conn.close()
            return

        if data.startswith("set_algo_"):
            new_val = data.replace("set_algo_", "")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('game_algorithm', ?)", (new_val,))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"✅ تم ضبط الخوارزمية بنجاح إلى: `{new_val}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]]))
            return

        if data == "adm_channels_menu":
            channels = conn.execute("SELECT * FROM channels").fetchall()
            kb = [[InlineKeyboardButton("➕ إضافة قناة إجبارية جديد", callback_data="adm_add_channel")]]
            for ch in channels:
                kb.append([InlineKeyboardButton(f"❌ حذف: {ch['channel_title']}", callback_data=f"adm_del_ch_{ch['channel_id']}")])
            kb.append([InlineKeyboardButton("🔙 رجوع للوحة", callback_data="open_admin_panel")])
            
            await query.message.edit_text("📢 **إدارة قنوات الاشتراك الإجباري:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            conn.close()
            return

        if data == "adm_add_channel":
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_id' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل معرّف القناة ID (مثال: `@mychannel` أو `-100123456789`):**")
            return

        if data.startswith("adm_del_ch_"):
            ch_id = data.replace("adm_del_ch_", "")
            conn.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم حذف القناة بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_channels_menu")]]))
            return

        if data == "adm_user_boost":
            conn.execute("UPDATE users SET step = 'adm_input_user_boost_id' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم المراد تعديل حظه الخاص:**")
            return

        if data == "adm_add_bal":
            conn.execute("UPDATE users SET step = 'adm_input_add_bal' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم ثم مسافة ثم المبلغ المراد إضافته:**\nمثال: `7255100997 1000`")
            return

        if data == "adm_sub_bal":
            conn.execute("UPDATE users SET step = 'adm_input_sub_bal' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم ثم مسافة ثم المبلغ المراد خصمه:**")
            return

        if data == "adm_make_gift":
            conn.execute("UPDATE users SET step = 'adm_input_make_gift' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل بيانات الكود بالشكل التالي:**\n`الكود المبلغ عدد_مرات_الاستخدام`\nمثال: `VIP100 500 10`")
            return

        if data == "adm_set_ref":
            conn.execute("UPDATE users SET step = 'adm_input_set_ref' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل القيمة الجديدة لمكافأة الإحالة (بالليرة):**")
            return

        if data == "adm_set_min_w":
            conn.execute("UPDATE users SET step = 'adm_input_set_min_w' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل الحد الأدنى الجديد للسحب (بالليرة):**")
            return

        if data == "adm_set_welcome":
            conn.execute("UPDATE users SET step = 'adm_input_set_welcome' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل قيمة البونص الترحيبي الجديد (بالليرة):**")
            return

        if data == "adm_user_info":
            conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم للبحث عن كافة بياناته:**")
            return

        if data == "adm_ban":
            conn.execute("UPDATE users SET step = 'adm_input_ban' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم المراد حظره:**")
            return

        if data == "adm_unban":
            conn.execute("UPDATE users SET step = 'adm_input_unban' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم المراد فك الحظر عنه:**")
            return

        if data == "adm_bc_txt":
            conn.execute("UPDATE users SET step = 'adm_input_bc_txt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل النص المراد إرساله كإذاعة جماعية لكافة المستخدمين:**")
            return

        if data == "adm_bc_img":
            conn.execute("UPDATE users SET step = 'adm_input_bc_img' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("📸 **أرسل الآن الصورة مع الكابشن المراد إرسالهما كإذاعة جماعية:**")
            return

        if data == "adm_pm_txt":
            conn.execute("UPDATE users SET step = 'adm_input_pm_txt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستلم ثم مسافة ثم النص:**\nمثال: `7255100997 تم تحويل أرباحك`")
            return

        if data == "adm_stats":
            u_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            w_sum = conn.execute("SELECT SUM(amount) as s FROM withdrawals WHERE status = 'approved'").fetchone()["s"] or 0.0
            p_sum = conn.execute("SELECT SUM(balance) as s FROM users").fetchone()["s"] or 0.0
            
            msg = (
                f"📊 **إحصائيات البوت الشاملة:**\n\n"
                f"👥 **إجمالي المستخدمين:** `{u_count}`\n"
                f"💰 **إجمالي الأرصدة المتوفرة بالحسابات:** `{p_sum:,.2f}` ليرة\n"
                f"💸 **إجمالي السحوبات المقبولة:** `{w_sum:,.2f}` ليرة"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]])
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
            conn.close()
            return

        if data == "adm_withdraws":
            pending = conn.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
            
            if not pending:
                await query.message.edit_text("📥 لا توجد طلبات سحب معلقة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]]))
                return

            for w in pending:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w['id']}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد", callback_data=f"rej_w_{w['id']}")]
                ])
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"📥 **طلب سحب رقم (# {w['id']}):**\n🆔 **ID:** `{w['user_id']}`\n💳 **الطريقة:** {w['method']}\n🔢 **الكود:** `{w['account_code']}`\n💰 **المبلغ:** `{w['amount']}` ليرة",
                    parse_mode="Markdown",
                    reply_markup=kb
                )
            return

        if data.startswith("app_w_"):
            wid = int(data.replace("app_w_", ""))
            w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (wid,)).fetchone()
            if w and w["status"] == "pending":
                conn.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (wid,))
                conn.commit()
                await query.message.edit_text(f"✅ تم الموافقة على الطلب رقم #{wid} بنجاح.")
                try: await context.bot.send_message(w["user_id"], f"✅ تم الموافقة على طلب سحب المبلغ `{w['amount']}` ليرة وتحويله بنجاح!")
                except: pass
            conn.close()
            return

        if data.startswith("rej_w_"):
            wid = int(data.replace("rej_w_", ""))
            w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (wid,)).fetchone()
            if w and w["status"] == "pending":
                conn.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (wid,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w["amount"], w["user_id"]))
                conn.commit()
                await query.message.edit_text(f"❌ تم رفض الطلب رقم #{wid} وإعادة المبلغ لحساب العميل.")
                try: await context.bot.send_message(w["user_id"], f"❌ تم رفض طلب السحب الخاص بك لمبلغ `{w['amount']}` ليرة وإعادة الرصيد إلى حسابك.")
                except: pass
            conn.close()
            return

    conn.close()

# ----------------------------------------------------
# 10. تشغيل التطبيق (Flask + Telegram Bot)
# ----------------------------------------------------
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot & Flask Engine starting successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
