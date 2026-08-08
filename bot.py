import os
import sqlite3
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
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
        'welcome_enabled': '1',
        'maintenance': '0'
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    cursor.execute("SELECT COUNT(*) FROM wheel_probs")
    if cursor.fetchone()[0] == 0:
        default_probs = {0: 40, 5: 25, 10: 15, 15: 10, 20: 5, 25: 3, 50: 1.5, 100: 0.5, 200: 0.2}
        for p, w in default_probs.items():
            cursor.execute("INSERT OR IGNORE INTO wheel_probs (prize, weight) VALUES (?, ?)", (p, int(w * 10)))
            
    conn.commit()
    conn.close()

init_db()

TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-webapp-url.com/wheel.html") # ضع رابط استضافتك لصفحة الويب الخاصة بالعجلة هنا

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
    
    # تعيين زر القائمة على اليسار (Menu Button) ليفتح صفحة الويب الخاصة بالعجلة أو الواجهة
    try:
        await context.bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(text="🎡 تشغيل البوت", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception:
        pass

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
            cursor.execute("UPDATE users SET referred_by = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
            cursor.execute("SELECT value FROM settings WHERE key='ref_spins'")
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
        [InlineKeyboardButton("🎡 عجلة الحظ (Web)", web_app=WebAppInfo(url=WEBAPP_URL)), InlineKeyboardButton("🎁 كود هدية", callback_data="redeem_code")],
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

    elif data == "admin_panel":
        conn.close()
        if user_id != ADMIN_ID:
            cursor = sqlite3.connect('bot_full_database.db').cursor()
            cursor.execute("SELECT permissions FROM admins WHERE admin_id=?", (user_id,))
            if not cursor.fetchone():
                await query.answer("لست مديراً!", show_alert=True)
                return
        
        cursor = sqlite3.connect('bot_full_database.db').cursor()
        cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
        m_status = "🟢 البوت يعمل" if cursor.fetchone()[0] == '0' else "🔴 البوت في الصيانة"
        conn.close()

        kb = [
            [InlineKeyboardButton("👥 المستخدمين والبحث", callback_data="adm_users"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
            [InlineKeyboardButton("💳 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("📢 القنوات الإجبارية", callback_data="adm_channels")],
            [InlineKeyboardButton("🎁 توليد أكواد الهدايا", callback_data="adm_create_code"), InlineKeyboardButton("⚙️ تحكم البونص والإعدادات", callback_data="adm_settings")],
            [InlineKeyboardButton("🚫 حظر / فك حظر", callback_data="adm_ban_menu"), InlineKeyboardButton(m_status, callback_data="adm_toggle_maint")],
            [InlineKeyboardButton("🛠️ رسائل الدعم", callback_data="adm_support"), InlineKeyboardButton("📢 الإذاعة", callback_data="adm_broadcast")]
        ]
        await query.message.reply_text("👑 لوحة تحكم الإدارة الاحترافية:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        u_count = cursor.fetchone()[0]
        conn.close()
        await query.message.reply_text(f"📊 عدد المستخدمين الكلي في البوت: {u_count}")

    elif data == "adm_users":
        conn.close()
        await query.message.reply_text("🔍 لعرض تفاصيل لاعب أو حظره، أرسل الأمر:\n`/user_info الأيدي`")

    elif data == "adm_ban_menu":
        conn.close()
        await query.message.reply_text("🚫 لحظر مستخدم أرسل:\n`/ban الأيدي`\n\nولفك الحظر أرسل:\n`/unban الأيدي`")

    elif data == "adm_toggle_maint":
        cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
        m_val = cursor.fetchone()[0]
        new_val = '1' if m_val == '0' else '0'
        cursor.execute("UPDATE settings SET value=? WHERE key='maintenance'", (new_val,))
        conn.commit()
        conn.close()
        status_text = "🔴 تم تفعيل وضع الصيانة بنجاح." if new_val == '1' else "🟢 تم إيقاف الصيانة وتشغيل البوت."
        await query.answer(status_text, show_alert=True)
        await query.message.edit_text(status_text)

    elif data == "adm_create_code":
        conn.close()
        kb = [
            [InlineKeyboardButton("🎁 كود لفات عشوائي", callback_data="gen_code_spin"), InlineKeyboardButton("🎁 كود رصيد عشوائي", callback_data="gen_code_balance")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
        ]
        await query.message.reply_text("🎁 اختر نوع الكود التلقائي المراد توليده:", reply_markup=InlineKeyboardMarkup(kb))

    elif data in ["gen_code_spin", "gen_code_balance"]:
        c_type = "spin" if "spin" in data else "balance"
        code = "GIFT-" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=6))
        amount = random.randint(5, 20) if c_type == "spin" else random.randint(50, 200)
        uses = 1
        cursor.execute("INSERT OR REPLACE INTO gift_codes (code, type, amount, uses) VALUES (?, ?, ?, ?)", (code, c_type, amount, uses))
        conn.commit()
        conn.close()
        await query.message.edit_text(f"✅ تم توليد كود الهدية بنجاح!\n\n🎁 الكود: `{code}`\n النوع: {c_type}\n القيمة: {amount}\n الاستخدامات: {uses}", parse_mode="Markdown")

    elif data == "adm_settings":
        cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
        w_en = cursor.fetchone()[0]
        w_status = "🟢 البونص مفعل" if w_en == '1' else "🔴 البونص معطل"
        
        cursor.execute("SELECT value FROM settings WHERE key='welcome_spins'")
        w_val = cursor.fetchone()[0]
        conn.close()

        kb = [
            [InlineKeyboardButton(w_status, callback_data="toggle_welcome")],
            [InlineKeyboardButton("🔄 تغيير قيمة البونص الترحيبي", callback_data="change_welcome_val")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
        ]
        await query.message.reply_text(f"⚙️ إعدادات البونص الترحيبي:\n- القيمة الحالية: {w_val} لفات", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "toggle_welcome":
        cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
        val = cursor.fetchone()[0]
        new_v = '0' if val == '1' else '1'
        cursor.execute("UPDATE settings SET value=? WHERE key='welcome_enabled'", (new_v,))
        conn.commit()
        conn.close()
        await query.answer("✅ تم تحديث حالة البونص.", show_alert=True)
        await admin_panel_refresh(query, context)

    elif data == "change_welcome_val":
        conn.close()
        context.user_data['waiting_new_welcome'] = True
        await query.message.reply_text("أرسل القيمة الجديدة لعدد لفات البونص الترحيبي (رقم فقط):")

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

    elif data == "adm_support":
        conn.close()
        await query.message.reply_text("🛠️ لتلقي رسائل الدعم والرد، تظهر رسائل المستخدمين هنا مع زر الرد المباشر.")

    elif data == "adm_broadcast":
        conn.close()
        context.user_data['waiting_broadcast'] = True
        await query.message.reply_text("📢 أرسل الآن الرسالة الإذاعية لجميع المستخدمين:")

    elif data == "main_menu":
        conn.close()
        await start(update, context)

async def admin_panel_refresh(query, context):
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
    w_en = cursor.fetchone()[0]
    w_status = "🟢 البونص مفعل" if w_en == '1' else "🔴 البونص معطل"
    cursor.execute("SELECT value FROM settings WHERE key='welcome_spins'")
    w_val = cursor.fetchone()[0]
    conn.close()
    kb = [
        [InlineKeyboardButton(w_status, callback_data="toggle_welcome")],
        [InlineKeyboardButton("🔄 تغيير قيمة البونص الترحيبي", callback_data="change_welcome_val")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    try:
        await query.message.edit_text(f"⚙️ إعدادات البونص الترحيبي:\n- القيمة الحالية: {w_val} لفات", reply_markup=InlineKeyboardMarkup(kb))
    except:
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text
    
    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()

    if context.user_data.get('waiting_new_welcome') and user_id == ADMIN_ID:
        context.user_data['waiting_new_welcome'] = False
        try:
            val = int(text)
            cursor.execute("UPDATE settings SET value=? WHERE key='welcome_spins'", (str(val),))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم تحديث قيمة البونص الترحيبي إلى: {val} لفات.")
        except:
            conn.close()
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح.")
        return

    cursor.execute("SELECT verified, captcha_ans FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    if u_row and u_row[0] == 0:
        try:
            ans = int(text)
            if ans == u_row[1]:
                cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
                w_en = cursor.fetchone()[0]
                w_spins = 0
                if w_en == '1':
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
            try:
                await context.bot.send_message(user_id, f"🎁 تم منحك {g_amt} لفات مجانية عبر كود الهدية!")
            except:
                pass
        else:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (g_amt, user_id))
            msg = f"🎉 مبروك! استمتعت بكود الهدية وحصلت على {g_amt} نقطة رصيد."
            try:
                await context.bot.send_message(user_id, f"🎁 تم منحك {g_amt} نقطة رصيد عبر كود الهدية!")
            except:
                pass
        conn.commit()
        conn.close()
        
        await update.message.reply_text(msg)
        try:
            await context.bot.send_message(ADMIN_ID, f"🔔 المستخدم `{user_id}` استخدم كود الهدية `{text}` بنجاح وحصل على {g_amt}.")
        except:
            pass
        return

    if context.user_data.get('waiting_support') or update.message.photo:
        context.user_data['waiting_support'] = False
        conn.close()
        kb = [[InlineKeyboardButton("✍️ رد على المستخدم", callback_data=f"reply_to_{user_id}")]]
        if update.message.photo:
            photo_file = update.message.photo[-1].file_id
            await context.bot.send_photo(ADMIN_ID, photo=photo_file, caption=f"🛠️ رسالة دعم من المستخدم: `{user_id}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            await context.bot.send_message(ADMIN_ID, f"🛠️ رسالة دعم من المستخدم `{user_id}`:\n\n{text}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        await update.message.reply_text("✅ تم إرسال رسالتك إلى الدعم الفني بنجاح وسيتم الرد قريباً.")
        return

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
    elif command == "/ban" and len(cmd) > 1:
        target_id = int(cmd[1])
        cursor.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (target_id,))
        conn.commit()
        await update.message.reply_text(f"🚫 تم حظر المستخدم `{target_id}` بنجاح.")
    elif command == "/unban" and len(cmd) > 1:
        target_id = int(cmd[1])
        cursor.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (target_id,))
        conn.commit()
        await update.message.reply_text(f"✅ تم فك الحظر عن المستخدم `{target_id}` بنجاح.")
    elif command == "/user_info" and len(cmd) > 1:
        target_id = int(cmd[1])
        cursor.execute("SELECT balance, spins, is_banned FROM users WHERE user_id=?", (target_id,))
        u = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (target_id,))
        refs = cursor.fetchone()[0]
        if u:
            await update.message.reply_text(f"👤 معلومات اللاعب `{target_id}`:\n💰 الرصيد: {u[0]}\n🎡 اللفات: {u[1]}\n👥 الإحالات: {refs}\n🚫 محظور: {'نعم' if u[2]==1 else 'لا'}")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود.")
    
    conn.close()

def main():
    if TOKEN:
        application = Application.builder().token(TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler(["addchan", "delchan", "ban", "unban", "user_info"], admin_commands))
        application.add_handler(CallbackQueryHandler(buttons_handler))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
        
        print("Bot is starting via Polling...")
        application.run_polling()

if __name__ == '__main__':
    main()
