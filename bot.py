import os
import json
import logging
import threading
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from database import get_db, init_db
from server import run_flask

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8842721926:AAFn7HGsi7MPsPO7KtN4Z9PE5lj-j6OOhvY")
WEBAPP_URL = "https://my-bot-j658.onrender.com/games"
DEVELOPER_CHANNEL = "@lerafree"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Utility Functions
def is_admin(tg_id):
    conn = get_db()
    adm = conn.execute("SELECT tg_id FROM admins WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return adm is not None

def get_user(tg_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return u

# Navigation Keyboards
def get_main_keyboard():
    kb = [
        [KeyboardButton("🎮 صفحة الألعاب", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton("👤 حسابي ورصيدي"), KeyboardButton("💸 سحب رصيدي")],
        [KeyboardButton("🔗 رابط إحالاتي"), KeyboardButton("🤖 شراء بوت")],
        [KeyboardButton("📞 مراسلة الدعم"), KeyboardButton("🎁 إدخال كود هدية")],
        [KeyboardButton("📜 سجلاتي"), KeyboardButton("📢 قناة المبرمج")]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# Command Handlers
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = user.id
    
    # Check referral
    ref_id = None
    if context.args and context.args[0].isdigit():
        ref_id = int(context.args[0])

    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    
    if not existing:
        conn.execute(
            "INSERT INTO users (tg_id, name, ref_by, step) VALUES (?, ?, ?, 'CAPTCHA')",
            (tg_id, user.full_name, ref_id if ref_id != tg_id else None)
        )
        conn.commit()
    conn.close()
    
    u = get_user(tg_id)
    if u['is_banned']:
        await update.message.reply_text("❌ حسابك مجمد ومحظور بسبب مخالفة شروط الاستخدام.")
        return

    if u['step'] == 'CAPTCHA':
        kb = [[InlineKeyboardButton("5", callback_data="cap_wrong"), InlineKeyboardButton("7", callback_data="cap_correct"), InlineKeyboardButton("9", callback_data="cap_wrong")]]
        await update.message.reply_text("🤖 **اختبار التحقق:** كم يساوي 3 + 4 ؟", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
        
    elif u['step'] == 'PHONE':
        kb = [[KeyboardButton("📱 مشاركة رقم الهاتف السوري للتأكيد", request_contact=True)]]
        await update.message.reply_text("رجاءً قم بمشاركة رقم هاتفك لإتمام عملية التأكيد:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))
        return

    elif u['step'] == 'CHANNELS':
        await check_channels_step(update, context)
        return

    elif u['step'] == 'TERMS':
        kb = [[InlineKeyboardButton("✅ أوافق على الشروط والأحكام", callback_data="accept_terms")]]
        terms_text = "⚠️ **تعهد عدم الاحتيال:**\n\nبموافقتك على الشروط، تتعهد بعدم استخدام أي وسائل احتيال. عند اكتشاف أي تلاعب سيتم تجميد رصيدك وحظر حسابك فوراً."
        await update.message.reply_text(terms_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    # Fully Verified User
    welcome_msg = (
        f"🙋‍♂️ أهلاً بك يا **{u['name']}**!\n"
        f"🆔 آيدي الحساب: `{u['tg_id']}`\n"
        f"💰 رصيدك الحالي: **{u['balance']:.2f} ليرة سورية جديدة**\n\n"
        f"استمتع بأفضل تجربة ألعاب وكازينو احترافية!"
    )
    await update.message.reply_text(welcome_msg, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    tg_id = query.from_user.id
    
    conn = get_db()

    if data == "cap_correct":
        conn.execute("UPDATE users SET step = 'PHONE' WHERE tg_id = ?", (tg_id,))
        conn.commit()
        conn.close()
        kb = [[KeyboardButton("📱 مشاركة رقم الهاتف السوري للتأكيد", request_contact=True)]]
        await query.message.reply_text("✅ تم التحقق بنجاح! الآن قم بمشاركة رقم هاتفك:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))

    elif data == "cap_wrong":
        conn.close()
        await query.message.reply_text("❌ إجابة خاطئة، حاول مرة أخرى عبر إرسال /start.")

    elif data == "check_sub":
        # Check channels
        settings = conn.execute("SELECT channels FROM settings WHERE id = 1").fetchone()
        channels = json.loads(settings['channels']) if settings else []
        subscribed = True
        
        for ch in channels:
            try:
                member = await context.bot.get_chat_member(chat_id=ch, user_id=tg_id)
                if member.status in ['left', 'kicked']:
                    subscribed = False
                    break
            except Exception:
                pass
                
        if subscribed or not channels:
            conn.execute("UPDATE users SET step = 'TERMS' WHERE tg_id = ?", (tg_id,))
            conn.commit()
            conn.close()
            kb = [[InlineKeyboardButton("✅ أوافق على الشروط والأحكام", callback_data="accept_terms")]]
            terms_text = "⚠️ **تعهد عدم الاحتيال:**\n\nبموافقتك على الشروط، تتعهد بعدم استخدام أي وسائل احتيال. عند اكتشاف أي تلاعب سيتم تجميد رصيدك وحظر حسابك فوراً."
            await query.message.reply_text(terms_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            conn.close()
            await query.message.reply_text("❌ لم تقم بالاشتراك في جميع القنوات بعد. يرجى الاشتراك ثم الضغط على تحقق.")

    elif data == "accept_terms":
        u = get_user(tg_id)
        settings = conn.execute("SELECT welcome_bonus, welcome_bonus_active, ref_reward FROM settings WHERE id = 1").fetchone()
        
        bonus = settings['welcome_bonus'] if settings['welcome_bonus_active'] else 0.0
        new_balance = u['balance'] + bonus
        
        conn.execute("UPDATE users SET step = 'COMPLETED', balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        
        # Referral reward execution
        if u['ref_by']:
            ref_user = conn.execute("SELECT * FROM users WHERE tg_id = ?", (u['ref_by'],)).fetchone()
            if ref_user:
                ref_reward = settings['ref_reward']
                conn.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE tg_id = ?", (ref_reward, u['ref_by']))
                try:
                    await context.bot.send_message(
                        chat_id=u['ref_by'],
                        text=f"🎉 **مبروك!** انضم مستخدم جديد عن طريق رابطك ({u['name']}). تمت إضافة مكافأة الإحالة: **{ref_reward} ليرة سورية جديدة** إلى رصيدك!",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        conn.commit()
        conn.close()

        await query.message.reply_text(f"🎉 تم تفعيل حسابك بنجاح! حصلت على بونص ترحيبي: **{bonus} ليرة جديدة**.", parse_mode="Markdown")
        await start_handler(update, context)

async def check_channels_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    settings = conn.execute("SELECT channels FROM settings WHERE id = 1").fetchone()
    conn.close()
    
    channels = json.loads(settings['channels']) if settings else []
    
    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(f"📢 اشترك في القناة: {ch}", url=f"https://t.me/{ch.replace('@','')}")])
    kb.append([InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")])
    
    await update.message.reply_text("📢 للاستمرار، يرجى الاشتراك في قنوات البوت الإجبارية التالية:", reply_markup=InlineKeyboardMarkup(kb))

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    tg_id = update.effective_user.id
    
    conn = get_db()
    conn.execute("UPDATE users SET phone = ?, step = 'CHANNELS' WHERE tg_id = ?", (contact.phone_number, tg_id))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ تم حفظ رقم الهاتف بنجاح.")
    await check_channels_step(update, context)

# Message Router
async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    tg_id = user.id
    u = get_user(tg_id)
    
    if not u or u['step'] != 'COMPLETED':
        await start_handler(update, context)
        return

    if text == "👤 حسابي ورصيدي":
        msg = (
            f"👤 **تفاصيل حسابك الشخصي:**\n\n"
            f"• الاسم: {u['name']}\n"
            f"• الآيدي: `{u['tg_id']}`\n"
            f"• الهاتف: `{u['phone'] or 'غير مسجل'}`\n"
            f"• الرصيد: **{u['balance']:.2f} ليرة جديدة**\n"
            f"• عدد إحالاتك: {u['ref_count']}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "💸 سحب رصيدي":
        kb = [
            [InlineKeyboardButton("شام كاش (Sham Cash)", callback_data="withdraw_sham")],
            [InlineKeyboardButton("سيريتل كاش (Syriatel Cash)", callback_data="withdraw_syriatel")]
        ]
        await update.message.reply_text("اختر طريقة السحب المناسبة لك:", reply_markup=InlineKeyboardMarkup(kb))

    elif text == "🔗 رابط إحالاتي":
        conn = get_db()
        settings = conn.execute("SELECT ref_reward FROM settings WHERE id = 1").fetchone()
        conn.close()
        reward = settings['ref_reward'] if settings else 500.0
        
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={tg_id}"
        
        msg = (
            f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
            f"👥 عدد إحالاتك الناجحة: **{u['ref_count']}**\n"
            f"💰 تحصل على **{reward} ليرة جديدة** مقابل كل صديق يدخل عبر رابطك ويكمل خطوات التسجيل!"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "🤖 شراء بوت":
        context.user_data['state'] = 'BUY_BOT'
        await update.message.reply_text("🤖 قم بكتابة تفاصيل البوت الذي تريد إنشاءه والمتطلبات الخاصة بك:")

    elif text == "📞 مراسلة الدعم":
        context.user_data['state'] = 'SUPPORT'
        await update.message.reply_text("📞 يمكنك الآن كتابة نص المشكلة أو إرسال صورة وسيقوم فريق الدعم بالرد عليك فوراً:")

    elif text == "🎁 إدخال كود هدية":
        context.user_data['state'] = 'REDEEM_CODE'
        await update.message.reply_text("🎁 أدخل كود الهدية الخاص بك الآن:")

    elif text == "📜 سجلاتي":
        conn = get_db()
        w_logs = conn.execute("SELECT * FROM withdrawals WHERE tg_id = ? ORDER BY id DESC LIMIT 5", (tg_id,)).fetchall()
        conn.close()
        
        res = "📜 **سجل عمليات السحب الأخيرة:**\n\n"
        if not w_logs:
            res += "لا توجد عمليات سحب معالجة بعد."
        else:
            for w in w_logs:
                res += f"• المبلغ: {w['amount']} | الطريقة: {w['method']} | الحالة: {w['status']}\n"
        await update.message.reply_text(res, parse_mode="Markdown")

    elif text == "📢 قناة المبرمج":
        await update.message.reply_text(f"📢 تابع قناة المبرمج الرسمية للحصول على التحديثات:\n{DEVELOPER_CHANNEL}")

    else:
        # Handle state inputs
        state = context.user_data.get('state')
        if state == 'BUY_BOT':
            context.user_data['state'] = None
            est_price = 1000 + len(text) * 10
            kb = [
                [InlineKeyboardButton("✅ موافقة وإرسال للطلب", callback_data=f"confirm_bot_{est_price}")],
                [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_bot")]
            ]
            context.user_data['bot_details'] = text
            await update.message.reply_text(f"💡 السعر التقريبي المطلوب للبوت بناءً على المواصفات هو: **{est_price} ليرة جديدة**.\nهل تريد التأكيد؟", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

        elif state == 'SUPPORT':
            context.user_data['state'] = None
            conn = get_db()
            conn.execute("INSERT INTO support_tickets (tg_id, message) VALUES (?, ?)", (tg_id, text))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ تم إرسال رسالتك لإدارة البوت بنجاح! سيتم الرد عليك قريباً.")

        elif state == 'REDEEM_CODE':
            context.user_data['state'] = None
            code = text.strip()
            conn = get_db()
            promo = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)).fetchone()
            used = conn.execute("SELECT * FROM code_usage WHERE code = ? AND tg_id = ?", (code, tg_id)).fetchone()
            
            if not promo:
                await update.message.reply_text("❌ هذا الكود غير موجود أو منتهي الصلاحية.")
            elif used:
                await update.message.reply_text("⚠️ لقد قمت بتبديل هذا الكود من قبل!")
            elif promo['current_uses'] >= promo['max_uses']:
                await update.message.reply_text("❌ نفذت مرات استخدام هذا الكود.")
            else:
                conn.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (code,))
                conn.execute("INSERT INTO code_usage (code, tg_id) VALUES (?, ?)", (code, tg_id))
                conn.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (promo['reward'], tg_id))
                conn.commit()
                await update.message.reply_text(f"🎉 تهانينا! تمت إضافة **{promo['reward']} ليرة جديدة** إلى حسابك.")
            conn.close()

# Admin Command & Control Panel
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    if not is_admin(tg_id) and tg_id != 123456789: # Master admin fallback
        return
        
    kb = [
        [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🔍 تفاصيل لاعب", callback_data="adm_user_info"), InlineKeyboardButton("📊 إحصائيات اللاعبين", callback_data="adm_stats")],
        [InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_gen_code"), InlineKeyboardButton("👤 إضافة أدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("🟢 فك الحظر", callback_data="adm_unban")],
        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_bc"), InlineKeyboardButton("✉️ رسالة خاصة", callback_data="adm_pm")],
        [InlineKeyboardButton("💸 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("📞 طلبات المراسلة", callback_data="adm_support")],
        [InlineKeyboardButton("⚙️ التحكم بخوارزمية الألعاب (RTP)", callback_data="adm_rtp")],
        [InlineKeyboardButton("📢 إدارة قنوات الاشتراك", callback_data="adm_channels")]
    ]
    await update.message.reply_text("🛠 **لوحة التحكم الاحترافية بالإدارة:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def main():
    # Start Flask Web Server in parallel thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Initialize SQLite DB
    init_db()
    
    # Build Telegram Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))
    
    app.run_polling()

if __name__ == '__main__':
    main()
