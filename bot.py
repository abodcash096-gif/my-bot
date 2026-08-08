from flask import Flask, render_template_string, request, jsonify
import os
import threading
import sqlite3
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# إعداد سيرفر الويب (Flask) لتشغيل عجلة الحظ كصفحة ويب ومنع رندر من الإغلاق
app = Flask(__name__)

ADMIN_ID = 123456789  # استبدل هذا الأيدي بأيدي حسابك الحقيقي

# قاعدة البيانات الشاملة
def init_db():
    conn = sqlite3.connect('bot_full_database.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY, 
                        balance REAL DEFAULT 0, 
                        spins INTEGER DEFAULT 0, 
                        referred_by INTEGER, 
                        is_banned INTEGER DEFAULT 0
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    
    # إعداد جدول نسب العجلة (الخوارزمية الافتراضية بمجموع نسب مئوية)
    cursor.execute('''CREATE TABLE IF NOT EXISTS wheel_probs (prize INTEGER PRIMARY KEY, weight INTEGER)''')
    cursor.execute("SELECT COUNT(*) FROM wheel_probs")
    if cursor.fetchone()[0] == 0:
        default_probs = {0: 40, 5: 25, 10: 15, 15: 10, 25: 5, 50: 3, 100: 1.5, 200: 0.5}
        for p, w in default_probs.items():
            cursor.execute("INSERT INTO wheel_probs (prize, weight) VALUES (?, ?)", (p, int(w * 10)))
    conn.commit()
    conn.close()

init_db()

# مسار صفحة عجلة الحظ الحية (Web App)
WHEEL_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>عجلة الحظ</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { background: #0f172a; color: white; font-family: Tahoma, sans-serif; text-align: center; margin: 0; padding: 20px; }
        h2 { color: #38bdf8; }
        .wheel-container { position: relative; width: 300px; height: 300px; margin: 20px auto; }
        canvas { width: 100%; height: 100%; border-radius: 50%; box-shadow: 0 0 20px rgba(56, 189, 248, 0.5); }
        .pointer { position: absolute; top: -10px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 15px solid transparent; border-right: 15px solid transparent; border-top: 25px solid #ef4444; z-index: 10; }
        button { background: #38bdf8; color: #0f172a; border: none; padding: 12px 30px; font-size: 18px; font-weight: bold; border-radius: 8px; cursor: pointer; margin-top: 20px; box-shadow: 0 4px 10px rgba(56,189,248,0.3); }
        button:active { transform: scale(0.95); }
        #result { font-size: 22px; margin-top: 15px; color: #4ade80; font-weight: bold; }
    </style>
</head>
<body>
    <h2>🎡 عجلة الحظ الكبرى</h2>
    <p>لديك لفات متاحة، أدر العجلة واربح الآن!</p>
    <div class="wheel-container">
        <div class="pointer"></div>
        <canvas id="wheel" width="400" height="400"></canvas>
    </div>
    <br>
    <button id="spinBtn" onclick="spinWheel()">إدارة العجلة 🎲</button>
    <div id="result"></div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        const userId = tg.initDataUnsafe?.user?.id || 123;

        const prizes = [0, 5, 10, 15, 25, 50, 100, 200];
        const colors = ['#f87171', '#fb923c', '#facc15', '#4ade80', '#38bdf8', '#818cf8', '#c084fc', '#f472b6'];
        const canvas = document.getElementById('wheel');
        const ctx = canvas.getContext('2d');
        const arc = Math.PI / (prizes.length / 2);
        let startAngle = 0;
        let spinning = false;

        function drawWheel() {
            ctx.clearRect(0, 0, 400, 400);
            const outsideRadius = 180;
            const textRadius = 130;
            const center = 200;

            for(let i = 0; i < prizes.length; i++) {
                const angle = startAngle + i * arc;
                ctx.fillStyle = colors[i];
                ctx.beginPath();
                ctx.arc(center, center, outsideRadius, angle, angle + arc, false);
                ctx.arc(center, center, 0, angle + arc, angle, true);
                ctx.fill();
                ctx.save();

                ctx.fillStyle = "#fff";
                ctx.font = "bold 20px Tahoma";
                ctx.translate(center + Math.cos(angle + arc / 2) * textRadius, center + Math.sin(angle + arc / 2) * textRadius);
                ctx.rotate(angle + arc / 2 + Math.PI / 2);
                ctx.fillText(prizes[i], -ctx.measureText(prizes[i]).width / 2, 0);
                ctx.restore();
            }
        }

        drawWheel();

        function spinWheel() {
            if (spinning) return;
            spinning = true;
            document.getElementById('spinBtn').disabled = true;
            document.getElementById('result').innerText = "";

            fetch('/api/spin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: userId})
            })
            .then(res => res.json())
            .then(data => {
                if(data.error) {
                    alert(data.error);
                    spinning = false;
                    document.getElementById('spinBtn').disabled = false;
                    return;
                }

                const prizeIndex = prizes.indexOf(data.prize);
                const spins = 5; // عدد الدورات الكاملة
                const arcSize = 360 / prizes.length;
                const targetAngle = 360 * spins + (prizes.length - prizeIndex) * arcSize - arcSize / 2;
                
                let currentAngle = 0;
                let step = 0;
                const totalSteps = 100;

                const timer = setInterval(() => {
                    currentAngle += (targetAngle - currentAngle) * 0.08;
                    startAngle = (currentAngle * Math.PI) / 180;
                    drawWheel();
                    step++;
                    if(step >= totalSteps) {
                        clearInterval(timer);
                        spinning = false;
                        document.getElementById('spinBtn').disabled = false;
                        document.getElementById('result').innerText = "🎉 مبروك! ربحت: " + data.prize + " نقطة";
                        if(tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
                    }
                }, 20);
            })
            .catch(err => {
                spinning = false;
                document.getElementById('spinBtn').disabled = false;
                alert("حدث خطأ ما!");
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return "Bot & Mini App are running successfully!"

@app.route('/wheel')
def wheel_page():
    return render_template_string(WHEEL_HTML)

@app.route('/api/spin', methods=['POST'])
def api_spin():
    data = request.json
    user_id = data.get('user_id')
    
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT spins FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    if not res or res[0] <= 0:
        conn.close()
        return jsonify({"error": "❌ ليس لديك لفات كافية!"})
    
    # جلب خوارزمية النسب من قاعدة البيانات للأدمن
    cursor.execute("SELECT prize, weight FROM wheel_probs")
    probs = cursor.fetchall()
    conn.close()

    prizes = [p[0] for p in probs]
    weights = [p[1] for p in probs]
    
    # اختيار الجائزة عشوائياً بناءً على النسبة المئوية المحددة من الأدمن
    won_prize = random.choices(prizes, weights=weights, k=1)[0]

    # خصم لفة وإضافة الجائزة للرصيد
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET spins = spins - 1, balance = balance + ? WHERE user_id = ?", (won_prize, user_id))
    conn.commit()
    conn.close()

    return jsonify({"prize": won_prize})

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# فحص وضع الصيانة
def is_maintenance():
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
    res = cursor.fetchone()
    conn.close()
    return res and res[0] == "1"

# فحص القنوات الإجبارية
async def check_all_channels(user_id, context):
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT channel_username FROM channels")
    channels = cursor.fetchall()
    conn.close()
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch[0], user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception:
            return False
    return True

# أمر /start الشامل مع نظام الإحالة والحماية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if is_maintenance() and user_id != ADMIN_ID:
        await update.message.reply_text("🛠️ البوت في وضع الصيانة حالياً.")
        return

    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    ban_check = cursor.fetchone()
    if ban_check and ban_check[0] == 1:
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت.")
        conn.close()
        return

    # معالجة الإحالة مع إرسال إشعار للمحيل
    if context.args and not ban_check:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:
                cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                if not cursor.fetchone():
                    cursor.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
                    conn.commit()
                    # إضافة لفة مجانية للمحيل
                    cursor.execute("UPDATE users SET spins = spins + 1 WHERE user_id = ?", (referrer_id,))
                    conn.commit()
                    try:
                        await context.bot.send_message(referrer_id, f"🎉 شخص جديد انضم عبر رابط إحالتك! تم إضافة لفة مجانية لرصيدك 🎁")
                    except Exception:
                        pass
        except ValueError:
            pass

    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

    # فحص الاشتراك الإجباري
    is_subbed = await check_all_channels(user_id, context)
    if not is_subbed:
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT channel_username FROM channels")
        ch_list = cursor.fetchall()
        conn.close()
        
        keyboard = []
        for ch in ch_list:
            keyboard.append([InlineKeyboardButton(f"اشترك في {ch[0]} 📢", url=f"https://t.me/{ch[0].replace('@', '')}")])
        keyboard.append([InlineKeyboardButton("تحقق من الاشتراك ✅", callback_data="verify_sub")])
        await update.message.reply_text("⚠️ يجب عليك الاشتراك في القنوات التالية أولاً لاستخدام البوت:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await show_main_menu(update, context)

async def show_main_menu(update_or_query, context):
    user_id = update_or_query.effective_user.id
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    # جلب رابط الدومين الخاص برندر أو المحلي لعجلة الويب
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://my-bot-j658.onrender.com")

    keyboard = [
        [InlineKeyboardButton("🎡 عجلة الحظ", web_app={"url": f"{render_url}/wheel"}), InlineKeyboardButton("💰 رصيدي", callback_data="my_balance")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="ref_link"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="redeem_code")],
        [InlineKeyboardButton("💳 طلب سحب", callback_data="withdraw"), InlineKeyboardButton("🛍️ شراء بوت", callback_data="buy_bot")],
        [InlineKeyboardButton("🛠️ مراسلة الدعم", callback_data="support")]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 لوحة الإدارة", callback_data="admin_panel")])

    text = f"مرحباً بك في القائمة الرئيسية 🚀\n\nرصيدك: {data[0]} | اللفات: {data[1]}"
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update_or_query.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "verify_sub":
        is_subbed = await check_all_channels(user_id, context)
        if is_subbed:
            await query.edit_message_text("✅ تم التحقق بنجاح! أرسل /start للمتابعة.")
        else:
            await query.answer("❌ لم تشترك في كل القنوات بعد!", show_alert=True)

    elif data == "my_balance":
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        await query.answer(f"💰 رصيدك: {res[0]}\n🎡 اللفات المتاحة: {res[1]}", show_alert=True)

    elif data == "ref_link":
        bot_username = context.bot.username
        ref_url = f"https://t.me/{bot_username}?start={user_id}"
        await query.message.reply_text(f"🔗 رابط إحالتك الشخصي:\n{ref_url}\n\nشاركه مع أصدقائك لتحصل على لفات مجانية عند اشتراكهم!")

    elif data == "admin_panel" and user_id == ADMIN_ID:
        kb = [
            [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="adm_stats"), InlineKeyboardButton("🛠️ وضع الصيانة", callback_data="adm_maint")],
            [InlineKeyboardButton("🎲 نسب خوارزمية العجلة", callback_data="adm_wheel_settings"), InlineKeyboardButton("➕ قناة إجبارية", callback_data="adm_add_ch")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="adm_back")]
        ]
        await query.edit_message_text("👑 لوحة التحكم بالإدارة العليا:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_stats" and user_id == ADMIN_ID:
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        await query.answer(f"👥 إجمالي عدد المستخدمين: {count}", show_alert=True)

    elif data == "adm_maint" and user_id == ADMIN_ID:
        current = is_maintenance()
        new_val = "0" if current else "1"
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance', ?)", (new_val,))
        conn.commit()
        conn.close()
        status = "مفعل 🛠️" if new_val == "1" else "معطل ✅"
        await query.answer(f"تم تغير وضع الصيانة إلى: {status}", show_alert=True)

    elif data == "adm_wheel_settings" and user_id == ADMIN_ID:
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT prize, weight FROM wheel_probs")
        probs = cursor.fetchall()
        conn.close()
        txt = "📊 نسب أرباح العجلة الحالية (الأوزان):\n"
        for p in probs:
            txt += f"• الجائزة {p[0]}: الوزن {p[1]}\n"
        txt += "\n(يمكن تعديلها من قاعدة البيانات مباشرة أو عبر الأوامر البرمجية)"
        await query.message.reply_text(txt)

    elif data == "adm_back":
        await show_main_menu(query, context)

def start_telegram_bot():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN not found!")
        return
        
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(buttons_handler))
    
    print("✅ البوت الشامل مع عجلة الويب والنسب يعمل بكامل الميزات وبدون توقف!")
    application.run_polling()

if __name__ == "__main__":
    start_telegram_bot()
