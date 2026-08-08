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
    ContextTypes
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

app = Flask(__name__)
ADMIN_ID = 123456789  # استبدل هذا الأيدي بأيدي حسابك الحقيقي

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
    cursor.execute('''CREATE TABLE IF NOT EXISTS wheel_probs (prize INTEGER PRIMARY KEY, weight INTEGER)''')
    cursor.execute("SELECT COUNT(*) FROM wheel_probs")
    if cursor.fetchone()[0] == 0:
        default_probs = {0: 40, 5: 25, 10: 15, 15: 10, 25: 5, 50: 3, 100: 1.5, 200: 0.5}
        for p, w in default_probs.items():
            cursor.execute("INSERT INTO wheel_probs (prize, weight) VALUES (?, ?)", (p, int(w * 10)))
    conn.commit()
    conn.close()

init_db()

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
        button { background: #38bdf8; color: #0f172a; border: none; padding: 12px 30px; font-size: 18px; font-weight: bold; border-radius: 8px; cursor: pointer; margin-top: 20px; }
        #result { font-size: 22px; margin-top: 15px; color: #4ade80; font-weight: bold; }
    </style>
</head>
<body>
    <h2>🎡 عجلة الحظ الكبرى</h2>
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
            for(let i = 0; i < prizes.length; i++) {
                const angle = startAngle + i * arc;
                ctx.fillStyle = colors[i];
                ctx.beginPath();
                ctx.arc(200, 200, 180, angle, angle + arc, false);
                ctx.arc(200, 200, 0, angle + arc, angle, true);
                ctx.fill();
                ctx.save();
                ctx.fillStyle = "#fff";
                ctx.font = "bold 20px Tahoma";
                ctx.translate(200 + Math.cos(angle + arc / 2) * 130, 200 + Math.sin(angle + arc / 2) * 130);
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
                const targetAngle = 360 * 5 + (prizes.length - prizeIndex) * (360 / prizes.length) - (360 / prizes.length) / 2;
                let currentAngle = 0, step = 0;
                const timer = setInterval(() => {
                    currentAngle += (targetAngle - currentAngle) * 0.08;
                    startAngle = (currentAngle * Math.PI) / 180;
                    drawWheel();
                    step++;
                    if(step >= 100) {
                        clearInterval(timer);
                        spinning = false;
                        document.getElementById('spinBtn').disabled = false;
                        document.getElementById('result').innerText = "🎉 مبروك! ربحت: " + data.prize + " نقطة";
                    }
                }, 20);
            });
        }
    </script>
</body>
</html>
"""

# إعداد التطبيق والـ Webhook لاستقبال الرسائل عبر سيرفر الويب مباشرة
TOKEN = os.environ.get("BOT_TOKEN")
application = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://my-bot-j658.onrender.com")
    keyboard = [
        [InlineKeyboardButton("🎡 عجلة الحظ", web_app={"url": f"{render_url}/wheel"}), InlineKeyboardButton("💰 رصيدي", callback_data="my_balance")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="ref_link")]
    ]
    await update.message.reply_text(f"مرحباً بك في البوت 🚀\n\nرصيدك: {data[0]} | اللفات: {data[1]}", reply_markup=InlineKeyboardMarkup(keyboard))

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "my_balance":
        user_id = query.from_user.id
        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        await query.answer(f"💰 رصيدك: {res[0]}\n🎡 اللفات: {res[1]}", show_alert=True)
    elif query.data == "ref_link":
        ref_url = f"https://t.me/{context.bot.username}?start={query.from_user.id}"
        await query.message.reply_text(f"🔗 رابط إحالتك:\n{ref_url}")

application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(buttons_handler))

@app.route('/')
def home():
    return "Bot & Web App are running successfully!"

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
    
    cursor.execute("SELECT prize, weight FROM wheel_probs")
    probs = cursor.fetchall()
    conn.close()
    prizes = [p[0] for p in probs]
    weights = [p[1] for p in probs]
    won_prize = random.choices(prizes, weights=weights, k=1)[0]

    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET spins = spins - 1, balance = balance + ? WHERE user_id = ?", (won_prize, user_id))
    conn.commit()
    conn.close()
    return jsonify({"prize": won_prize})

# ربط التيليجرام بـ Flask Webhook لتفادي مشاكل التوافقية كلياً
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK"

@app.before_first_request_func if hasattr(app, 'before_first_request') else app.before_request
def setup_webhook():
    # تعيين الويب هوك تلقائياً عند الإقلاع
    pass

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # تشغيل البوت عبر Webhook ضمن سيرفر فلاسك
    async def set_wh():
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            await application.bot.set_webhook(url=f"{render_url}/{TOKEN}")
    
    import asyncio
    try:
        asyncio.run(set_wh())
    except Exception:
        pass
        
    app.run(host="0.0.0.0", port=port)
