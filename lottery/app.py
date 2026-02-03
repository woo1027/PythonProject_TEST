from flask import Flask, render_template, request, jsonify
from oauth2client.service_account import ServiceAccountCredentials
import random
import pandas as pd

import gspread



# ===== Google Sheet 設定 =====
SHEET_KEY = "1_19lyKoIIiHxUoJ4PjRGGZBXBeZMAhCd52vzn7RPGAo"  # e.g., '1_19lyKoIIiHxUoJ4PjRGGZBXBeZMAhCd52vzn7RPGAo'
SHEET_NAME = "Sheet1"
CRED_FILE = "C:\\Users\\user\\PycharmProjects\\PythonProject\\lottery\\KEY.json"  # 下載的 key

scope = ['https://spreadsheets.google.com/feeds',
         'https://www.googleapis.com/auth/drive']

creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_KEY).worksheet(SHEET_NAME)


app = Flask(__name__)
# 金額權重
WEIGHTS = [
    (300, 5),
    (200, 8),
    (100, 12),
    (50, 17),
    (30, 23),
    (20, 30),
    (10, 50)
]
import threading

PRIZES_LOCK = threading.Lock()

# 獎項池
PRIZES = [300]*5 + [200]*8 + [100]*12 + [50]*17 + [30]*23 + [20]*30 + [10]*50
# PRIZES = [300]*5 + [200]*8
random.shuffle(PRIZES)


def spin_one():
    pool = []
    for value, count in WEIGHTS:
        pool.extend([value] * count)
    return random.choice(pool)


# ===== 載入 Google Sheet 到 DataFrame =====
def load_sheet():
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    df['id'] = df['id'].astype(str)
    df['name'] = df['name'].astype(str)

    return df

# ===== 儲存 DataFrame 回 Google Sheet =====
def save_sheet(df):
    # 先更新整個 sheet
    sheet.update([df.columns.values.tolist()] + df.values.tolist())

# ===== Flask 路由 =====
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/spin', methods=['POST'])
def spin():
    data = request.json or {}
    id = str(data.get('id') or "").strip()
    name = (data.get('name') or "").strip()
    if not id or not name:
        return jsonify({"status": "invalid", "message": "請輸入編號與姓名"})

    df = load_sheet()

    # 1️⃣ 檢查名字
    if id not in df['id'].astype(str).values:
        return jsonify({
            "status": "not_eligible",
            "reason": "id_name_mismatch",
            "message": "⚠️ 查無此id，請重新確認"
        })

    # ===== 檢查 id 對應的 name =====
    real_name = df.loc[df['id'].astype(str) == id, 'name'].values[0]
    if real_name != name:
        return jsonify({
            "status": "not_eligible",
            "reason": "id_name_mismatch",
            "message": "⚠️ 編號與姓名不匹配，請找中新小編重新確認"
        })

    # 2️⃣ 檢查是否已抽過
    used = df.loc[df['id'].astype(str) == id, 'used'].values[0]
    if used in [True, 'TRUE', 'true', 1]:
        return jsonify({"status": "not_eligible", "reason": "already_used"})


    remaining_users = df[df['used'].astype(str).isin(['False','false','0'])].shape[0]
    possible_nums = [10,20,30,50,100,200,300]  # 輪盤可顯示數字
    with PRIZES_LOCK:
        if len(PRIZES) == 0:
            # 所有獎項抽完 → 一定未中
            def random_slots_no_triple():
                while True:
                    s = random.choices(possible_nums, k=3)
                    if len(set(s)) > 1:
                        return s

            slots = random_slots_no_triple()
            win = False
            prize = 0
        elif remaining_users == len(PRIZES):
            # 保底邏輯：剩下的人數 = 剩餘獎項 → 每個人必中
            prize = PRIZES.pop(0)
            slots = [prize] * 3
            win = True
        # 計算剩餘還沒抽的人數
        else:
            # 剩餘人數 > 剩餘獎項 → 可能沒中
            pool = PRIZES + [0] * (remaining_users - len(PRIZES))
            idx = random.randint(0, len(pool) - 1)
            prize = pool.pop(idx)
            if prize in PRIZES:
                PRIZES.remove(prize)
                slots = [prize] * 3
                win = True
            else:
                # 未中獎 → 數字可重複，但不能三個一樣
                def random_slots_no_triple():
                    while True:
                        s = random.choices(possible_nums, k=3)
                        if len(set(s)) > 1:
                            return s

                slots = random_slots_no_triple()
                win = False


    # 4️⃣ 更新 Sheet (不論中獎與否)
    # 更新 Sheet
    df.loc[df['id'].astype(str) == id, 'used'] = True
    df.loc[df['id'].astype(str) == id, 'prize'] = ",".join(map(str, slots))  # 存成 "數字,數字,數字"
    df.loc[df['id'].astype(str) == id, 'time'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    save_sheet(df)

    # 5️⃣ 回傳結果
    if win:
        return jsonify({"status": "win", "slots": slots, "prize": prize})
    else:
        return jsonify({"status": "lose", "slots": slots})

@app.route('/check', methods=['POST'])
def check():
    data = request.json or {}
    id_input = str(data.get('id') or "").strip()
    name_input = str(data.get('name') or "").strip()

    if not id_input or not name_input:
        return jsonify({"ok": False, "error": "missing_id_or_name"})

    df = load_sheet()

    # 1️⃣ id 是否存在
    if id_input not in df['id'].astype(str).values:
        return jsonify({"ok": False, "error": "id_name_mismatch"})

    # 2️⃣ id 對應的 name 是否正確
    real_name = df.loc[df['id'].astype(str) == id_input, 'name'].values[0]
    if real_name != name_input:
        return jsonify({"ok": False, "error": "id_name_mismatch"})

    # 3️⃣ 是否已抽過
    used = df.loc[df['id'].astype(str) == id_input, 'used'].values[0]
    if used in [True, 'TRUE', 'true', 1]:
        return jsonify({"ok": False, "error": "already_used"})

    return jsonify({"ok": True})



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)