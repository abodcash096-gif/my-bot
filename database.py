import sqlite3
import threading

# نستخدم Threading Lock لمنع تداخل البيانات عند استخدام SQLite
db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        # جدول المستخدمين
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        name TEXT,
                        phone TEXT,
                        balance INTEGER DEFAULT 0,
                        referrals INTEGER DEFAULT 0,
                        invited_by INTEGER,
                        is_banned INTEGER DEFAULT 0,
                        is_verified INTEGER DEFAULT 0
                    )''')
        # جدول الإعدادات وخوارزميات اللعبة
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )''')
        # جدول قنوات الاشتراك الإجباري
        c.execute('''CREATE TABLE IF NOT EXISTS channels (
                        channel_id TEXT PRIMARY KEY,
                        url TEXT
                    )''')
        # جدول أكواد الهدايا
        c.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
                        code TEXT PRIMARY KEY,
                        amount INTEGER,
                        is_used INTEGER DEFAULT 0,
                        used_by INTEGER
                    )''')
        
        # الإعدادات الافتراضية
        default_settings = {
            'welcome_bonus': '0',
            'ref_reward': '500',
            'min_withdraw': '5000',
            'algo_lose': '40',
            'algo_normal': '30',
            'algo_mid': '15',
            'algo_high': '10',
            'algo_huge': '5'
        }
        for k, v in default_settings.items():
            c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))
            
        conn.commit()
        conn.close()

def add_user(user_id, name, invited_by=None):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO users (user_id, name, invited_by) VALUES (?, ?, ?)', (user_id, name, invited_by))
        conn.commit()
        conn.close()

def get_user(user_id):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = c.fetchone()
        conn.close()
        return user

def update_user_status(user_id, phone=None, is_verified=None, add_balance=0):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        if phone:
            c.execute('UPDATE users SET phone = ? WHERE user_id = ?', (phone, user_id))
        if is_verified is not None:
            c.execute('UPDATE users SET is_verified = ? WHERE user_id = ?', (is_verified, user_id))
        if add_balance != 0:
            c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (add_balance, user_id))
        conn.commit()
        conn.close()

def process_successful_referral(new_user_id):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        # جلب الشخص الذي قام بالدعوة
        c.execute('SELECT invited_by FROM users WHERE user_id = ?', (new_user_id,))
        inviter = c.fetchone()
        if inviter and inviter['invited_by']:
            inviter_id = inviter['invited_by']
            c.execute('SELECT value FROM settings WHERE key = "ref_reward"')
            reward = int(c.fetchone()['value'])
            
            # تحديث رصيد وعدد إحالات صاحب الرابط
            c.execute('UPDATE users SET balance = balance + ?, referrals = referrals + 1 WHERE user_id = ?', (reward, inviter_id))
            conn.commit()
            conn.close()
            return inviter_id, reward
        conn.close()
        return None, 0

def get_channels():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM channels')
        channels = c.fetchall()
        conn.close()
        return channels
        
def add_channel(channel_id, url):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO channels (channel_id, url) VALUES (?, ?)', (channel_id, url))
        conn.commit()
        conn.close()

def get_setting(key):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        res = c.fetchone()
        conn.close()
        return res['value'] if res else None
