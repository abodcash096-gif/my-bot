import os
import sqlite3
import random
import asyncio
import threading
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, ContextTypes, filters
)

# --- إعدادات اللعبة ورابط الاستضافة ---
WEBAPP_URL = "https://my-bot-j658.onrender.com/index.html"

# --- 1. سيرفر الاستضافة الحية (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Professional VIP Bot Engine Running")

    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- 2. إعدادات قاعدة البيانات والتكوين الأساسي ---
TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
SUPER_ADMIN = 7255100997  # أيدي الآدمن الأساسي

def get_db_connection():
    conn = sqlite3.connect('bot_permanent_data.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. جدول المستخدمين وحفظ الأرصدة
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        username TEXT,
        phone TEXT,
        balance REAL DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        ref_by INTEGER,
        ref_count INTEGER DEFAULT 0,
        ref_rewarded INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        terms_accepted INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        captcha_answer INTEGER DEFAULT 0,
        last_daily_claim REAL DEFAULT 0
    )''')
    
    # 2. جدول الإعدادات العامة
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL)''')
    
    # 3. جدول المسؤولين
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    
    # 4. جدول القنوات الإجبارية
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY, channel_title TEXT)''')
    
    # 5. جدول أكواد الهدايا
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_code_logs (code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id))''')
    
    # 6. جدول طلبات السحب
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdraw_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        method TEXT,
        account_info TEXT,
        status TEXT DEFAULT 'pending'
    )''')

    # 7. جدول طلبات شراء البوتات
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        details TEXT,
        price REAL,
        status TEXT DEFAULT 'pending'
    )''')

    # الإعدادات الافتراضية
    defaults = [
        ('ref_price', 10.0), 
        ('min_withdraw', 100.0), 
        ('game_cost', 10.0), 
        ('game_win_rate', 50.0),
        ('daily_bonus', 15.0),
        ('welcome_bonus', 20.0),
        ('welcome_bonus_active', 1.0),
        ('maintenance_mode', 0.0),
        ('bot_start_price', 1000.0),
        ('game_algo_mode', 1.0)  # 0: loss, 1: normal, 2: medium, 3: high
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (SUPER_ADMIN,))
    conn.commit()
    conn.close()

init_db()

# --- أدوات إدارية مساعدة ---
def get_setting(key):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else 0.0

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = get_db_connection()
    res = conn.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return res is not None

def add_new_admin(admin_id):
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
    conn.commit()
    conn.close()

def get_algo_mode_str():
    val = get_setting('game_algo_mode')
    modes = {0.0: "loss", 1.0: "normal", 2.0: "medium", 3.0: "high"}
    return modes.get(val, "normal")

def add_xp(user_id, amount):
    conn = get_db_connection()
    user = conn.execute("SELECT xp, level FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user:
        new_xp = user['xp'] + amount
        new_level = (new_xp // 100) + 1
        conn.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (new_xp, new_level, user_id))
        conn.commit()
    conn.close()

# --- 3. نظام الاشتراك الإجباري (يفحص القنوات لحظياً للجميع) ---
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
        kb.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الآن", callback_data="check_sub_again")])
        
        msg_text = "⚠️ **عذراً، يجب عليك الاشتراك بالقنوات التالية لاستخدام البوت:**"
        markup = InlineKeyboardMarkup(kb)
        if hasattr(update_or_query, 'edit_text'):
            await update_or_query.edit_text(msg_text, reply_markup=markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=msg_text, reply_markup=markup, parse_mode="Markdown")
        return False
    return True

# --- 4. فتح لعبة Golden Bull وتحديث الرصيد ---
async def open_golden_bull_game(update_or_query, user_id, context):
    algo_mode = get_algo_mode_str()
    game_url = f"{WEBAPP_URL}?mode={algo_mode}"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎰 افتح لعبة Golden Bull (شجرة الفواكه)", web_app=WebAppInfo(url=game_url))]
    ])
    text = (
        "🎰 **مرحباً بك في لعبة GOLDEN BULL**\n\n"
        "🍒 طابق 3 فواكه متتالية لتربح **نفس سعر الرهان (1x)**\n"
        "🍇 طابق 4 فواكه متتالية لتربح **ضعف سعر الرهان (2x)**\n"
        "🔥 طابق 5 فواكه متتالية لتربح **10 أضعاف سعر الرهان (10x)**!\n\n"
        "اضغط الزر أدناه لبدء اللعب برصيدك مباشرة:"
    )
    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    elif hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="Markdown")

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        web_data = update.effective_message.web_app_data.data
        data = json.loads(web_data)
        
        if data.get("action") == "spin_result":
            new_balance = float(data.get("new_balance", 0))
            
            conn = get_db_connection()
            conn.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Error updating game balance: {e}")

# --- 5. إنهاء الإحالة وإرسال التنبيهات ---
async def finalize_verification_and_ref(user_id, context):
    conn = get_db_connection()
    user = conn.execute("SELECT ref_by, ref_rewarded, full_name FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user['ref_by'] and user['ref_rewarded'] == 0:
        ref_id = user['ref_by']
        reward = get_setting('ref_price')
        
        conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id = ?", (reward, ref_id))
        conn.execute("UPDATE users SET ref_rewarded = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=f"🎉 **إشعار إحالة مكتملة!**\n\nانضم العضو `{user['full_name']}` عن طريق رابطك.\n💰 **تم إيداع `{reward:.2f}` ليرة في حسابك.**",
                parse_mode="Markdown"
            )
        except Exception:
            pass
            
        try:
            await context.bot.send_message(
                chat_id=SUPER_ADMIN,
                text=f"🔔 **إشعار إحالة جديدة:**\nالداعي: `{ref_id}`\nالمنضم: `{user['full_name']}` (`{user_id}`)\nالمكافأة: `{reward}` ليرة",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    conn.close()

# --- 6. مسار البدء وتأكيد الأمان ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    username = update.effective_user.username or "لا يوجد"

    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة حالياً، يرجى المحاولة لاحقاً.**", parse_mode="Markdown")
        return

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    if user and user['is_banned'] == 1:
        await update.message.reply_text("❌ **حسابك محظور نهائياً بسبب مخالفة الشروط.**")
        conn.close()
        return

    if not user:
        ref_id = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user_id else None
        welcome_bonus = get_setting('welcome_bonus') if get_setting('welcome_bonus_active') == 1.0 else 0.0
        
        conn.execute("INSERT INTO users (user_id, full_name, username, ref_by, balance) VALUES (?, ?, ?, ?, ?)", 
                     (user_id, full_name, username, ref_id, welcome_bonus))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    conn.close()

    # Captcha
    if user['is_verified'] == 0:
        num1, num2 = random.randint(1, 9), random.randint(1, 9)
        ans = num1 + num2
        conn = get_db_connection()
        conn.execute("UPDATE users SET captcha_answer=? WHERE user_id=?", (ans, user_id))
        conn.commit()
        conn.close()
        
        context.user_data['state'] = 'waiting_captcha'
        await update.message.reply_text(
            f"🛡️ **نظام التحقق والأمان:**\n\nيرجى الإجابة على السؤال الرياضي للتأكد من أنك لست روبوت:\n\n❓ **كم يبلغ مجموع: `{num1} + {num2}` ؟**",
            parse_mode="Markdown"
        )
        return

    # Phone verification
    if not user['phone']:
        btn = KeyboardButton("📱 مشاركة رقم هاتفي السوري", request_contact=True)
        kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "⚠️ **يجب مشاركة رقم هاتفك لتأكيد هوية الحساب:**\nاضغط على الزر أدناه لمشاركة الرقم بضغطة واحدة.",
            reply_markup=kb, parse_mode="Markdown"
        )
        return

    # Terms agreement
    if user['terms_accepted'] == 0:
        terms_text = (
            "📜 **شروط واستخدام البوت:**\n\n"
            "1️⃣ يمنع منعاً باتاً استخدام الحسابات الوهمية أو الرشق.\n"
            "2️⃣ أي محاولة احتيال ستؤدي لحظر الحساب وتجميد الأرصدة.\n"
            "3️⃣ يُسمح بالأرقام السورية الحقيقية فقط.\n\n"
            "هل توافق على الشروط والالتزام بها؟"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ أوافق على كافة الشروط", callback_data="accept_terms")]])
        await update.message.reply_text(terms_text, reply_markup=kb, parse_mode="Markdown")
        return

    if not await enforce_subscription(user_id, context, update.message):
        return

    await finalize_verification_and_ref(user_id, context)
    await main_menu(user_id, context, update.message)

# --- 7. معالج النصوص والرسائل الشامل (مدمج بالكامل) ---
async def text_and_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    if not user or user['is_banned'] == 1:
        return

    state = context.user_data.get('state')

    # إضافة أدمن جديد
    if state == 'waiting_add_admin' and is_admin(user_id):
        txt = update.message.text.strip() if update.message.text else ''
        context.user_data['state'] = None
        if txt.isdigit():
            new_admin_id = int(txt)
            add_new_admin(new_admin_id)
            await update.message.reply_text(f"👑 **تم إضافة الأدمن الجديد بنجاح!**\nID: `{new_admin_id}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ **يرجى إرسال ID عددي صحيح للآدمن!**")
        return

    # مشاركة رقم الهاتف
    if update.message.contact:
        contact = update.message.contact
        if contact.user_id != user_id:
            await update.message.reply_text("❌ **يجب مشاركة رقم الهاتف الخاص بك فقط!**", parse_mode="Markdown")
            return
            
        phone = contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        
        if not (phone.startswith('+963') or phone.startswith('963') or phone.startswith('09')):
            await update.message.reply_text("❌ **عذراً، البوت مخصص للأرقام السورية فقط (+963)!**", parse_mode="Markdown")
            return

        conn = get_db_connection()
        conn.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        conn.commit()
        conn.close()

        await update.message.reply_text("✅ **تم توثيق رقم الهاتف بنجاح!**", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return

    # الكابتشا
    if state == 'waiting_captcha':
        txt = update.message.text
        if txt and txt.isdigit() and int(txt) == user['captcha_answer']:
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
            
            context.user_data['state'] = None
            await update.message.reply_text("✅ **إجابة صحيحة! تم التحقق بنجاح.**")
            await start(update, context)
        else:
            await update.message.reply_text("❌ **إجابة خاطئة! أعد كتابة الناتج الصحيح:**")
        return

    # كود الهدية
    if state == 'waiting_gift_code':
        code_txt = update.message.text.strip()
        conn = get_db_connection()
        code_data = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code_txt,)).fetchone()
        
        if not code_data:
            await update.message.reply_text("❌ **كود الهدية غير صحيح!**")
            context.user_data['state'] = None
            conn.close()
            return

        if code_data['used_count'] >= code_data['max_uses']:
            await update.message.reply_text("❌ **انتهى عدد مرات استخدام هذا الكود!**")
            context.user_data['state'] = None
            conn.close()
            return

        already_used = conn.execute("SELECT * FROM gift_code_logs WHERE code=? AND user_id=?", (code_txt, user_id)).fetchone()
        if already_used:
            await update.message.reply_text("⚠️ **لقد قمت باستخدام هذا الكود سابقاً!**")
            context.user_data['state'] = None
            conn.close()
            return

        reward = code_data['amount']
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
        conn.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (code_txt,))
        conn.execute("INSERT INTO gift_code_logs (code, user_id) VALUES (?, ?)", (code_txt, user_id))
        conn.commit()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text(f"🎉 **تم تفعيل الكود بنجاح وإضافة `{reward:.2f}` ليرة لرصيدك.**", parse_mode="Markdown")

        try:
            await context.bot.send_message(
                chat_id=SUPER_ADMIN,
                text=f"🎟️ **إشعار استخدام كود هدية:**\nالمستخدم: `{user['full_name']}` (`{user_id}`)\nالكود: `{code_txt}`\nالقيمة: `{reward}` ليرة",
                parse_mode="Markdown"
            )
        except Exception: pass
        return

    # طلب شراء بوت
    if state == 'waiting_bot_details':
        details = update.message.text
        start_price = get_setting('bot_start_price')
        context.user_data['bot_details'] = details
        context.user_data['state'] = None

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد وإرسال الطلب", callback_data="confirm_bot_buy")],
            [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_bot_buy")]
        ])

        await update.message.reply_text(
            f"🤖 **تفاصيل طلبك لشراء البوت:**\n\n"
            f"📝 المواصفات المطلوبة:\n`{details}`\n\n"
            f"💵 السعر المبدئي التقديري: يبدأ من `{start_price:,.0f}` ليرة سورية.\n\n"
            f"هل تريد إرسال الطلب للإدارة لمراجعته والبدء في تنفيذه؟",
            reply_markup=kb, parse_mode="Markdown"
        )
        return

    # حساب السحب
    if state == 'waiting_withdraw_account':
        amt = context.user_data.get('withdraw_amt', 0)
        method = context.user_data.get('withdraw_method', '')
        acc = update.message.text
        
        conn = get_db_connection()
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, user_id))
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, method, account_info) VALUES (?, ?, ?, ?)",
                       (user_id, amt, method, acc))
        req_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text("✅ **تم إرسال طلب السحب للإدارة بنجاح!**")

        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ قبول", callback_data=f"adm_acc_w_{req_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"adm_rej_w_{req_id}")
            ],
            [InlineKeyboardButton("🚫 حظر العضو", callback_data=f"adm_ban_w_{req_id}")]
        ])
        
        admin_msg = (
            f"📥 **طلب سحب جديد #{req_id}:**\n\n"
            f"👤 العميل: `{update.effective_user.full_name}` (`{user_id}`)\n"
            f"📱 الهاتف: `{user['phone']}`\n"
            f"💰 المبلغ: `{amt}` ليرة\n"
            f"💳 طريقة السحب: `{method}`\n"
            f"🔢 حساب التحويل: `{acc}`"
        )
        try:
            await context.bot.send_message(chat_id=SUPER_ADMIN, text=admin_msg, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception: pass
        return

    # الدعم الفني
    if state == 'waiting_support_msg':
        context.user_data['state'] = None
        reply_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العضو", callback_data=f"reply_sup_{user_id}")]])
        
        try:
            if update.message.photo:
                photo_file_id = update.message.photo[-1].file_id
                caption = update.message.caption or "بدون نص"
                await context.bot.send_photo(
                    chat_id=SUPER_ADMIN,
                    photo=photo_file_id,
                    caption=f"📩 **رسالة دعم (صورة) من:**\n`{user['full_name']}` (`{user_id}`)\n📱: `{user['phone']}`\n\n📝 النص: {caption}",
                    reply_markup=reply_kb,
                    parse_mode="Markdown"
                )
            else:
                txt = update.message.text
                await context.bot.send_message(
                    chat_id=SUPER_ADMIN,
                    text=f"📩 **رسالة دعم جديدة من:**\n`{user['full_name']}` (`{user_id}`)\n📱: `{user['phone']}`\n\n💬 النص:\n{txt}",
                    reply_markup=reply_kb,
                    parse_mode="Markdown"
                )
            await update.message.reply_text("✅ **تم إرسال رسالتك إلى الدعم الفني! سيتم الرد عليك قريباً.**")
        except Exception:
            await update.message.reply_text("❌ حدث خطأ أثناء إرسال رسالة الدعم.")
        return

    # --- مدخلات الإدارة ---
    if state == 'waiting_find_user':
        context.user_data['state'] = None
        if update.message.text.isdigit():
            target_id = int(update.message.text)
            conn = get_db_connection()
            u = conn.execute("SELECT * FROM users WHERE user_id=?", (target_id,)).fetchone()
            conn.close()
            if u:
                txt = (
                    f"👤 **تفاصيل اللاعب (`{u['user_id']}`):**\n\n"
                    f"🔹 **الاسم:** `{u['full_name']}`\n"
                    f"🔹 **المعرف:** @{u['username']}\n"
                    f"📱 **الهاتف:** `{u['phone']}`\n"
                    f"💰 **الرصيد:** `{u['balance']:.2f}` ليرة\n"
                    f"👥 **عدد الإحالات:** `{u['ref_count']}`\n"
                    f"🏅 **المستوى:** `{u['level']}`\n"
                    f"🚫 **محظور:** {'نعم' if u['is_banned'] else 'لا'}"
                )
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 حظر", callback_data=f"adm_do_ban_{u['user_id']}"), InlineKeyboardButton("🟢 رفع حظر", callback_data=f"adm_do_unban_{u['user_id']}")], [InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel")]])
                await update.message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم يتم العثور على مستخدم بهذا الأيدي.")
        return

    elif state == 'waiting_ban_user_id':
        context.user_data['state'] = None
        if update.message.text.isdigit():
            t_id = int(update.message.text)
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (t_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"🚫 تم حظر المستخدم `{t_id}` بنجاح!")
        return

    elif state == 'waiting_unban_user_id':
        context.user_data['state'] = None
        if update.message.text.isdigit():
            t_id = int(update.message.text)
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (t_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"🟢 تم رفع الحظر عن المستخدم `{t_id}` بنجاح!")
        return

    elif state == 'waiting_ref_price':
        context.user_data['state'] = None
        try:
            val = float(update.message.text)
            set_setting('ref_price', val)
            await update.message.reply_text(f"✅ تم تعديل سعر الإحالة إلى `{val}` ليرة.")
        except: await update.message.reply_text("❌ قيمة غير صالحة.")
        return

    elif state == 'waiting_min_withdraw':
        context.user_data['state'] = None
        try:
            val = float(update.message.text)
            set_setting('min_withdraw', val)
            await update.message.reply_text(f"✅ تم تعديل الحد الأدنى للسحب إلى `{val}` ليرة.")
        except: await update.message.reply_text("❌ قيمة غير صالحة.")
        return

    elif state == 'waiting_daily_bonus':
        context.user_data['state'] = None
        try:
            val = float(update.message.text)
            set_setting('daily_bonus', val)
            await update.message.reply_text(f"✅ تم تعديل الهدية اليومية إلى `{val}` ليرة.")
        except: await update.message.reply_text("❌ قيمة غير صالحة.")
        return

    elif state == 'waiting_win_rate':
        context.user_data['state'] = None
        try:
            val = float(update.message.text)
            set_setting('game_win_rate', val)
            await update.message.reply_text(f"✅ تم تعديل نسبة ربح الألعاب إلى `{val}%`")
        except: await update.message.reply_text("❌ قيمة غير صالحة.")
        return

    elif state == 'waiting_admin_reply_user':
        target_user = context.user_data.get('target_reply_user')
        reply_txt = update.message.text
        context.user_data['state'] = None
        try:
            await context.bot.send_message(chat_id=target_user, text=f"💬 **رد من الدعم الفني:**\n\n{reply_txt}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرد بنجاح!")
        except Exception:
            await update.message.reply_text("❌ تعذر إرسال الرد للمستخدم.")
        return

    elif state == 'waiting_add_channel':
        ch_data = update.message.text.split()
        if len(ch_data) >= 1:
            ch_user = ch_data[0].replace('@', '')
            ch_title = ' '.join(ch_data[1:]) if len(ch_data) > 1 else ch_user
            conn = get_db_connection()
            conn.execute("INSERT OR REPLACE INTO channels (channel_username, channel_title) VALUES (?, ?)", (ch_user, ch_title))
            conn.commit()
            conn.close()
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ تم إضافة القناة `@{ch_user}` بنجاح!")
        return

    elif state == 'waiting_broadcast_msg':
        msg = update.message.text
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        context.user_data['state'] = None
        
        s, f = 0, 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=msg, parse_mode="Markdown")
                s += 1
            except Exception: f += 1
        await update.message.reply_text(f"📢 **نتيجة الإذاعة الجماعية:**\n✅ تم إرسال: {s}\n❌ فشل: {f}")
        return

    elif state == 'waiting_private_msg':
        txt = update.message.text.split(maxsplit=1)
        context.user_data['state'] = None
        if len(txt) == 2 and txt[0].isdigit():
            t_id, t_msg = int(txt[0]), txt[1]
            try:
                await context.bot.send_message(chat_id=t_id, text=f"🔔 **إشعار خاص من الإدارة:**\n\n{t_msg}", parse_mode="Markdown")
                await update.message.reply_text("✅ تم إرسال الرسالة الخاصة!")
            except Exception:
                await update.message.reply_text("❌ تعذر إرسال الرسالة للمستخدم.")
        else:
            await update.message.reply_text("❌ صيغة غير صحيحة! التنسيق: `الأيدي الرسالة`")
        return

    elif state == 'waiting_create_gift':
        data = update.message.text.split()
        context.user_data['state'] = None
        if len(data) >= 2:
            g_code = data[0]
            g_amt = float(data[1])
            g_uses = int(data[2]) if len(data) > 2 else 1
            conn = get_db_connection()
            conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (g_code, g_amt, g_uses))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إنشاء كود الهدية `{g_code}` بقيمة `{g_amt}` ليرة والاستخدامات `{g_uses}`.")
        else:
            await update.message.reply_text("❌ صيغة خاطئة! الإدخال: `الكود المبلغ عدد_الاستخدامات`")
        return

# --- 8. القائمة الرئيسية ---
async def main_menu(user_id, context, message_obj=None, text="🌟 **القائمة الرئيسية:**"):
    if not await enforce_subscription(user_id, context, message_obj or user_id):
        return

    conn = get_db_connection()
    user = conn.execute("SELECT full_name, balance, level, xp FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    msg = (
        f"👑 **أهلاً بك:** `{user['full_name']}`\n"
        f"💰 **رصيدك الحالي:** `{user['balance']:.2f}` ليرة سورية\n"
        f"🏅 **المستوى:** {user['level']} | **XP:** {user['xp']}/100\n"
        "--------------------------------------"
    )
    
    kb = [
        [InlineKeyboardButton("🎰 لعبة Golden Bull 🔥", callback_data="play_golden_bull"), InlineKeyboardButton("🎲 الألعاب التفاعلية", callback_data="games_menu")],
        [InlineKeyboardButton("👤 حسابي", callback_data="my_account"), InlineKeyboardButton("🎁 المكافأة اليومية", callback_data="daily_bonus")],
        [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="my_ref"), InlineKeyboardButton("💳 سحب الرصيد", callback_data="withdraw_start")],
        [InlineKeyboardButton("🎟️ إدخال كود هدية", callback_data="enter_gift_code"), InlineKeyboardButton("🛍️ طلب شراء بوت", callback_data="buy_bot_request")],
        [InlineKeyboardButton("💬 الدعم الفني", callback_data="support_msg"), InlineKeyboardButton("👨‍💻 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("⚙️ لوحة التحكم الشاملة للإدارة ⚙️", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(kb)
    if message_obj and hasattr(message_obj, 'edit_text'):
        try: await message_obj.edit_text(f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
        except: await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=markup, parse_mode="Markdown")

# --- 9. صالة الألعاب التفاعلية ---
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
            [InlineKeyboardButton("🎰 لعبة Golden Bull (الشجرة)", callback_data="play_golden_bull")],
            [InlineKeyboardButton("🎲 النرد الملكي", callback_data="play_dice"), InlineKeyboardButton("🎰 ماكينة السلوت", callback_data="play_slot")],
            [InlineKeyboardButton("🎯 رمي الأسهم", callback_data="play_dart")],
            [InlineKeyboardButton("🔙 العودة للقائمة", callback_data="back_main")]
        ])
        await query.message.edit_text(
            f"🎰 **صالة الألعاب التفاعلية:**\n\n"
            f"🎯 تكلفة الجولة (للألعاب العادية): `{cost}` ليرة.\n"
            f"💰 اختر اللعبة لتجربة حظك:",
            reply_markup=kb, parse_mode="Markdown"
        )
        conn.close()
        return

    if user['balance'] < cost:
        await query.answer("❌ رصيدك غير كافٍ للعب!", show_alert=True)
        conn.close()
        return

    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
    conn.commit()

    is_win = random.randint(1, 100) <= win_rate
    win_amount = cost * 2.0

    kb_again = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 جولة أخرى", callback_data=data)],
        [InlineKeyboardButton("🔙 صالة الألعاب", callback_data="games_menu")]
    ])

    if data == "play_dice":
        await context.bot.send_dice(chat_id=user_id, emoji='🎲')
        await asyncio.sleep(2.5)
        if is_win:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **مبروك! فزت بـ `{win_amount}` ليرة!**", reply_markup=kb_again, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=f"💔 **خسرت هذه الجولة (`{cost}` ليرة).**", reply_markup=kb_again, parse_mode="Markdown")

    elif data == "play_slot":
        await context.bot.send_dice(chat_id=user_id, emoji='🎰')
        await asyncio.sleep(2.5)
        if is_win:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, user_id))
            conn.commit()
            add_xp(user_id, 20)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **فزت بـ `{win_amount}` ليرة!**", reply_markup=kb_again, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=f"💔 **لم يكتمل الخط! خسرت `{cost}` ليرة.**", reply_markup=kb_again, parse_mode="Markdown")

    elif data == "play_dart":
        await context.bot.send_dice(chat_id=user_id, emoji='🎯')
        await asyncio.sleep(2.5)
        if is_win:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **اصبت الهدف! فزت بـ `{win_amount}` ليرة!**", reply_markup=kb_again, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=f"💔 **لم تصب الهدف! خسرت `{cost}` ليرة.**", reply_markup=kb_again, parse_mode="Markdown")

    conn.close()

# --- 10. موجّه الاستدعاءات الأوتوماتيكي (Callback Router) ---
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "accept_terms":
        conn = get_db_connection()
        conn.execute("UPDATE users SET terms_accepted=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        await query.answer("✅ تم قبول الشروط والأحكام!")
        await start(update, context)
        return

    if data == "check_sub_again":
        if await enforce_subscription(user_id, context, query):
            await query.answer("✅ تم التحقق بنجاح!", show_alert=True)
            await start(update, context)
        else:
            await query.answer("❌ لم تشترك بكافة القنوات بعد!", show_alert=True)
        return

    if data == "back_main":
        await main_menu(user_id, context, query.message)
        return

    if data == "play_golden_bull":
        await open_golden_bull_game(query, user_id, context)
        return

    if data == "my_account":
        conn = get_db_connection()
        u = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        acc_text = (
            f"👤 **حسابك الشخصي:**\n\n"
            f"🔹 **الاسم:** `{u['full_name']}`\n"
            f"🆔 **الأيدي:** `{u['user_id']}`\n"
            f"📱 **الهاتف:** `{u['phone']}`\n"
            f"💰 **الرصيد:** `{u['balance']:.2f}` ليرة\n"
            f"👥 **الإحالات:** `{u['ref_count']}`\n"
            f"🏅 **المستوى:** `{u['level']}` ({u['xp']}/100 XP)"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_main")]])
        await query.message.edit_text(acc_text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "daily_bonus":
        conn = get_db_connection()
        u = conn.execute("SELECT last_daily_claim FROM users WHERE user_id=?", (user_id,)).fetchone()
        now = time.time()
        
        if now - u['last_daily_claim'] >= 86400:
            bonus = get_setting('daily_bonus')
            conn.execute("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE user_id = ?", (bonus, now, user_id))
            conn.commit()
            conn.close()
            await query.answer(f"🎉 حصلت على المكافأة اليومية ({bonus} ليرة)!", show_alert=True)
            await main_menu(user_id, context, query.message)
        else:
            conn.close()
            rem = int((86400 - (now - u['last_daily_claim'])) / 3600)
            await query.answer(f"⏳ يمكنك المطالبة بعد {rem} ساعة!", show_alert=True)
        return

    if data == "my_ref":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        price = get_setting('ref_price')
        
        msg = (
            f"🔗 **رابط الإحالة الخاص بك:**\n\n`{ref_link}`\n\n"
            f"💰 **المكافأة:** كسب `{price}` ليرة سورية عن كل شخص ينضم ويستكمل التوثيق عبر رابطك."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_main")]])
        await query.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "withdraw_start":
        min_w = get_setting('min_withdraw')
        conn = get_db_connection()
        u = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()

        if u['balance'] < min_w:
            await query.answer(f"❌ الحد الأدنى للسحب هو {min_w} ليرة!", show_alert=True)
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_meth_syriatel")],
            [InlineKeyboardButton("🏦 شام ماش (Cham Cash)", callback_data="w_meth_cham")],
            [InlineKeyboardButton("🔙 العودة", callback_data="back_main")]
        ])
        await query.message.edit_text(f"💳 **اختر طريقة السحب:**\n\n💰 رصيدك: `{u['balance']}` ليرة\n🔻 الحد الأدنى: `{min_w}` ليرة", reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("w_meth_"):
        method = "سيريتل كاش" if "syriatel" in data else "شام ماش"
        context.user_data['withdraw_method'] = method
        conn = get_db_connection()
        u = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        
        context.user_data['withdraw_amt'] = u['balance']
        context.user_data['state'] = 'waiting_withdraw_account'
        
        await query.message.edit_text(
            f"📲 **طريقة السحب:** `{method}`\n"
            f"💰 **المبلغ المطلوب:** `{u['balance']}` ليرة\n\n"
            f"📝 **أرسل رقم الحساب/الهاتف للتحويل إليه الآن:**",
            parse_mode="Markdown"
        )
        return

    if data == "enter_gift_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎟️ **يرجى إرسال كود الهدية في رسالة:**", parse_mode="Markdown")
        return

    if data == "buy_bot_request":
        context.user_data['state'] = 'waiting_bot_details'
        await query.message.edit_text("🛍️ **خدمة شراء بوت:**\n\nأرسل مواصفات وتفاصيل البوت المطلوب في رسالة واحدة:", parse_mode="Markdown")
        return

    if data == "confirm_bot_buy":
        details = context.user_data.get('bot_details', 'بدون تفاصيل')
        start_price = get_setting('bot_start_price')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bot_orders (user_id, details, price) VALUES (?, ?, ?)", (user_id, details, start_price))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        await query.message.edit_text("✅ **تم إرسال طلب الشراء للإدارة بنجاح!**")

        admin_msg = f"🛍️ **طلب شراء بوت جديد #{order_id}:**\n👤 العميل: `{query.from_user.full_name}` (`{user_id}`)\n📝 التفاصيل:\n`{details}`\n💵 السعر التقديري: `{start_price}` ليرة"
        try: await context.bot.send_message(chat_id=SUPER_ADMIN, text=admin_msg, parse_mode="Markdown")
        except Exception: pass
        return

    if data == "cancel_bot_buy":
        await query.message.edit_text("❌ **تم إلغاء طلب الشراء.**")
        return

    if data == "support_msg":
        context.user_data['state'] = 'waiting_support_msg'
        await query.message.edit_text("💬 **أرسل رسالتك الآن للدعم (نص أو صورة):**", parse_mode="Markdown")
        return

    if data.startswith("play_") or data == "games_menu":
        await games_section(update, context)
        return

    # --- 👑 لوحة التحكم الإدارية الكاملة 👑 ---
    if is_admin(user_id):
        if data == "admin_panel":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 جميع اللاعبين والإحصائيات", callback_data="adm_all_users"), InlineKeyboardButton("🔍 تفاصيل لاعب معين", callback_data="adm_find_user")],
                [InlineKeyboardButton("⚙️ خوارزمية Golden Bull", callback_data="adm_golden_algo_menu"), InlineKeyboardButton("👑 إضافة أدمن جديد", callback_data="adm_add_admin_btn")],
                [InlineKeyboardButton("🎁 تفعيل/إلغاء البونص الترحيبي", callback_data="adm_toggle_welcome"), InlineKeyboardButton("🛠️ تفعيل/إلغاء الصيانة", callback_data="adm_toggle_maint")],
                [InlineKeyboardButton("🎟️ إنشاء كود هدية", callback_data="adm_create_gift"), InlineKeyboardButton("📢 القنوات الإجبارية", callback_data="adm_channels_menu")],
                [InlineKeyboardButton("📢 إذاعة جماعية", callback_data="adm_broadcast"), InlineKeyboardButton("✉️ رسالة خاصة", callback_data="adm_private_msg")],
                [InlineKeyboardButton("💳 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("🛍️ طلبات شراء البوتات", callback_data="adm_bot_orders")],
                [InlineKeyboardButton("🎲 خوارزمية ربح الألعاب", callback_data="adm_edit_winrate"), InlineKeyboardButton("🎁 تعديل الهدية اليومية", callback_data="adm_edit_daily")],
                [InlineKeyboardButton("🔗 تعديل سعر الإحالة", callback_data="adm_edit_refprice"), InlineKeyboardButton("🔻 تعديل أدنى حد للسحب", callback_data="adm_edit_min_withdraw")],
                [InlineKeyboardButton("💥 تصفير الأرصدة", callback_data="adm_reset_balances_conf")],
                [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_user_btn"), InlineKeyboardButton("🟢 رفع حظر مستخدم", callback_data="adm_unban_user_btn")],
                [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_main")]
            ])
            await query.message.edit_text("⚙️ **لوحة التحكم الشاملة والاحترافية للإدارة:**", reply_markup=kb, parse_mode="Markdown")
            return

        # التحكم بخوارزمية Golden Bull
        if data == "adm_golden_algo_menu":
            curr_mode = get_algo_mode_str()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 خسارة دائمة (0x)", callback_data="set_slot_algo_0")],
                [InlineKeyboardButton("🟢 ربح عادي (1x)", callback_data="set_slot_algo_1")],
                [InlineKeyboardButton("🟡 ربح متوسط (1x / 2x)", callback_data="set_slot_algo_2")],
                [InlineKeyboardButton("🔥 ربح عالي (10x / متوسط)", callback_data="set_slot_algo_3")],
                [InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel")]
            ])
            await query.message.edit_text(
                f"🎰 **إدارة خوارزمية لعبة Golden Bull:**\nالوضع الحالي المفعل: `{curr_mode}`\n\nاختر الوضع الجديد:",
                reply_markup=kb, parse_mode="Markdown"
            )
            return

        if data.startswith("set_slot_algo_"):
            mode_num = float(data.replace("set_slot_algo_", ""))
            set_setting('game_algo_mode', mode_num)
            curr_mode = get_algo_mode_str()
            await query.answer(f"✅ تم تغيير خوارزمية اللعبة إلى: {curr_mode}", show_alert=True)
            await callback_router(update, context)
            return

        # إضافة أدمن جديد
        if data == "adm_add_admin_btn":
            context.user_data['state'] = 'waiting_add_admin'
            await query.message.edit_text("👑 **أرسل أيدي (ID) الأدمن الجديد للتعيين:**", parse_mode="Markdown")
            return

        if data == "adm_all_users":
            conn = get_db_connection()
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            banned_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
            total_bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0.0
            conn.close()

            txt = (
                f"👥 **تفاصيل جميع اللاعبين والإحصائيات:**\n\n"
                f"🔹 **إجمالي عدد اللاعبين:** `{total_users}` مستخدم\n"
                f"🚫 **عدد المحظورين:** `{banned_users}` مستخدم\n"
                f"💰 **إجمالي الأرصدة المخزنة:** `{total_bal:,.2f}` ليرة"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel")]])
            await query.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
            return

        if data == "adm_find_user":
            context.user_data['state'] = 'waiting_find_user'
            await query.message.edit_text("🔍 **أرسل أيدي (ID) اللاعب للبحث عنه وعرض تفاصيله:**", parse_mode="Markdown")
            return

        if data == "adm_toggle_welcome":
            curr = get_setting('welcome_bonus_active')
            new_val = 0.0 if curr == 1.0 else 1.0
            set_setting('welcome_bonus_active', new_val)
            status = "تم تفعيل ✅" if new_val == 1.0 else "تم إلغاء ❌"
            await query.answer(f"البونص الترحيبي: {status}", show_alert=True)
            await callback_router(update, context)
            return

        if data == "adm_toggle_maint":
            curr = get_setting('maintenance_mode')
            new_val = 0.0 if curr == 1.0 else 1.0
            set_setting('maintenance_mode', new_val)
            status = "تم تفعيل وضع الصيانة ✅" if new_val == 1.0 else "تم إلغاء وضع الصيانة ❌"
            await query.answer(status, show_alert=True)
            await callback_router(update, context)
            return

        if data == "adm_channels_menu":
            conn = get_db_connection()
            chs = conn.execute("SELECT channel_username, channel_title FROM channels").fetchall()
            conn.close()

            kb = [[InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="adm_add_ch")]]
            for c in chs:
                kb.append([InlineKeyboardButton(f"❌ حذف: @{c['channel_username']}", callback_data=f"adm_del_ch_{c['channel_username']}")])
            kb.append([InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel")])

            await query.message.edit_text("📢 **إدارة القنوات الإجبارية:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        if data == "adm_add_ch":
            context.user_data['state'] = 'waiting_add_channel'
            await query.message.edit_text("📢 **أرسل اسم القناة وعنوانها بالتنسيق:**\n`@channel_username عنوان القناة`", parse_mode="Markdown")
            return

        if data.startswith("adm_del_ch_"):
            ch_user = data.replace("adm_del_ch_", "")
            conn = get_db_connection()
            conn.execute("DELETE FROM channels WHERE channel_username=?", (ch_user,))
            conn.commit()
            conn.close()
            await query.answer(f"✅ تم حذف القناة @{ch_user}", show_alert=True)
            await callback_router(update, context)
            return

        if data == "adm_edit_winrate":
            curr = get_setting('game_win_rate')
            context.user_data['state'] = 'waiting_win_rate'
            await query.message.edit_text(f"🎲 **نسبة الفوز الحالية:** `{curr}%`\n\nأرسل النسبة المئوية الجديدة لربح الألعاب (مثال: `40` أو `60`):", parse_mode="Markdown")
            return

        if data == "adm_edit_daily":
            curr = get_setting('daily_bonus')
            context.user_data['state'] = 'waiting_daily_bonus'
            await query.message.edit_text(f"🎁 **الهدية اليومية الحالية:** `{curr}` ليرة\n\nأرسل القيمة الجديدة للهدية اليومية:", parse_mode="Markdown")
            return

        if data == "adm_edit_refprice":
            curr = get_setting('ref_price')
            context.user_data['state'] = 'waiting_ref_price'
            await query.message.edit_text(f"🔗 **سعر الإحالة الحالي:** `{curr}` ليرة\n\nأرسل السعر الجديد لكل إحالة:", parse_mode="Markdown")
            return

        if data == "adm_edit_min_withdraw":
            curr = get_setting('min_withdraw')
            context.user_data['state'] = 'waiting_min_withdraw'
            await query.message.edit_text(f"🔻 **الحد الأدنى للسحب الحالي:** `{curr}` ليرة\n\nأرسل القيمة الجديدة للحد الأدنى للسحب:", parse_mode="Markdown")
            return

        if data == "adm_reset_balances_conf":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💥 نعم، أكد تصفير جميع الأرصدة", callback_data="adm_do_reset_balances")],
                [InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")]
            ])
            await query.message.edit_text("⚠️ **تحذير هام جداً!**\nهل أنت أسباب لتصفير أرصدة جميع المستخدمين إلى 0 ليرة؟", reply_markup=kb, parse_mode="Markdown")
            return

        if data == "adm_do_reset_balances":
            conn = get_db_connection()
            conn.execute("UPDATE users SET balance=0")
            conn.commit()
            conn.close()
            await query.answer("💥 تم تصفير جميع أرصدة المستخدمين بنجاح!", show_alert=True)
            await callback_router(update, context)
            return

        if data == "adm_ban_user_btn":
            context.user_data['state'] = 'waiting_ban_user_id'
            await query.message.edit_text("🚫 **أرسل أيدي (ID) المستخدم المراد حظره:**", parse_mode="Markdown")
            return

        if data == "adm_unban_user_btn":
            context.user_data['state'] = 'waiting_unban_user_id'
            await query.message.edit_text("🟢 **أرسل أيدي (ID) المستخدم المراد رفع الحظر عنه:**", parse_mode="Markdown")
            return

        if data.startswith("adm_do_ban_"):
            t_id = int(data.replace("adm_do_ban_", ""))
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (t_id,))
            conn.commit()
            conn.close()
            await query.answer("🚫 تم حظر المستخدم بنجاح!", show_alert=True)
            return

        if data.startswith("adm_do_unban_"):
            t_id = int(data.replace("adm_do_unban_", ""))
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (t_id,))
            conn.commit()
            conn.close()
            await query.answer("🟢 تم رفع الحظر بنجاح!", show_alert=True)
            return

        if data == "adm_broadcast":
            context.user_data['state'] = 'waiting_broadcast_msg'
            await query.message.edit_text("📢 **أرسل نص الرسالة للإذاعة الجماعية:**", parse_mode="Markdown")
            return

        if data == "adm_private_msg":
            context.user_data['state'] = 'waiting_private_msg'
            await query.message.edit_text("✉️ **أرسل الأيدي ثم النص (مثال: `7255100997 نص الرسالة`):**", parse_mode="Markdown")
            return

        if data == "adm_create_gift":
            context.user_data['state'] = 'waiting_create_gift'
            await query.message.edit_text("🎟️ **أرسل الكود بالتنسيق التالية:**\n`الكود المبلغ عدد_الاستخدامات`", parse_mode="Markdown")
            return

        if data == "adm_withdraws":
            conn = get_db_connection()
            reqs = conn.execute("SELECT * FROM withdraw_requests WHERE status='pending' LIMIT 10").fetchall()
            conn.close()
            
            if not reqs:
                await query.answer("لا توجد طلبات سحب معلقة حالياً.", show_alert=True)
                return
                
            for r in reqs:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ قبول", callback_data=f"adm_acc_w_{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"adm_rej_w_{r['id']}")],
                    [InlineKeyboardButton("🚫 حظر", callback_data=f"adm_ban_w_{r['id']}")]
                ])
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"💳 **طلب سحب #{r['id']}:**\n👤 المستخدم: `{r['user_id']}`\n💰 المبلغ: `{r['amount']}` ليرة\n💳 الوسيلة: `{r['method']}`\n🔢 الحساب: `{r['account_info']}`",
                    reply_markup=kb, parse_mode="Markdown"
                )
            return

        if data == "adm_bot_orders":
            conn = get_db_connection()
            orders = conn.execute("SELECT * FROM bot_orders WHERE status='pending' LIMIT 10").fetchall()
            conn.close()

            if not orders:
                await query.answer("لا توجد طلبات شراء بوت معلقة حالياً.", show_alert=True)
                return

            for o in orders:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛍️ **طلب شراء بوت #{o['id']}:**\n👤 المستخدم: `{o['user_id']}`\n💵 السعر التقديري: `{o['price']}` ليرة\n📝 التفاصيل:\n`{o['details']}`",
                    parse_mode="Markdown"
                )
            return

        if data.startswith("adm_acc_w_"):
            req_id = int(data.replace("adm_acc_w_", ""))
            conn = get_db_connection()
            req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
            if req:
                conn.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
                conn.commit()
                await query.edit_message_text(f"✅ تم قبول طلب السحب #{req_id}")
                try: await context.bot.send_message(chat_id=req['user_id'], text=f"✅ **تم تحويل مبلغ السحب الخاص بك بقيمة `{req['amount']}` ليرة.**")
                except Exception: pass
            conn.close()
            return

        if data.startswith("adm_rej_w_"):
            req_id = int(data.replace("adm_rej_w_", ""))
            conn = get_db_connection()
            req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
            if req:
                conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (req_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (req['amount'], req['user_id']))
                conn.commit()
                await query.edit_message_text(f"❌ تم رفض طلب السحب #{req_id} وإعادة الرصيد.")
                try: await context.bot.send_message(chat_id=req['user_id'], text=f"❌ **تم رفض طلب السحب وإعادة `{req['amount']}` ليرة لحسابك.**")
                except Exception: pass
            conn.close()
            return

        if data.startswith("reply_sup_"):
            target_user = int(data.replace("reply_sup_", ""))
            context.user_data['target_reply_user'] = target_user
            context.user_data['state'] = 'waiting_admin_reply_user'
            await query.message.reply_text(f"💬 **أدخل الرد الموجه للمستخدم (`{target_user}`):**")
            return

# --- 11. التشغيل النهائي للبوت ---
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, text_and_contact_handler))

    print("VIP Telegram Bot is online and listening...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
