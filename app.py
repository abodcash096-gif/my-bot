import os
import sqlite3
import random
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder='.', static_folder='.')

DB_NAME = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 100.0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

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

# استقبال جميع روابط الصفحة الرئيسية
@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

@app.route('/api/get_user', methods=['POST'])
def get_user():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    balance = get_user_balance(user_id)
    return jsonify({"success": True, "balance": balance})

@app.route('/api/play_spin', methods=['POST'])
def play_spin():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    
    try:
        bet = float(data.get('bet', 3))
    except (ValueError, TypeError):
        bet = 3.0

    current_balance = get_user_balance(user_id)

    if current_balance < bet:
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب!"})

    current_balance -= bet

    symbols = ['🍋', '🍍', '🍊', '🍒', '🔔', '🍇', '7']
    grid = []
    has_jar = False
    jar_reel_index = -1
    jar_multiplier = 1

    for reel_idx in range(5):
        column = []
        for row_idx in range(3):
            if random.random() < 0.03 and not has_jar:
                has_jar = True
                jar_reel_index = reel_idx
                jar_multiplier = random.choice([2, 3, 5])
                column.append({"sym": "🏺", "mult": jar_multiplier})
            else:
                sym = random.choice(symbols)
                column.append({"sym": sym, "mult": 1})
        grid.append(column)

    paylines = [
        [(0,0), (1,0), (2,0), (3,0), (4,0)],
        [(0,1), (1,1), (2,1), (3,1), (4,1)],
        [(0,2), (1,2), (2,2), (3,2), (4,2)],
        [(0,0), (1,1), (2,2), (3,1), (4,0)],
        [(0,2), (1,1), (2,0), (3,1), (4,2)]
    ]

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
