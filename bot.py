import os
import sqlite3
import random
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# --- حل مشكلة Render (فتح منفذ شبكة وهمي للخطة المجانية) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

    def log_message(self, format, *args):
        return  # إيقاف طباعة السجلات الخاصة بالسيرفر الوهمي لمنع الزحمة

def run_health_server():
    # Render يمرر البورت المطلوب تلقائياً في متغير البيئة PORT
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# تشغيل خادم الصحة في Thread منفصل عند بدء البوت
threading.Thread(target=run_health_server, daemon=True).start()


# --- الإعدادات الأساسية ---
TOKEN = os.environ.get("BOT_TOKEN")
SUPER_ADMIN = 7255100997

def init_db():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        phone TEXT,
        balance REAL DEFAULT 0,
        ref_by INTEGER,
        ref_count INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0
    )''')
    
    # جدول الإعدادات العامة
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value REAL
    )''')
    
    # جدول الأدمنية
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    
    # جدول القنوات
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    
    # جدول أكواد الهدايا
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL)''')

    # قيم افتراضية للإعدادات
    defaults = [('ref_price', 5.0), ('min_withdraw', 200.0), ('game_cost', 5.0), ('game_win_rate', 40.0)]
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

# --- التحقق من الاشتراك الإجباري ---
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

# --- أمر البداية /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    
    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT is_banned, is_verified FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    if user and user[0] == 1:
        await update.message.reply_text("❌ أنت محظور من استخدام هذا البوت.")
        conn.close()
        return

    # التسجيل الأولي ومعالجة كود الاحالة
    if not user:
        ref_id = None
        if context.args and context.args[0].isdigit():
            possible_ref = int(context.args[0])
            if possible_ref != user_id:
                ref_id = possible_ref
        conn.execute("INSERT INTO users (user_id, full_name, ref_by) VALUES (?, ?, ?)", (user_id, full_name, ref_id))
        conn.commit()
        user = (0, 0)
    
    conn.close()

    # إذا لم يوافق على الشروط بعد
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافق على الشروط", callback_data="accept_terms")]])
    await update.message.reply_text(
        f"أهلاً وسهلاً بك عزيزي {full_name}!\n\n"
        "⚠️ **الشروط والقوانين:**\n"
        "- يشرط العمل على إحالات سورية حقيقية.\n"
        "- عند اكتشاف أي عملية نصب، تزييف أو إحالات وهمية سيتم حظرك نهائياً وتصفير رصيدك.",
        reply_markup=kb, parse_mode="Markdown"
    )

# --- لوحة التحكم الرئيسية والمقابلة ---
async def main_menu(user_id, context, text="القائمة الرئيسية:"):
    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT full_name, balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    
    msg = f"أهلاً وسهلاً بك {user[0]}\n💰 **رصيدك الحالي:** {user[1]} ليرة"
    
    kb = [
        [InlineKeyboardButton("👤 حسابي", callback_data="my_account"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("🎮 الألعاب", callback_data="games_menu"), InlineKeyboardButton("🛍️ شراء بوت", callback_data="buy_bot")],
        [InlineKeyboardButton("💳 اسحب رصيدي", callback_data="withdraw"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="enter_code")],
        [InlineKeyboardButton("💬 رسالة للدعم", callback_data="support_msg"), InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
        
    await context.bot.send_message(chat_id=user_id, text=f"{msg}\n\n{text}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# --- معالجة الأزرار (Callback Queries) ---
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    conn = sqlite3.connect('bot_data.db')
    user = conn.execute("SELECT is_banned, is_verified, balance, ref_count FROM users WHERE user_id=?", (user_id,)).fetchone()

    if user and user[0] == 1:
        await query.message.edit_text("❌ أنت محظور من استخدام البوت.")
        conn.close()
        return

    # --- قبول الشروط والتحقق من الهاتف ---
    if data == "accept_terms":
        if user[1] == 1:
            await query.message.edit_text("✅ أنت متصل بالفعل.")
            await main_menu(user_id, context)
        else:
            btn = KeyboardButton("📱 مشاركة جهة الاتصال", request_contact=True)
            await context.bot.send_message(
                chat_id=user_id, 
                text="📱 لتأكيد أنك شخص حقيقي، يرجى مشاركة رقم هاتفك السوري عبر الزر أدناه:",
                reply_markup=ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
            )

    # --- تحقق قنوات الاشتراك الإجباري ---
    elif data == "check_sub_again":
        unsub = await check_sub(user_id, context)
        if unsub:
            await query.message.edit_text("❌ لم تقم بالاشتراك بكافة القنوات المطلوب الاشتراك بها بعد!")
        else:
            conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
            
            # احتساب الإحالة للشخص الذي دعاه إن وجد
            ref_by = conn.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
            if ref_by:
                ref_price = get_setting('ref_price')
                conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id=?", (ref_price, ref_by))
                try:
                    await context.bot.send_message(chat_id=ref_by, text=f"🎉 شكراً لإحالتك صديقك! أتم الاختبار وحصلت على {ref_price} ليرة.")
                except: pass
            
            conn.commit()
            await query.message.edit_text("✅ تم التحقق بنجاح! مرحباً بك.")
            await main_menu(user_id, context)

    # --- القوائم الأساسية ---
    elif data == "my_account":
        await query.message.edit_text(f"👤 **بيانات حسابك:**\n\n🆔 **الأيدي:** `{user_id}`\n👥 **عدد إحالاتك:** {user[3]}\n💰 **الرصيد:** {user[2]} ليرة", parse_mode="Markdown")

    elif data == "my_ref":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        await query.message.edit_text(f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n👥 عدد إحالاتك الناجحة: {user[3]}\n💰 تربح عن كل إحالة: {get_setting('ref_price')} ليرة.", parse_mode="Markdown")

    elif data == "buy_bot":
        context.user_data['state'] = 'waiting_bot_desc'
        await query.message.edit_text("📝 يرجى إرسال وصف البوت الذي تريد شراءه وتفاصيله بجميع اللغات/الخصائص:")

    elif data == "support_msg":
        context.user_data['state'] = 'waiting_support_msg'
        await query.message.edit_text("💬 أرسل رسالتك الآن للدعم (يمكنك إرسال نص أو صورة):")

    elif data == "enter_code":
        context.user_data['state'] = 'waiting_gift_code'
        await query.message.edit_text("🎁 أدخل كود الهدية الذي حصلت عليه:")

    elif data == "withdraw":
        min_w = get_setting('min_withdraw')
        if user[2] < min_w:
            await query.message.edit_text(f"❌ الحد الأدنى للسحب هو {min_w} ليرة. رصيدك الحالي {user[2]} ليرة غير كافٍ.")
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("شام كاش", callback_data="w_sham"), InlineKeyboardButton("سيريتل كاش", callback_data="w_syriatel")]
            ])
            await query.message.edit_text("💳 اختر طريقة السحب المفضلة لديك:", reply_markup=kb)

    elif data in ["w_sham", "w_syriatel"]:
        method = "شام كاش" if data == "w_sham" else "سيريتل كاش"
        context.user_data['withdraw_method'] = method
        context.user_data['state'] = 'waiting_withdraw_num'
        await query.message.edit_text(f"📲 يرجى إرسال رقم حسابك على ({method}):")

    # --- قائمة الألعاب ---
    elif data == "games_menu":
        game_cost = get_setting('game_cost')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎰 عجلة الحظ", callback_data="play_wheel"), InlineKeyboardButton("🎲 النرد السحري", callback_data="play_dice")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ])
        await query.message.edit_text(f"🎮 **قائمة الألعاب الحية**\n\n💵 سعر كل ضربة: {game_cost} ليرة.\nاختر اللعبة وابدأ التحدي!", reply_markup=kb, parse_mode="Markdown")

    elif data in ["play_wheel", "play_dice"]:
        cost = get_setting('game_cost')
        if user[2] < cost:
            await query.answer("❌ رصيدك غير كافٍ للعب!", show_alert=True)
            conn.close()
            return
        
        # خصم سعر الضربة
        new_bal = user[2] - cost
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (new_bal, user_id))
        conn.commit()

        win_rate = get_setting('game_win_rate')
        is_win = random.randint(1, 100) <= win_rate

        # أنيميشن وتنفيذ اللعبة حياً داخل التليجرام
        msg = await context.bot.send_message(chat_id=user_id, text="🔄 جاري بدء اللعبة وتدوير النتيجة...")
        
        if data == "play_wheel":
            frames = ["🌀 [ 🎡 | 🎁 | ❌ ]", "🌀 [ ❌ | 🎡 | 🎁 ]", "🌀 [ 🎁 | ❌ | 🎡 ]"]
            for f in frames:
                await asyncio.sleep(0.4)
                await msg.edit_text(f)
            
            if is_win:
                prize = cost * 2
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (prize, user_id))
                conn.commit()
                await msg.edit_text(f"🎉 **مبروك!** ربحت {prize} ليرة في عجلة الحظ! 🎡\n💰 رصيدك الجديد: {new_bal + prize}")
            else:
                await msg.edit_text(f"❌ **للأسف!** حظاً أفر في المرة القادمة.\n💰 رصيدك الجديد: {new_bal}")

        elif data == "play_dice":
            dice_msg = await context.bot.send_dice(chat_id=user_id, emoji="🎲")
            await asyncio.sleep(2.5) # الانتظار لانتهاء انيميشن التليجرام
            
            if is_win:
                prize = cost * 2
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (prize, user_id))
                conn.commit()
                await context.bot.send_message(chat_id=user_id, text=f"🎉 **مبروك!** النرد منحك الفوز بـ {prize} ليرة! 🎲\n💰 رصيدك: {new_bal + prize}")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"❌ **خسارة!** لم يحالفك الحظ هذه المرة.\n💰 رصيدك: {new_bal}")

    elif data == "back_main":
        await main_menu(user_id, context)

    # --- إدارة البوت ---
    elif data == "admin_panel" and is_admin(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك حظر", callback_data="adm_unban")],
            [InlineKeyboardButton("👤 إحالات عميل", callback_data="adm_user_refs"), InlineKeyboardButton("👥 إحالات الجميع", callback_data="adm_all_refs")],
            [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_info"), InlineKeyboardButton("📊 عدد المستخدمين", callback_data="adm_user_count")],
            [InlineKeyboardButton("🎁 بونص ترحيبي", callback_data="adm_add_bonus"), InlineKeyboardButton("🗑️ إزالة البونص", callback_data="adm_rem_bonus")],
            [InlineKeyboardButton("⚙️ تغير سعر الإحالة", callback_data="adm_set_ref_price"), InlineKeyboardButton("⚙️ تغير حد السحب", callback_data="adm_set_min_w")],
            [InlineKeyboardButton("⚙️ تغير سعر اللعبة", callback_data="adm_set_game_cost"), InlineKeyboardButton("🎯 نسبة ربح الألعاب", callback_data="adm_set_win_rate")],
            [InlineKeyboardButton("➕ إضافة قناة", callback_data="adm_add_ch"), InlineKeyboardButton("➖ إزالة قناة", callback_data="adm_del_ch")],
            [InlineKeyboardButton("👑 إضافة أدمن", callback_data="adm_add_admin"), InlineKeyboardButton("🎟️ توليد كود هدية", callback_data="adm_gen_code")],
            [InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_bc_all"), InlineKeyboardButton("✉️ رسالة خاصة", callback_data="adm_pm_user")],
            [InlineKeyboardButton("🧹 تصفير الأرصدة", callback_data="adm_reset_bal")]
        ])
        await query.message.edit_text("⚙️ **لوحة التحكم بالنظام والتحكم السريع:**", reply_markup=kb, parse_mode="Markdown")

    # مفاتيح الإدارة
    elif is_admin(user_id):
        if data == "adm_user_count":
            cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            await query.message.edit_text(f"📊 عدد مستخدمين البوت الكلي: {cnt}")
        elif data == "adm_all_refs":
            tot_refs = conn.execute("SELECT SUM(ref_count) FROM users").fetchone()[0] or 0
            await query.message.edit_text(f"👥 إجمالي الإحالات في البوت: {tot_refs}")
        elif data == "adm_reset_bal":
            conn.execute("UPDATE users SET balance=0")
            conn.commit()
            await query.message.edit_text("🧹 تم تصفير جميع أرصدة المستخدمين بنجاح.")
        elif data in ["adm_add_bal", "adm_sub_bal", "adm_ban", "adm_unban", "adm_user_refs", "adm_user_info", 
                      "adm_add_bonus", "adm_set_ref_price", "adm_set_min_w", "adm_set_game_cost", 
                      "adm_set_win_rate", "adm_add_ch", "adm_del_ch", "adm_add_admin", "adm_gen_code", 
                      "adm_bc_all", "adm_pm_user"]:
            context.user_data['admin_action'] = data
            await query.message.edit_text(f"📥 أرسل المدخلات المطلوبة لتنفيذ الأمر ({data}):")

    conn.close()

# --- معالج الاتصال (الرقم السوري) ---
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    contact = update.message.contact
    
    if contact.user_id != user_id:
        await update.message.reply_text("❌ يرجى مشاركة جهة اتصالك الخاصة بك فقط.")
        return

    phone = contact.phone_number
    if not phone.startswith("+963") and not phone.startswith("963") and not phone.startswith("09"):
        await update.message.reply_text("❌ اعتذار، البوت خاص بالأرقام السورية فقط (+963).", reply_markup=ReplyKeyboardRemove())
        return

    conn = sqlite3.connect('bot_data.db')
    conn.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    
    # فحص القنوات للااشتراك الإجباري
    unsub = await check_sub(user_id, context)
    if unsub:
        kb = []
        for ch in unsub:
            kb.append([InlineKeyboardButton(f"الاشتراك في {ch}", url=f"https://t.me/{ch.replace('@','')}")])
        kb.append([InlineKeyboardButton("🔄 تم الاشتراك، تحقق الآن", callback_data="check_sub_again")])
        
        await update.message.reply_text("✅ تم التأكد من رقمك السوري بنجاح!\n⚠️ يرجى الاشتراك بالقنوات التالية لإكمال التفعيل:", 
                                       reply_markup=InlineKeyboardMarkup(kb), reply_markup_remove=True)
    else:
        conn.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (user_id,))
        await update.message.reply_text("✅ تم التأكد من رقمك والتحقق بنجاح!", reply_markup=ReplyKeyboardRemove())
        await main_menu(user_id, context)
        
    conn.commit()
    conn.close()

# --- معالجة الرسائل النصية والصور والوسائط ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    state = context.user_data.get('state')
    adm_action = context.user_data.get('admin_action')

    # 1. طلبات شراء البوت
    if state == 'waiting_bot_desc':
        for adm in [SUPER_ADMIN]:
            await context.bot.send_message(chat_id=adm, text=f"🛍️ **طلب شراء بوت جديد:**\nمن: {user_id}\nالتفاصيل:\n{text}")
        await update.message.reply_text("✅ تم إرسال طلب شراء البوت للادارة، سيتم التواصل معك قريباً.")
        context.user_data['state'] = None
        return

    # 2. إرسال الدعم
    if state == 'waiting_support_msg':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 رد على الرسالة", callback_data=f"reply_{user_id}")]])
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            await context.bot.send_photo(chat_id=SUPER_ADMIN, photo=photo, caption=f"💬 **رسالة دعم من:** `{user_id}`\n{update.message.caption or ''}", reply_markup=kb, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💬 **رسالة دعم من:** `{user_id}`\n\n{text}", reply_markup=kb, parse_mode="Markdown")
        await update.message.reply_text("✅ تم إرسال رسالتك إلى الدعم بنجاح.")
        context.user_data['state'] = None
        return

    # 3. إدخال كود هدية
    if state == 'waiting_gift_code':
        conn = sqlite3.connect('bot_data.db')
        code_data = conn.execute("SELECT amount FROM gift_codes WHERE code=?", (text,)).fetchone()
        if code_data:
            amount = code_data[0]
            conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
            conn.execute("DELETE FROM gift_codes WHERE code=?", (text,))
            conn.commit()
            await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وحصلت على {amount} ليرة!")
        else:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم من قبل.")
        conn.close()
        context.user_data['state'] = None
        return

    # 4. طلب السحب
    if state == 'waiting_withdraw_num':
        method = context.user_data.get('withdraw_method')
        conn = sqlite3.connect('bot_data.db')
        bal = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
        
        await context.bot.send_message(chat_id=SUPER_ADMIN, text=f"💳 **طلب سحب جديد:**\nالعميل: `{user_id}`\nالطريقة: {method}\nالحساب/الرقم: `{text}`\nالمبلغ: {bal} ليرة", parse_mode="Markdown")
        await update.message.reply_text("✅ تم رفع طلب السحب الخاص بك للادارة بنجاح وسيتم المعالجة فوراً.")
        context.user_data['state'] = None
        conn.close()
        return

    # 5. إجراءات الإدارة
    if is_admin(user_id) and adm_action:
        conn = sqlite3.connect('bot_data.db')
        # تنفيذ الأوامر بحسب adm_action ...
        conn.close()

# --- نقطة التشغيل الرئيسية ---
def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN غير معرّف في متغيرات البيئة!")
        
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
