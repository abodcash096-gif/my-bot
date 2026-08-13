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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 100.0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("win_rate", "30")')
    conn.commit()
    conn.close()

init_db()

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

def get_win_rate():
    conn = get_db_connection()
    res = conn.execute('SELECT value FROM settings WHERE key = "win_rate"').fetchone()
    conn.close()
    return int(res['value']) if res else 30

def set_win_rate_db(rate):
    conn = get_db_connection()
    conn.execute('UPDATE settings SET value = ? WHERE key = "win_rate"', (str(rate),))
    conn.commit()
    conn.close()

# --- دالة تقييم شبكة اللعبة وإرجاع الأرباح ---
def evaluate_grid(grid, bet):
    paylines = [
        [(0,0), (1,0), (2,0), (3,0), (4,0)],
        [(0,1), (1,1), (2,1), (3,1), (4,1)],
        [(0,2), (1,2), (2,2), (3,2), (4,2)],
        [(0,0), (1,1), (2,2), (3,1), (4,0)],
        [(0,2), (1,1), (2,0), (3,1), (4,2)]
    ]

    has_jar = False
    jar_reel_index = -1
    jar_multiplier = 1

    for r_idx, column in enumerate(grid):
        for cell in column:
            if cell["sym"] == "🏺":
                has_jar = True
                jar_reel_index = r_idx
                jar_multiplier = cell["mult"]

    win_amount = 0.0
    winning_coords = []

    for line in paylines:
        first_coord = line[0]
        first_sym = grid[first_coord[0]][first_coord[1]]["sym"]
        
        if first_sym == "🏺" and len(line) > 1:
            first_sym = grid[line[1][0]][line[1][1]]["sym"]

        count = 0
        current_line_coords = []

        for coord in line:
            c_sym = grid[coord[0]][coord[1]]["sym"]
            if c_sym == first_sym or c_sym == "🏺":
                count += 1
                current_line_coords.append(list(coord))
            else:
                break

        line_mult = 0
        if count >= 3:
            if first_sym in ['🍋', '🍍', '🍊', '🍒', '🔔']:
                line_mult = 1 if count == 3 else (2 if count == 4 else 5)
            elif first_sym == '🍇':
                line_mult = 2 if count == 3 else (4 if count == 4 else 10)
            elif first_sym == '7':
                line_mult = 3 if count == 3 else (6 if count == 4 else 20)

        if line_mult > 0:
            if has_jar and (jar_reel_index in [c[0] for c in current_line_coords]):
                line_mult *= jar_multiplier
            win_amount += line_mult * bet
            winning_coords.extend(current_line_coords)

    return win_amount, winning_coords, has_jar, jar_reel_index, jar_multiplier

# --- دالة توليد الشبكة بناءً على خوارزمية الربح والخسارة ---
def generate_controlled_grid(should_win, bet):
    symbols = ['🍋', '🍍', '🍊', '🍒', '🔔', '🍇', '7']
    
    for _ in range(100): # محاولات حتى تحقيق النتيجة المطلوبة
        grid = []
        has_jar = False
        
        for reel_idx in range(5):
            column = []
            for row_idx in range(3):
                if random.random() < 0.03 and not has_jar:
                    has_jar = True
                    column.append({"sym": "🏺", "mult": random.choice([2, 3, 5])})
                else:
                    column.append({"sym": random.choice(symbols), "mult": 1})
            grid.append(column)

        win_amount, winning_coords, h_jar, j_idx, j_mult = evaluate_grid(grid, bet)

        if should_win and win_amount > 0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult
        elif not should_win and win_amount == 0:
            return grid, 0.0, [], h_jar, j_idx, j_mult

    # شبكة خاسرة مضمونة في حال عدم الوصول لنتيجة خلال 100 محاولة
    safe_symbols = ['🍋', '🍍', '🍊', '🍒', '🔔', '🍇', '7']
    grid = []
    for reel_idx in range(5):
        column = [{"sym": safe_symbols[(reel_idx + row) % len(safe_symbols)], "mult": 1} for row in range(3)]
        grid.append(column)
    return grid, 0.0, [], False, -1, 1

# --- API المسارات ---
@app.route('/api/get_user', methods=['POST'])
def get_user():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    balance = get_user_balance(user_id)
    return jsonify({"success": True, "balance": balance})

@app.route('/api/set_win_rate', methods=['POST'])
def set_win_rate_api():
    data = request.get_json() or {}
    rate = data.get('rate')
    if rate is not None and 0 <= int(rate) <= 100:
        set_win_rate_db(int(rate))
        return jsonify({"success": True, "win_rate": int(rate)})
    return jsonify({"success": False, "message": "نسبة غير صالحة"})

@app.route('/api/play_spin', methods=['POST'])
def play_spin():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    
    try:
        bet = float(data.get('bet', 3.0))
    except (ValueError, TypeError):
        bet = 3.0

    current_balance = get_user_balance(user_id)

    if current_balance < bet:
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب!"})

    current_balance -= bet

    win_rate = get_win_rate()
    should_win = (random.randint(1, 100) <= win_rate)

    grid, win_amount, winning_coords, has_jar, jar_reel_index, jar_multiplier = generate_controlled_grid(should_win, bet)

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
