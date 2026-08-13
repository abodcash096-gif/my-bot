import os
import sqlite3
import random
from flask import Flask, render_template, request, jsonify

GAME_FOLDER = 'templates'
app = Flask(__name__, template_folder=GAME_FOLDER, static_folder=GAME_FOLDER)
DB_NAME = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 100.0
        )
    ''')
    
    # جدول الإعدادات للتحكم بالنظام من البوت
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # القيم الافتراضية
    default_settings = {
        "win_rate": "30",          # نسبة الربح العامة (30%)
        "bonus_win_rate": "40",    # نسبة الربح عند شراء المكافأة (40%)
        "bonus_cap_1": "200",      # سقف ربح 1 جرة (200 ليرة لفئة 3)
        "bonus_cap_2": "500",      # سقف ربح 2 جرة (500 ليرة لفئة 3)
        "bonus_cap_3": "1000"      # سقف ربح 3 جرات (1000 ليرة لفئة 3)
    }
    
    for k, v in default_settings.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))
        
    conn.commit()
    conn.close()

init_db()

# --- دالة جلب وتحديث الإعدادات ---
def get_setting(key, default_val):
    conn = get_db_connection()
    res = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return res['value'] if res else str(default_val)

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

# --- إدارة رصيد المستخدم ---
def get_user_balance(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT balance FROM users WHERE user_id = ?', (str(user_id),)).fetchone()
    if user is None:
        conn.execute('INSERT INTO users (user_id, balance) VALUES (?, ?)', (str(user_id), 100.0))
        conn.commit()
        conn.close()
        return 100.0
    conn.close()
    return float(user['balance'])

def update_user_balance(user_id, new_balance):
    conn = get_db_connection()
    conn.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, str(user_id)))
    conn.commit()
    conn.close()

# --- دالة تقييم شبكة اللعبة وحساب الربح من اليمين إلى اليسار (RTL) ---
def evaluate_grid(grid, bet):
    # خطوط الربح من اليمين إلى اليسار (تبدأ من البكرة 4 إلى 0)
    paylines = [
        [(4,0), (3,0), (2,0), (1,0), (0,0)],
        [(4,1), (3,1), (2,1), (1,1), (0,1)],
        [(4,2), (3,2), (2,2), (1,2), (0,2)],
        [(4,0), (3,1), (2,2), (1,1), (0,0)],
        [(4,2), (3,1), (2,0), (1,1), (0,2)]
    ]

    jars_count = 0
    jar_reels = []
    max_jar_mult = 1

    for r_idx, column in enumerate(grid):
        for cell in column:
            if cell["sym"] == "🏺":
                jars_count += 1
                if r_idx not in jar_reels:
                    jar_reels.append(r_idx)
                if cell["mult"] > max_jar_mult:
                    max_jar_mult = cell["mult"]

    # عند ظهور 3 جرات، تلغى المضاعفات
    if jars_count >= 3:
        max_jar_mult = 1

    win_amount = 0.0
    winning_coords = []

    for line in paylines:
        # تحديد الرمز الأساسي للخط (أول رمز ليس جرة من اليمين)
        first_sym = None
        for coord in line:
            sym = grid[coord[0]][coord[1]]["sym"]
            if sym != "🏺":
                first_sym = sym
                break

        if not first_sym:
            first_sym = "7"

        count = 0
        current_coords = []

        for coord in line:
            c_sym = grid[coord[0]][coord[1]]["sym"]
            if c_sym == first_sym or c_sym == "🏺":
                count += 1
                current_coords.append(list(coord))
            else:
                break

        line_mult = 0
        
        # ربح السبعات (تتحقق عند 2 سبعة فما فوق من اليمين)
        if first_sym == '7':
            if count == 2:
                line_mult = 1.5
            elif count == 3:
                line_mult = 3
            elif count == 4:
                line_mult = 6
            elif count >= 5:
                line_mult = 20
        else:
            if count == 3:
                line_mult = 2 if first_sym == '🍇' else 1
            elif count == 4:
                line_mult = 4 if first_sym == '🍇' else 2
            elif count >= 5:
                line_mult = 10 if first_sym == '🍇' else 5

        if line_mult > 0:
            # تطبيق مضاعف الجرة عند ظهور 1 أو 2 جرة فقط
            if 0 < jars_count < 3:
                line_mult *= max_jar_mult
            win_amount += line_mult * bet
            winning_coords.extend(current_coords)

    has_jar = jars_count > 0
    primary_jar_reel = jar_reels[0] if jar_reels else -1

    return win_amount, winning_coords, has_jar, primary_jar_reel, max_jar_mult, jars_count

# --- توليد الشبكة بناءً على الخوارزمية والسقوف والشروط ---
def generate_controlled_grid(should_win, bet, forced_jars=0, max_win_cap=None):
    symbols = ['🍋', '🍍', '🍊', '🍒', '🔔', '🍇', '7']
    jar_mults = [1, 2, 3, 5]

    for _ in range(150):
        grid = []
        
        # تحديد عدد الجرات المستهدفة
        if forced_jars > 0:
            target_jars = forced_jars
        else:
            rnd = random.random()
            if rnd < 0.015:
                target_jars = 3  # نادر جداً
            elif rnd < 0.06:
                target_jars = 2  # نادر أكثر
            elif rnd < 0.18:
                target_jars = 1  # نادر
            else:
                target_jars = 0

        jar_reels = random.sample(range(5), min(target_jars, 5)) if target_jars > 0 else []

        for reel_idx in range(5):
            column = []
            has_jar = reel_idx in jar_reels
            jar_row = random.randint(0, 2) if has_jar else -1

            for row_idx in range(3):
                if row_idx == jar_row:
                    # بدون مضاعفات عند ظهور 3 جرات
                    mult = 1 if target_jars >= 3 else random.choice(jar_mults)
                    column.append({"sym": "🏺", "mult": mult})
                else:
                    column.append({"sym": random.choice(symbols), "mult": 1})
            grid.append(column)

        win_amount, winning_coords, h_jar, j_idx, j_mult, j_count = evaluate_grid(grid, bet)

        # التحقق من سقف الأرباح المحدد
        if max_win_cap is not None and win_amount > max_win_cap:
            continue

        if should_win and win_amount > 0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif not should_win and win_amount == 0:
            return grid, 0.0, [], h_jar, j_idx, j_mult

    # شبكة خاسرة مضمونة في حال عدم الوصول لنتيجة خلال المحاولات
    safe_symbols = ['🍋', '🍍', '🍊', '🍒', '🔔']
    grid = []
    for reel_idx in range(5):
        column = []
        for row in range(3):
            sym = safe_symbols[(reel_idx * 2 + row) % len(safe_symbols)]
            column.append({"sym": sym, "mult": 1})
        grid.append(column)

    if forced_jars > 0:
        jar_reels = random.sample(range(5), min(forced_jars, 5))
        for r_idx in jar_reels:
            grid[r_idx][1] = {"sym": "🏺", "mult": 1}

    win_amount, winning_coords, h_jar, j_idx, j_mult, j_count = evaluate_grid(grid, bet)
    if max_win_cap is not None and win_amount > max_win_cap:
        win_amount = 0.0
        winning_coords = []

    return grid, win_amount, winning_coords, h_jar, j_idx, j_mult

# --- APIs التواصل والربط مع البوت واللعبة ---

@app.route('/api/get_user', methods=['POST'])
def get_user():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    balance = get_user_balance(user_id)
    return jsonify({"success": True, "balance": balance})

@app.route('/api/get_settings', methods=['GET', 'POST'])
def get_settings_api():
    """ جلب جميع الإعدادات الحالية للتحكم بها من البوت """
    conn = get_db_connection()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    settings_dict = {row['key']: row['value'] for row in rows}
    return jsonify({"success": True, "settings": settings_dict})

@app.route('/api/set_settings', methods=['POST'])
def set_settings_api():
    """ أندبوينت لتحديث أي إعدادات مباشرة من البوت """
    data = request.get_json() or {}
    for key, value in data.items():
        if key != 'user_id':
            set_setting(key, str(value))
    return jsonify({"success": True, "message": "تم تحديث الإعدادات بنجاح"})

@app.route('/api/update_balance', methods=['POST'])
def update_balance_api():
    """ شحن أو خصم رصيد مستخدم من البوت """
    data = request.get_json() or {}
    user_id = data.get('user_id')
    amount = data.get('amount')
    action = data.get('action', 'add') # add | set
    
    if not user_id or amount is None:
        return jsonify({"success": False, "message": "بيانات غير مكتملة"})
    
    current = get_user_balance(user_id)
    new_bal = (current + float(amount)) if action == 'add' else float(amount)
    if new_bal < 0:
        new_bal = 0.0
        
    update_user_balance(user_id, new_bal)
    return jsonify({"success": True, "new_balance": new_bal})

@app.route('/api/set_win_rate', methods=['POST'])
def set_win_rate_api():
    data = request.get_json() or {}
    rate = data.get('rate')
    if rate is not None and 0 <= int(rate) <= 100:
        set_setting("win_rate", str(rate))
        return jsonify({"success": True, "win_rate": int(rate)})
    return jsonify({"success": False, "message": "نسبة غير صالحة"})

@app.route('/api/play_spin', methods=['POST'])
def play_spin():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    buy_bonus_jars = int(data.get('buy_bonus_jars', 0))
    
    try:
        bet = float(data.get('bet', 3.0))
    except (ValueError, TypeError):
        bet = 3.0

    current_balance = get_user_balance(user_id)

    # عند الدورة العادية نتحقق من وجود رصيد الرهان
    if buy_bonus_jars == 0 and current_balance < bet:
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب!"})

    if buy_bonus_jars == 0:
        current_balance -= bet

    # تحديد الشروط بناءً على الوضع (عادي / شراء مكافأة)
    if buy_bonus_jars > 0:
        bonus_win_rate = int(get_setting("bonus_win_rate", 40))
        should_win = (random.randint(1, 100) <= bonus_win_rate)
        
        # تحديد سقف الأرباح بحسب الجرات ومضاعفة السقف مع فئة الرهان
        bet_ratio = bet / 3.0
        cap_key = f"bonus_cap_{buy_bonus_jars}"
        base_cap = float(get_setting(cap_key, 200 * buy_bonus_jars))
        max_win_cap = base_cap * bet_ratio
    else:
        win_rate = int(get_setting("win_rate", 30))
        should_win = (random.randint(1, 100) <= win_rate)
        max_win_cap = None

    grid, win_amount, winning_coords, has_jar, jar_reel_index, jar_multiplier = generate_controlled_grid(
        should_win=should_win, 
        bet=bet, 
        forced_jars=buy_bonus_jars,
        max_win_cap=max_win_cap
    )

    new_balance = current_balance + win_amount
    update_user_balance(user_id, new_balance)

    return jsonify({
        "success": True,
        "grid": grid,
        "win_amount": win_amount,
        "new_balance": new_balance,
        "has_jar": has_jar,
        "jar_reel_index": jar_reel_index,
        "jar_multiplier": jar_multiplier,
        "winning_coords": winning_coords
    })

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
