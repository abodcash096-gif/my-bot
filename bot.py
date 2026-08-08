import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# إعدادات تسجيل الأخطاء
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! البوت يعمل الآن بنجاح 🚀")

# الرد التلقائي
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text(f"أنت أرسلت: {user_text}")

if __name__ == '__main__':
    # جلب التوكن من إعدادات السيرفر
    TOKEN = os.environ.get("BOT_TOKEN"8802444714:AAFoaN82TuPv3wqf6RwHFgS7yFyd0ZMhToY
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    print("تم تشغيل البوت بنجاح...")
    app.run_polling()
