from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL","sqlite:///./bot.db")
REQUIRED_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS","@lerafree").split(",") if c.strip()]
HOST_URL = os.getenv("HOST_URL","http://localhost:8000")
WELCOME_BONUS = int(os.getenv("WELCOME_BONUS","0"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW","50"))
REFERRAL_REWARD = int(os.getenv("REFERRAL_REWARD","20"))
