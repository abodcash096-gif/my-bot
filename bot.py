import os
import sqlite3
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_ID = 7255100997  # الأيدي الأساسي للمدير

def init_db():
    conn = sqlite3.connect('bot_full_database.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY, 
                        balance REAL DEFAULT 0, 
                        spins INTEGER DEFAULT 3, 
                        referred_by INTEGER, 
                        is_banned INTEGER DEFAULT 0,
                        verified INTEGER DEFAULT 0,
                        captcha_ans INTEGER
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_username TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS wheel_probs (prize INTEGER PRIMARY KEY, weight INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, type TEXT, amount REAL, uses INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, amount REAL, status TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (admin_id INTEGER PRIMARY KEY, permissions TEXT)''')
    
    # الإعدادات الافتراضية
    defaults = {
        'min_withdraw': '100',
        'ref_spins': '1',
        'welcome_spins': '3',
        'maintenance': '0'
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    cursor.execute("SELECT COUNT(*) FROM wheel_probs")
    if cursor.fetchone()[0] == 0:
        default_probs = {0: 40, 5: 25, 10: 15, 15: 10, 20: 5, 25: 3, 50: 1.5, 100: 0.5, 200: 0.2}
        for p, w in default_probs.items():
            cursor.execute("INSERT INTO wheel_probs (prize, weight) VALUES (?, ?)", (p, int(w * 10)))
            
    conn.commit()
    conn.close()

init_db()

TOKEN = os.environ.get("BOT_TOKEN")

async def check_subscription(user_id, bot):
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT channel_username FROM channels")
    channels = cursor.fetchall()
    conn.close()
    
    for ch in channels:
        ch_username = ch[0]
        try:
            member = await bot.get_chat_member(chat_id=ch_username, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            pass
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    
    # فحص الصيانة
    cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
    m_val = cursor.fetchone()[0]
    if m_val == '1' and user_id != ADMIN_ID:
        cursor.execute("SELECT permissions FROM admins WHERE admin_id=?", (user_id,))
        if not cursor.fetchone():
            await update.message.reply_text("🛠️ البوت في حالة صيانة حالياً، يرجى العودة لاحقاً.")
            conn.close()
            return

    cursor.execute("SELECT is_banned, verified FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    if user_data and user_data[0] == 1:
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت.")
        conn.close()
        return

    # فحص كابتشا منع الروبوتات
    if not user_data or user_data[1] == 0:
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        ans = num1 + num2
        cursor.execute("INSERT OR REPLACE INTO users (user_id, verified, captcha_ans, spins) VALUES (?, 0, ?, 0)", (user_id, ans))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🤖 لمنع الروبوتات، أجب على المسألة التالية:\nكم ناتج جمع: {num1} + {num2}؟\nأرسل الرقم في رسالة.")
        return

    # معالجة الإحالة المعلقة إذا اشترك بالقنوات
    cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
    ref_row = cursor.fetchone()
    if ref_row and ref_row[0]:
        ref_id = ref_row[0]
        is_sub = await check_subscription(user_id, context.bot)
        if is_sub:
            # التأكد من عدم احتسابها مسبقاً
            cursor.execute("UPDATE users SET referred_by = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
            cursor.execute("SELECT value FROM settings WHERE key='ref_spins'", ( ))
            r_spins = int(cursor.fetchone()[0])
            cursor.execute("UPDATE users SET spins = spins + ? WHERE user_id = ?", (r_spins, ref_id))
            conn.commit()
            try:
                await context.bot.send_message(ref_id, f"🎉 انضم شخص جديد عبر رابط إحالتك وأتم اشتراكاته، وحصلت على {r_spins} لفة مجانية!")
            except:
                pass

    if context.args and not user_data:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:
                cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))
                conn.commit()
        except:
            pass

    cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("💰 رصيدي", callback_data="my_balance"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="ref_link")],
        [InlineKeyboardButton("💳 طلب سحب", callback_data="withdraw"), InlineKeyboardButton("🛠️ الدعم", callback_data="support")],
        [InlineKeyboardButton("🎡 العجلة (تليجرام)", callback_data="spin_wheel"), InlineKeyboardButton("🎁 كود هدية", callback_data="redeem_code")],
        [InlineKeyboardButton("🛒 شراء بوت", callback_data="buy_bot"), InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 لوحة الإدارة", callback_data="admin_panel")])

    await update.message.reply_text(f"مرحباً بك يا عبود في بوتك المجاني السريع 🚀\n\nرصيدك: {data[0]} نقطة | اللفات المتاحة: {data[1]}", reply_markup=InlineKeyboardMarkup(keyboard))

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()

    if data == "my_balance":
        cursor.execute("SELECT balance, spins FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        await query.answer(f"🆔 أيديك: {user_id}\n💰 رصيدك: {res[0]} نقطة\n🎡 اللفات المتاحة: {res[1]}", show_alert=True)
    
    elif data == "ref_link":
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        refs_count = cursor.fetchone()[0]
        conn.close()
        ref_url = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.message.reply_text(f"🔗 رابط إحالتك الشخصي:\n{ref_url}\n\n👥 عدد الأشخاص الذين أحلتهم: {refs_count}\n\n⚠️ ملاحظة: لا تحتسب الإحالة إلا بعد اشتراك العضو بالقنوات الإجبارية!")

    elif data == "withdraw":
        conn.close()
        kb = [
            [InlineKeyboardButton("شام كاش", callback_data="w_sham"), InlineKeyboardButton("سيريتل كاش", callback_data="w_syriatel")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.reply_text("💳 اختر وسيلة السحب:", reply_markup=InlineKeyboardMarkup(kb))

    elif data in ["w_sham", "w_syriatel"]:
        method = "شام كاش" if data == "w_sham" else "سيريتل كاش"
        context.user_data['withdraw_method'] = method
        conn.close()
        await query.message.reply_text(f"أرسل الآن المبلغ المراد سحبه وعنوان المحفظة أو رقم الحساب لـ ({method}):")

    elif data == "support":
        conn.close()
        context.user_data['waiting_support'] = True
        await query.message.reply_text("🛠️ أرسل الآن رسالتك أو صورتك للدعم الفني وسيتم تحويلها للإدارة مباشرة.")

    elif data == "buy_bot":
        conn.close()
        await query.message.reply_text("🛒 لشراء بوت خاص بك:\nنحن نقوم بتصميم وبرمجة بوتات التليجرام بمختلف الأنواع (بوتات أرباح، إحالات، متاجر، خدمات عامة).\nلطلب الشراء واستعراض الأسعار، تواصل مع قناة المبرمج: @lerafree")

    elif data == "redeem_code":
        conn.close()
        context.user_data['waiting_code'] = True
        await query.message.reply_text("🎁 أرسل كود الهدية الآن:")

    elif data == "spin_wheel":
        cursor.execute("SELECT spins FROM users WHERE user_id = ?", (user_id,))
        spins = cursor.fetchone()[0]
        if spins <= 0:
            conn.close()
            await query.answer("❌ لا تملك لفات كافية! قم بدعوة أصدقائك للحصول على لفات جديدة.", show_alert=True)
            return

        cursor.execute("UPDATE users SET spins = spins - 1 WHERE user_id = ?", (user_id,))
        
        # خوارزمية الأوزان للعجلة
        cursor.execute("SELECT prize, weight FROM wheel_probs")
        probs = cursor.fetchall()
        conn.close()

        prizes = [p[0] for p in probs]
        weights = [p[1] for p in probs]
        won_prize = random.choices(prizes, weights=weights)[0]

        conn = sqlite3.connect('bot_full_database.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (won_prize, user_id))
        conn.commit()
        conn.close()

        await query.message.reply_text(f"🎡 تدور العجلة...\n\n🎉 مبروك! لقد ربحت من العجلة مبلغ: {won_prize} نقطة!")

    elif data == "admin_panel":
        conn.close()
        if user_id != ADMIN_ID:
            cursor = sqlite3.connect('bot_full_database.db').cursor()
            cursor.execute("SELECT permissions FROM admins WHERE admin_id=?", (user_id,))
            if not cursor.fetchone():
                await query.answer("لست مديراً!", show_alert=True)
                return
        
        kb = [
            [InlineKeyboardButton("👥 المستخدمين", callback_data="adm_users"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
            [InlineKeyboardButton("💳 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("📢 القنوات الإجبارية", callback_data="adm_channels")],
            [InlineKeyboardButton("🎁 إدارة الكودات والمنح", callback_data="adm_gifts"), InlineKeyboardButton("⚙️ الإعدادات والبونص", callback_data="adm_settings")],
            [InlineKeyboardButton("🛠️ رسائل الدعم", callback_data="adm_support"), InlineKeyboardButton("📢 الإذاعة", callback_data="adm_broadcast")]
        ]
        await query.message.reply_text("👑 لوحة تحكم الإدارة:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        u_count = cursor.fetchone()[0]
        conn.close()
        await query.message.reply_text(f"📊 عدد المستخدمين الكلي في البوت: {u_count}")

    elif data == "adm_users":
        cursor.execute("SELECT user_id, balance, spins FROM users")
        all_u = cursor.fetchall()
        conn.close()
        text = "👥 قائمة اللاعبين:\n\n"
        for u in all_u[:30]:
            text += f"🆔 `{u[0]}` | الرصيد: {u[1]} | اللفات: {u[2]}\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    elif data == "adm_withdraws":
        cursor.execute("SELECT id, user_id, method, amount FROM withdrawals WHERE status='pending'")
        reqs = cursor.fetchall()
        conn.close()
        if not reqs:
            await query.message.reply_text("لا توجد طلبات سحب معلقة.")
            return
        for r in reqs:
            kb = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"w_ok_{r[0]}"), InlineKeyboardButton("❌ رفض", callback_data=f"w_no_{r[0]}")]
            ]
            await query.message.reply_text(f"💳 طلب سحب:\n🆔 الأيدي: {r[1]}\nالطريقة: {r[2]}\nالمبلغ: {r[3]}", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("w_ok_") or data.startswith("w_no_"):
        req_id = int(data.split("_")[2])
        status = "approved" if "ok" in data else "rejected"
        cursor.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (req_id,))
        w_data = cursor.fetchone()
        cursor.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, req_id))
        conn.commit()
        conn.close()
        if w_data:
            u_id, amt = w_data
            try:
                msg = f"✅ تم قبول طلب سحبك بقيمة {amt} بنجاح!" if status == "approved" else f"❌ تم رفض طلب سحبك بقيمة {amt}."
                await context.bot.send_message(u_id, msg)
            except:
                pass
        await query.message.edit_text(f"تم تحديث حالة الطلب إلى: {status}")

    elif data == "adm_channels":
        cursor.execute("SELECT channel_username FROM channels")
        chs = cursor.fetchall()
        conn.close()
        text = "📢 القنوات الإجبارية الحالية:\n" + "\n".join([c[0] for c in chs]) + "\n\nلإضافة قناة أرسل: `/addchan @username`\nلإلغاء قناة أرسل: `/delchan @username`"
        await query.message.reply_text(text)

    elif data == "adm_gifts":
        conn.close()
        await query.message.reply_text("🎁 لإنشاء كود هدية أرسل الأمر:\n`/createcode الكود النوع(spin/balance) القيمة العدد`")

    elif data == "adm_settings":
        cursor.execute("SELECT key, value FROM settings")
        sets = cursor.fetchall()
        conn.close()
        text = "⚙️ الإعدادات الحالية:\n" + "\n".join([f"{s[0]}: {s[1]}" for s in sets]) + "\n\nلتعديل حد السحب أرسل: `/setminval الرقم`"
        await query.message.reply_text(text)

    elif data == "adm_support":
        conn.close()
        await query.message.reply_text("🛠️ لتلقي رسائل الدعم والرد، تظهر الرسائل مباشرة مع زر الرد عند إرسال المستخدمين.")

    elif data == "adm_broadcast":
        conn.close()
        context.user_data['waiting_broadcast'] = True
        await query.message.reply_text("📢 أرسل الآن الرسالة الإذاعية لجميع المستخدمين:")

    elif data == "main_menu":
        conn.close()
        await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text
    
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()

    # فحص الكابتشا
    cursor.execute("SELECT verified, captcha_ans FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    if u_row and u_row[0] == 0:
        try:
            ans = int(text)
            if ans == u_row[1]:
                cursor.execute("SELECT value FROM settings WHERE key='welcome_spins'")
                w_spins = int(cursor.fetchone()[0])
                cursor.execute("UPDATE users SET verified = 1, spins = ? WHERE user_id = ?", (w_spins, user_id))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم التحقق بنجاح!\n🎉 حصلت على بونص ترحيبي بقيمة {w_spins} لفات مجانية!")
                await start(update, context)
            else:
                await update.message.reply_text("❌ الإجابة خاطئة، حاول مجدداً:")
        except:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح كإجابة:")
        return

    # إدخال كود الهدية
    if context.user_data.get('waiting_code'):
        context.user_data['waiting_code'] = False
        cursor.execute("SELECT type, amount, uses FROM gift_codes WHERE code = ?", (text,))
        g_data = cursor.fetchone()
        if not g_data or g_data[2] <= 0:
            conn.close()
            await update.message.reply_text("❌ الكود غير صالح أو انتهت صلاحيته.")
            return
        
        g_type, g_amt, g_uses = g_data
        cursor.execute("UPDATE gift_codes SET uses = uses - 1 WHERE code = ?", (text,))
        if g_type == 'spin':
            cursor.execute("UPDATE users SET spins = spins + ? WHERE user_id = ?", (g_amt, user_id))
            msg = f"🎉 مبروك! استمتعت بكود الهدية وحصلت على {g_amt} لفات."
        else:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (g_amt, user_id))
            msg = f"🎉 مبروك! استمتعت بكود الهدية وحصلت على {g_amt} نقطة رصيد."
        conn.commit()
        conn.close()
        
        # إشعار لمن استخدم الكود
        await update.message.reply_text(msg)
        try:
            await context.bot.send_message(ADMIN_ID, f"🔔 المستخدم `{user_id}` استخدم كود الهدية `{text}` بنجاح وحصل على {g_amt}.")
        except:
            pass
        return

    # رسائل الدعم
    if context.user_data.get('waiting_support') or update.message.photo:
        context.user_data['waiting_support'] = False
        conn.close()
        kb = [[InlineKeyboardButton("✍️ رد على المستخدم", callback_data=f"reply_to_{user_id}")]]
        if update.message.photo:
            photo_file = update.message.photo[-1].file_id
            await context.bot.send_photo(ADMIN_ID, photo=photo_file, caption=f"🛠️ رسالة دعم من المستخدم: `{user_id}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            await context.bot.send_message(ADMIN_ID, f"🛠️ رسالة دعم من المستخدم `{user_id}`:\n\n{text}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        await update.message.reply_text("✅ تم إرسال رسالتك إلى الدعم الفني بنجاح وستم الرد قريباً.")
        return

    # الإذاعة
    if context.user_data.get('waiting_broadcast') and user_id == ADMIN_ID:
        context.user_data['waiting_broadcast'] = False
        cursor.execute("SELECT user_id FROM users")
        all_users = cursor.fetchall()
        conn.close()
        count = 0
        for u in all_users:
            try:
                await context.bot.send_message(u[0], text)
                count += 1
            except:
                pass
        await update.message.reply_text(f"📢 تمت الإذاعة بنجاح إلى {count} مستخدم.")
        return

    conn.close()

async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    cmd = update.message.text.split()
    command = cmd[0]
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()

    if command == "/addchan" and len(cmd) > 1:
        ch = cmd[1]
        cursor.execute("INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", (ch,))
        conn.commit()
        await update.message.reply_text(f"✅ تمت إضافة القناة {ch} بنجاح.")
    elif command == "/delchan" and len(cmd) > 1:
        ch = cmd[1]
        cursor.execute("DELETE FROM channels WHERE channel_username = ?", (ch,))
        conn.commit()
        await update.message.reply_text(f"❌ تم حذف القناة {ch}.")
    elif command == "/setminval" and len(cmd) > 1:
        val = cmd[1]
        cursor.execute("UPDATE settings SET value=? WHERE key='min_withdraw'", (val,))
        conn.commit()
        await update.message.reply_text(f"✅ تم تحديث الحد الأدنى للسحب إلى {val}.")
    elif command == "/createcode" and len(cmd) > 4:
        # /createcode CODE type amount uses
        code, c_type, amt, uses = cmd[1], cmd[2], float(cmd[3]), int(cmd[4])
        cursor.execute("INSERT OR REPLACE INTO gift_codes (code, type, amount, uses) VALUES (?, ?, ?, ?)", (code, c_type, amt, uses))
        conn.commit()
        await update.message.reply_text(f"🎁 تم إنشاء كود الهدية `{code}` بنجاح!")
    
    conn.close()

def main():
    if TOKEN:
        application = Application.builder().token(TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler(["addchan", "delchan", "setminval", "createcode"], admin_commands))
        application.add_handler(CallbackQueryHandler(buttons_handler))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
        
        print("Bot is starting via Polling...")
        application.run_polling()

if __name__ == '__main__':
    main()
