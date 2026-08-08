import os
import sqlite3
import random
import asyncio
import threading
import time
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- 1. سيرفر الاستضافة الحية (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Professional Bot Engine Active")

    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- 2. إعدادات قاعدة البيانات والإعدادات العامة ---
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
        ref_rewarded INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        terms_accepted INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        captcha_answer INTEGER DEFAULT 0,
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
        ('game_win_rate', 40.0),
        ('daily_bonus', 10.0),
        ('welcome_bonus', 15.0),
        ('welcome_bonus_active', 1.0),
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

# --- 3. نظام فحص القنوات الإجبارية ---
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
        
        msg_text = "⚠️ **عذراً عزيزي، يجب عليك الاشتراك بالقنوات التالية لاستخدام البوت:**"
        markup = InlineKeyboardMarkup(kb)
        if hasattr(update_or_query, 'edit_text'):
            await update_or_query.edit_text(msg_text, reply_markup=markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=msg_text, reply_markup=markup, parse_mode="Markdown")
        return False
    return True

# --- 4. معالجة الإحالات المكتملة والأمن الشامل ---
async def finalize_verification_and_ref(user_id, context):
    conn = get_db_connection()
    user = conn.execute("SELECT ref_by, ref_rewarded FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user['ref_by'] and user['ref_rewarded'] == 0:
        ref_id = user['ref_by']
        reward = get_setting('ref_price')
        
        # إضافة المكافأة للشخص الداعي
        conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id = ?", (reward, ref_id))
        conn.execute("UPDATE users SET ref_rewarded = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        
        # إرسال إشعار فوري للداعي
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=f"🎉 **تم تفعيل إحالتك بنجاح!**\n\nقام صديقك بجميع خطوات التحقق وأكمل شروط الاشتراك.\n💰 **تم إيداع `{reward:.2f}` ليرة في حسابك.**",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    conn.close()

# --- 5. مسار البدء والتحقق الأمني ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name

    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة حالياً، حاول لاحقاً!**", parse_mode="Markdown")
        return

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    if user and user['is_banned'] == 1:
        await update.message.reply_text("❌ **حسابك محظور نهائياً وتجميد أموالك بسبب مخالفة الشروط.**")
        conn.close()
        return

    if not user:
        ref_id = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user_id else None
        welcome_bonus = get_setting('welcome_bonus') if get_setting('welcome_bonus_active') == 1.0 else 0.0
        
        conn.execute("INSERT INTO users (user_id, full_name, ref_by, balance) VALUES (?, ?, ?, ?)", 
                     (user_id, full_name, ref_id, welcome_bonus))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    conn.close()

    # الخطوة 1: اختبار السؤال الرياضي (Captcha)
    if user['is_verified'] == 0:
        num1, num2 = random.randint(1, 9), random.randint(1, 9)
        ans = num1 + num2
        conn = get_db_connection()
        conn.execute("UPDATE users SET captcha_answer=? WHERE user_id=?", (ans, user_id))
        conn.commit()
        conn.close()
        
        context.user_data['state'] = 'waiting_captcha'
        await update.message.reply_text(
            f"🛡️ **نظام التحقق والأمان:**\n\nيرجى الإجابة على السؤال التالي للتأكد من أنك لست روبوت:\n\n❓ **كم يبلغ مجموع: `{num1} + {num2}` ؟**",
            parse_mode="Markdown"
        )
        return

    # الخطوة 2: مشاركة رقم الهاتف السوري
    if not user['phone']:
        btn = KeyboardButton("📱 مشاركة رقم هاتفي السوري", request_contact=True)
        kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "⚠️ **عذراً، يجب عليك مشاركة رقم هاتفك للتحقق من هوية الحساب:**\n\nاضغط على الزر في الأسفل لمشاركة الرقم بضغطة واحدة.",
            reply_markup=kb, parse_mode="Markdown"
        )
        return

    # الخطوة 3: الموافقة على شروط الاستخدام
    if user['terms_accepted'] == 0:
        terms_text = (
            "📜 **شروط واستخدام البوت والأحكام الصارمة:**\n\n"
            "1️⃣ يمنع منعاً باتاً استخدام الحسابات الوهمية أو برامج الرشق لزيادة الإحالات.\n"
            "2️⃣ أي محاولة احتيال أو ثغرة ستؤدي إلى **حظر حسابك فوراً وتجميد جميع أموالك**.\n"
            "3️⃣ يُسمح بالحسابات الحقيقية ذات الأرقام السورية المفعّلة فقط.\n\n"
            "هل توافق على كافة الشروط والالتزام بها؟"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ أوافق على شروط الاستخدام", callback_data="accept_terms")]])
        await update.message.reply_text(terms_text, reply_markup=kb, parse_mode="Markdown")
        return

    # الخطوة 4: الاشتراك بالقنوات
    if not await enforce_subscription(user_id, context, update.message):
        return

    # استكمال الإحالة إن وجدت
    await finalize_verification_and_ref(user_id, context)
    await main_menu(user_id, context, update.message)

# --- 6. معالج الرسائل النصية ومشاركة جهة الاتصال ---
async def text_and_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    if not user or user['is_banned'] == 1:
        return

    # معالجة جهة الاتصال (رقم الهاتف)
    if update.message.contact:
        contact = update.message.contact
        if contact.user_id != user_id:
            await update.message.reply_text("❌ **يجب مشاركة رقم الهاتف الخاص بحسابك فقط!**", parse_mode="Markdown")
            return
            
        phone = contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        
        # التحقق من أن الرقم سوري (+963)
        if not (phone.startswith('+963') or phone.startswith('963') or phone.startswith('09')):
            await update.message.reply_text("❌ **عذراً، البوت مخصص للأرقام السورية فقط (+963)!**", parse_mode="Markdown")
            return

        conn = get_db_connection()
        conn.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        conn.commit()
        conn.close()

        await update.message.reply_text("✅ **تم توثيق رقم هاتفك بنجاح!**", reply_markup=ReplyKeyboardRemove())
        
        # الانتقال للخطوة التالية
        if user['terms_accepted'] == 0:
            terms_text = (
                "📜 **شروط واستخدام البوت والأحكام الصارمة:**\n\n"
                "1️⃣ يمنع منعاً باتاً استخدام الحسابات الوهمية أو برامج الرشق لزيادة الإحالات.\n"
                "2️⃣ أي محاولة احتيال أو ثغرة ستؤدي إلى **حظر حسابك فوراً وتجميد جميع أموالك**.\n"
                "3️⃣ يُسمح بالحسابات الحقيقية ذات الأرقام السورية المفعّلة فقط.\n\n"
                "هل توافق على كافة الشروط والالتزام بها؟"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ أوافق على شروط الاستخدام", callback_data="accept_terms")]])
            await update.message.reply_text(terms_text, reply_markup=kb, parse_mode="Markdown")
        else:
            await start(update, context)
        return

    # معالجة إجابة الكابتشا
    state = context.user_data.get('state')
    if state == 'waiting_captcha':
        txt = update.message.text
        if txt and txt.isdigit() and int(txt) == user['captcha_answer']:
            conn = get_db_connection()
            conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
            
            context.user_data['state'] = None
            await update.message.reply_text("✅ **إجابة صحيحة! تم التحقق من أنك لست روبوت.**")
            await start(update, context)
        else:
            await update.message.reply_text("❌ **إجابة خاطئة! يرجى إعادة كتابة الناتج الصحيح:**")
        return

    # معالجة إدخال رقم حساب السحب للعميل
    if state == 'waiting_withdraw_account':
        amt = context.user_data.get('withdraw_amt', 0)
        method = context.user_data.get('withdraw_method', '')
        acc = update.message.text
        
        conn = get_db_connection()
        # خصم المبلغ مؤقتاً لحين قبول أو رفض الطلب
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, user_id))
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, method, account_info) VALUES (?, ?, ?, ?)",
                       (user_id, amt, method, acc))
        req_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text("✅ **تم إرسال طلب السحب بنجاح إلى الإدارة للمراجعة.**")

        # إشعار لوحة الإدارة بالطلب مع الأزرار
        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ قبول الطلب", callback_data=f"adm_acc_w_{req_id}"),
                InlineKeyboardButton("❌ رفض الطلب", callback_data=f"adm_rej_w_{req_id}")
            ],
            [InlineKeyboardButton("🚫 حظر العضو وتجميد أمواله", callback_data=f"adm_ban_w_{req_id}")]
        ])
        
        admin_msg = (
            f"📥 **طلب سحب جديد #{req_id}:**\n\n"
            f"👤 العميل: `{update.effective_user.full_name}` (`{user_id}`)\n"
            f"📱 الهاتف: `{user['phone']}`\n"
            f"💰 المبلغ: `{amt}` ليرة\n"
            f"💳 طريقة السحب: `{method}`\n"
            f"🔢 رقم/حساب التحويل: `{acc}`"
        )
        try:
            await context.bot.send_message(chat_id=SUPER_ADMIN, text=admin_msg, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception:
            pass
        return

    # --- الأوامر الخاصة بالإدارة ---
    if is_admin(user_id):
        if state == 'waiting_add_channel':
            ch_data = update.message.text.split()
            if len(ch_data) >= 1:
                ch_user = ch_data[0].replace('@', '')
                ch_title = ' '.join(ch_data[1:]) if len(ch_data) > 1 else ch_user
                conn = get_db_connection()
                conn.execute("INSERT OR REPLACE INTO channels (channel_username, channel_title) VALUES (?, ?)", (ch_user, ch_title))
                conn.commit()
                conn.close()
                context.user_data['state'] = None
                await update.message.reply_text(f"✅ تم إضافة القناة `@ {ch_user}` بنجاح!")
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
                except Exception:
                    f += 1
            await update.message.reply_text(f"📢 **اكتملت الإذاعة:**\n✅ تم بنجاح: {s}\n❌ فشل: {f}")
            return

        elif state == 'waiting_win_rate':
            try:
                rate = float(update.message.text)
                set_setting('game_win_rate', rate)
                context.user_data['state'] = None
                await update.message.reply_text(f"✅ تم تحديث نسبة الفوز إلى `{rate}%`")
            except:
                await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
            return

# --- 7. القائمة الرئيسية ---
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

# --- 8. صالة الألعاب ---
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

    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
    conn.commit()

    is_win = random.randint(1, 100) <= win_rate
    kb_again = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 جولة أخرى", callback_data=data), InlineKeyboardButton("🔙 الصالة", callback_data="games_menu")]])

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

# --- 9. معالج الأزرار التفاعلية ولائحة الإدارة السريعة ---
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "accept_terms":
        conn = get_db_connection()
        conn.execute("UPDATE users SET terms_accepted=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("✅ **شكراً لموافقتك على الشروط.**")
        await finalize_verification_and_ref(user_id, context)
        await main_menu(user_id, context)
        return

    if data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.answer("❌ لم تقم بالاشتراك في كافة القنوات المطلوبة بعد!", show_alert=True)
            return
        else:
            await query.message.edit_text("✅ **تم التأكد من جميع الاشتراكات بنجاح!**")
            await finalize_verification_and_ref(user_id, context)
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
            f"👤 **بيانات حسابك الشخصي:**\n\n"
            f"🆔 الأيدي: `{user['user_id']}`\n"
            f"📱 الرقم التوثيقي: `{user['phone'] or 'غير مسجل'}`\n"
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
            f"🔗 **رابط الإحالة المباشر الخاص بك:**\n`https://t.me/{bot_uname}?start={user_id}`\n\n"
            f"👥 عدد إحالاتك المعتمدة: {user['ref_count']}\n"
            f"💰 الربح عند كل إحالة حقيقية: `{get_setting('ref_price')}` ليرة.\n\n"
            f"⚠️ **ملاحظة:** لا تحسب الإحالة إلا بعد إكمال صديقك للتحقق ومشاركة رقمه السوري والاشتراك في القنوات.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]), parse_mode="Markdown"
        )

    # --- نظام السحب المتطور ---
    elif data == "withdraw_start":
        conn = get_db_connection()
        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        min_w = get_setting('min_withdraw')
        if user['balance'] < min_w:
            await query.answer(f"❌ رصيدك أقل من الحد الأدنى للسحب ({min_w} ليرة)", show_alert=True)
            return
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش (Syriatel Cash)", callback_data="w_method_syriatel")],
            [InlineKeyboardButton("💳 شام كاش (Cham Cash)", callback_data="w_method_cham")],
            [InlineKeyboardButton("🔙 إلغاء", callback_data="back_main")]
        ])
        await query.message.edit_text(f"💳 **اختر طريقة السحب المناسبة لك:**\n\n💰 رصيدك المتاح: `{user['balance']:.2f}` ليرة", reply_markup=kb, parse_mode="Markdown")

    elif data in ["w_method_syriatel", "w_method_cham"]:
        method_name = "سيريتل كاش" if data == "w_method_syriatel" else "شام كاش"
        context.user_data['withdraw_method'] = method_name
        
        conn = get_db_connection()
        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        
        context.user_data['withdraw_amt'] = user['balance'] # سحب كامل المبلغ تلقائياً أو تحديد جزء منه
        context.user_data['state'] = 'waiting_withdraw_account'
        
        await query.message.edit_text(
            f"📥 **تم اختيار ({method_name}):**\n\n"
            f"يرجى الآن إرسال **رقم المحفظة / الرقم المالي** المراد التحويل إليه:",
            parse_mode="Markdown"
        )

    # ==================== أزرار لوحة الإدارة وسحب الأموال ====================
    elif data.startswith("adm_acc_w_"):
        req_id = int(data.split("_")[3])
        conn = get_db_connection()
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
        if req:
            conn.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
            conn.commit()
            await query.message.edit_text(f"{query.message.text}\n\n✅ **تمت الموافقة على الطلب بنجاح!**")
            try:
                await context.bot.send_message(chat_id=req['user_id'], text=f"✅ **تم قبول طلب السحب الخاص بك بمبلغ `{req['amount']}` ليرة وإرسال الأموال بنجاح!**", parse_mode="Markdown")
            except Exception: pass
        conn.close()

    elif data.startswith("adm_rej_w_"):
        req_id = int(data.split("_")[3])
        conn = get_db_connection()
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
        if req:
            conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (req_id,))
            # إرجاع المبلغ لرصيد المستخدم
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (req['amount'], req['user_id']))
            conn.commit()
            await query.message.edit_text(f"{query.message.text}\n\n❌ **تم رفض الطلب وإعادة المبلغ لرصيد العميل.**")
            try:
                await context.bot.send_message(chat_id=req['user_id'], text=f"❌ **تم رفض طلب السحب الخاص بك وإعادة المبلغ `{req['amount']}` ليرة لرصيدك.**", parse_mode="Markdown")
            except Exception: pass
        conn.close()

    elif data.startswith("adm_ban_w_"):
        req_id = int(data.split("_")[3])
        conn = get_db_connection()
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
        if req:
            conn.execute("UPDATE withdraw_requests SET status='banned' WHERE id=?", (req_id,))
            conn.execute("UPDATE users SET is_banned=1, balance=0 WHERE user_id=?", (req['user_id'],))
            conn.commit()
            await query.message.edit_text(f"{query.message.text}\n\n🚫 **تم حظر المستخدم وتجميد أمواله بنجاح.**")
            try:
                await context.bot.send_message(chat_id=req['user_id'], text="❌ **تم حظر حسابك نهائياً وتجميد أرصدتك بسبب مخالفة الشروط.**")
            except Exception: pass
        conn.close()

    # لوحة الإدارة الفائقة
    elif data == "admin_panel" and is_admin(user_id):
        kb = [
            [InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_broadcast"), InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="adm_add_channel")],
            [InlineKeyboardButton("📊 تفاصيل اللاعبين", callback_data="adm_stats"), InlineKeyboardButton("🎲 خوارزمية الربح", callback_data="adm_win_rate")],
            [InlineKeyboardButton("⚙️ الصيانة", callback_data="adm_toggle_maint"), InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_main")]
        ]
        await query.message.edit_text("⚙️ **لوحة الإدارة العليا الذكية:**\nاختر الإجراء المناسب للتحكم الكامل:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data == "adm_add_channel" and is_admin(user_id):
        context.user_data['state'] = 'waiting_add_channel'
        await query.message.edit_text("📢 **أرسل يوزر القناة واسمها بفاصل مسافة:**\n(مثال: `@lerafree قناة المبرمج`)")

    elif data == "adm_stats" and is_admin(user_id):
        conn = get_db_connection()
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0
        total_refs = conn.execute("SELECT SUM(ref_count) FROM users").fetchone()[0] or 0
        conn.close()
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_panel")]])
        await query.message.edit_text(
            f"📊 **إحصائيات وتقارير البوت الشاملة:**\n\n"
            f"👥 إجمالي المسجلين: `{total_users}`\n"
            f"💰 إجمالي أرصدة اللاعبين: `{total_bal:.2f}` ليرة\n"
            f"🔗 إجمالي الإحالات الحقيقية: `{total_refs}`", reply_markup=kb, parse_mode="Markdown"
        )

# --- 10. تشغيل التطبيق محلياً وبنائه ---
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(games_section, pattern="^(games_menu|play_wheel|play_slot|play_dice)$"))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.CONTACT, text_and_contact_handler))

    print("🤖 Bot engine is running natively...")
    app.run_polling()

if __name__ == '__main__':
    main()
