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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8842721926:AAFn7HGsi7MPsPO7KtN4Z9PE5lj-j6OOhvY")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "7255100997"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

ALLOWED_STRIKE_PRICES = [3, 6, 9, 12, 15, 20, 50, 100]
MULTIPLIERS = [1, 2, 4, 5, 8, 10, 20, 100]
MULTIPLIER_WEIGHTS = [40, 25, 15, 10, 5, 3, 1.8, 0.2]

ALLOWED_GAMES = [
    'wheel', 'aviator', 'mines', 'slots', 'chests', 
    'dice', 'coinflip', 'cards', 'thimbles', 'roulette', 'goold_lera'
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
            consecutive_losses INTEGER DEFAULT 0,
            consecutive_wins INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            terms_accepted INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            captcha_answer INTEGER DEFAULT 0,
            step TEXT DEFAULT 'start',
            custom_boost REAL DEFAULT 0.0
        )
    ''')
    
    # تحديثات الأعمدة في حال لم تكن موجودة سابقاً
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_boost REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN consecutive_losses INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN consecutive_wins INTEGER DEFAULT 0")
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
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('game_algorithm', 'normal')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_win_chance', '35')") # نسبة الربح العامة الافتراضية 35%

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
# 5. خادم Flask API والألعاب وخوارزمية التحكم الذكية
# ----------------------------------------------------
flask_app = Flask(__name__, template_folder="templates")

@flask_app.route("/")
def home():
    return "goold Lera Casino Engine Running!"

@flask_app.route("/games")
def games_page():
    return render_template("index.html", server_url=SERVER_URL)

@flask_app.route("/api/get_user_data", methods=["POST"])
def api_get_user():
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        init_data = data.get("init_data", "")

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
        game_type = data.get("game", "goold_lera")
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
        user = conn.execute(
            "SELECT balance, is_banned, games_played, custom_boost, consecutive_losses, consecutive_wins FROM users WHERE user_id = ?", 
            (user_id,)
        ).fetchone()
        
        if not user or user["is_banned"]:
            conn.close()
            return jsonify({"error": "الحساب محظور أو غير موجود"}), 403

        if user["balance"] < bet:
            conn.close()
            return jsonify({"error": "رصيدك غير كافٍ لتغطية هذه الضربة"}), 400

        # خصم مبلغ الرهان الأولي
        new_balance = user["balance"] - bet

        # ------------------------------------
        # الخوارزمية المتقدمة للربح والخسارة
        # ------------------------------------
        global_setting = conn.execute("SELECT value FROM settings WHERE key = 'global_win_chance'").fetchone()
        global_win_pct = float(global_setting["value"]) if global_setting else 35.0

        user_boost = user["custom_boost"] or 0.0
        c_losses = user["consecutive_losses"] or 0
        c_wins = user["consecutive_wins"] or 0

        # تعديل ديناميكي لنسبة الحظ حسب السلسلة
        streak_modifier = 0.0
        if c_losses >= 3:
            streak_modifier += min(15.0, c_losses * 2.5)  # تعويض اللاعب لخسرانه المتتالي
        elif c_wins >= 3:
            streak_modifier -= min(15.0, c_wins * 3.0)   # كبح حظ اللاعب المتميز بسلسلة فوز

        calculated_chance = global_win_pct + user_boost + streak_modifier
        final_win_chance = max(0.0, min(95.0, calculated_chance)) / 100.0

        is_win = random.random() < final_win_chance
        chosen_multiplier = 0
        win_amount = 0

        if is_win:
            chosen_multiplier = random.choices(MULTIPLIERS, weights=MULTIPLIER_WEIGHTS)[0]
            win_amount = round(bet * chosen_multiplier, 2)
            new_balance += win_amount
            
            c_wins_new = c_wins + 1
            c_losses_new = 0

            conn.execute(
                "UPDATE users SET balance = ?, games_played = games_played + 1, consecutive_wins = ?, consecutive_losses = ? WHERE user_id = ?",
                (new_balance, c_wins_new, c_losses_new, user_id)
            )
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                         (user_id, f"فوز في {game_type} (x{chosen_multiplier})", win_amount))
        else:
            c_losses_new = c_losses + 1
            c_wins_new = 0

            conn.execute(
                "UPDATE users SET balance = ?, games_played = games_played + 1, consecutive_losses = ?, consecutive_wins = ? WHERE user_id = ?",
                (new_balance, c_losses_new, c_wins_new, user_id)
            )
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
        [InlineKeyboardButton("goold Lera", web_app=WebAppInfo(url=games_url))],
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
        [InlineKeyboardButton("🎛️ نسبة الربح والخسارة العامة (%)", callback_data="adm_global_rtp")],
        [InlineKeyboardButton("🎯 حظ لاعب معين", callback_data="adm_user_boost"), InlineKeyboardButton("📢 قنوات الاشتراك", callback_data="adm_channels_menu")],
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
            "يرجى الاشتراك بالقنوات التالية لاستخدام البوت:",
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
            f"👋 أهلاً بك يا {user.full_name} في لعبة goold Lera!\n\n"
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
            await update.message.reply_text("📱 يرجى مشاركة رقمك للتوثيق والبدء:", reply_markup=btn)
            return

    await send_main_dashboard(chat_id, user.id, user.full_name, is_admin, context)

async def send_main_dashboard(chat_id, user_id, full_name, is_admin, context):
    conn = get_db()
    u = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    bal = u["balance"] if u else 0.0
    text = (
        f"👑 **مرحباً بك في لعبة goold Lera**\n\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🆔 **معرف الحساب (ID):** `{user_id}`\n"
        f"💰 **رصيدك الحالي:** `{bal:,.2f}` NSP\n\n"
        f"اضغط على زر اللعبة أدناه للبدء:"
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
                await context.bot.send_message(u["referred_by"], f"🎉 قام المستخدم {user.full_name} بالتسجيل عبر رابطك!")
            except Exception: pass

    conn.commit()
    conn.close()

    is_admin = (user.id == DEFAULT_ADMIN_ID)
    await update.message.reply_text(
        f"✅ تم تأكيد حسابك ورقم هاتفك بنجاح!\n"
        f"🎁 حصلت على بونص ترحيبي قدره `{welcome_bonus}` NSP.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin, context)

# ----------------------------------------------------
# 7. معالج الصور (للإذاعة الجماعية)
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
# 8. معالجة الرسائل النصية والمدخلات الإدارية والعامة
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
        await update.message.reply_text("✅ تم حفظ الحساب.\n\n✍️ **أدخل المبلغ المراد سحبه (NSP):**")
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
            await update.message.reply_text(f"❌ الحد الأدنى المسموح به للسحب هو `{min_w}` NSP.")
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

        await update.message.reply_text("✅ تم تقديم طلب السحب بنجاح وهو قيد المراجعة.")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد", callback_data=f"rej_w_{w_id}")]
        ])
        await notify_admins(context, 
            f"📥 **طلب سحب جديد (# {w_id}):**\n"
            f"👤 **اللاعب:** {user.full_name} (`{user.id}`)\n"
            f"💳 **الطريقة:** {method}\n"
            f"🔢 **الكود / الرقم:** `{acc_code}`\n"
            f"💰 **المبلغ:** `{amt}` NSP", reply_markup=kb)
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
        await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` NSP إلى رصيدك!")
        return

    if step == "input_support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم.")
        await notify_admins(context, f"💬 **رسالة دعم جديدة من:** {user.full_name} (`{user.id}`)\n\nالرسالة:\n{text}")
        return

    is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None
    if is_admin:
        if step == "adm_input_global_rtp":
            try:
                pct = float(text)
                if pct < 0 or pct > 100:
                    raise ValueError("النسبة يجب أن تكون بين 0 و 100")
                
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_win_chance', ?)", (str(pct),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تحديث نسبة الربح العامة للعبة لتصبح بنسبة `{pct}%` لكل الدورات.")
            except Exception:
                conn.close()
                await update.message.reply_text(f"❌ خطأ: يرجى إدخال رقم صحيح بين 0 و 100. (مثال: `35`)")
            return

        if step == "adm_input_add_ch_id":
            context.user_data["new_ch_id"] = text
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_title' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل اسم القناة:**")
            return

        if step == "adm_input_add_ch_title":
            context.user_data["new_ch_title"] = text
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_link' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل رابط القناة:**")
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
            await update.message.reply_text(f"✅ تم إضافة القناة ({ch_title}) بنجاح!")
            return

        if step == "adm_input_user_boost_id":
            context.user_data["boost_user_id"] = text
            conn.execute("UPDATE users SET step = 'adm_input_user_boost_val' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل نسبة تعديل الحظ (مثال: 20 أو -20):**")
            return

        if step == "adm_input_user_boost_val":
            try:
                target_id = int(context.user_data.get("boost_user_id"))
                boost_val = float(text)
                
                conn.execute("UPDATE users SET custom_boost = ? WHERE user_id = ?", (boost_val, target_id))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم ضبط الحظ للمستخدم `{target_id}` بنسبة `{boost_val}%` بنجاح.")
            except Exception as e:
                conn.close()
                await update.message.reply_text(f"❌ خطأ: {e}")
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
                await update.message.reply_text(f"✅ تم إضافة `{amt}` NSP للمستخدم `{target_id}`.")
                try: await context.bot.send_message(target_id, f"🎁 تم إضافة `{amt}` NSP لرصيدك من الإدارة!")
                except: pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. مثال: `7255100997 500`")
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
                await update.message.reply_text(f"✅ تم خصم `{amt}` NSP من المستخدم `{target_id}`.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة.")
            return

        if step == "adm_input_make_gift":
            try:
                parts = text.split()
                code_str, amt, uses = parts[0], float(parts[1]), int(parts[2])
                conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code_str, amt, uses))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"🎁 تم إنتاج الكود: `{code_str}` بقيمة `{amt}` NSP.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. مثال: `VIP100 500 10`")
            return

        if step == "adm_input_set_ref":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_reward', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل مكافأة الإحالة إلى `{val}` NSP.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
            return

        if step == "adm_input_set_min_w":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('min_withdraw', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل حد السحب إلى `{val}` NSP.")
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
                await update.message.reply_text(f"✅ تم تعديل البونص الترحيبي إلى `{val}` NSP.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
            return

        if step == "adm_input_user_info":
            try:
                tid = int(text)
                info = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
                if not info:
                    await update.message.reply_text("❌ المستخدم غير موجود.")
                else:
                    await update.message.reply_text(
                        f"👤 **معلومات العميل:**\n"
                        f"🆔 **ID:** `{info['user_id']}`\n"
                        f"✏️ **الاسم:** {info['full_name']}\n"
                        f"📱 **الهاتف:** `{info['phone']}`\n"
                        f"💰 **الرصيد:** `{info['balance']}` NSP\n"
                        f"👥 **الإحالات:** `{info['referrals_count']}`\n"
                        f"🎮 **الضربات:** `{info['games_played']}`\n"
                        f"🎯 **تعديل الحظ:** `{info['custom_boost']}%`\n"
                        f"🚫 **الحالة:** {'محظور' if info['is_banned'] else 'نشط'}"
                    )
            except Exception:
                await update.message.reply_text("❌ أدخل ID صحيح بالأرقام.")
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

        if step == "adm_input_ban":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET is_banned = 1, step = 'main' WHERE user_id = ?", (tid,))
                conn.commit()
                await update.message.reply_text(f"🚫 تم حظر المستخدم `{tid}`.")
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
            await update.message.reply_text(f"📢 تم إرسال الإذاعة لـ `{count}` مستخدم.")
            return

        if step == "adm_input_pm_txt":
            try:
                parts = text.split(" ", 1)
                tid, msg_content = int(parts[0]), parts[1]
                await context.bot.send_message(chat_id=tid, text=f"💬 **رسالة خاصة من الإدارة:**\n\n{msg_content}", parse_mode="Markdown")
                await update.message.reply_text(f"✅ تم إرسال الرسالة للمستخدم `{tid}`.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطأ: {e}")
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
            await query.message.reply_text("✅ شكرًا لاشتراكك!")
            conn = get_db()
            is_admin = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user.id,)).fetchone() is not None
            conn.close()
            await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
        else:
            await query.message.edit_text(
                "⚠️ **يرجى الاشتراك بالقنوات أولاً:**",
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
            f"👤 **بيانات حسابك:**\n\n"
            f"✏️ **الاسم:** {u['full_name']}\n"
            f"🆔 **ID:** `{u['user_id']}`\n"
            f"📱 **الهاتف:** `{u['phone'] or 'غير مرتبط'}`\n"
            f"💰 **الرصيد:** `{u['balance']:,.2f}` NSP\n"
            f"👥 **الإحالات:** `{u['referrals_count']}`\n"
            f"🎮 **الضربات:** `{u['games_played']}`"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        conn.close()
        return

    if data == "btn_withdraw":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_meth_Syriatel Cash")],
            [InlineKeyboardButton("📱 إم تي إن كاش", callback_data="w_meth_MTN Cash")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="w_meth_Bank Cham Cash")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ])
        min_w = conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"]
        await query.message.edit_text(
            f"💸 **قسم سحب الأرباح:**\n\n"
            f"💰 **رصيدك:** `{u['balance']:,.2f}` NSP\n"
            f"⚠️ **الحد الأدنى:** `{min_w}` NSP\n\n"
            f"اختر وسيلة السحب:",
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
        await query.message.edit_text(f"✍️ **الطريقة:** {method}\n\nأدخل رقم الحساب أو المحفظة:")
        return

    if data == "btn_referral":
        ref_reward = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        
        msg = (
            f"🔗 **نظام الإحالة:**\n\n"
            f"احصل على `{ref_reward}` NSP عن كل صديق يسجل عبر رابطك!\n\n"
            f"👥 **إحالاتك:** `{u['referrals_count']}`\n"
            f"🔗 **رابطك:**\n`{ref_link}`"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        conn.close()
        return

    if data == "btn_gift":
        conn.execute("UPDATE users SET step = 'input_gift_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("🎁 **أدخل كود الهدية:**")
        return

    if data == "btn_logs":
        logs = conn.execute("SELECT action, amount, timestamp FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
        conn.close()
        
        if not logs:
            txt = "📜 لا توجد سجلات."
        else:
            txt = "📜 **آخر 10 عمليات:**\n\n"
            for lg in logs:
                txt += f"• `{lg['timestamp']}` | {lg['action']} | `{lg['amount']}` NSP\n"
                
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "btn_support":
        conn.execute("UPDATE users SET step = 'input_support_msg' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("💬 **اكتب رسالتك للدعم:**")
        return

    if data == "btn_buy_bot":
        conn.close()
        msg = "🤖 **لشراء بوت تواصل مع المبرمج:**\n\n📢 **قناة المبرمج:** @lerafree"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
        return

    if is_admin:
        if data == "open_admin_panel":
            conn.close()
            await query.message.edit_text("⚙️ **لوحة التحكم الإدارية:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_global_rtp":
            cur_rtp = conn.execute("SELECT value FROM settings WHERE key='global_win_chance'").fetchone()["value"]
            conn.execute("UPDATE users SET step = 'adm_input_global_rtp' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text(
                f"🎛️ **التحكم بنسبة الربح والخسارة العامة للعبة:**\n\n"
                f"📊 النسبة الحالية لفرصة الربح: `{cur_rtp}%`\n\n"
                f"✍️ أرسل الآن النسبة المئوية الجديدة المطلوبة (أرقام من 0 إلى 100):\n"
                f"*(مثال: أرسل `30` لتعيين نسبة الربح العامة 30% والخسارة 70%)*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="open_admin_panel")]])
            )
            return

        if data == "adm_channels_menu":
            channels = conn.execute("SELECT * FROM channels").fetchall()
            kb = [[InlineKeyboardButton("➕ إضافة قناة", callback_data="adm_add_channel")]]
            for ch in channels:
                kb.append([InlineKeyboardButton(f"❌ حذف: {ch['channel_title']}", callback_data=f"adm_del_ch_{ch['channel_id']}")])
            kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")])
            
            await query.message.edit_text("📢 **إدارة القنوات:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            conn.close()
            return

        if data == "adm_add_channel":
            conn.execute("UPDATE users SET step = 'adm_input_add_ch_id' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل معرّف القناة ID:**")
            return

        if data.startswith("adm_del_ch_"):
            ch_id = data.replace("adm_del_ch_", "")
            conn.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم الحذف.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_channels_menu")]]))
            return

        if data == "adm_user_boost":
            conn.execute("UPDATE users SET step = 'adm_input_user_boost_id' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم:**")
            return

        if data == "adm_add_bal":
            conn.execute("UPDATE users SET step = 'adm_input_add_bal' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID ثم مسافة ثم المبلغ للإضافة:**")
            return

        if data == "adm_sub_bal":
            conn.execute("UPDATE users SET step = 'adm_input_sub_bal' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID ثم مسافة ثم المبلغ للخصم:**")
            return

        if data == "adm_make_gift":
            conn.execute("UPDATE users SET step = 'adm_input_make_gift' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل: الكود المبلغ عدد_المرات**")
            return

        if data == "adm_set_ref":
            conn.execute("UPDATE users SET step = 'adm_input_set_ref' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل قيمة مكافأة الإحالة الجديدة:**")
            return

        if data == "adm_set_min_w":
            conn.execute("UPDATE users SET step = 'adm_input_set_min_w' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل الحد الأدنى للسحب:**")
            return

        if data == "adm_set_welcome":
            conn.execute("UPDATE users SET step = 'adm_input_set_welcome' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل البونص الترحيبي:**")
            return

        if data == "adm_user_info":
            conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم للبحث:**")
            return

        if data == "adm_ban":
            conn.execute("UPDATE users SET step = 'adm_input_ban' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID للحظر:**")
            return

        if data == "adm_unban":
            conn.execute("UPDATE users SET step = 'adm_input_unban' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID لفك الحظر:**")
            return

        if data == "adm_bc_txt":
            conn.execute("UPDATE users SET step = 'adm_input_bc_txt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل نص الإذاعة:**")
            return

        if data == "adm_bc_img":
            conn.execute("UPDATE users SET step = 'adm_input_bc_img' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("📸 **أرسل الصورة مع النص للإذاعة:**")
            return

        if data == "adm_pm_txt":
            conn.execute("UPDATE users SET step = 'adm_input_pm_txt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID ثم مسافة ثم النص:**")
            return

        if data == "adm_all_logs":
            logs = conn.execute("SELECT user_id, action, amount, timestamp FROM logs ORDER BY id DESC LIMIT 15").fetchall()
            conn.close()
            if not logs:
                txt = "📜 لا توجد سجلات عامة حتى الآن."
            else:
                txt = "📜 **آخر 15 عملية على مستوى البوت:**\n\n"
                for lg in logs:
                    txt += f"• `{lg['timestamp']}` | `{lg['user_id']}` | {lg['action']} | `{lg['amount']}` NSP\n"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]])
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=kb)
            return

        if data == "adm_stats":
            u_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            w_sum = conn.execute("SELECT SUM(amount) as s FROM withdrawals WHERE status = 'approved'").fetchone()["s"] or 0.0
            p_sum = conn.execute("SELECT SUM(balance) as s FROM users").fetchone()["s"] or 0.0
            
            msg = (
                f"📊 **الإحصائيات:**\n\n"
                f"👥 **المستخدمين:** `{u_count}`\n"
                f"💰 **الأرصدة:** `{p_sum:,.2f}` NSP\n"
                f"💸 **السحوبات:** `{w_sum:,.2f}` NSP"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]])
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
            conn.close()
            return

        if data == "adm_withdraws":
            pending = conn.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
            
            if not pending:
                await query.message.edit_text("📥 لا توجد طلبات سحب معلقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]]))
                return

            for w in pending:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w['id']}"), InlineKeyboardButton("❌ رفض وإعادة", callback_data=f"rej_w_{w['id']}")]
                ])
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"📥 **طلب سحب (# {w['id']}):**\n🆔 **ID:** `{w['user_id']}`\n💳 **الطريقة:** {w['method']}\n🔢 **الكود:** `{w['account_code']}`\n💰 **المبلغ:** `{w['amount']}` NSP",
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
                await query.message.edit_text(f"✅ تم الموافقة على الطلب #{wid}.")
                try: await context.bot.send_message(w["user_id"], f"✅ تم الموافقة على سحب `{w['amount']}` NSP!")
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
                await query.message.edit_text(f"❌ تم رفض الطلب #{wid} وإعادة الرصيد.")
                try: await context.bot.send_message(w["user_id"], f"❌ تم رفض طلب السحب وإعادة `{w['amount']}` NSP لحسابك.")
                except: pass
            conn.close()
            return

    conn.close()

# ----------------------------------------------------
# 10. تشغيل التطبيق
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
