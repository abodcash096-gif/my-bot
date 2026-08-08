import os
import sqlite3
import random
import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- 1. سيرفر وهمي لإبقاء البوت نشطاً على Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active and running smooth!")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- 2. الإعدادات الأساسية وقاعدة البيانات ---
TOKEN = os.environ.get("BOT_TOKEN")
SUPER_ADMIN = 7255100997

def init_db():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
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
        is_verified INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        last_daily_claim REAL DEFAULT 0
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL)''')

    # إضافة إعداد maintenance_mode بقيمة 0 (تعني الصيانة معطلة)
    defaults = [
        ('ref_price', 5.0), 
        ('min_withdraw', 200.0), 
        ('game_cost', 5.0), 
        ('game_win_rate', 40.0),
        ('daily_bonus', 10.0),
        ('maintenance_mode', 0.0) # 0 = يعمل عادي, 1 = وضع الصيانة مفعل
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (SUPER_ADMIN,))
    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة لقاعدة البيانات ---
def get_setting(key):
    conn = sqlite3.connect('bot_data.db')
    val = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]
    conn.close()
    return val

def set_setting(key, value):
    conn = sqlite3.connect('bot_data.db')
    conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = sqlite3.connect('bot_data.db')
    res = conn.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return res is not None

def add_xp(user_id, amount):
    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT xp, level FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user:
        new_xp = user[0] + amount
        new_level = (new_xp // 100) + 1
        conn.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (new_xp, new_level, user_id))
        conn.commit()
    conn.close()

async def check_sub(user_id, context):
    conn = sqlite3.connect('bot_data.db')
    channels = conn.execute("SELECT channel_username FROM channels").fetchall()
    conn.close()
    
    unsubbed = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch[0], user_id=user_id)
            if member.status in ['left', 'kicked']:
                unsubbed.append(ch[0])
        except Exception:
            continue
    return unsubbed

# --- 3. أمر البداية /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    
    # فحص وضع الصيانة لغير المسؤولين
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**\n\nيرجى العودة لاحقاً، سنعود للعمل قريباً جداً! 🛠️", parse_mode="Markdown")
        return

    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT is_banned, is_verified FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user[0] == 1:
        await update.message.reply_text("❌ حسابك محظور حالياً من استخدام الخدمة.")
        conn.close()
        return

    if not user:
        ref_id = None
        if context.args and context.args[0].isdigit():
            possible_ref = int(context.args[0])
            if possible_ref != user_id:
                ref_id = possible_ref
        conn.execute("INSERT INTO users (user_id, full_name, ref_by) VALUES (?, ?, ?)", (user_id, full_name, ref_id))
        conn.commit()
    
    conn.close()

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✨ الموافقة والدخول للبوت", callback_data="accept_terms")]])
    await update.message.reply_text(
        f"🌟 **أهلاً بك يا {full_name} في النظام المطور!**\n\n"
        "🏛️ **شروط الاستخدام:**\n"
        "• يمنع استخدام الأرقام الوهمية أو الإحالات المزيفة.\n"
        "• الحظر النهائي وتصفير الرصيد سيكون عقوبة أي تلاعب.\n\n"
        "اضغط على الزر أدناه للبدء:",
        reply_markup=kb, parse_mode="Markdown"
    )

# --- 4. القائمة الرئيسية الاحترافية ---
async def main_menu(user_id, context, text="🌟 القائمة الرئيسية:"):
    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT full_name, balance, level, xp FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    
    msg = (
        f"👑 **مرحباً بك:** {user[0]}\n"
        f"💰 **الرصيد الحالي:** `{user[1]}` ليرة\n"
        f"🏅 **المستوى:** {user[2]} | **الخبرة (XP):** {user[3]}/100\n"
        "--------------------------------------"
    )
    
    kb = [
        [InlineKeyboardButton("👤 حسابي", callback_data="my_account"), InlineKeyboardButton("🎁 المكافأة اليومية", callback_data="daily_bonus")],
        [InlineKeyboardButton("🎮 صالة الألعاب (4 ألعاب)", callback_data="games_menu"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("💳 سحب الرصيد", callback_data="withdraw"), InlineKeyboardButton("🎟️ تفعيل كود", callback_data="enter_code")],
        [InlineKeyboardButton("🛍️ طلب بوت خاص", callback_data="buy_bot"), InlineKeyboardButton("💬 الدعم الفني", callback_data="support_msg")],
        [InlineKeyboardButton("📢 القناة الرسمية", url="https://t.me/cashinsher")]
    ]
    
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("⚙️ لوحة الإدارة الذكية", callback_data="admin_panel")])
        
    await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# --- 5. معالج الأزرار التفاعلية ---
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # فحص الصيانة عند الضغط على أي زر لغير المسؤولين
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await query.message.edit_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**\n\nيرجى العودة لاحقاً!", parse_mode="Markdown")
        return

    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT is_banned, is_verified, balance, ref_count, level, xp, last_daily_claim FROM users WHERE user_id=?", (user_id,)).fetchone()

    if user and user[0] == 1:
        await query.message.edit_text("❌ حسابك محظور من استخدام البوت.")
        conn.close()
        return

    # قبول الشروط والتحقق من الهاتف
    if data == "accept_terms":
        if user[1] == 1:
            await main_menu(user_id, context)
        else:
            btn = KeyboardButton("📱 مشاركة رقم الهاتف للتأكيد", request_contact=True)
            await context.bot.send_message(
                chat_id=user_id, 
                text="📱 لتأكيد هويتك، يرجى الضغط على الزر أدناه لمشاركة رقمك السوري:",
                reply_markup=ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
            )

    elif data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.message.edit_text("❌ لم تقم بالاشتراك في جميع القنوات بعد!")
        else:
            conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
            ref_by = conn.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
            if ref_by:
                ref_price = get_setting('ref_price')
                conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id=?", (ref_price, ref_by))
                add_xp(ref_by, 20)
                try:
                    await context.bot.send_message(chat_id=ref_by, text=f"🎉 **إحالة جديدة!** انضم صديق عن طريقك وحصلت على {ref_price} ليرة و +20 XP!")
                except: pass
            
            conn.commit()
            await query.message.edit_text("✅ تم التحقق من الاشتراك بنجاح!")
            await main_menu(user_id, context)

    # المكافأة اليومية
    elif data == "daily_bonus":
        current_time = time.time()
        last_claim = user[6]
        if current_time - last_claim >= 86400:
            bonus = get_setting('daily_bonus')
            conn.execute("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE user_id = ?", (bonus, current_time, user_id))
            conn.commit()
            add_xp(user_id, 10)
            await query.message.edit_text(f"🎁 **مبروك!** حصلت على مكافأتك اليومية بقيمة `{bonus}` ليرة و +10 XP!\nعد بعد 24 ساعة للحصول على المزيد.", parse_mode="Markdown")
        else:
            remaining = int((86400 - (current_time - last_claim)) // 3600)
            await query.answer(f"⏳ لقد أخذت مكافأتك اليوم! يرجى العودة بعد {remaining} ساعة.", show_alert=True)

    # حسابي ورابط الإحالة
    elif data == "my_account":
        await query.message.edit_text(
            f"👤 **بيانات حسابك الشخصي:**\n\n"
            f"🆔 **الأيدي:** `{user_id}`\n"
            f"💰 **الرصيد:** `{user[2]}` ليرة\n"
            f"🏅 **المستوى:** {user[4]} (XP: {user[5]}/100)\n"
            f"👥 **إجمالي الإحالات:** {user[3]} إحالة", 
            parse_mode="Markdown"
        )

    elif data == "my_ref":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        await query.message.edit_text(
            f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
            f"👥 **إحالاتك الناجحة:** {user[3]}\n"
            f"💎 **ربح الإحالة:** {get_setting('ref_price')} ليرة لكل صديق يسجل ويؤكد حسابه!", 
            parse_mode="Markdown"
        )

    # خدمات الدعم والطلبات
    elif data == "buy_bot":
        context.user_data['state'] = 'waiting_bot_desc'
        await query.message.edit_text("📝 يرجى إرسال تفاصيل ومواصفات البوت الذي ترغب بشرائه:")

    elif data == "support_msg":
        context.user_data['state'] = 'waiting_support_msg'
        await query.message.edit_text("💬 أرسل رسالتك أو استفسارك للدعم الفني الآن:")

    elif data == "enter_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎟️ أرسل كود الهدية للتحقيق والتفعيل:")

    elif data == "withdraw":
        min_w = get_setting('min_withdraw')
        if user[2] < min_w:
            await query.message.edit_text(f"❌ الحد الأدنى للسحب هو `{min_w}` ليرة. رصيدك الحالي غير كافٍ.", parse_mode="Markdown")
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("شام كاش", callback_data="w_sham"), InlineKeyboardButton("سيريتل كاش", callback_data="w_syriatel")]
            ])
            await query.message.edit_text("💳 اختر طريقة السحب المناسبة لك:", reply_markup=kb)

    elif data in ["w_sham", "w_syriatel"]:
        method = "شام كاش" if data == "w_sham" else "سيريتل كاش"
        context.user_data['withdraw_method'] = method
        context.user_data['state'] = 'waiting_withdraw_num'
        await query.message.edit_text(f"📲 أرسل رقم حسابك على ({method}):")

    # ==================== صالة الألعاب ====================
    elif data == "games_menu":
        cost = get_setting('game_cost')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎡 عجلة الحظ", callback_data="g_wheel"), InlineKeyboardButton("🎲 النرد السحري", callback_data="g_dice")],
            [InlineKeyboardButton("🪙 ملك أم كتاب", callback_data="g_coin"), InlineKeyboardButton("🔢 تخمين الرقم", callback_data="g_num")],
            [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main")]
        ])
        await query.message.edit_text(
            f"🎮 **صالة الألعاب والتحدي:**\n\n"
            f"💵 **تكلفة الجولة:** `{cost}` ليرة.\n"
            f"🏆 كل جولة تمنحك نقاط خبرة XP لرفع مستواك!\n"
            f"اختر اللعبة وابدأ الحظ:", 
            reply_markup=kb, parse_mode="Markdown"
        )

    elif data == "g_wheel":
        cost = get_setting('game_cost')
        if user[2] < cost:
            await query.answer("❌ رصيدك غير كافٍ للعب!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        msg = await context.bot.send_message(chat_id=user_id, text="🌀 **جاري تدوير العجلة...**")
        for frame in ["🎡 [ 🎯 | 🎁 | ❌ ]", "🎡 [ ❌ | 🎯 | 🎁 ]", "🎡 [ 🎁 | ❌ | 🎯 ]"]:
            await asyncio.sleep(0.3)
            await msg.edit_text(frame)

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await msg.edit_text(f"🎉 **فوز رائع!** ربحت `{prize}` ليرة و +15 XP! 🎡", parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await msg.edit_text("❌ **حظاً أوفر!** توقفت العجلة على الخسارة. حصلت على +5 XP.")

    elif data == "g_dice":
        cost = get_setting('game_cost')
        if user[2] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        await context.bot.send_dice(chat_id=user_id, emoji="🎲")
        await asyncio.sleep(2.5)

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **النرد منحك الفوز!** كسبت `{prize}` ليرة!", parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await context.bot.send_message(chat_id=user_id, text="❌ **لم يوفق النرد!** حاول مرة أخرى.")

    elif data == "g_coin":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 ملك", callback_data="coin_king"), InlineKeyboardButton("📖 كتاب", callback_data="coin_book")]
        ])
        await query.message.edit_text("🪙 **اختر وجه العملة التنافسي:**", reply_markup=kb)

    elif data in ["coin_king", "coin_book"]:
        cost = get_setting('game_cost')
        if user[2] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        msg = await context.bot.send_message(chat_id=user_id, text="🪙 **جاري رمي العملة في الهواء...**")
        await asyncio.sleep(1.2)

        win = random.randint(1, 100) <= get_setting('game_win_rate')
        choice = "ملك 👑" if data == "coin_king" else "كتاب 📖"
        
        if win:
            prize = cost * 2
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 15)
            await msg.edit_text(f"🎉 **إجابة صحيحة!** ظهرت العملة على ({choice}) وربحت `{prize}` ليرة!", parse_mode="Markdown")
        else:
            add_xp(user_id, 5)
            await msg.edit_text(f"❌ **تخمين خاطئ!** سقطت العملة على الوجه الآخر.")

    elif data == "g_num":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1", callback_data="num_1"), InlineKeyboardButton("2", callback_data="num_2"), InlineKeyboardButton("3", callback_data="num_3")],
            [InlineKeyboardButton("4", callback_data="num_4"), InlineKeyboardButton("5", callback_data="num_5")]
        ])
        await query.message.edit_text("🔢 **خمن الرقم الذي سيفكر به البوت من (1 إلى 5):**", reply_markup=kb)

    elif data.startswith("num_"):
        cost = get_setting('game_cost')
        if user[2] < cost:
            await query.answer("❌ رصيدك غير كافٍ!", show_alert=True)
            conn.close()
            return
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()

        guessed_num = data.split("_")[1]
        win = random.randint(1, 100) <= get_setting('game_win_rate')

        if win:
            prize = cost * 2.5
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            add_xp(user_id, 20)
            await query.message.edit_text(f"🎉 **ذكاء خارق!** تخمينك للرقم ({guessed_num}) كان صحيحاً وحصلت على `{prize}` ليرة!", parse_mode="Markdown")
        else:
            secret = random.choice([n for n in ["1","2","3","4","5"] if n != guessed_num])
            add_xp(user_id, 5)
            await query.message.edit_text(f"❌ **تخمين خاطئ!** الرقم الصحيح كان ({secret}).")

    elif data == "back_main":
        await main_menu(user_id, context)

    # ==================== لوحة الإدارة ====================
    elif data == "admin_panel" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_users_menu"), InlineKeyboardButton("⚙️ ضبط النظام والأسعار", callback_data="adm_settings_menu")],
            [InlineKeyboardButton("📢 الإذاعة والقنوات", callback_data="adm_ch_bc_menu"), InlineKeyboardButton("🎟️ الأكواد والشحن", callback_data="adm_codes_menu")],
            [InlineKeyboardButton("🔙 العودة للمنيو الرئيسي", callback_data="back_main")]
        ])
        await query.message.edit_text("⚙️ **لوحة التحكم الاحترافية الشاملة:**\nاختر القسم المراد إدارته بنقرة واحدة:", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_users_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك حظر", callback_data="adm_unban")],
            [InlineKeyboardButton("🔍 تقرير عميل", callback_data="adm_user_info"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_user_count")],
            [InlineKeyboardButton("👑 ترقية أدمن", callback_data="adm_add_admin")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("👥 **قسم التحكم في الأعضاء والحسابات:**", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_settings_menu" and is_admin(user_id):
        ref_p = get_setting('ref_price')
        min_w = get_setting('min_withdraw')
        g_cost = get_setting('game_cost')
        w_rate = get_setting('game_win_rate')
        d_bonus = get_setting('daily_bonus')
        m_mode = get_setting('maintenance_mode')
        
        m_status = "⚠️ مفعل (البوت مغلق للعملاء)" if m_mode == 1.0 else "✅ معطل (البوت يعمل للجميع)"

        msg = (
            f"⚙️ **إعدادات البوت الحالية:**\n\n"
            f"• **حالة الصيانة:** {m_status}\n"
            f"• سعر الإحالة: `{ref_p}` ليرة\n"
            f"• الحد الأدنى للسحب: `{min_w}` ليرة\n"
            f"• سعر ضربة الألعاب: `{g_cost}` ليرة\n"
            f"• نسبة الربح في الألعاب: `{w_rate}%`\n"
            f"• المكافأة اليومية: `{d_bonus}` ليرة"
        )
        
        m_btn = InlineKeyboardButton("🟢 إلغاء وضع الصيانة", callback_data="disable_maintenance") if m_mode == 1.0 else InlineKeyboardButton("🛠️ تفعيل وضع الصيانة", callback_data="enable_maintenance")
        
        kb = InlineKeyboardMarkup([
            [m_btn],
            [InlineKeyboardButton("🎯 نسبة الربح بالأزرار", callback_data="adm_set_win_rate_menu")],
            [InlineKeyboardButton("💵 سعر الإحالة", callback_data="adm_set_ref_price"), InlineKeyboardButton("💳 حد السحب", callback_data="adm_set_min_w")],
            [InlineKeyboardButton("🎲 تكلفة اللعبة", callback_data="adm_set_game_cost"), InlineKeyboardButton("🎁 البونص اليومي", callback_data="adm_set_daily_bonus")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")

    # أزرار الصيانة
    elif data == "enable_maintenance" and is_admin(user_id):
        set_setting('maintenance_mode', 1.0)
        await query.answer("🛠️ تم تفعيل وضع الصيانة بنجاح. البوت الآن قيد الصيانة للعملاء.", show_alert=True)
        await query.message.edit_text("⚠️ **تم تفعيل وضع الصيانة.** لن يتمكن أي عميل من استخدام البوت الآن حتى إلغاؤه.")
        await main_menu(user_id, context)

    elif data == "disable_maintenance" and is_admin(user_id):
        set_setting('maintenance_mode', 0.0)
        await query.answer("✅ تم إلغاء وضع الصيانة. البوت يعمل بشكل طبيعي للجميع الآن.", show_alert=True)
        await query.message.edit_text("✅ **تم إلغاء وضع الصيانة.** البوت متاح الآن للعملاء.")
        await main_menu(user_id, context)

    elif data == "adm_set_win_rate_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("10%", callback_data="set_wr_10"), InlineKeyboardButton("30%", callback_data="set_wr_30"), InlineKeyboardButton("50%", callback_data="set_wr_50")],
            [InlineKeyboardButton("70%", callback_data="set_wr_70"), InlineKeyboardButton("🔥 100% (فوز دائم)", callback_data="set_wr_100")],
            [InlineKeyboardButton("✏️ إدخال نسبة يدوياً", callback_data="adm_set_win_rate_manual")],
            [InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data="adm_settings_menu")]
        ])
        await query.message.edit_text("🎯 **تعديل نسبة الربح الحالية في الألعاب مباشرة:**", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("set_wr_") and is_admin(user_id):
        rate = float(data.split("_")[2])
        set_setting('game_win_rate', rate)
        await query.answer(f"✅ تم تعديل نسبة ربح الألعاب إلى {rate}%", show_alert=True)
        await main_menu(user_id, context)

    elif data == "adm_ch_bc_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="adm_add_ch"), InlineKeyboardButton("➖ حذف قناة", callback_data="adm_del_ch")],
            [InlineKeyboardButton("📢 إذاعة عامة لكل المشتركين", callback_data="adm_bc_all")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("📢 **قسم الإذاعة والقنوات الإجبارية:**", reply_markup=kb, parse_mode="Markdown")

    elif data == "adm_codes_menu" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
            [InlineKeyboardButton("🎟️ إنشاء كود هدية", callback_data="adm_gen_code"), InlineKeyboardButton("🧹 تصفير كافة الأرصدة", callback_data="adm_reset_bal")],
            [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
        ])
        await query.message.edit_text("🎟️ **قسم التحكم بالرصيد والكوبونات:**", reply_markup=kb, parse_mode="Markdown")

    elif is_admin(user_id):
        if data == "adm_user_count":
            cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            await query.answer(f"📊 عدد المشتركين الكلي: {cnt} مشترك", show_alert=True)
        elif data == "adm_reset_bal":
            conn.execute("UPDATE users SET balance=0")
            conn.commit()
            await query.message.edit_text("🧹 تم تصفير جميع أرصدة المستخدمين بنجاح.")
        elif data in ["adm_add_bal", "adm_sub_bal", "adm_ban", "adm_unban", "adm_user_info", 
                      "adm_set_ref_price", "adm_set_min_w", "adm_set_game_cost", "adm_set_daily_bonus",
                      "adm_set_win_rate_manual", "adm_add_ch", "adm_del_ch", "adm_add_admin", "adm_gen_code", "adm_bc_all"]:
            
            context.user_data['admin_action'] = data
            hints = {
                "adm_add_bal": "أرسل الأيدي والمبلغ (مثال: `123456789 50`)",
                "adm_sub_bal": "أرسل الأيدي والمبلغ المخصوم (مثال: `123456789 20`)",
                "adm_ban": "أرسل أيدي العميل للحظر",
                "adm_unban": "أرسل أيدي العميل لفك الحظر",
                "adm_user_info": "أرسل أيدي العميل لتأكيد تقرير حسابه",
                "adm_set_ref_price": "أرسل قيمة الإحالة الجديدة",
                "adm_set_min_w": "أرسل قيمة الحد الأدنى للسحب",
                "adm_set_game_cost": "أرسل سعر ضغطة اللعبة",
                "adm_set_daily_bonus": "أرسل قيمة البونص اليومي الجديد",
                "adm_set_win_rate_manual": "أرسل نسبة الربح من 1 إلى 100",
                "adm_add_ch": "أرسل معرف القناة (مثال: `@mychannel`)",
                "adm_del_ch": "أرسل معرف القناة بحذفها (مثال: `@mychannel`)",
                "adm_add_admin": "أرسل أيدي المستخدم لترقيته كـ أدمن",
                "adm_gen_code": "أرسل الكود والمبلغ (مثال: `BONUS 100`)",
                "adm_bc_all": "أرسل النص المراد إذاعته للجميع"
            }
            await query.message.edit_text(f"📥 **إدخال مطلوب:**\n{hints.get(data, 'أرسل المطلوب الآن:')}", parse_mode="Markdown")

    conn.close()

# --- 6. معالج الأرقام السورية ---
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة حالياً.**", reply_markup=ReplyKeyboardRemove())
        return

    contact = update.message.contact
    if contact.user_id != user_id:
        await update.message.reply_text("❌ يرجى مشاركة جهة اتصالك الخاصة فقط.")
        return

    phone = contact.phone_number
    if not phone.startswith("+963") and not phone.startswith("963") and not phone.startswith("09"):
        await update.message.reply_text("❌ اعتذار، الخدمة تعمل حصراً على الأرقام السورية (+963).", reply_markup=ReplyKeyboardRemove())
        return

    conn = sqlite3.connect('bot_data.db')
    conn.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    
    unsub = await check_sub(user_id, context)
    if unsub:
        kb = []
        for ch in unsub:
            kb.append([InlineKeyboardButton(f"الاشتراك في {ch}", url=f"https://t.me/{ch.replace('@','')}")])
        kb.append([InlineKeyboardButton("🔄 تم الاشتراك، تحقق الآن", callback_data="check_sub_again")])
        
        await update.message.reply_text("✅ تم التأكد من الهاتف بنجاح!\n⚠️ يرجى الانضمام للقنوات للتفعيل:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
        await update.message.reply_text("✅ تم إكمال التحقق وتأكيد حسابك بنجاح!", reply_markup=ReplyKeyboardRemove())
        await main_menu(user_id, context)
        
    conn.commit()
    conn.close()

# --- 7. معالجة النصوص وأوامر الإدارة ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    state = context.user_data.get('state')
    adm_action = context.user_data.get('admin_action')

    if get_setting('maintenance_mode') == 1.0 and not is_admin(user_id):
        await update.message.reply_text("⚙️ **البوت قيد الصيانة والتطوير حالياً.**")
        return

    if state == 'waiting_bot_desc':
        await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"🛍️ **طلب شراء بوت جديد:**\nالعميل: `{user_id}`\nالوصف:\n{text}", parse_mode="Markdown")
        await update.message.reply_text("✅ تم استلام طلبك، وسنتواصل معك بأقرب وقت.")
        context.user_data['state'] = None
        return

    if state == 'waiting_support_msg':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 رد", callback_data=f"reply_{user_id}")]])
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            await context.bot.send_photo(chat_id=SUPER_ADMIN, photo=photo, caption=f"💬 **دعم من:** `{user_id}`\n{update.message.caption or ''}", reply_markup=kb, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💬 **دعم من:** `{user_id}`\n\n{text}", reply_markup=kb, parse_mode="Markdown")
        await update.message.reply_text("✅ تم توصيل رسالتك لفريق الدعم.")
        context.user_data['state'] = None
        return

    if state == 'waiting_gift_code':
        conn = sqlite3.connect('bot_data.db')
        code_data = conn.execute("SELECT amount FROM gift_codes WHERE code=?", (text.strip(),)).fetchone()
        if code_data:
            amount = code_data[0]
            conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
            conn.execute("DELETE FROM gift_codes WHERE code=?", (text.strip(),))
            conn.commit()
            await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amount}` ليرة لرصيدك!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم مسبقاً.")
        conn.close()
        context.user_data['state'] = None
        return

    if state == 'waiting_withdraw_num':
        method = context.user_data.get('withdraw_method')
        conn = sqlite3.connect('bot_data.db')
        bal = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
        
        await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💳 **طلب سحب:**\nالعميل: `{user_id}`\nالطريقة: {method}\nالرقم: `{text}`\nالمبلغ: {bal} ليرة", parse_mode="Markdown")
        await update.message.reply_text("✅ تم رفع طلب السحب للادارة وتتم المعالجة الآن.")
        context.user_data['state'] = None
        conn.close()
        return

    # أوامر الأدمن
    if is_admin(user_id) and adm_action:
        conn = sqlite3.connect('bot_data.db')
        try:
            if adm_action == "adm_set_ref_price":
                set_setting('ref_price', float(text))
                await update.message.reply_text("✅ تم تحديث سعر الإحالة.")

            elif adm_action == "adm_set_min_w":
                set_setting('min_withdraw', float(text))
                await update.message.reply_text("✅ تم تحديث حد السحب.")

            elif adm_action == "adm_set_game_cost":
                set_setting('game_cost', float(text))
                await update.message.reply_text("✅ تم تحديث سعر ضغطة اللعبة.")

            elif adm_action == "adm_set_daily_bonus":
                set_setting('daily_bonus', float(text))
                await update.message.reply_text("✅ تم تحديث قيمة البونص اليومي.")

            elif adm_action == "adm_set_win_rate_manual":
                set_setting('game_win_rate', float(text))
                await update.message.reply_text(f"✅ تم تحديث نسبة الربح إلى {text}%.")

            elif adm_action == "adm_add_bal":
                parts = text.split()
                target_id, amount = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target_id))
                conn.commit()
                await update.message.reply_text(f"💰 تم إضافة {amount} ليرة للمستخدم `{target_id}`.", parse_mode="Markdown")

            elif adm_action == "adm_sub_bal":
                parts = text.split()
                target_id, amount = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, target_id))
                conn.commit()
                await update.message.reply_text(f"📉 تم خصم {amount} ليرة من المستخدم `{target_id}`.", parse_mode="Markdown")

            elif adm_action == "adm_ban":
                conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"🚫 تم حظر المستخدم `{text}`.", parse_mode="Markdown")

            elif adm_action == "adm_unban":
                conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"✅ تم فك الحظر عن `{text}`.", parse_mode="Markdown")

            elif adm_action == "adm_user_info":
                u = conn.execute("SELECT full_name, phone, balance, ref_count, is_banned, level, xp FROM users WHERE user_id=?", (int(text),)).fetchone()
                if u:
                    status = "محظور 🚫" if u[4] == 1 else "نشط ✅"
                    await update.message.reply_text(f"👤 **تقرير حساب `{text}`:**\n\n• الاسم: {u[0]}\n• الرقم: {u[1] or 'غير مسجل'}\n• الرصيد: `{u[2]}` ليرة\n• المستوى: {u[5]} (XP: {u[6]})\n• الإحالات: {u[3]}\n• الحالة: {status}", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ لم يتم العثور على هذا المستخدم.")

            elif adm_action == "adm_add_ch":
                ch = text.strip() if text.strip().startswith("@") else f"@{text.strip()}"
                conn.execute("INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", (ch,))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة القناة {ch}.")

            elif adm_action == "adm_del_ch":
                ch = text.strip() if text.strip().startswith("@") else f"@{text.strip()}"
                conn.execute("DELETE FROM channels WHERE channel_username=?", (ch,))
                conn.commit()
                await update.message.reply_text(f"🗑️ تم حذف القناة {ch}.")

            elif adm_action == "adm_add_admin":
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(text),))
                conn.commit()
                await update.message.reply_text(f"👑 تم إضافة الأدمن `{text}` بنجاح.", parse_mode="Markdown")

            elif adm_action == "adm_gen_code":
                parts = text.split()
                code, amount = parts[0], float(parts[1])
                conn.execute("INSERT INTO gift_codes (code, amount) VALUES (?, ?)", (code, amount))
                conn.commit()
                await update.message.reply_text(f"🎟️ تم إنشاء الكود `{code}` بقيمة {amount} ليرة.", parse_mode="Markdown")

            elif adm_action == "adm_bc_all":
                users = conn.execute("SELECT user_id FROM users").fetchall()
                sent = 0
                for u in users:
                    try:
                        await context.bot.send_message(chat_id=u[0], text=text)
                        sent += 1
                        await asyncio.sleep(0.04)
                    except: pass
                await update.message.reply_text(f"📢 تم إرسال الإذاعة بنجاح إلى {sent} مشترك.")

        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ في الصيغة: {e}")

        context.user_data['admin_action'] = None
        conn.close()
        return

# --- 8. تشغيل البوت ---
def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN غير معرّف!")
        
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
