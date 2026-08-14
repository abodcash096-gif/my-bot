import os
import sqlite3
import random
from flask import Flask, render_template, request, jsonify

GAME_FOLDER = 'templates'
app = Flask(__name__, template_folder=GAME_FOLDER, static_folder=GAME_FOLDER)
DB_NAME = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=15)
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
    
    # الإعدادات الافتراضية
    default_settings = {
        "bonus_win_rate": "40",    # نسبة الربح عند شراء المكافأة
        "bonus_cap_1": "200",      # سقف ربح 1 جرة
        "bonus_cap_2": "500",      # سقف ربح 2 جرة
        "bonus_cap_3": "1000",     # سقف ربح 3 جرات
        
        # 🎯 النسب المئوية للتحكم بالخوارزمية
        "chance_loss": "50",       # نسبة الخسارة
        "chance_win1": "20",       # نسبة ربح 1x (سعر الرهان)
        "chance_win2": "10",       # نسبة ربح 2x
        "chance_win5": "10",       # نسبة ربح 5x
        "chance_win10": "6",       # نسبة ربح 10x
        "chance_win20": "3",       # نسبة ربح 20x
        "chance_win50": "1",       # نسبة ربح 50x (نادر جداً)
        
        # 🛠️ وضع الصيانة (off = شغال / on = صيانة)
        "maintenance_mode": "off",
        
        # ⚙️ نمط التشغيل الإجباري (auto / loss / win1 / win2 / win5 / win10 / win20 / win50)
        "global_win_mode": "auto"
    }
    
    for k, v in default_settings.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, str(v)))
        
    conn.commit()
    conn.close()

init_db()

# --- جلب وتحديث الإعدادات ---
def get_setting(key, default_val=""):
    try:
        conn = get_db_connection()
        res = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        return res['value'] if res else str(default_val)
    except Exception:
        return str(default_val)

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
    conn.execute('UPDATE users SET balance = ? WHERE user_id = ?', (float(new_balance), str(user_id)))
    conn.commit()
    conn.close()

# --- دالة تقييم شبكة اللعبة ---
def evaluate_grid(grid, bet):
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
                if cell.get("mult", 1) > max_jar_mult:
                    max_jar_mult = cell.get("mult", 1)

    if jars_count >= 3:
        max_jar_mult = 1

    win_amount = 0.0
    winning_coords = []
    winning_jar_reels = set()

    for line in paylines:
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
        line_jars = []

        for coord in line:
            c_sym = grid[coord[0]][coord[1]]["sym"]
            if c_sym == first_sym or c_sym == "🏺":
                count += 1
                current_coords.append(list(coord))
                if c_sym == "🏺":
                    line_jars.append(coord[0])
            else:
                break

        line_mult = 0
        
        if first_sym == '7':
            if count == 2:
                line_mult = 1.5
            elif count == 3:
                line_mult = 3.5
            elif count == 4:
                line_mult = 8.0
            elif count >= 5:
                line_mult = 25.0
        else:
            if count == 3:
                line_mult = 2.5 if first_sym == '🍇' else 1.5
            elif count == 4:
                line_mult = 5.0 if first_sym == '🍇' else 3.0
            elif count >= 5:
                line_mult = 12.0 if first_sym == '🍇' else 6.0

        if line_mult > 0:
            if 0 < jars_count < 3:
                line_mult *= max_jar_mult
            win_amount += line_mult * bet
            winning_coords.extend(current_coords)
            
            for r in line_jars:
                winning_jar_reels.add(r)

    has_jar = len(winning_jar_reels) > 0
    primary_jar_reel = list(winning_jar_reels)[0] if has_jar else -1

    return win_amount, winning_coords, has_jar, primary_jar_reel, max_jar_mult, jars_count

# --- دالة اختيار الفئة بناءً على النسب المحددة من البوت ---
def choose_tier(is_bonus_buy=False):
    global_mode = get_setting("global_win_mode", "auto")
    if global_mode in ["loss", "win1", "win2", "win5", "win10", "win20", "win50"]:
        return global_mode

    if is_bonus_buy:
        bonus_win_rate = int(get_setting("bonus_win_rate", 40))
        if random.randint(1, 100) > bonus_win_rate:
            return "loss"
        return random.choices(["win1", "win2", "win5", "win10", "win20", "win50"], weights=[30, 25, 20, 15, 7, 3])[0]
    else:
        c_loss = float(get_setting("chance_loss", 50))
        c_win1 = float(get_setting("chance_win1", 20))
        c_win2 = float(get_setting("chance_win2", 10))
        c_win5 = float(get_setting("chance_win5", 10))
        c_win10 = float(get_setting("chance_win10", 6))
        c_win20 = float(get_setting("chance_win20", 3))
        c_win50 = float(get_setting("chance_win50", 1))
        
        tiers = ["loss", "win1", "win2", "win5", "win10", "win20", "win50"]
        weights = [c_loss, c_win1, c_win2, c_win5, c_win10, c_win20, c_win50]
        total = sum(weights)
        if total <= 0: 
            return "loss"
        return random.choices(tiers, weights=weights)[0]

# --- توليد الشبكة بناءً على مضاعفات الربح الدقيقة المطلوب تحقيقها ---
def generate_controlled_grid(tier, bet, forced_jars=0, max_win_cap=None):
    symbols = ['🍋', '🍍', '🍊', '🍒', '🔔', '🍇', '7']
    jar_mults = [1, 2, 3, 5]

    for _ in range(250):
        grid = []
        if forced_jars > 0:
            target_jars = forced_jars
        else:
            if tier in ["win20", "win50"]:
                target_jars = random.choice([2, 3])
            elif tier in ["win5", "win10"]:
                target_jars = random.choice([1, 2])
            elif tier in ["win1", "win2"]:
                target_jars = 1 if random.random() < 0.2 else 0
            else:
                target_jars = 1 if random.random() < 0.05 else 0

        jar_reels = random.sample(range(5), min(target_jars, 5)) if target_jars > 0 else []

        for reel_idx in range(5):
            column = []
            has_jar = reel_idx in jar_reels
            jar_row = random.randint(0, 2) if has_jar else -1

            for row_idx in range(3):
                if row_idx == jar_row:
                    mult = 1 if target_jars >= 3 else random.choice(jar_mults)
                    column.append({"sym": "🏺", "mult": mult})
                else:
                    column.append({"sym": random.choice(symbols), "mult": 1})
            grid.append(column)

        win_amount, winning_coords, h_jar, j_idx, j_mult, j_count = evaluate_grid(grid, bet)

        if max_win_cap is not None and win_amount > max_win_cap:
            continue

        win_ratio = win_amount / bet if bet > 0 else 0

        # مطابقة النتيجة بالمضاعف المحدد
        if tier == "loss" and win_amount == 0:
            return grid, 0.0, [], h_jar, j_idx, j_mult
        elif tier == "win1" and 0.8 <= win_ratio <= 1.8:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif tier == "win2" and 1.8 < win_ratio <= 3.8:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif tier == "win5" and 3.8 < win_ratio <= 7.5:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif tier == "win10" and 7.5 < win_ratio <= 15.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif tier == "win20" and 15.0 < win_ratio <= 35.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif tier == "win50" and win_ratio > 35.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult

    # صمام أمان لتأكيد توليد دورة خاسرة في حال عدم مطابقة الشروط بعد عدة محاولات
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

# --- APIs التواصل والتكامل ---

@app.route('/api/get_user', methods=['POST'])
def get_user():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    balance = get_user_balance(user_id)
    maintenance = get_setting("maintenance_mode", "off") == "on"
    return jsonify({"success": True, "balance": balance, "maintenance": maintenance})

@app.route('/api/get_settings', methods=['GET', 'POST'])
def get_settings_api():
    conn = get_db_connection()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    settings_dict = {row['key']: row['value'] for row in rows}
    return jsonify({"success": True, "settings": settings_dict})

@app.route('/api/set_settings', methods=['POST'])
def set_settings_api():
    data = request.get_json() or {}
    for key, value in data.items():
        if key != 'user_id':
            set_setting(key, str(value))
    return jsonify({"success": True, "message": "تم تحديث الإعدادات بنجاح"})

@app.route('/api/update_balance', methods=['POST'])
def update_balance_api():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    amount = data.get('amount')
    action = data.get('action', 'add')
    
    if not user_id or amount is None:
        return jsonify({"success": False, "message": "بيانات غير مكتملة"})
    
    current = get_user_balance(user_id)
    new_bal = (current + float(amount)) if action == 'add' else float(amount)
    if new_bal < 0: 
        new_bal = 0.0
        
    update_user_balance(user_id, new_bal)
    return jsonify({"success": True, "new_balance": new_bal})

@app.route('/api/play_spin', methods=['POST'])
def play_spin():
    # التحقق من وضع الصيانة أولاً
    if get_setting("maintenance_mode", "off") == "on":
        return jsonify({
            "success": False, 
            "maintenance": True, 
            "message": "⚠️ اللعبة حالياً في وضع الصيانة. يرجى المحاولة لاحقاً."
        })

    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    buy_bonus_jars = int(data.get('buy_bonus_jars', 0))
    
    try:
        bet = float(data.get('bet', 3.0))
    except (ValueError, TypeError):
        bet = 3.0

    current_balance = get_user_balance(user_id)

    if buy_bonus_jars > 0:
        bonus_cost = data.get('bonus_cost') or data.get('cost')
        if bonus_cost is not None:
            try:
                spin_cost = float(bonus_cost)
            except (ValueError, TypeError):
                spin_cost = bet * 10 * buy_bonus_jars
        else:
            spin_cost = bet * 10 * buy_bonus_jars

        if current_balance < spin_cost:
            return jsonify({"success": False, "message": "رصيدك غير كافٍ لشراء المكافأة!"})

        current_balance -= spin_cost

        bet_ratio = bet / 3.0
        cap_key = f"bonus_cap_{buy_bonus_jars}"
        base_cap = float(get_setting(cap_key, 200 * buy_bonus_jars))
        max_win_cap = base_cap * bet_ratio
        tier = choose_tier(is_bonus_buy=True)
    else:
        spin_cost = bet
        if current_balance < spin_cost:
            return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب!"})

        current_balance -= spin_cost
        max_win_cap = None
        tier = choose_tier(is_bonus_buy=False)

    grid, win_amount, winning_coords, has_jar, jar_reel_index, jar_multiplier = generate_controlled_grid(
        tier=tier,
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
