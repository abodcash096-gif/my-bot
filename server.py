import os
import random
from flask import Flask, render_template, request, jsonify
from database import get_db

app = Flask(__name__, template_folder="templates")

@app.route('/')
def home():
    return "Server is running smoothly!"

@app.route('/games')
def games_page():
    return render_template('games.html')

@app.route('/api/get_user', methods=['GET'])
def get_user_api():
    tg_id = request.args.get('tg_id')
    if not tg_id:
        return jsonify({'error': 'Missing tg_id'}), 400
    
    conn = get_db()
    user = conn.execute("SELECT name, balance FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    return jsonify({
        'name': user['name'],
        'balance': user['balance']
    })

@app.route('/api/play_game', methods=['POST'])
def play_game_api():
    data = request.json or {}
    tg_id = data.get('tg_id')
    bet = float(data.get('bet', 0))
    game_type = data.get('game_type', 'wheel')
    
    if not tg_id or bet <= 0:
        return jsonify({'success': False, 'message': 'بيانات الطلب غير صالحة'}), 400
        
    conn = get_db()
    user = conn.execute("SELECT balance, is_banned FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    
    if not user or user['is_banned']:
        conn.close()
        return jsonify({'success': False, 'message': 'الحساب غير موجود أو محظور'}), 403
        
    if user['balance'] < bet:
        conn.close()
        return jsonify({'success': False, 'message': 'رصيدك غير كافٍ للعب'}), 400

    settings = conn.execute("SELECT rtp_mode FROM settings WHERE id = 1").fetchone()
    rtp_mode = settings['rtp_mode'] if settings else 'NORMAL'
    
    # Game Algorithm Calculation based on Admin RTP Control
    win_chance_map = {
        'LOSS': 0.05,       # 5% chance win
        'NORMAL': 0.40,     # 40% chance win
        'MEDIUM': 0.55,     # 55% chance win
        'HIGH': 0.70,       # 70% chance win
        'BIG_WIN': 0.88     # 88% chance win
    }
    
    win_chance = win_chance_map.get(rtp_mode, 0.40)
    is_win = random.random() < win_chance
    
    multiplier = 0.0
    if is_win:
        if rtp_mode == 'BIG_WIN':
            multiplier = random.choice([5.0, 10.0, 25.0, 50.0, 100.0])
        elif rtp_mode == 'HIGH':
            multiplier = random.choice([2.0, 3.0, 5.0, 10.0])
        else:
            multiplier = random.choice([1.2, 1.5, 2.0, 3.0])
            
    payout = bet * multiplier
    new_balance = user['balance'] - bet + payout
    
    conn.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'is_win': is_win,
        'multiplier': multiplier,
        'payout': payout,
        'new_balance': new_balance
    })

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_flask()
