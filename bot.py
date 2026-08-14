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
import json
import time
import urllib.request
from urllib.parse import parse_qsl
from datetime import datetime, timedelta

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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956961450:AAHw0vuGWm-ME7VVz9P6a5xOYRhd7EvEJ-0-j6OOhvY")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

# ----------------------------------------------------
# 2. إعداد قاعدة البيانات الموحدة (database.db)
# ----------------------------------------------------
DB_NAME = "database.db"

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def is_maintenance_active() -> bool:
    """فحص ما إذا كان وضع الصيانة مفعلاً"""
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='maintenance_mode'").fetchone()
        conn.close()
        return row is not None and row["value"] == "1"
    except Exception:
        return False

def is_admin_user(user_id: int) -> bool:
    """فحص هل المستخدم أدمن أم لا"""
    if user_id == DEFAULT_ADMIN_ID:
        return True
    try:
        conn = get_db()
        row = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            balance REAL DEFAULT 100.0,
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
        CREATE TABLE IF NOT EXISTS deposit_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            method_name TEXT,
            account_details TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            amount REAL,
            tx_id TEXT,
            photo_file_id TEXT,
            status TEXT DEFAULT 'pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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

    if DEFAULT_ADMIN_ID:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
        
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus', '100')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus_enabled', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_reward', '50')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', '1000')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_deposit', '50')")
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('win_rate', '30')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bonus_win_rate', '40')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bonus_cap_1', '200')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bonus_cap_2', '500')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bonus_cap_3', '1000')")
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chance_loss', '50')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chance_normal', '30')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chance_medium', '12')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chance_high', '6')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chance_huge', '2')")

    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_win_mode', 'auto')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_mult_normal', '5.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_mult_medium', '10.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_mult_high', '20.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_mult_huge', '50.0')")

    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 3. دالة إرسال رسائل تليجرام متزامنة
# ----------------------------------------------------
def send_telegram_msg_sync(chat_id, text, reply_markup=None):
    if not BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            pass
    except Exception as e:
        logger.error(f"Error in send_telegram_msg_sync: {e}")

# ----------------------------------------------------
# 4. إبقاء السيرفر نشطاً (Self-Ping)
# ----------------------------------------------------
def keep_alive():
    """نظام إبقاء السيرفر نشطاً لمنع النوم على خادم Render المجاني"""
    time.sleep(15)
    while True:
        try:
            logger.info(f"Pinging self at {SERVER_URL}...")
            req = urllib.request.Request(SERVER_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                logger.info(f"Keep-alive response code: {response.getcode()}")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        time.sleep(540) # طلب كل 9 دقائق

# ----------------------------------------------------
# 5. فحص الاشتراك الإجباري بالقنوات
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
# 6. لوحات التحكم والأوامر (Telegram Engine)
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    keyboard = [
        [InlineKeyboardButton("Golden Lera 2026 🎰", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="btn_deposit"), InlineKeyboardButton("💸 سحب رصيدي", callback_data="btn_withdraw")],
        [InlineKeyboardButton("👤 حسابي ورصيدي", callback_data="btn_account"), InlineKeyboardButton("🔗 رابط إحالاتي", callback_data="btn_referral")],
        [InlineKeyboardButton("🤖 شراء بوت", callback_data="btn_buy_bot"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="btn_gift")],
        [InlineKeyboardButton("💬 مراسلة الدعم", callback_data="btn_support"), InlineKeyboardButton("📜 سجلاتي", callback_data="btn_logs")],
        [InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if is_admin:
        keyboard.insert(1, [InlineKeyboardButton("⚙️ لوحة الإدارة الشاملة", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    maint_status = "🔴 مفعل (البوت مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
    keyboard = [
        [InlineKeyboardButton(f"🛠️ وضع الصيانة: {maint_status}", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🎛️ تحكم بخوارزميات الربح والمكافآت", callback_data="adm_algo_menu")],
        [InlineKeyboardButton("🎯 حظ لاعب معين", callback_data="adm_user_boost"), InlineKeyboardButton("📢 قنوات الاشتراك", callback_data="adm_channels_menu")],
        [InlineKeyboardButton("💳 حسابات الشحن", callback_data="adm_dep_methods"), InlineKeyboardButton("📥 طلبات الشحن", callback_data="adm_deposits")],
        [InlineKeyboardButton("💰 الحد الأدنى للشحن", callback_data="adm_set_min_dep"), InlineKeyboardButton("💸 الحد الأدنى للسحب", callback_data="adm_set_min_w")],
        [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_make_gift"), InlineKeyboardButton("🔗 سعر الإحالة", callback_data="adm_set_ref")],
        [InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_set_welcome"), InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_info")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban")],
        [InlineKeyboardButton("📢 رسالة جماعية (نص)", callback_data="adm_bc_txt"), InlineKeyboardButton("📸 رسالة جماعية (صورة)", callback_data="adm_bc_img")],
        [InlineKeyboardButton("📩 رسالة خاصة (نص)", callback_data="adm_pm_txt"), InlineKeyboardButton("👮 إضافة أدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("❌ إزالة أدمن", callback_data="adm_del_admin"), InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats")],
        [InlineKeyboardButton("📜 سجلات العملاء", callback_data="adm_all_logs"), InlineKeyboardButton("📥 طلبات السحب", callback_data="adm_withdraws")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def algo_panel_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚡ النمط المباشر (تلقائي / قفل ربح)", callback_data="adm_global_mode_menu")],
        [InlineKeyboardButton("🎯 نسبة الربح العامة (Win Rate %)", callback_data="adm_set_win_rate")],
        [InlineKeyboardButton("🎁 نسبة ربح شراء المكافأة %", callback_data="adm_set_bonus_win_rate")],
        [InlineKeyboardButton("🏺 سقف أرباح 1 جرة (فئة 3)", callback_data="adm_set_bonus_cap_1")],
        [InlineKeyboardButton("🏺🏺 سقف أرباح 2 جرة (فئة 3)", callback_data="adm_set_bonus_cap_2")],
        [InlineKeyboardButton("🏺🏺🏺 سقف أرباح 3 جرات (فئة 3)", callback_data="adm_set_bonus_cap_3")],
        [InlineKeyboardButton("📉 نسبة الخسارة العامة %", callback_data="adm_set_ch_loss")],
        [InlineKeyboardButton("🥉 نسبة الربح العادي (حتى 5x) %", callback_data="adm_set_ch_normal")],
        [InlineKeyboardButton("🥈 نسبة الربح المتوسط (حتى 10x) %", callback_data="adm_set_ch_medium")],
        [InlineKeyboardButton("🥇 نسبة الربح العالي (حتى 20x) %", callback_data="adm_set_ch_high")],
        [InlineKeyboardButton("👑 نسبة الربح الضخم (حتى 50x) %", callback_data="adm_set_ch_huge")],
        [InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="open_admin_panel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def global_mode_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔄 تلقائي (حسب نسب الخوارزمية)", callback_data="set_gmode_auto")],
        [InlineKeyboardButton("❌ قفل على الخسارة (Loss)", callback_data="set_gmode_loss")],
        [InlineKeyboardButton("🥉 قفل على ربح عادي (1x - 5x)", callback_data="set_gmode_normal")],
        [InlineKeyboardButton("🥈 قفل على ربح متوسط (5x - 10x)", callback_data="set_gmode_medium")],
        [InlineKeyboardButton("🥇 قفل على ربح عالي (10x - 20x)", callback_data="set_gmode_high")],
        [InlineKeyboardButton("👑 قفل على ربح ضخم (20x - 50x)", callback_data="set_gmode_huge")],
        [InlineKeyboardButton("🔙 رجوع لخوارزمية الربح", callback_data="adm_algo_menu")]
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
    if is_admin_user(user.id):
        await update.message.reply_text("👮‍♂️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
    else:
        await update.message.reply_text("❌ عذراً، هذا الأمر مخصص للإدارة فقط.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **البوت حالياً في حالة صيانة وتحديثات دورية.**\nيرجى المحاولة في وقت لاحق.")
        return

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
    is_admin = is_admin_user(user.id)

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
            f"👋 أهلاً بك يا {user.full_name} في لعبة Golden Tree 2026!\n\n"
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
        f"👑 **مرحباً بك في لعبة Golden Tree 2026**\n\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🆔 **معرف الحساب (ID):** `{user_id}`\n"
        f"💰 **رصيدك الحالي:** `{bal:,.2f}` NSP\n\n"
        f"اضغط على زر اللعبة أدناه للبدء:"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin))

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **البوت حالياً في حالة صيانة وتحديثات دورية.**")
        return

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
        ref_user = conn.execute("SELECT full_name, balance, referrals_count FROM users WHERE user_id = ?", (u["referred_by"],)).fetchone()
        if ref_user:
            ref_new_bal = ref_user["balance"] + ref_reward
            ref_count = ref_user["referrals_count"] + 1
            conn.execute("UPDATE users SET balance = ?, referrals_count = ? WHERE user_id = ?", (ref_new_bal, ref_count, u["referred_by"]))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (u["referred_by"], f"مكافأة إحالة {user.id}", ref_reward))
            try:
                await context.bot.send_message(u["referred_by"], f"🎉 قام المستخدم {user.full_name} بالتسجيل عبر رابطك وحصلت على مكافأة!")
            except Exception: pass

            admin_ref_msg = (
                f"🔔 **إشعار إحالة جديد!**\n\n"
                f"👤 **اللاعب الجديد:** {user.full_name} (`{user.id}`)\n"
                f"📱 **رقم هاتفه:** `{contact.phone_number}`\n"
                f"🔗 **تمت إحالته بواسطة:** {ref_user['full_name']} (`{u['referred_by']}`)\n"
                f"🎁 **المكافأة الممنوحة للمحيل:** `{ref_reward}` NSP"
            )
            await notify_admins(context, admin_ref_msg)

    conn.commit()
    conn.close()

    is_admin = is_admin_user(user.id)
    await update.message.reply_text(
        f"✅ تم تأكيد حسابك ورقم هاتفك بنجاح!\n"
        f"🎁 حصلت على بونص ترحيبي قدره `{welcome_bonus}` NSP.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin, context)

async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **البوت حالياً في حالة صيانة وتحديثات دورية.**")
        return

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
        return

    if u and u["step"] == "deposit_step_tx":
        photo_file_id = update.message.photo[-1].file_id
        amt = context.user_data.get("dep_amount", 0.0)
        method = context.user_data.get("dep_method", "غير محدد")

        cursor = conn.execute(
            "INSERT INTO deposits (user_id, method, amount, tx_id, photo_file_id) VALUES (?, ?, ?, ?, ?)",
            (user.id, method, amt, "صورة إيصال", photo_file_id)
        )
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        dep_id = cursor.lastrowid

        await update.message.reply_text("✅ تم إرسال صورة الإيصال بنجاح وطلب الشحن قيد المراجعة من الإدارة.")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة وتعبئة", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض الطلب", callback_data=f"rej_dep_{dep_id}")]
        ])
        
        admin_dep_msg = (
            f"📥 **طلب شحن جديد بـ (إيصال صورة) (# {dep_id}):**\n"
            f"👤 **اللاعب:** {user.full_name} (`{user.id}`)\n"
            f"💳 **الطريقة:** {method}\n"
            f"💰 **المبلغ المطلوب:** `{amt}` NSP"
        )
        
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        for adm in admins:
            try:
                await context.bot.send_photo(chat_id=adm["user_id"], photo=photo_file_id, caption=admin_dep_msg, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
                
        conn.close()
        return

    conn.close()

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""
    
    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **البوت حالياً في حالة صيانة وتحديثات دورية.**\nيرجى المحاولة لاحقاً.")
        return

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

    if step == "deposit_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ يرجى إدخال مبلغ مالي صحيح بالأرقام فقط.")
            return

        min_dep = float(conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"])
        if amt < min_dep:
            conn.close()
            await update.message.reply_text(f"❌ الحد الأدنى المسموح به للشحن هو `{min_dep}` NSP.")
            return

        context.user_data["dep_amount"] = amt
        conn.execute("UPDATE users SET step = 'deposit_step_tx' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✍️ **الآن أدخل رقم العملية / الإشعار، أو قم بإرسال صورة الإيصال مباشرة:**")
        return

    if step == "deposit_step_tx":
        amt = context.user_data.get("dep_amount", 0.0)
        method = context.user_data.get("dep_method", "غير محدد")
        tx_id = text

        cursor = conn.execute(
            "INSERT INTO deposits (user_id, method, amount, tx_id) VALUES (?, ?, ?, ?)",
            (user.id, method, amt, tx_id)
        )
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        dep_id = cursor.lastrowid
        conn.close()

        await update.message.reply_text("✅ تم تقديم طلب الشحن بنجاح وهو قيد المراجعة من قبل الإدارة.")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة وتعبئة", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض الطلب", callback_data=f"rej_dep_{dep_id}")]
        ])
        await notify_admins(context, 
            f"📥 **طلب شحن جديد (# {dep_id}):**\n"
            f"👤 **اللاعب:** {user.full_name} (`{user.id}`)\n"
            f"💳 **الطريقة:** {method}\n"
            f"🔢 **رقم العملية:** `{tx_id}`\n"
            f"💰 **المبلغ المطلوب:** `{amt}` NSP", reply_markup=kb)
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
        last_log = conn.execute(
            "SELECT timestamp FROM logs WHERE user_id = ? AND action LIKE 'استخدام كود هدية%' ORDER BY id DESC LIMIT 1",
            (user.id,)
        ).fetchone()

        if last_log and last_log["timestamp"]:
            try:
                last_time = datetime.strptime(last_log["timestamp"], "%Y-%m-%d %H:%M:%S")
                diff = datetime.utcnow() - last_time
                if diff.total_seconds() < 86400:
                    remaining = 86400 - diff.total_seconds()
                    hours = int(remaining // 3600)
                    minutes = int((remaining % 3600) // 60)
                    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                    conn.commit()
                    conn.close()
                    await update.message.reply_text(
                        f"⏳ **عذراً!** يحق لك استخدام كود هدية واحد فقط كل 24 ساعة.\n"
                        f"⏱️ يرجى الانتظار: `{hours}` ساعة و `{minutes}` دقيقة."
                    )
                    return
            except Exception:
                pass

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

        admin_gift_msg = (
            f"🎁 **إشعار استخدام كود هدية!**\n\n"
            f"👤 **المستخدم:** {user.full_name} (`{user.id}`)\n"
            f"🎫 **الكود:** `{text}`\n"
            f"💰 **المبلغ المضاف:** `{amt}` NSP"
        )
        await notify_admins(context, admin_gift_msg)

        await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` NSP إلى رصيدك!")
        return

    if step == "input_support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم وسيتم الرد عليك قريباً.")
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ الرد على الرسالة", callback_data=f"adm_rep_supp_{user.id}")]])
        support_msg = (
            f"💬 **رسالة دعم جديدة:**\n\n"
            f"👤 **من:** {user.full_name} (`{user.id}`)\n\n"
            f"📝 **الرسالة:**\n{text}"
        )
        await notify_admins(context, support_msg, reply_markup=kb)
        return

    if is_admin_user(user.id):
        if step == "adm_set_win_rate":
            try:
                val = int(text)
                if not (0 <= val <= 100): raise ValueError
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('win_rate', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل نسبة الربح العامة إلى `{val}%` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_algo_menu")]]))
            except ValueError:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال نسبة مئوية صحيحة بين 0 و 100.")
            return

        if step == "adm_set_bonus_win_rate":
            try:
                val = int(text)
                if not (0 <= val <= 100): raise ValueError
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('bonus_win_rate', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل نسبة ربح شراء المكافأة إلى `{val}%` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_algo_menu")]]))
            except ValueError:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال نسبة مئوية صحيحة بين 0 و 100.")
            return

        if step in ["adm_set_bonus_cap_1", "adm_set_bonus_cap_2", "adm_set_bonus_cap_3"]:
            try:
                val = float(text)
                if val < 0: raise ValueError
                cap_key = step.replace("adm_set_", "")
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (cap_key, str(val)))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                
                jar_num = cap_key.replace("bonus_cap_", "")
                await update.message.reply_text(f"✅ تم ضبط سقف ربح شراء {jar_num} جرة إلى `{val}` NSP (لفئة 3).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_algo_menu")]]))
            except ValueError:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال مبلغ مالي صحيح بالأرقام.")
            return

        if step in ["adm_set_ch_loss", "adm_set_ch_normal", "adm_set_ch_medium", "adm_set_ch_high", "adm_set_ch_huge"]:
            try:
                val = int(text)
                if not (0 <= val <= 100): raise ValueError
                
                key_map = {
                    "adm_set_ch_loss": "chance_loss",
                    "adm_set_ch_normal": "chance_normal",
                    "adm_set_ch_medium": "chance_medium",
                    "adm_set_ch_high": "chance_high",
                    "adm_set_ch_huge": "chance_huge"
                }
                setting_key = key_map[step]
                
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (setting_key, str(val)))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل النسبة بنجاح إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_algo_menu")]]))
            except ValueError:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال نسبة مئوية صحيحة بين 0 و 100.")
            return

        if step == "adm_input_add_dep_name":
            context.user_data["new_dep_name"] = text
            conn.execute("UPDATE users SET step = 'adm_input_add_dep_details' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✍️ **أدخل تفاصيل الحساب أو رقم المحفظة للشحن:**\n(مثال: `0912345678` أو `اسم الحساب: X - الرقم: Y`)")
            return

        if step == "adm_input_add_dep_details":
            method_name = context.user_data.get("new_dep_name")
            account_details = text
            conn.execute("INSERT INTO deposit_methods (method_name, account_details) VALUES (?, ?)", (method_name, account_details))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إضافة حساب الشحن للطريقة ({method_name}) بنجاح!")
            return

        if step == "adm_input_set_min_dep":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('min_deposit', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل الحد الأدنى للشحن إلى `{val}` NSP.")
            except Exception:
                conn.close()
                await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
            return

        if step == "adm_input_support_reply":
            target_id = context.user_data.get("support_target_id")
            if target_id:
                try:
                    await context.bot.send_message(
                        chat_id=int(target_id),
                        text=f"👨‍💻 **رد من الدعم الفني:**\n\n{text}",
                        parse_mode="Markdown"
                    )
                    await update.message.reply_text(f"✅ تم إرسال الرد للمستخدم `{target_id}` بنجاح.")
                except Exception as e:
                    await update.message.reply_text(f"❌ فشل إرسال الرد: {e}")
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

        if step == "adm_input_add_admin":
            try:
                new_admin_id = int(text)
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_admin_id,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة `{new_admin_id}` كأدمن بنجاح.")
                try:
                    await context.bot.send_message(chat_id=new_admin_id, text="👮 تم ترقيتك لتكون أدمن في البوت.")
                except Exception: pass
            except ValueError:
                await update.message.reply_text("❌ يرجى إدخال ID صحيح بالأرقام.")
            conn.close()
            return

        if step == "adm_input_del_admin":
            try:
                del_admin_id = int(text)
                if del_admin_id == DEFAULT_ADMIN_ID:
                    await update.message.reply_text("❌ لا يمكنك إزالة الأدمن الأساسي للبوت.")
                else:
                    conn.execute("DELETE FROM admins WHERE user_id = ?", (del_admin_id,))
                    await update.message.reply_text(f"✅ تم إزالة `{del_admin_id}` من قائمة الأدمنية.")
            except ValueError:
                await update.message.reply_text("❌ يرجى إدخال ID صحيح بالأرقام.")
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
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
                except Exception:
                    pass
            await update.message.reply_text(f"📢 تم إرسال الإذاعة لـ `{count}` مستخدم.")
            return

        if step == "adm_input_pm_txt":
            try:
                parts = text.split(" ", 1)
                if len(parts) < 2:
                    await update.message.reply_text("❌ صيغة خاطئة! أرسل الـ ID ثم مسافة ثم النص المطلوب.")
                else:
                    tid, msg_content = int(parts[0]), parts[1]
                    await context.bot.send_message(chat_id=tid, text=f"💬 **رسالة خاصة من الإدارة:**\n\n{msg_content}", parse_mode="Markdown")
                    await update.message.reply_text(f"✅ تم إرسال الرسالة للمستخدم `{tid}`.")
            except ValueError:
                await update.message.reply_text("❌ خطأ: يرجى التأكد من أن ID المستخدم يتكون من أرقام فقط.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطأ غير متوقع: {e}")
            
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

    conn.close()

# ----------------------------------------------------
# 7. معالجة النقرات (Callback Queries)
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

    if data == "btn_deposit":
        min_dep = conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_meth_Syriatel Cash")],
            [InlineKeyboardButton("📱 إم تي إن كاش", callback_data="dep_meth_MTN Cash")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_meth_Bank Cham Cash")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ])
        await query.message.edit_text(
            f"💳 **قسم شحن الرصيد:**\n\n"
            f"💰 **الحد الأدنى للشحن:** `{min_dep}` NSP\n\n"
            f"اختر طريقة الشحن المناسبة لك:",
            parse_mode="Markdown",
            reply_markup=kb
        )
        conn.close()
        return

    if data.startswith("dep_meth_"):
        method_name = data.replace("dep_meth_", "")
        acc = conn.execute("SELECT * FROM deposit_methods WHERE method_name LIKE ?", (f"%{method_name}%",)).fetchone()
        
        if not acc:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="btn_deposit")]])
            await query.message.edit_text(f"⏳ **طريقة الشحن عن طريق ({method_name}) غير متوفرة حالياً (قريباً!).**", reply_markup=kb)
            conn.close()
            return

        context.user_data["dep_method"] = acc["method_name"]
        min_dep = conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"]
        
        conn.execute("UPDATE users SET step = 'deposit_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()

        msg = (
            f"💳 **شحن عبر {acc['method_name']}**\n\n"
            f"📌 **يرجى التحويل إلى الحساب التالي:**\n"
            f"`{acc['account_details']}`\n\n"
            f"✍️ **أدخل المبلغ المراد شحنه (NSP):**\n"
            f"⚠️ **الحد الأدنى للشحن:** `{min_dep}` NSP"
        )
        await query.message.edit_text(msg, parse_mode="Markdown")
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

    # ----------------------------------------------------
    # لوحة المشرفين
    # ----------------------------------------------------
    if is_admin:
        if data == "open_admin_panel":
            conn.close()
            await query.message.edit_text("⚙️ **لوحة التحكم الإدارية:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_algo_menu":
            settings = dict(conn.execute("SELECT key, value FROM settings").fetchall())
            conn.close()

            mode_map = {
                "auto": "🔄 تلقائي (حسب نسب الاحتمالات)",
                "loss": "❌ قفل خاسر دائماً (Loss Mode)",
                "normal": "🥉 قفل ربح عادي (حتى 5x)",
                "medium": "🥈 قفل ربح متوسط (حتى 10x)",
                "high": "🥇 قفل ربح عالي (حتى 20x)",
                "huge": "👑 قفل ربح ضخم (حتى 50x)"
            }
            current_mode = mode_map.get(settings.get('global_win_mode', 'auto'), "🔄 تلقائي")

            msg = (
                "🎛️ **لوحة تحكم خوارزميات الربح والمكافآت المحدثة:**\n\n"
                f"⚡ **النمط المباشر المفعّل:** `{current_mode}`\n"
                f"-----------------------------------\n"
                f"🎯 نسبة الربح العامة: `{settings.get('win_rate', 30)}%`\n"
                f"🎁 نسبة ربح شراء المكافأة: `{settings.get('bonus_win_rate', 40)}%`\n"
                f"🏺 سقف 1 جرة (فئة 3): `{settings.get('bonus_cap_1', 200)}` NSP\n"
                f"🏺🏺 سقف 2 جرة (فئة 3): `{settings.get('bonus_cap_2', 500)}` NSP\n"
                f"🏺🏺🏺 سقف 3 جرات (فئة 3): `{settings.get('bonus_cap_3', 1000)}` NSP\n"
                f"-----------------------------------\n"
                f"📉 نسبة الخسارة: `{settings.get('chance_loss', 50)}%`\n"
                f"🥉 نسبة الربح العادي (حتى 5x): `{settings.get('chance_normal', 30)}%`\n"
                f"🥈 نسبة الربح المتوسط (حتى 10x): `{settings.get('chance_medium', 12)}%`\n"
                f"🥇 نسبة الربح العالي (حتى 20x): `{settings.get('chance_high', 6)}%`\n"
                f"👑 نسبة الربح الضخم (حتى 50x): `{settings.get('chance_huge', 2)}%`\n\n"
                "اختر الخيار المراد تعديله من الأزرار أدناه:"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=algo_panel_keyboard())
            return

        if data == "adm_global_mode_menu":
            conn.close()
            await query.message.edit_text(
                "⚡ **التحكم المباشر بنمط السيرفر:**\n\n"
                "يمكنك قفل جميع أدوار السيرفر فورياً على نمط ربح أو خسارة محدد، أو تركه يعمل تلقائياً حسب نسب الخوارزمية.",
                parse_mode="Markdown",
                reply_markup=global_mode_keyboard()
            )
            return

        if data.startswith("set_gmode_"):
            new_mode = data.replace("set_gmode_", "")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_win_mode', ?)", (new_mode,))
            conn.commit()
            conn.close()

            names = {
                "auto": "تلقائي (حسب نسب الخوارزمية)",
                "loss": "خسارة دائمة",
                "normal": "ربح عادي (حتى 5x)",
                "medium": "ربح متوسط (حتى 10x)",
                "high": "ربح عالي (حتى 20x)",
                "huge": "ربح ضخم (حتى 50x)"
            }
            await query.message.edit_text(
                f"✅ تم تغيير النمط المباشر للعب بنجاح إلى: **{names.get(new_mode, new_mode)}**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع لخوارزمية الربح", callback_data="adm_algo_menu")]])
            )
            return

        if data == "adm_dep_methods":
            dep_accs = conn.execute("SELECT * FROM deposit_methods").fetchall()
            kb = [[InlineKeyboardButton("➕ إضافة حساب شحن جديد", callback_data="adm_add_dep_acc")]]
            for acc in dep_accs:
                kb.append([InlineKeyboardButton(f"❌ حذف: {acc['method_name']}", callback_data=f"adm_del_dep_{acc['id']}")])
            kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")])
            
            await query.message.edit_text("💳 **إدارة حسابات الشحن:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            conn.close()
            return

        if data == "adm_add_dep_acc":
            conn.execute("UPDATE users SET step = 'adm_input_add_dep_name' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل اسم طريقة الشحن (مثال: سيريتل كاش):**")
            return

        if data.startswith("adm_del_dep_"):
            dep_id = int(data.replace("adm_del_dep_", ""))
            conn.execute("DELETE FROM deposit_methods WHERE id = ?", (dep_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم حذف حساب الشحن.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_dep_methods")]]))
            return

        if data == "adm_set_min_dep":
            conn.execute("UPDATE users SET step = 'adm_input_set_min_dep' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل الحد الأدنى المسموح به للشحن (NSP):**")
            return

        if data == "adm_deposits":
            pending = conn.execute("SELECT * FROM deposits WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
            
            if not pending:
                await query.message.edit_text("📥 لا توجد طلبات شحن معلقة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="open_admin_panel")]]))
                return

            for dep in pending:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ موافقة وتعبئة", callback_data=f"app_dep_{dep['id']}"), InlineKeyboardButton("❌ رفض الطلب", callback_data=f"rej_dep_{dep['id']}")]
                ])
                txt = (
                    f"📥 **طلب شحن (# {dep['id']}):**\n"
                    f"🆔 **ID العميل:** `{dep['user_id']}`\n"
                    f"💳 **الطريقة:** {dep['method']}\n"
                    f"🔢 **رقم العملية/الإشعار:** `{dep['tx_id']}`\n"
                    f"💰 **المبلغ:** `{dep['amount']}` NSP"
                )
                if dep["photo_file_id"]:
                    try:
                        await context.bot.send_photo(chat_id=user.id, photo=dep["photo_file_id"], caption=txt, parse_mode="Markdown", reply_markup=kb)
                    except Exception:
                        await context.bot.send_message(chat_id=user.id, text=txt, parse_mode="Markdown", reply_markup=kb)
                else:
                    await context.bot.send_message(chat_id=user.id, text=txt, parse_mode="Markdown", reply_markup=kb)
            return

        if data.startswith("app_dep_"):
            dep_id = int(data.replace("app_dep_", ""))
            dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
            if dep and dep["status"] == "pending":
                conn.execute("UPDATE deposits SET status = 'approved' WHERE id = ?", (dep_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (dep["amount"], dep["user_id"]))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (dep["user_id"], f"شحن رصيد ({dep['method']})", dep["amount"]))
                conn.commit()
                await query.message.edit_text(f"✅ تم الموافقة على طلب الشحن #{dep_id} وتعبئة `{dep['amount']}` NSP لرصيد العميل.")
                try: 
                    await context.bot.send_message(dep["user_id"], f"🎉 تم الموافقة على طلب الشحن وإضافة `{dep['amount']}` NSP لرصيدك!")
                except Exception: 
                    pass
            conn.close()
            return

        if data.startswith("rej_dep_"):
            dep_id = int(data.replace("rej_dep_", ""))
            dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
            if dep and dep["status"] == "pending":
                conn.execute("UPDATE deposits SET status = 'rejected' WHERE id = ?", (dep_id,))
                conn.commit()
                await query.message.edit_text(f"❌ تم رفض طلب الشحن #{dep_id}.")
                try: 
                    await context.bot.send_message(dep["user_id"], f"❌ تم رفض طلب الشحن الخاص بك.")
                except Exception: 
                    pass
            conn.close()
            return

        if data.startswith("adm_rep_supp_"):
            target_id = data.replace("adm_rep_supp_", "")
            context.user_data["support_target_id"] = target_id
            conn.execute("UPDATE users SET step = 'adm_input_support_reply' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"✍️ **أدخل نص الرد على العميل (`{target_id}`):**")
            return

        algo_steps = [
            "adm_set_win_rate", "adm_set_bonus_win_rate", 
            "adm_set_bonus_cap_1", "adm_set_bonus_cap_2", "adm_set_bonus_cap_3",
            "adm_set_ch_loss", "adm_set_ch_normal", "adm_set_ch_medium", "adm_set_ch_high", "adm_set_ch_huge"
        ]
        if data in algo_steps:
            conn.execute("UPDATE users SET step = ? WHERE user_id = ?", (data, user.id))
            conn.commit()
            conn.close()
            
            prompts = {
                "adm_set_win_rate": "✍️ أرسل نسبة الربح العامة الجديدة (0 إلى 100):",
                "adm_set_bonus_win_rate": "✍️ أرسل نسبة الربح الجديدة عند شراء المكافأة (0 إلى 100):",
                "adm_set_bonus_cap_1": "✍️ أرسل سقف الأرباح الجديد لشراء 1 جرة (لفئة 3 NSP):",
                "adm_set_bonus_cap_2": "✍️ أرسل سقف الأرباح الجديد لشراء 2 جرة (لفئة 3 NSP):",
                "adm_set_bonus_cap_3": "✍️ أرسل سقف الأرباح الجديد لشراء 3 جرات (لفئة 3 NSP):",
                "adm_set_ch_loss": "✍️ أرسل نسبة الخسارة العامة الجديدة (0 إلى 100):",
                "adm_set_ch_normal": "✍️ أرسل نسبة الربح العادي الجديدة (0 إلى 100):",
                "adm_set_ch_medium": "✍️ أرسل نسبة الربح المتوسط الجديدة (0 إلى 100):",
                "adm_set_ch_high": "✍️ أرسل نسبة الربح العالي الجديدة (0 إلى 100):",
                "adm_set_ch_huge": "✍️ أرسل نسبة الربح الضخم الجديدة (0 إلى 100):"
            }
            await query.message.edit_text(prompts[data])
            return

        if data == "adm_add_admin":
            conn.execute("UPDATE users SET step = 'adm_input_add_admin' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID المستخدم لترقيته كأدمن:**")
            return
            
        if data == "adm_del_admin":
            conn.execute("UPDATE users SET step = 'adm_input_del_admin' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID الأدمن لإزالته من الإدارة:**")
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
                try: 
                    await context.bot.send_message(w["user_id"], f"✅ تم الموافقة على سحب `{w['amount']}` NSP!")
                except Exception: 
                    pass
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
                try: 
                    await context.bot.send_message(w["user_id"], f"❌ تم رفض طلب السحب وإعادة `{w['amount']}` NSP لحسابك.")
                except Exception: 
                    pass
            conn.close()
            return

    conn.close()

# ----------------------------------------------------
# 8. تشغيل التطبيق
# ----------------------------------------------------
def main():
    if not BOT_TOKEN:
        logger.error("❌ لم يتم العثور على BOT_TOKEN في متغيرات البيئة!")
        return

    init_db()

    # تشغيل خيط الـ Keep-Alive في الخلفية
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()

    # تشغيل بوت تليجرام
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot & Keep-Alive Engine starting successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
