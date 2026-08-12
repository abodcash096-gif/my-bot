import sqlite3
import json
import os

DB_NAME = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            balance REAL DEFAULT 0.0,
            ref_by INTEGER,
            ref_count INTEGER DEFAULT 0,
            step TEXT DEFAULT 'CAPTCHA',
            is_banned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Settings Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            rtp_mode TEXT DEFAULT 'NORMAL',
            min_withdraw REAL DEFAULT 5000.0,
            ref_reward REAL DEFAULT 500.0,
            welcome_bonus REAL DEFAULT 1000.0,
            welcome_bonus_active INTEGER DEFAULT 1,
            channels TEXT DEFAULT '[]'
        )
    ''')
    
    # Gift Codes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward REAL,
            max_uses INTEGER,
            current_uses INTEGER DEFAULT 0
        )
    ''')
    
    # Code Usage Logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS code_usage (
            code TEXT,
            tg_id INTEGER,
            PRIMARY KEY(code, tg_id)
        )
    ''')
    
    # Withdrawals
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            method TEXT,
            account_code TEXT,
            amount REAL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Support Tickets
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            message TEXT,
            photo_id TEXT,
            status TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Admins
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            tg_id INTEGER PRIMARY KEY
        )
    ''')

    # Insert default settings if not exists
    cursor.execute("SELECT COUNT(*) FROM settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO settings (id) VALUES (1)")

    conn.commit()
    conn.close()

init_db()
