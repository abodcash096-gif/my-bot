import os
import sqlite3
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, type TEXT, amount REAL, uses INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, amount REAL, status TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (admin_id INTEGER PRIMARY KEY, permissions TEXT)''')
    
    # الإعدادات الافتراضية (مكافأة الإحالة 5 ليرات كما طلبت)
    defaults = {
        'min_withdraw': '100',
        'ref_reward': '5',    # مكافأة الإحالة الافتراضية 5 ليرات
        'welcome_reward': '20', 
        'welcome_enabled': '1',
        'game_cost': '5',    
        'maintenance': '0',
        'win_chance': '50'   # نسبة الخوارزمية للربح بالمئة
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
    conn.commit()
    conn.close()

init_db()

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()
    
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

    if not user_data or user_data[1] == 0:
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        ans = num1 + num2
        cursor.execute("INSERT OR REPLACE INTO users (user_id, verified, captcha_ans, balance) VALUES (?, 0, ?, 0)", (user_id, ans))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🤖 لمنع الروبوتات، أجب على المسألة التالية:\nكم ناتج جمع: {num1} + {num2}؟\nأرسل الرقم في رسالة.")
        return

    if context.args and not user_data:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:
                cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))
                conn.commit()
                cursor.execute("SELECT value FROM settings WHERE key='ref_reward'")
                r_reward = float(cursor.fetchone()[0])
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (r_reward, referrer_id))
                conn.commit()
                try:
                    await context.bot.send_message(referrer_id, f"🎉 انضم شخص جديد عبر رابط إحالتك، وحصلت على {r_reward} ليرة!")
                except:
                    pass
        except:
            pass

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("💰 رصيدي", callback_data="my_balance"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="ref_link")],
        [InlineKeyboardButton("🎮 قسم الألعاب والتسلية", callback_data="games_menu")],
        [InlineKeyboardButton("💳 طلب سحب", callback_data="withdraw"), InlineKeyboardButton("🎁 كود هدية", callback_data="redeem_code")],
        [InlineKeyboardButton("🛠️ الدعم الفني", callback_data="support"), InlineKeyboardButton("📢 قناة المبرمج", url="https://t.me/lerafree")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 لوحة الإدارة", callback_data="admin_panel")])

    await update.message.reply_text(f"مرحباً بك يا عبود في بوت الألعاب والأرباح المتطور 🚀\n\n💰 رصيدك الحالي: {data[0]} ليرة", reply_markup=InlineKeyboardMarkup(keyboard))

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    conn = sqlite3.connect('bot_full_database.db')
    cursor = conn.cursor()

    if data == "my_balance":
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        await query.answer(f"🆔 أيديك: {user_id}\n💰 رصيدك: {res[0]} ليرة", show_alert=True)
    
    elif data == "ref_link":
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        refs_count = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM settings WHERE key='ref_reward'")
        r_reward = cursor.fetchone()[0]
        conn.close()
        ref_url = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.message.reply_text(f"🔗 رابط إحالتك الشخصي:\n{ref_url}\n\n👥 عدد الأشخاص الذين أحلتهم: {refs_count}\n🎁 تربح {r_reward} ليرة عن كل شخص يضغط على رابطك ويدخل البوت!")

    elif data == "games_menu":
        cursor.execute("SELECT value FROM settings WHERE key='game_cost'")
        g_cost = cursor.fetchone()[0]
        conn.close()
        kb = [
            [InlineKeyboardButton("📦 لعبة الصناديق السحرية", callback_data="game_boxes")],
            [InlineKeyboardButton("🎡 عجلة الحظ التفاعلية", callback_data="game_wheel")],
            [InlineKeyboardButton("❌ لعبة إكس-أو (X/O)", callback_data="game_xo")],
            [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
        ]
        await query.message.edit_text(f"🎮 **أهلاً بك في قسم الألعاب الحية!**\n\n⚠️ تكلفة الضربة/اللعبة الواحدة: `{g_cost}` ليرة.\nاختر إحدى الألعاب للاستمتاع ومضاعفة أرباحك:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    def check_win_algorithm():
        cursor.execute("SELECT value FROM settings WHERE key='win_chance'")
        chance = int(cursor.fetchone()[0])
        rand_val = random.randint(1, 100)
        return rand_val <= chance

    # --- 1. لعبة الصناديق ---
    elif data == "game_boxes":
        cursor.execute("SELECT balance, value FROM users JOIN settings WHERE user_id = ? AND key='game_cost'", (user_id,))
        row = cursor.fetchone()
        balance = row[0]
        cost = float(row[1])
        if balance < cost:
            conn.close()
            await query.answer(f"❌ رصيدك غير كافٍ! تحتاج إلى {cost} ليرة للعب.", show_alert=True)
            return
        
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()
        
        is_winner = check_win_algorithm()
        conn.close()

        await query.message.edit_text("📦 جاري فتح الصناديق السحرية...\n箱 📭 📭 📭")
        await asyncio_sleep(0.8)
        await query.message.edit_text("📦 جاري فتح الصناديق السحرية...\n🎁 📭 📭")
        await asyncio_sleep(0.8)
        
        if is_winner:
            prize = random.choice([10, 25, 50, 100, 200])
            conn = sqlite3.connect('bot_full_database.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            conn.close()
            res_text = f"🎉 مبروك! اخترت الصندوق الصحيح وربحت **{prize}** ليرة داخل الصندوق الذهبي حسب الخوارزمية! ✨"
        else:
            res_text = "❌ عشوائياً كان صندوقاً فارغاً، حظاً أوفر في المرة القادمة!"

        kb = [[InlineKeyboardButton("🎮 لعب مرة أخرى", callback_data="game_boxes"), InlineKeyboardButton("🔙 الألعاب", callback_data="games_menu")]]
        await query.message.edit_text(res_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # --- 2. لعبة العجلة التفاعلية ---
    elif data == "game_wheel":
        cursor.execute("SELECT balance, value FROM users JOIN settings WHERE user_id = ? AND key='game_cost'", (user_id,))
        row = cursor.fetchone()
        balance = row[0]
        cost = float(row[1])
        if balance < cost:
            conn.close()
            await query.answer(f"❌ رصيدك غير كافٍ! تحتاج إلى {cost} ليرة للعب.", show_alert=True)
            return
        
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()
        
        is_winner = check_win_algorithm()
        conn.close()

        frames = [
            "🎡 تدور العجلة: 🔄 [ 10 | 50 | 0 | 100 ]",
            "🎡 تدور العجلة: 🔄 [ 50 | 0 | 100 | 25 ]",
            "🎡 تدور العجلة: 🔄 [ 0 | 100 | 25 | 10 ]",
            "🎡 تدور العجلة: 🔄 [ 100 | 25 | 10 | 50 ]"
        ]
        for f in frames:
            await query.message.edit_text(f)
            await asyncio_sleep(0.5)

        if is_winner:
            prize = random.choice([20, 50, 100, 250, 500])
            conn = sqlite3.connect('bot_full_database.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, user_id))
            conn.commit()
            conn.close()
            res_text = f"🎯 توقفت العجلة بنجاح!\n🎉 مبروك ربحت **{prize}** ليرة إضافية وفق الخوارزمية المحددة!"
        else:
            res_text = "🎯 توقفت العجلة على الصفر.. حظاً أوفر في المرة القادمة!"

        kb = [[InlineKeyboardButton("🎡 لفة أخرى", callback_data="game_wheel"), InlineKeyboardButton("🔙 الألعاب", callback_data="games_menu")]]
        await query.message.edit_text(res_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # --- 3. لعبة إكس أو ---
    elif data == "game_xo":
        cursor.execute("SELECT balance, value FROM users JOIN settings WHERE user_id = ? AND key='game_cost'", (user_id,))
        row = cursor.fetchone()
        balance = row[0]
        cost = float(row[1])
        if balance < cost:
            conn.close()
            await query.answer(f"❌ رصيدك غير كافٍ للبدء!", show_alert=True)
            return
        
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        conn.commit()
        
        is_winner = check_win_algorithm()
        conn.close()

        await query.message.edit_text("❌ ⭕ جاري تحضير ساحة المعركة وتوزيع الرموز...")
        await asyncio_sleep(0.8)

        if is_winner:
            reward = 40
            conn = sqlite3.connect('bot_full_database.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
            conn.commit()
            conn.close()
            res_text = f"❌ ⭕\n[ X ] [ O ] [ X ]\n[ O ] [ X ] [ _ ]\n[ X ] [ _ ] [ O ]\n\n🏆 هنيئاً لك! لقد هزمت البوت بذكائك وربحت **{reward}** ليرة!"
        else:
            res_text = "❌ ⭕\n[ X ] [ O ] [ X ]\n[ X ] [ O ] [ O ]\n[ O ] [ X ] [ X ]\n\n🤖 تفوق عليك البوت هذه المرة! حظاً أوفر."

        kb = [[InlineKeyboardButton("🎮 العب مجدداً", callback_data="game_xo"), InlineKeyboardButton("🔙 الألعاب", callback_data="games_menu")]]
        await query.message.edit_text(res_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data == "withdraw":
        conn.close()
        kb = [
            [InlineKeyboardButton("شام كاش", callback_data="w_sham"), InlineKeyboardButton("سيريتل كاش", callback_data="w_syriatel")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.edit_text("💳 اختر وسيلة السحب المفضلة لديك:", reply_markup=InlineKeyboardMarkup(kb))

    elif data in ["w_sham", "w_syriatel"]:
        method = "شام كاش" if data == "w_sham" else "سيريتل كاش"
        context.user_data['withdraw_method'] = method
        conn.close()
        await query.message.edit_text(f"أرسل الآن المبلغ المراد سحبه ورقم الحساب أو المحفظة لـ ({method}):")

    elif data == "support":
        conn.close()
        context.user_data['waiting_support'] = True
        await query.message.edit_text("🛠️ أرسل الآن رسالتك أو صورتك للدعم الفني وسيقوم فريق الإدارة بالرد عليك.")

    elif data == "redeem_code":
        conn.close()
        context.user_data['waiting_code'] = True
        await query.message.edit_text("🎁 أرسل كود الهدية الخاص بك هنا:")

    elif data == "admin_panel":
        conn.close()
        if user_id != ADMIN_ID:
            cursor = sqlite3.connect('bot_full_database.db').cursor()
            cursor.execute("SELECT permissions FROM admins WHERE admin_id=?", (user_id,))
            if not cursor.fetchone():
                await query.answer("لست مديراً للبوت!", show_alert=True)
                return
        
        cursor = sqlite3.connect('bot_full_database.db').cursor()
        cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
        m_status = "🟢 البوت يعمل" if cursor.fetchone()[0] == '0' else "🔴 البوت بالصيانة"
        cursor.execute("SELECT value FROM settings WHERE key='win_chance'")
        current_chance = cursor.fetchone()[0]
        conn.close()

        kb = [
            [InlineKeyboardButton("👥 المستخدمين والبحث", callback_data="adm_users"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
            [InlineKeyboardButton("💳 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("🎁 توليد كود هدية", callback_data="adm_create_code")],
            [InlineKeyboardButton(f"🎯 نسبة الفوز: {current_chance}%", callback_data="adm_chance_menu")],
            [InlineKeyboardButton("⚙️ إعدادات البونص والأسعار", callback_data="adm_settings"), InlineKeyboardButton(m_status, callback_data="adm_toggle_maint")],
            [InlineKeyboardButton("🚫 حظر / فك حظر مستخدم", callback_data="adm_ban_menu"), InlineKeyboardButton("📢 الإذاعة", callback_data="adm_broadcast")]
        ]
        await query.message.edit_text("👑 لوحة تحكم الإدارة الاحترافية:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_chance_menu":
        conn.close()
        kb = [
            [InlineKeyboardButton("20 % 📉", callback_data="set_chance_20"), InlineKeyboardButton("40 % 📊", callback_data="set_chance_40")],
            [InlineKeyboardButton("50 % ⚖️", callback_data="set_chance_50"), InlineKeyboardButton("70 % 📈", callback_data="set_chance_70")],
            [InlineKeyboardButton("90 % 🔥", callback_data="set_chance_90"), InlineKeyboardButton("100 % 🚀", callback_data="set_chance_100")],
            [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]
        ]
        await query.message.edit_text("🎯 **تحكم بخوارزمية وحظوظ الربح مئوياً:**\nاختر النسبة المئوية المحددة لفوز المستخدمين في الألعاب:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data.startswith("set_chance_"):
        new_chance = data.split("_")[2]
        cursor.execute("UPDATE settings SET value=? WHERE key='win_chance'", (new_chance,))
        conn.commit()
        conn.close()
        await query.answer(f"✅ تم تعديل نسبة الفوز الخوارزمية لتصبح {new_chance}% بنجاح!", show_alert=True)
        query.data = "admin_panel"
        await buttons_handler(update, context)

    elif data == "adm_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        u_count = cursor.fetchone()[0]
        conn.close()
        await query.message.edit_text(f"📊 إحصائيات البوت الكلية:\n- عدد المستخدمين المسجلين: {u_count}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]))

    elif data == "adm_users":
        conn.close()
        await query.message.edit_text("🔍 لعرض تفاصيل مستخدم أو البحث عنه أرسل الأمر:\n`/user_info الأيدي`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]))

    elif data == "adm_ban_menu":
        conn.close()
        await query.message.edit_text("🚫 لحظر أي مستخدم أرسل الأمر:\n`/ban الأيدي`\n\nولفك الحظر أرسل:\n`/unban الأيدي`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]))

    elif data == "adm_toggle_maint":
        cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
        m_val = cursor.fetchone()[0]
        new_val = '1' if m_val == '0' else '0'
        cursor.execute("UPDATE settings SET value=? WHERE key='maintenance'", (new_val,))
        conn.commit()
        conn.close()
        status_text = "🔴 تم تفعيل وضع الصيانة بنجاح." if new_val == '1' else "🟢 تم إيقاف الصيانة وتشغيل البوت."
        await query.answer(status_text, show_alert=True)
        query.data = "admin_panel"
        await buttons_handler(update, context)

    elif data == "adm_create_code":
        conn.close()
        code = "GIFT-" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=6))
        amount = random.randint(20, 100)
        uses = 1
        cursor = sqlite3.connect('bot_full_database.db')
        cursor.execute("INSERT OR REPLACE INTO gift_codes (code, type, amount, uses) VALUES (?, 'balance', ?, ?)", (code, amount, uses))
        cursor.commit()
        cursor.close()
        await query.message.edit_text(f"✅ تم توليد كود الهدية بنجاح!\n\n🎁 الكود: `{code}`\n💰 القيمة: {amount} ليرة\n الاستخدامات: {uses}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]))

    elif data == "adm_settings":
        cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
        w_en = cursor.fetchone()[0]
        w_status = "🟢 البونص الترحيبي مفعل" if w_en == '1' else "🔴 البونص الترحيبي معطل"
        
        cursor.execute("SELECT value FROM settings WHERE key='welcome_reward'")
        w_val = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM settings WHERE key='game_cost'")
        g_cost = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM settings WHERE key='ref_reward'")
        r_val = cursor.fetchone()[0]
        conn.close()

        kb = [
            [InlineKeyboardButton(w_status, callback_data="toggle_welcome")],
            [InlineKeyboardButton("🔄 تعديل قيمة بونص الترحيب", callback_data="change_welcome_val")],
            [InlineKeyboardButton("🔗 تعديل مكافأة الإحالة (لكل شخص)", callback_data="change_ref_val")],
            [InlineKeyboardButton("💰 تعديل سعر ضربة/لعبة الألعاب", callback_data="change_game_cost")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
        ]
        await query.message.edit_text(f"⚙️ إعدادات المكافآت والأسعار:\n- قيمة البونص: {w_val} ليرة\n- مكافأة الإحالة: {r_val} ليرة\n- سعر اللعبة: {g_cost} ليرة", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "toggle_welcome":
        cursor.execute("SELECT value FROM settings WHERE key='welcome_enabled'")
        v
