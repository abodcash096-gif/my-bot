import os
import random
import sqlite3
import logging
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# ----------------------------------------------------
# 1. إعدادات التسجيل وقاعدة البيانات
# ----------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        points REAL DEFAULT 0,
        spins INTEGER DEFAULT 0,
        referred_by INTEGER,
        referrals_count INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0
    )''')
    
    # جدول الإعدادات العامة
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    # جدول الأكواد والمكافآت
    cursor.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        reward_type TEXT,
        reward_value REAL,
        max_uses INTEGER,
        times_used INTEGER DEFAULT 0
    )''')
    
    # القيم الافتراضية
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('min_withdraw', '500')")
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('maintenance', 'off')")
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('welcome_bonus', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('required_channel', '')")
    cursor.execute("INSERT OR IGNORE INTO settings VALUES ('wheel_rates', '0:50,5:25,10:15,15:5,25:3,50:1.5,100:0.5')")
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 2. تطبيق الويب والـ API (Render)
# ----------------------------------------------------
flask_app = Flask(__name__)

WHEEL_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>عجلة الحظ</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: white; text-align: center; padding: 20px; }
        .card { background: #1e293b; border-radius: 16px; padding: 20px; max-width: 350px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .wheel-box { position: relative; width: 220px; height: 220px; margin: 20px auto; border-radius: 50%; border: 6px solid #38bdf8; display: flex; align-items: center; justify-content: center; font-size: 26px; background: radial-gradient(circle, #334155, #0f172a); font-weight: bold; }
        .spin-btn { background: #2563eb; border: none; color: white; padding: 14px 28px; font-size: 18px; border-radius: 30px; cursor: pointer; font-weight: bold; width: 100%; transition: 0.2s; }
        .spin-btn:active { transform: scale(0.95); }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎉 عجلة الحظ السورية 🎉</h2>
        <div class="wheel-box" id="wheel">🎡 أدر واكسب</div>
        <button class="spin-btn" onclick="spinWheel()">🚀 جرب حظك الآن</button>
    </div>

    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();

        function spinWheel() {
            let userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
            if(!userId) { alert("❌ يرجى فتح اللعبة من داخل تيليجرام حصراً"); return; }

            fetch('/api/spin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ user_id: userId })
            })
            .then(res => res.json())
            .then(data => {
                if(data.success) {
                    document.getElementById('wheel').innerText = "🎁 + " + data.reward;
                    alert("🥳 مبروك! ربحت " + data.reward + " نقطة!");
                } else {
                    alert("❌ " + data.message);
                }
            });
        }
    </script>
</body>
</html>
"""

@flask_app.route('/')
def home():
    return "Bot Service is ONLINE"

@flask_app.route('/wheel')
def wheel_page():
    return render_template_string(WHEEL_HTML)

@flask_app.route('/api/spin', methods=['POST'])
def api_spin():
    data = request.json
    user_id = data.get('user_id')
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key = 'maintenance'")
    m_state = cursor.fetchone()
    if m_state and m_state[0] == 'on':
        conn.close()
        return jsonify({'success': False, 'message': '🛠️ البوت حالياً في وضع الصيانة.'})

    cursor.execute("SELECT spins, points FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if not row or row[0] <= 0:
        conn.close()
        return jsonify({'success': False, 'message': 'لا تملك لفات مجانية كافية!'})
    
    cursor.execute("SELECT value FROM settings WHERE key = 'wheel_rates'")
    rates_str = cursor.fetchone()[0]
    
    rewards, weights = [], []
    for pair in rates_str.split(','):
        r, w = pair.split(':')
        rewards.append(int(r))
        weights.append(float(w))
        
    win_reward = random.choices(rewards, weights=weights)[0]
    
    cursor.execute("UPDATE users SET spins = spins - 1, points = points + ? WHERE user_id = ?", (win_reward, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'reward': win_reward})

# ----------------------------------------------------
# 3. أحداث وأوامر تيليجرام
# ----------------------------------------------------
async def check_channel_subscription(user_id, context):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'required_channel'")
    channel_row = cursor.fetchone()
    conn.close()
    
    if not channel_row or not channel_row[0]:
        return True
        
    channel = channel_row[0]
    try:
        member = await context.bot.get_chat_member(chat_id=f"@{channel.replace('@','')}", user_id=user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
        return False
    except:
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key = 'maintenance'")
    m_state = cursor.fetchone()
    
    cursor.execute("SELECT is_admin, is_banned, is_verified FROM users WHERE user_id = ?", (user_id,))
    u_data = cursor.fetchone()
    
    if not u_data:
        ref_by = int(context.args[0]) if context.args and context.args[0].isdigit() else None
        cursor.execute("SELECT COUNT(*) FROM users")
        is_first = 1 if cursor.fetchone()[0] == 0 else 0
        
        # فحص البونص الترحيبي
        cursor.execute("SELECT value FROM settings WHERE key = 'welcome_bonus'")
        wb = cursor.fetchone()[0]
        init_spins = 1 if wb == 'on' else 0
        
        cursor.execute("INSERT INTO users (user_id, referred_by, is_admin, spins) VALUES (?, ?, ?, ?)", (user_id, ref_by, is_first, init_spins))
        conn.commit()
        u_data = (is_first, 0, 0)
    
    if u_data[1] == 1:
        await update.message.reply_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return
        
    if m_state and m_state[0] == 'on' and u_data[0] != 1:
        await update.message.reply_text("🛠️ البوت حالياً في وضع الصيانة لتحديث الخدمات، يرجى المحاولة لاحقاً.")
        conn.close()
        return

    # فحص القناة الإجبارية
    is_subbed = await check_channel_subscription(user_id, context)
    if not is_subbed:
        cursor.execute("SELECT value FROM settings WHERE key = 'required_channel'")
        ch = cursor.fetchone()[0]
        conn.close()
        kb = [[InlineKeyboardButton("📢 اشترك بالقناة هنا", url=f"https://t.me/{ch.replace('@','')}")],
              [InlineKeyboardButton("✅ تم الاشتراك", callback_data="check_sub")]]
        await update.message.reply_text("⚠️ لاستخدام البوت يرجى الاشتراك في القناة الرسمية أولاً:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if u_data[2] == 0:
        keyboard = [[KeyboardButton("📱 مشاركة جهة الاتصال لتأكيد رقمك", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "🔒 لحماية نظام البوت، يرجى الضغط على الزر أدناه لمشاركة رقمك والتأكد من أنه خط سوري (+963):",
            reply_markup=reply_markup
        )
    else:
        await show_main_menu(update, context)
        
    conn.close()

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user_id = update.effective_user.id
    phone = contact.phone_number
    
    if not phone.startswith("+963") and not phone.startswith("963"):
        await update.message.reply_text("❌ عذراً! هذا البوت مخصص للأرقام السورية (+963) فقط.")
        return

    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    context.user_data['captcha_res'] = n1 + n2
    context.user_data['phone'] = phone
    
    await update.message.reply_text(f"🧩 اختبار أمان سريع لمنع الروبوتات:\nكم حاصل جمع {n1} + {n2} ؟")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
    is_admin = cursor.fetchone()
    is_admin = is_admin[0] if is_admin else 0

    # 1. اختبار الكابتشا
    if 'captcha_res' in context.user_data:
        if text.isdigit() and int(text) == context.user_data['captcha_res']:
            cursor.execute("UPDATE users SET is_verified = 1, phone = ? WHERE user_id = ?", (context.user_data.get('phone'), user_id))
            
            cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
            ref_row = cursor.fetchone()
            if ref_row and ref_row[0]:
                ref_id = ref_row[0]
                cursor.execute("UPDATE users SET spins = spins + 1, referrals_count = referrals_count + 1 WHERE user_id = ?", (ref_id,))
                try:
                    await context.bot.send_message(ref_id, "🎉 قام مستخدم جديد بالانضمام عبر رابطك وحصلت على 1 لفة مجانية!")
                except:
                    pass
            
            conn.commit()
            conn.close()
            del context.user_data['captcha_res']
            await update.message.reply_text("✅ تم تأكيد حسابك بنجاح! مرحباً بك.")
            await show_main_menu(update, context)
        else:
            await update.message.reply_text("❌ إجابة خاطئة، حاول مجدداً:")
            conn.close()
        return

    # 2. إدخال كود هدية
    if context.user_data.get('awaiting_code'):
        code = text.strip()
        cursor.execute("SELECT reward_type, reward_value, max_uses, times_used FROM promo_codes WHERE code = ?", (code,))
        promo = cursor.fetchone()
        
        if not promo or promo[3] >= promo[2]:
            await update.message.reply_text("❌ هذا الكود غير صالح أو تم استخدامه بالكامل.")
        else:
            rtype, rval, max_u, times_u = promo
            if rtype == 'points':
                cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (rval, user_id))
                msg = f"🎉 مبروك! تم إضافة {rval} نقطة لرصيدك!"
            else:
                cursor.execute("UPDATE users SET spins = spins + ? WHERE user_id = ?", (int(rval), user_id))
                msg = f"🎉 مبروك! تم إضافة {int(rval)} لفة مجانية لرصيدك!"
                
            cursor.execute("UPDATE promo_codes SET times_used = times_used + 1 WHERE code = ?", (code,))
            conn.commit()
            await update.message.reply_text(msg)
            
        conn.close()
        del context.user_data['awaiting_code']
        return

    # 3. إرسال رسالة دعم
    if context.user_data.get('awaiting_support_msg'):
        cursor.execute("SELECT user_id FROM users WHERE is_admin = 1")
        admins = cursor.fetchall()
        for adm in admins:
            try:
                kb = [[InlineKeyboardButton("💬 رد مباشر", callback_data=f"reply_to_{user_id}")]]
                await context.bot.send_message(adm[0], f"📩 **رسالة دعم جديدة:**\n- من المستخدم: `{user_id}`\n\n💬 النص:\n{text}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except:
                pass
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم الفني، وسنرد عليك بأسرع وقت.")
        conn.close()
        del context.user_data['awaiting_support_msg']
        return

    # ------------------ مدخلات لوحة الإدارة ------------------
    if is_admin == 1:
        # البحث عن لاعب
        if context.user_data.get('adm_search_user'):
            target_id = text.strip()
            cursor.execute("SELECT points, spins, referrals_count, is_banned, phone FROM users WHERE user_id = ?", (target_id,))
            u = cursor.fetchone()
            if u:
                status = "🔴 محظور" if u[3] == 1 else "🟢 نشط"
                await update.message.reply_text(f"👤 **بيانات اللاعب `{target_id}`:**\n- الهاتف: `{u[4]}`\n- النقاط: {u[0]}\n- اللفات: {u[1]}\n- الإحالات: {u[2]}\n- الحالة: {status}", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم يتم العثور على هذا المستخدم.")
            del context.user_data['adm_search_user']
            conn.close()
            return

        # إضافة كود
        if context.user_data.get('adm_add_code'):
            try:
                c_name, c_type, c_val, c_max = text.split(':')
                cursor.execute("INSERT OR REPLACE INTO promo_codes VALUES (?, ?, ?, ?, 0)", (c_name.strip(), c_type.strip(), float(c_val), int(c_max)))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة الكود `{c_name}` بنجاح!")
            except:
                await update.message.reply_text("❌ الصيغة خاطئة! استخدم: `اسم_الكود:points/spins:القيمة:العدد`", parse_mode="Markdown")
            del context.user_data['adm_add_code']
            conn.close()
            return

        # حظر/فك حظر
        if context.user_data.get('adm_ban_user'):
            target_id, action = text.split(':') if ':' in text else (text, 'ban')
            val = 1 if action.strip() == 'ban' else 0
            cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (val, target_id.strip()))
            conn.commit()
            await update.message.reply_text(f"✅ تم التحديث للمستخدم `{target_id}`.")
            del context.user_data['adm_ban_user']
            conn.close()
            return

        # تعيين قناة إجبارية
        if context.user_data.get('adm_set_channel'):
            ch_name = text.strip().replace('@', '')
            cursor.execute("UPDATE settings SET value = ? WHERE key = 'required_channel'", (ch_name,))
            conn.commit()
            await update.message.reply_text(f"✅ تم تعيين القناة الإجبارية: @{ch_name}")
            del context.user_data['adm_set_channel']
            conn.close()
            return

        # إضافة أدمن
        if context.user_data.get('adm_add_admin'):
            cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (text.strip(),))
            conn.commit()
            await update.message.reply_text(f"✅ تم إعطاء صلاحية الأدمن للمستخدم `{text.strip()}`.")
            del context.user_data['adm_add_admin']
            conn.close()
            return

        # رد مباشر على الدعم
        if context.user_data.get('adm_reply_target'):
            t_id = context.user_data['adm_reply_target']
            try:
                await context.bot.send_message(t_id, f"🎧 **رد من الدعم الفني:**\n\n{text}", parse_mode="Markdown")
                await update.message.reply_text("✅ تم إرسال الرد للعميل بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ فشل الإرسال: {e}")
            del context.user_data['adm_reply_target']
            conn.close()
            return

        # بث جماعي
        if context.user_data.get('adm_broadcast'):
            cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
            all_users = cursor.fetchall()
            count = 0
            for u in all_users:
                try:
                    await context.bot.send_message(u[0], text)
                    count += 1
                except:
                    pass
            await update.message.reply_text(f"📢 تم إرسال البث إلى {count} مستخدم بنجاح.")
            del context.user_data['adm_broadcast']
            conn.close()
            return

        # بث خاص
        if context.user_data.get('adm_private_msg'):
            try:
                t_id, msg = text.split(':', 1)
                await context.bot.send_message(t_id.strip(), f"📩 **رسالة من الإدارة:**\n\n{msg.strip()}", parse_mode="Markdown")
                await update.message.reply_text("✅ تم إرسال الرسالة الخاصة.")
            except:
                await update.message.reply_text("❌ الصيغة خاطئة! استخدم: `User_ID:النص`", parse_mode="Markdown")
            del context.user_data['adm_private_msg']
            conn.close()
            return

        # تعديل خوارزمية العجلة
        if context.user_data.get('adm_set_rates'):
            cursor.execute("UPDATE settings SET value = ? WHERE key = 'wheel_rates'", (text.strip(),))
            conn.commit()
            await update.message.reply_text("✅ تم تحديث نسب ربح العجلة بنجاح!")
            del context.user_data['adm_set_rates']
            conn.close()
            return

    conn.close()

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    
    bot_domain = os.environ.get("RENDER_EXTERNAL_URL", "https://my-bot-j658.onrender.com")
    web_url = f"{bot_domain}/wheel"
    
    keyboard = [
        [InlineKeyboardButton("🎡 عجلة الحظ (إربح الآن)", web_app=WebAppInfo(url=web_url))],
        [InlineKeyboardButton("💰 رصيد نقاطي", callback_data="my_points"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("💸 طلب سحب", callback_data="withdraw"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="use_code")],
        [InlineKeyboardButton("🛍️ شراء بوت", callback_data="buy_bot"), InlineKeyboardButton("🛠️ مراسلة الدعم", callback_data="support")]
    ]
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
    is_admin = cursor.fetchone()
    conn.close()
    
    if is_admin and is_admin[0] == 1:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة الإدارة الشاملة", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "✨ أهلاً بك في القائمة الرئيسية للبوت! اختر من الخدمات أدناه:"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

# ----------------------------------------------------
# 4. معالج الأزرار التفاعلية
# ----------------------------------------------------
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    if data == "check_sub":
        is_subbed = await check_channel_subscription(user_id, context)
        if is_subbed:
            await query.message.reply_text("✅ شكراً لاشتراكك! يمكنك استخدام البوت الآن عبر /start")
        else:
            await query.message.reply_text("❌ لم يتم التحقق من اشتراكك، يرجى الاشتراك ثم الضغط مجدداً.")
            
    elif data == "my_points":
        cursor.execute("SELECT points, spins FROM users WHERE user_id = ?", (user_id,))
        p, s = cursor.fetchone()
        await query.message.reply_text(f"💳 رصيدك الحالي:\n- النقاط: {p}\n- لفات العجلة: {s}")
        
    elif data == "my_ref":
        cursor.execute("SELECT referrals_count FROM users WHERE user_id = ?", (user_id,))
        ref_cnt = cursor.fetchone()[0]
        bot_uname = context.bot.username
        link = f"https://t.me/{bot_uname}?start={user_id}"
        await query.message.reply_text(f"🔗 رابط إحالتك الخاص:\n{link}\n\n📊 عدد إحالاتك الناجحة: {ref_cnt}\n🎁 تحصل على لفة مجانية لكل إحالة موثقة!")

    elif data == "use_code":
        context.user_data['awaiting_code'] = True
        await query.message.reply_text("🎁 أرسل كود الهدية الآن:")

    elif data == "support":
        context.user_data['awaiting_support_msg'] = True
        await query.message.reply_text("🛠️ أرسل استفسارك أو مشكلتك الآن وسيتواصل معك الدعم الفني فوراً:")

    elif data == "admin_panel":
        cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
        adm = cursor.fetchone()
        if not adm or adm[0] != 1:
            await query.message.reply_text("❌ عذراً، هذه اللوحة مخصصة للأدمن فقط.")
            conn.close()
            return
            
        cursor.execute("SELECT value FROM settings WHERE key = 'maintenance'")
        m_curr = cursor.fetchone()[0]
        m_btn = "🔴 إيقاف وضع الصيانة" if m_curr == 'on' else "🟢 تفعيل وضع الصيانة"
        
        cursor.execute("SELECT value FROM settings WHERE key = 'welcome_bonus'")
        wb_curr = cursor.fetchone()[0]
        wb_btn = "🔴 إيقاف البونص الترحيبي" if wb_curr == 'on' else "🟢 تفعيل البونص الترحيبي"

        adm_kb = [
            [InlineKeyboardButton("🔍 كشف تفاصيل لاعب", callback_data="adm_search_user"), InlineKeyboardButton("📊 عدد المستخدمين", callback_data="adm_count_users")],
            [InlineKeyboardButton("🎁 إضافة كود هدية", callback_data="adm_add_code"), InlineKeyboardButton("🚫 حظر/فك حظر", callback_data="adm_ban_user")],
            [InlineKeyboardButton("📢 قناة إجبارية", callback_data="adm_set_channel"), InlineKeyboardButton("❌ إزالة القناة", callback_data="adm_del_channel")],
            [InlineKeyboardButton(wb_btn, callback_data="toggle_wb"), InlineKeyboardButton("👑 إضافة أدمن", callback_data="adm_add_admin")],
            [InlineKeyboardButton("📢 بث جماعي", callback_data="adm_broadcast"), InlineKeyboardButton("📩 بث خاص", callback_data="adm_private_msg")],
            [InlineKeyboardButton("⚙️ تعديل نسب العجلة", callback_data="adm_set_rates"), InlineKeyboardButton(m_btn, callback_data="toggle_maintenance")],
            [InlineKeyboardButton("💥 تصفير جميع الأرصدة 💥", callback_data="reset_all_balances")],
            [InlineKeyboardButton("⬅️ العودة للقائمة", callback_data="main_menu")]
        ]
        await query.message.edit_text("⚙️ **لوحة التحكم الشاملة بالإدارة:**", reply_markup=InlineKeyboardMarkup(adm_kb), parse_mode="Markdown")

    elif data == "adm_count_users":
        cursor.execute("SELECT COUNT(*) FROM users")
        c = cursor.fetchone()[0]
        await query.message.reply_text(f"📊 إجمالي عدد مستخدمين البوت: `{c}` مستخدم.", parse_mode="Markdown")

    elif data == "adm_search_user":
        context.user_data['adm_search_user'] = True
        await query.message.reply_text("🔍 أرسل الـ User ID للاعب المراد كشف بياناته:")

    elif data == "adm_add_code":
        context.user_data['adm_add_code'] = True
        await query.message.reply_text("🎁 أرسل بيانات الكود بالشكل التالي:\n`اسم_الكود:نوع_الهدية:القيمة:عدد_الاستخدامات`\n\nمثال للعبات: `GIFT2026:spins:3:50`\nمثال للنقاط: `CASH100:points:100:20`", parse_mode="Markdown")

    elif data == "adm_ban_user":
        context.user_data['adm_ban_user'] = True
        await query.message.reply_text("🚫 أرسل الـ User ID للحظر، وإذا أردت فك الحظر أرسل `ID:unban`\nمثال: `12345678:unban`", parse_mode="Markdown")

    elif data == "adm_set_channel":
        context.user_data['adm_set_channel'] = True
        await query.message.reply_text("📢 أرسل يوزر القناة الإجبارية بدون @ (مثال: `my_channel`):")

    elif data == "adm_del_channel":
        cursor.execute("UPDATE settings SET value = '' WHERE key = 'required_channel'")
        conn.commit()
        await query.message.reply_text("✅ تم إزالة القناة الإجبارية بنجاح.")

    elif data == "toggle_wb":
        cursor.execute("SELECT value FROM settings WHERE key = 'welcome_bonus'")
        curr = cursor.fetchone()[0]
        new_val = 'off' if curr == 'on' else 'on'
        cursor.execute("UPDATE settings SET value = ? WHERE key = 'welcome_bonus'", (new_val,))
        conn.commit()
        await query.message.reply_text(f"✅ تم تغيير حالة البونص الترحيبي إلى: {new_val.upper()}")

    elif data == "adm_add_admin":
        context.user_data['adm_add_admin'] = True
        await query.message.reply_text("👑 أرسل الـ User ID للشخص المراد رفعه كأدمن:")

    elif data == "reset_all_balances":
        cursor.execute("UPDATE users SET points = 0, spins = 0")
        conn.commit()
        await query.message.reply_text("⚠️ تم تصفير جميع أرصدة ولفات المستخدمين بنجاح.")

    elif data.startswith("reply_to_"):
        target_id = data.replace("reply_to_", "")
        context.user_data['adm_reply_target'] = target_id
        await query.message.reply_text(f"💬 أرسل نص الرد المباشر للمستخدم `{target_id}`:", parse_mode="Markdown")

    elif data == "adm_broadcast":
        context.user_data['adm_broadcast'] = True
        await query.message.reply_text("📢 أرسل النص المراد بثه لجميع مستخدمي البوت:")

    elif data == "adm_private_msg":
        context.user_data['adm_private_msg'] = True
        await query.message.reply_text("📩 أرسل الرسالة الخاصة بالنص والتنسيق التالي:\n`User_ID:نص الرسالة`", parse_mode="Markdown")

    elif data == "adm_set_rates":
        context.user_data['adm_set_rates'] = True
        cursor.execute("SELECT value FROM settings WHERE key = 'wheel_rates'")
        curr_r = cursor.fetchone()[0]
        await query.message.reply_text(f"⚙️ النسب الحالية:\n`{curr_r}`\n\nأرسل النسب الجديدة بالصيغة: `الربح:النسبة المئوية` تفصل بينها فاصلة.\nمثال: `0:50,5:25,10:15,15:5,25:3,50:1.5,100:0.5`", parse_mode="Markdown")

    elif data == "toggle_maintenance":
        cursor.execute("SELECT value FROM settings WHERE key = 'maintenance'")
        curr = cursor.fetchone()[0]
        new_val = 'off' if curr == 'on' else 'on'
        cursor.execute("UPDATE settings SET value = ? WHERE key = 'maintenance'", (new_val,))
        conn.commit()
        await query.message.reply_text(f"✅ تم تغيير حالة الصيانة إلى: {new_val.upper()}")

    elif data == "main_menu":
        await show_main_menu(update, context)

    conn.close()

# ----------------------------------------------------
# 5. تشغيل خيط البوت وخادم Gunicorn
# ----------------------------------------------------
def start_telegram_bot():
    TOKEN = os.environ.get("BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
        app.add_handler(CallbackQueryHandler(handle_callbacks))
        
        print("✅ تم تشغيل البوت ولوحة التحكم الشاملة بنجاح...")
        app.run_polling()

# تشغيل البوت في خيط خلفي
Thread(target=start_telegram_bot, daemon=True).start()

# متغير Flask العام لـ Gunicorn
app = flask_app
