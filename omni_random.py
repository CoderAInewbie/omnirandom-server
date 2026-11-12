import datetime
import http.server
import json
import os
import random
import secrets
import socket
import socketserver
import string
import time
import uuid
import webbrowser
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", 8080))
DATA_FILE = "random_data.json"
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")

ALL_FEATURES = [
    "race",
    "wheel",
    "cage",
    "slot",
    "hat",
    "bomb",
    "tabletop",
    "teams",
    "utility",
    "edit_roster",
]

DEFAULT_PRESETS = {
    "Danh sách Thành viên / Lớp": [
        "Gia Huy",
        "Thế Vinh",
        "Quốc Thịnh",
        "Trung Nghĩa",
        "Minh Anh",
        "Quỳnh Như",
        "Gia Hưng",
        "Hoàng Nam",
    ],
    "Hôm nay ăn gì?": [
        "Cơm tấm sườn bì chả",
        "Phở bò tái nạm",
        "Bún bò Huế",
        "Mì Quảng",
        "Bánh mì chảo",
        "Hủ tiếu Nam Vang",
        "Gà rán & Khoai tây",
        "Bún đậu mắm tôm",
        "Lẩu Thái chua cay",
    ],
    "Uống gì chiều nay?": [
        "Trà sữa trân châu đường đen",
        "Hồng trà kem phô mai",
        "Trà đào cam sả",
        "Cà phê sữa đá",
        "Bạc xỉu",
        "Matcha Latte",
        "Nước ép cam tươi",
        "Trà mãng cầu",
    ],
    "Thử thách / Phạt vui": [
        "Hát 1 đoạn điệp khúc",
        "Bao nước người bên cạnh",
        "Chống đẩy 10 cái",
        "Kể 1 kỷ niệm đáng xấu hổ",
        "Miễn phạt 1 lượt!",
        "Uống 1 ly nước đầy",
    ],
}


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class DataManager:
    @staticmethod
    def _default_admin_record():
        return {
            "username": DEFAULT_ADMIN_USER,
            "pin": DEFAULT_ADMIN_PIN,
            "role": "owner",
            "status": "active",
            "ban_until": 0,
            "ban_reason": "",
            "allowed_features": list(ALL_FEATURES),
            "session_token": str(uuid.uuid4()),
            "last_seen": 0,
            "ip": "127.0.0.1",
        }

    @staticmethod
    def load_data():
        if not os.path.exists(DATA_FILE):
            initial = {
                "presets": DEFAULT_PRESETS,
                "active_list": DEFAULT_PRESETS["Danh sách Thành viên / Lớp"],
                "history": [],
                "users": {DEFAULT_ADMIN_USER: DataManager._default_admin_record()},
                "allow_guest_join": True,
            }
            DataManager.save_data(initial)
            return initial
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "presets" not in data:
                    data["presets"] = DEFAULT_PRESETS
                if "active_list" not in data or not data["active_list"]:
                    data["active_list"] = DEFAULT_PRESETS["Danh sách Thành viên / Lớp"]
                if "history" not in data:
                    data["history"] = []
                if "users" not in data:
                    data["users"] = {}
                if DEFAULT_ADMIN_USER not in data["users"]:
                    data["users"][DEFAULT_ADMIN_USER] = DataManager._default_admin_record()
                if "allow_guest_join" not in data:
                    data["allow_guest_join"] = True
                return data
        except Exception:
            return {
                "presets": DEFAULT_PRESETS,
                "active_list": DEFAULT_PRESETS["Danh sách Thành viên / Lớp"],
                "history": [],
                "users": {DEFAULT_ADMIN_USER: DataManager._default_admin_record()},
                "allow_guest_join": True,
            }

    @staticmethod
    def save_data(data):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Lỗi lưu file]: {e}")

    @staticmethod
    def check_and_refresh_ban(user):
        """Tự động gỡ lệnh cấm có thời hạn khi đã hết giờ."""
        if user.get("status") == "banned_temp":
            now = time.time()
            if now >= float(user.get("ban_until", 0)):
                user["status"] = "active"
                user["ban_until"] = 0
                user["ban_reason"] = ""
                return True
        return False

    @staticmethod
    def add_history(game, summary, results, actor=""):
        data = DataManager.load_data()
        entry = {
            "id": str(uuid.uuid4())[:8],
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "game": f"{game}" + (f" ({actor})" if actor else ""),
            "summary": summary,
            "results": results if isinstance(results, list) else [str(results)],
        }
        data["history"].insert(0, entry)
        data["history"] = data["history"][:60]
        DataManager.save_data(data)
        return data["history"]


class RandomBackend:
    @staticmethod
    def generate_utility(payload):
        utype = payload.get("utype", "numbers")
        count = max(1, min(100, int(payload.get("count", 1))))

        if utype == "numbers":
            min_v = float(payload.get("min", 1))
            max_v = float(payload.get("max", 100))
            dec = int(payload.get("decimals", 0))
            unique = bool(payload.get("unique", True))
            if min_v > max_v:
                min_v, max_v = max_v, min_v
            if dec == 0:
                mi, ma = int(min_v), int(max_v)
                span = ma - mi + 1
                if unique and count <= span:
                    res = [str(x) for x in random.sample(range(mi, ma + 1), count)]
                else:
                    res = [str(random.randint(mi, ma)) for _ in range(count)]
            else:
                res = [str(round(random.uniform(min_v, max_v), dec)) for _ in range(count)]
            return f"Random {count} số trong [{min_v:g}, {max_v:g}]", res

        elif utype == "password":
            length = max(4, min(64, int(payload.get("length", 12))))
            chars = string.ascii_letters + string.digits + "!@#$%^&*"
            res = ["".join(secrets.choice(chars) for _ in range(length)) for _ in range(count)]
            return f"Tạo {count} mật khẩu ({length} ký tự)", res

        elif utype == "pin":
            length = max(3, min(12, int(payload.get("length", 6))))
            res = ["".join(secrets.choice(string.digits) for _ in range(length)) for _ in range(count)]
            return f"Tạo {count} mã PIN ({length} số)", res

        elif utype == "color":
            res = []
            for _ in range(count):
                r, g, b = random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
                res.append(f"#{r:02X}{g:02X}{b:02X} | rgb({r},{g},{b})")
            return f"Tạo {count} mã màu thiết kế", res

        elif utype == "uuid":
            res = [str(uuid.uuid4()) for _ in range(count)]
            return f"Tạo {count} mã UUID v4", res

        raise ValueError("Loại tiện ích không hợp lệ")


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>OmniRandom Arcade v4.0 — Cloud & Admin Edition</title>
<style>
  :root {
    --bg: #0f172a;
    --surface: #1e293b;
    --surface-2: #283548;
    --surface-hover: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #6366f1;
    --accent: #f59e0b;
    --success: #10b981;
    --danger: #ef4444;
    --border: rgba(255, 255, 255, 0.1);
    --radius: 16px;
    --shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
  }
  [data-theme="light"] {
    --bg: #f1f5f9;
    --surface: #ffffff;
    --surface-2: #f8fafc;
    --surface-hover: #e2e8f0;
    --text: #0f172a;
    --text-muted: #64748b;
    --primary: #4f46e5;
    --accent: #d97706;
    --border: #e2e8f0;
    --shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
  }
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-tap-highlight-color: transparent;
  }
  body {
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
    transition: background 0.25s ease, color 0.25s ease;
  }
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    position: sticky;
    top: 0;
    z-index: 50;
    flex-wrap: wrap;
  }
  .brand-area {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .brand-logo {
    font-size: 1.2rem;
    font-weight: 900;
    background: linear-gradient(135deg, #818cf8, #f472b6, #fbbf24);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    cursor: pointer;
  }
  .user-pill {
    background: var(--surface-2);
    border: 1px solid var(--border);
    padding: 5px 12px;
    border-radius: 99px;
    font-size: 0.8rem;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .role-badge {
    padding: 2px 8px;
    border-radius: 99px;
    font-size: 0.7rem;
    font-weight: 800;
    text-transform: uppercase;
  }
  .role-owner { background: #f59e0b; color: #0f172a; }
  .role-admin { background: #ec4899; color: #ffffff; }
  .role-member { background: #10b981; color: #ffffff; }
  .role-viewer { background: #64748b; color: #ffffff; }

  .top-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .btn-pill {
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 7px 13px;
    border-radius: 99px;
    font-size: 0.83rem;
    font-weight: 700;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.18s ease;
  }
  .btn-pill:hover {
    border-color: var(--primary);
    transform: translateY(-1px);
  }
  .btn-pill.highlight {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }
  .btn-pill.danger {
    background: rgba(239, 68, 68, 0.16);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.35);
  }

  .mode-strip {
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
    padding: 8px 20px;
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .mode-tab {
    padding: 7px 14px;
    border-radius: 10px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.84rem;
    font-weight: 700;
    white-space: nowrap;
    cursor: pointer;
    transition: all 0.18s ease;
  }
  .mode-tab:hover {
    color: var(--text);
    background: var(--surface);
  }
  .mode-tab.active {
    background: var(--surface);
    color: var(--primary);
    border-color: var(--primary);
  }
  .mode-tab.locked-tab {
    opacity: 0.45;
    text-decoration: line-through;
  }

  .main-wrap {
    max-width: 1320px;
    width: 100%;
    margin: 0 auto;
    padding: 20px;
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 310px;
    gap: 20px;
  }
  .main-wrap.full-hub {
    grid-template-columns: 1fr;
  }

  /* Arcade Hub */
  .hub-banner {
    text-align: center;
    margin-bottom: 22px;
  }
  .hub-banner h1 {
    font-size: clamp(1.4rem, 2.8vw, 2.1rem);
    font-weight: 900;
    margin-bottom: 6px;
  }
  .hub-banner p {
    color: var(--text-muted);
    font-size: 0.92rem;
  }
  .arcade-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
  }
  .arcade-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: var(--shadow);
    transition: transform 0.2s ease, border-color 0.2s ease;
  }
  .arcade-card:hover {
    transform: translateY(-4px);
    border-color: var(--primary);
  }
  .arcade-card.disabled-card {
    opacity: 0.45;
    filter: grayscale(0.7);
  }
  .arcade-icon {
    width: 54px;
    height: 54px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.85rem;
  }
  .arcade-card h3 { font-size: 1.08rem; font-weight: 800; }
  .arcade-card p { font-size: 0.84rem; color: var(--text-muted); line-height: 1.45; flex: 1; }
  .arcade-tag {
    align-self: flex-start;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 99px;
    background: var(--surface-2);
    color: var(--accent);
  }

  /* Views */
  .view-pane { display: none; animation: fadeIn 0.2s ease-out; }
  .view-pane.active { display: block; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .stage-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    box-shadow: var(--shadow);
  }
  .stage-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .stage-title { font-size: 1.2rem; font-weight: 800; }
  .controls-bar {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 16px;
    background: var(--surface-2);
    padding: 12px 14px;
    border-radius: 12px;
    border: 1px solid var(--border);
  }
  select, input[type="number"], input[type="text"], input[type="password"], textarea {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 0.88rem;
    outline: none;
  }
  select:focus, input:focus, textarea:focus { border-color: var(--primary); }
  .btn-action {
    background: linear-gradient(135deg, #6366f1, #4f46e5);
    color: #fff;
    border: none;
    padding: 10px 20px;
    border-radius: 11px;
    font-size: 0.94rem;
    font-weight: 800;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
    transition: transform 0.12s ease;
  }
  .btn-action:active { transform: scale(0.97); }
  .btn-action.orange { background: linear-gradient(135deg, #f59e0b, #d97706); }
  .btn-action.green { background: linear-gradient(135deg, #10b981, #059669); }
  .btn-action.red { background: linear-gradient(135deg, #ef4444, #dc2626); }

  .canvas-box {
    width: 100%;
    background: #090d16;
    border-radius: 14px;
    border: 2px solid var(--border);
    overflow: hidden;
    display: flex;
    justify-content: center;
    align-items: center;
  }
  canvas.game-canvas { width: 100%; height: auto; display: block; }

  .winner-banner {
    margin-top: 14px;
    padding: 14px 18px;
    border-radius: 13px;
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.18), rgba(245, 158, 11, 0.15));
    border: 1.5px solid var(--primary);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .winner-text { font-size: 1.2rem; font-weight: 900; color: var(--accent); }

  /* Slot, Hat, Tabletop, Teams */
  .slot-machine {
    background: linear-gradient(180deg, #1e1b4b, #0f172a);
    border: 4px solid #f59e0b;
    border-radius: 18px;
    padding: 24px 16px;
    text-align: center;
  }
  .slot-reels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }
  .slot-window {
    background: #fff; color: #0f172a; border-radius: 12px; border: 4px solid #cbd5e1;
    height: 110px; display: flex; align-items: center; justify-content: center;
    padding: 10px; font-size: clamp(1rem, 2vw, 1.5rem); font-weight: 900;
  }
  .slot-window.locked { border-color: #10b981; background: #ecfdf5; color: #065f46; }

  .magic-stage {
    min-height: 290px;
    background: radial-gradient(circle at center, #1e1b4b 0%, #090d16 100%);
    border-radius: 16px; padding: 24px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
  }
  .magic-icon { font-size: 5.5rem; cursor: pointer; user-select: none; }
  .magic-icon.shaking { animation: shakeHard 0.45s infinite; }
  @keyframes shakeHard {
    0%, 100% { transform: translate(0, 0) rotate(0deg); }
    25% { transform: translate(-10px, -6px) rotate(-10deg); }
    50% { transform: translate(10px, 4px) rotate(10deg); }
    75% { transform: translate(-8px, 6px) rotate(-6deg); }
  }
  .magic-cards { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 18px; }
  .magic-ticket {
    background: linear-gradient(135deg, #fef3c7, #fde68a); color: #78350f;
    border: 2px solid #f59e0b; padding: 12px 20px; border-radius: 12px;
    font-size: 1.15rem; font-weight: 900;
  }

  .felt-table {
    min-height: 260px;
    background: radial-gradient(circle at center, #065f46 0%, #064e3b 100%);
    border: 5px solid #78350f; border-radius: 16px; padding: 20px;
    display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: center;
  }
  .dice-3d {
    width: 78px; height: 78px; background: #fff; color: #0f172a;
    border-radius: 14px; display: flex; align-items: center; justify-content: center;
    font-size: 1.8rem; font-weight: 900;
  }
  .coin-3d {
    width: 88px; height: 88px; border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, #fde047, #ca8a04);
    border: 4px solid #fef08a; color: #422006;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-weight: 900;
  }
  .playing-card {
    width: 88px; height: 126px; background: #fff; color: #0f172a;
    border-radius: 10px; padding: 8px; display: flex; flex-direction: column;
    justify-content: space-between; font-weight: 900; font-size: 1.1rem;
  }
  .playing-card.red { color: #dc2626; }

  .teams-board { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
  .team-box { background: var(--surface-2); border: 2px solid var(--border); border-radius: 12px; padding: 14px; }
  .team-box h4 { font-size: 0.96rem; color: var(--accent); margin-bottom: 8px; display: flex; justify-content: space-between; }
  .team-box ul { list-style: none; display: flex; flex-direction: column; gap: 5px; font-size: 0.88rem; font-weight: 600; }

  /* ADMIN SECURITY TABLE */
  .admin-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-top: 12px;
  }
  table.admin-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  table.admin-table th, table.admin-table td {
    padding: 12px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  table.admin-table th {
    background: var(--surface-2);
    color: var(--text-muted);
    font-weight: 800;
  }
  .status-dot {
    width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 5px;
  }
  .dot-online { background: #10b981; box-shadow: 0 0 8px #10b981; }
  .dot-offline { background: #64748b; }
  .dot-banned { background: #ef4444; }
  .admin-actions-row {
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .btn-xs {
    padding: 5px 9px;
    border-radius: 7px;
    font-size: 0.76rem;
    font-weight: 700;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text);
    cursor: pointer;
  }
  .btn-xs:hover { border-color: var(--primary); }
  .btn-xs.warn { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border-color: rgba(245, 158, 11, 0.35); }
  .btn-xs.ban { background: rgba(239, 68, 68, 0.16); color: #f87171; border-color: rgba(239, 68, 68, 0.35); }
  .btn-xs.unban { background: rgba(16, 185, 129, 0.16); color: #34d399; border-color: rgba(16, 185, 129, 0.35); }

  /* MODALS (LOGIN, BAN SCREEN, PERMISSIONS) */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(9, 13, 22, 0.88);
    backdrop-filter: blur(8px);
    z-index: 100;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 18px;
  }
  .modal-overlay.open { display: flex; }
  .modal-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 24px;
    width: 100%;
    max-width: 440px;
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .perm-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    background: var(--surface-2);
    padding: 12px;
    border-radius: 12px;
  }
  .perm-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
  }

  /* Drawer & History */
  .drawer-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.65); z-index: 90;
    display: none; justify-content: flex-end;
  }
  .drawer-backdrop.open { display: flex; }
  .roster-drawer {
    width: 100%; max-width: 420px; background: var(--surface); height: 100%;
    padding: 22px; display: flex; flex-direction: column; gap: 14px;
  }
  .history-panel {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px; height: fit-content;
  }
  .history-list { display: flex; flex-direction: column; gap: 8px; max-height: 500px; overflow-y: auto; margin-top: 10px; }
  .history-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 10px; font-size: 0.82rem; }
  .history-card .meta { display: flex; justify-content: space-between; color: var(--text-muted); font-size: 0.72rem; }
  .history-card .val { font-weight: 800; color: var(--accent); margin-top: 2px; }

  #confetti-canvas { position: fixed; inset: 0; pointer-events: none; z-index: 120; }

  @media (max-width: 980px) {
    .main-wrap { grid-template-columns: 1fr; padding: 12px; }
    .slot-reels { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<canvas id="confetti-canvas"></canvas>

<!-- MODAL 1: ĐĂNG NHẬP / VÀO PHÒNG -->
<div class="modal-overlay open" id="login-modal">
  <div class="modal-card">
    <h2 style="font-size:1.35rem; font-weight:900;">🎪 Vào Phòng OmniRandom</h2>
    <p style="font-size:0.85rem; color:var(--text-muted);">
      Nhập tên của bạn để tham gia. Nếu bạn là Chủ phòng (Admin), hãy nhập tài khoản <b>admin</b> và mã PIN quản trị.
    </p>
    <div style="display:flex; flex-direction:column; gap:6px;">
      <label style="font-size:0.8rem; font-weight:700; color:var(--text-muted);">Tên người dùng (Username)</label>
      <input type="text" id="login-user" placeholder="VD: Gia Huy hoặc admin">
    </div>
    <div style="display:flex; flex-direction:column; gap:6px;">
      <label style="font-size:0.8rem; font-weight:700; color:var(--text-muted);">Mã PIN / Mật khẩu (Để trống nếu là khách mới)</label>
      <input type="password" id="login-pin" placeholder="Mặc định của admin là 1234">
    </div>
    <div id="login-error" style="color:#f87171; font-size:0.83rem; font-weight:700; display:none;"></div>
    <button class="btn-action" style="justify-content:center;" onclick="submitLogin()">🚀 Tham Gia Ngay</button>
  </div>
</div>

<!-- MODAL 2: MÀN HÌNH KHOÁ KHI BỊ KICK / BỊ CẤM CÓ THỜI HẠN / CẤM VĨNH VIỄN -->
<div class="modal-overlay" id="ban-modal">
  <div class="modal-card" style="border-color:#ef4444; text-align:center;">
    <div style="font-size:3.5rem;" id="ban-icon">🚫</div>
    <h2 style="color:#f87171; font-size:1.35rem; font-weight:900;" id="ban-title">TÀI KHOẢN ĐÃ BỊ HẠN CHẾ</h2>
    <p style="font-size:0.92rem; color:var(--text);" id="ban-desc"></p>
    <div id="ban-timer-box" style="background:rgba(239,68,68,0.15); padding:12px; border-radius:10px; font-weight:900; color:#fca5a5; display:none;"></div>
    <button class="btn-pill" style="justify-content:center;" onclick="logoutAndRelogin()">🔄 Đổi tài khoản khác</button>
  </div>
</div>

<!-- MODAL 3: PHÂN QUYỀN & HẠN CHẾ CHỨC NĂNG TỪNG USER -->
<div class="modal-overlay" id="perm-modal" onclick="if(event.target===this) closePermModal()">
  <div class="modal-card">
    <h3 style="font-size:1.15rem; font-weight:800;">⚙️ Giới Hạn Chức Năng: <span id="perm-target-user" style="color:var(--accent);"></span></h3>
    <p style="font-size:0.82rem; color:var(--text-muted);">Bỏ tích những công cụ bạn không muốn người dùng này được phép sử dụng:</p>
    <div class="perm-grid" id="perm-checkboxes"></div>
    <div style="display:flex; gap:8px; justify-content:flex-end;">
      <button class="btn-pill" onclick="closePermModal()">Huỷ</button>
      <button class="btn-action" style="padding:8px 16px;" onclick="saveUserPermissions()">💾 Lưu Quyền Hạn</button>
    </div>
  </div>
</div>

<header>
  <div class="brand-area">
    <div class="brand-logo" onclick="openMode('hub')">🎪 OmniRandom v4.0</div>
    <div class="user-pill">
      <span>👤 <b id="current-username">Khách</b></span>
      <span class="role-badge role-member" id="current-role-badge">MEMBER</span>
    </div>
    <button class="btn-pill" onclick="openMode('hub')">🏠 Sảnh</button>
    <button class="btn-pill highlight" id="admin-nav-btn" style="display:none;" onclick="openMode('admin')">👑 Quản Trị User</button>
  </div>
  <div class="top-actions">
    <button class="btn-pill highlight" id="roster-btn" onclick="toggleRosterDrawer()">
      📋 Danh Sách Chung (<span id="roster-count-badge">0</span>)
    </button>
    <button class="btn-pill" onclick="copyMobileLink()" id="mobileBtn">📱 Link Điện Thoại</button>
    <button class="btn-pill" onclick="toggleSound()" id="soundBtn">🔊 Âm thanh</button>
    <button class="btn-pill" onclick="toggleTheme()" id="themeBtn">☀️ Sáng</button>
    <button class="btn-pill danger" onclick="logoutAndRelogin()">🚪 Đổi User</button>
  </div>
</header>

<div class="mode-strip">
  <button class="mode-tab active" data-mode="hub" onclick="openMode('hub')">🏠 Tất Cả</button>
  <button class="mode-tab" data-mode="race" onclick="openMode('race')">🦆 Đua Vịt / Xe</button>
  <button class="mode-tab" data-mode="wheel" onclick="openMode('wheel')">🎡 Vòng Quay</button>
  <button class="mode-tab" data-mode="cage" onclick="openMode('cage')">🔮 Lồng Cầu</button>
  <button class="mode-tab" data-mode="slot" onclick="openMode('slot')">🎰 Máy Slot</button>
  <button class="mode-tab" data-mode="hat" onclick="openMode('hat')">🎩 Mũ Ma Thuật</button>
  <button class="mode-tab" data-mode="bomb" onclick="openMode('bomb')">💣 Bom Nổ</button>
  <button class="mode-tab" data-mode="tabletop" onclick="openMode('tabletop')">🎲 Xúc Xắc & Bài</button>
  <button class="mode-tab" data-mode="teams" onclick="openMode('teams')">👥 Chia Đội</button>
  <button class="mode-tab" data-mode="utility" onclick="openMode('utility')">⚡ Số & Tiện Ích</button>
  <button class="mode-tab" data-mode="admin" id="strip-admin-tab" style="display:none;" onclick="openMode('admin')">👑 Quản Trị & Cấm</button>
</div>

<div class="main-wrap full-hub" id="main-wrap">
  <div>
    <!-- 0. SẢNH TRÒ CHƠI -->
    <section id="view-hub" class="view-pane active">
      <div class="hub-banner">
        <h1>Sảnh Trò Chơi Random Trực Quan</h1>
        <p>Hệ thống đồng bộ đa thiết bị kèm phân quyền người dùng thời gian thực.</p>
      </div>
      <div class="arcade-grid">
        <div class="arcade-card" data-feat="race" onclick="openMode('race')">
          <div class="arcade-icon" style="background:rgba(16,185,129,0.18);">🦆</div>
          <span class="arcade-tag">Độ kịch tính cao</span>
          <h3>Đua Vịt / Siêu Xe / Tên Lửa</h3>
          <p>Các tay đua bơi/chạy bứt tốc ngẫu nhiên về vạch đích tìm nhà vô địch.</p>
        </div>
        <div class="arcade-card" data-feat="wheel" onclick="openMode('wheel')">
          <div class="arcade-icon" style="background:rgba(99,102,241,0.18);">🎡</div>
          <span class="arcade-tag">Hỗ trợ tỷ lệ %</span>
          <h3>Vòng Quay Siêu Cấp</h3>
          <p>Vòng quay có chốt đinh va chạm vật lý, hỗ trợ cài đặt trọng số riêng.</p>
        </div>
        <div class="arcade-card" data-feat="cage" onclick="openMode('cage')">
          <div class="arcade-icon" style="background:rgba(245,158,11,0.18);">🔮</div>
          <span class="arcade-tag">Vật lý 2D</span>
          <h3>Lồng Cầu Xổ Số (Bingo)</h3>
          <p>Quay lồng cầu chứa các quả bóng nảy liên tục và bật bóng trúng thưởng.</p>
        </div>
        <div class="arcade-card" data-feat="slot" onclick="openMode('slot')">
          <div class="arcade-icon" style="background:rgba(236,72,153,0.18);">🎰</div>
          <span class="arcade-tag">3 Cuộn Quay</span>
          <h3>Máy Kéo Slot 777</h3>
          <p>Kéo cần gạt để 3 trục quay dừng lại lần lượt cho cả Tên hoặc Số.</p>
        </div>
        <div class="arcade-card" data-feat="hat" onclick="openMode('hat')">
          <div class="arcade-icon" style="background:rgba(139,92,246,0.18);">🎩</div>
          <span class="arcade-tag">Hộp Mù / Ảo Thuật</span>
          <h3>Chiếc Mũ Ma Thuật</h3>
          <p>Bỏ tất cả lựa chọn vào mũ ảo thuật hoặc hộp mù Gacha, lắc mạnh rút thăm.</p>
        </div>
        <div class="arcade-card" data-feat="bomb" onclick="openMode('bomb')">
          <div class="arcade-icon" style="background:rgba(239,68,68,0.18);">💣</div>
          <span class="arcade-tag">Sensory Timer</span>
          <h3>Bom Hẹn Giờ Ngẫu Nhiên</h3>
          <p>Đốt dây cháy chậm với thời gian ẩn. Nổ trúng ai người đó chịu phạt!</p>
        </div>
        <div class="arcade-card" data-feat="tabletop" onclick="openMode('tabletop')">
          <div class="arcade-icon" style="background:rgba(20,184,166,0.18);">🎲</div>
          <span class="arcade-tag">Boardgame</span>
          <h3>Xúc Xắc 3D, Đồng Xu & Bài Tây</h3>
          <p>Đổ xúc xắc trên bàn nỉ, tung đồng xu vàng 3D và rút bài Tây 52 lá.</p>
        </div>
        <div class="arcade-card" data-feat="teams" onclick="openMode('teams')">
          <div class="arcade-icon" style="background:rgba(59,130,246,0.18);">👥</div>
          <span class="arcade-tag">Chia nhóm</span>
          <h3>Chia Đội Trực Quan</h3>
          <p>Trộn danh sách và chia đều thành viên vào các bảng đội màu sắc.</p>
        </div>
        <div class="arcade-card" data-feat="utility" onclick="openMode('utility')">
          <div class="arcade-icon" style="background:rgba(148,163,184,0.18);">⚡</div>
          <span class="arcade-tag">Kỹ thuật</span>
          <h3>Random Số, Mật Khẩu & Màu</h3>
          <p>Quay số phạm vi lớn, tạo mật khẩu bảo mật, mã PIN, UUID và bảng màu HEX.</p>
        </div>
      </div>
    </section>

    <!-- TRANG QUẢN TRỊ VIÊN (ADMIN CONTROL CENTER) -->
    <section id="view-admin" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">👑 Trung Tâm Quản Trị, Phân Quyền & Cấm Người Dùng</div>
        <label style="display:flex; align-items:center; gap:8px; font-size:0.86rem; cursor:pointer;">
          <input type="checkbox" id="toggle-guest-join" onchange="updateRoomAccessSetting()">
          Cho phép người mới tự tạo tên vào phòng
        </label>
      </div>

      <!-- Thêm người dùng mới -->
      <div class="controls-bar">
        <strong style="font-size:0.86rem;">➕ Thêm / Cấp tài khoản:</strong>
        <input type="text" id="new-u-name" placeholder="Tên người dùng..." style="width:160px;">
        <input type="text" id="new-u-pin" placeholder="Mã PIN (tuỳ chọn)" style="width:140px;">
        <select id="new-u-role">
          <option value="member">Member (Được chơi)</option>
          <option value="viewer">Viewer (Chỉ xem)</option>
          <option value="admin">Admin (Quản trị)</option>
        </select>
        <button class="btn-action green" style="padding:8px 16px; font-size:0.85rem;" onclick="adminAddUser()">Thêm User</button>
      </div>

      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Người dùng</th>
              <th>Quyền (Role)</th>
              <th>Trạng thái</th>
              <th>Chức năng được phép</th>
              <th>Hành động (Kick / Cấm / Phân quyền)</th>
            </tr>
          </thead>
          <tbody id="admin-users-tbody"></tbody>
        </table>
      </div>
    </section>

    <!-- 1. ĐUA VỊT / ĐUA XE -->
    <section id="view-race" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🦆 Đường Đua Ngẫu Nhiên</div>
      </div>
      <div class="controls-bar">
        <select id="race-theme" onchange="drawIdleRace()">
          <option value="duck">🦆 Vịt Vàng Bơi Đua</option>
          <option value="car">🏎️ Siêu Xe Công Thức 1</option>
          <option value="rocket">🚀 Tên Lửa Vũ Trụ</option>
          <option value="horse">🐎 Đua Ngựa Thảo Nguyên</option>
        </select>
        <select id="race-duration">
          <option value="6000">6 giây (Nhanh)</option>
          <option value="10000" selected>10 giây (Chuẩn)</option>
          <option value="15000">15 giây (Kịch tính)</option>
        </select>
        <button class="btn-action green" onclick="startRace()" id="race-start-btn">🏁 BẮT ĐẦU ĐUA!</button>
      </div>
      <div class="canvas-box">
        <canvas id="race-canvas" class="game-canvas" width="880" height="440"></canvas>
      </div>
      <div class="winner-banner" id="race-winner-banner" style="display:none;">
        <div class="winner-text" id="race-winner-text"></div>
      </div>
    </section>

    <!-- 2. VÒNG QUAY -->
    <section id="view-wheel" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🎡 Vòng Quay Siêu Cấp</div>
      </div>
      <div class="controls-bar">
        <button class="btn-action orange" onclick="spinArcadeWheel()">🎡 QUAY NGAY</button>
        <label style="display:flex; align-items:center; gap:6px; font-size:0.86rem;">
          <input type="checkbox" id="wheel-auto-remove"> Tự loại ô đã trúng
        </label>
      </div>
      <div class="canvas-box" style="padding:14px;">
        <canvas id="wheel-canvas" width="500" height="500" style="max-width:420px; width:100%; height:auto; cursor:pointer;" onclick="spinArcadeWheel()"></canvas>
      </div>
      <div class="winner-banner" id="wheel-winner-banner" style="display:none;">
        <div class="winner-text" id="wheel-winner-text"></div>
      </div>
    </section>

    <!-- 3. LỒNG CẦU -->
    <section id="view-cage" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🔮 Lồng Cầu Xổ Số Vật Lý</div>
      </div>
      <div class="controls-bar">
        <select id="cage-source" onchange="initCageBalls()">
          <option value="roster">📋 Dùng Danh Sách Chung</option>
          <option value="num50">🔢 Bóng số 1 → 50</option>
          <option value="num99">🔢 Bóng số 1 → 99</option>
        </select>
        <button class="btn-action orange" onclick="drawCageBall()">🔮 QUAY & BẬT BÓNG!</button>
        <button class="btn-pill" onclick="initCageBalls()">🔄 Nạp lại bóng</button>
      </div>
      <div class="canvas-box">
        <canvas id="cage-canvas" class="game-canvas" width="820" height="400"></canvas>
      </div>
      <div class="winner-banner" id="cage-winner-banner" style="display:none;">
        <div class="winner-text" id="cage-winner-text"></div>
      </div>
    </section>

    <!-- 4. MÁY SLOT -->
    <section id="view-slot" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">🎰 Máy Kéo Slot 3 Trục</div></div>
      <div class="controls-bar">
        <select id="slot-mode">
          <option value="roster_single">🎯 Bốc 1 mục (3 trục trùng nhau)</option>
          <option value="roster_triple">🎁 Bốc 3 mục khác nhau</option>
          <option value="numbers">🔢 Quay 3 chữ số (000 - 999)</option>
        </select>
        <button class="btn-action orange" onclick="pullSlotMachine()">🕹️ KÉO CẦN GẠT!</button>
      </div>
      <div class="slot-machine">
        <div style="color:#fde047; font-weight:900;">★ OMNIRANDOM JACKPOT ★</div>
        <div class="slot-reels">
          <div class="slot-window" id="reel-0">🎲</div>
          <div class="slot-window" id="reel-1">🎲</div>
          <div class="slot-window" id="reel-2">🎲</div>
        </div>
      </div>
    </section>

    <!-- 5. MŨ MA THUẬT -->
    <section id="view-hat" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">🎩 Chiếc Mũ Ma Thuật & Hộp Mù</div></div>
      <div class="controls-bar">
        <select id="hat-theme" onchange="document.getElementById('hat-emoji').textContent=this.value">
          <option value="🎩">🎩 Mũ Ảo Thuật</option>
          <option value="🎁">🎁 Hộp Mù Gacha</option>
          <option value="🧧">🧧 Bao Lì Xì Đỏ</option>
        </select>
        <label>Số phiếu: <input type="number" id="hat-count" value="1" min="1" max="10" style="width:70px;"></label>
        <button class="btn-action" onclick="drawFromHat()">✨ LẮC & RÚT THĂM!</button>
      </div>
      <div class="magic-stage">
        <div class="magic-icon" id="hat-emoji" onclick="drawFromHat()">🎩</div>
        <div class="magic-cards" id="hat-results"></div>
      </div>
    </section>

    <!-- 6. BOM HẸN GIỜ -->
    <section id="view-bomb" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">💣 Bom Hẹn Giờ Ngẫu Nhiên</div></div>
      <div class="controls-bar">
        <label>Từ (s): <input type="number" id="bomb-min" value="5" min="2" max="300" style="width:70px;"></label>
        <label>Đến (s): <input type="number" id="bomb-max" value="15" min="2" max="300" style="width:70px;"></label>
        <label style="display:flex; align-items:center; gap:6px; font-size:0.85rem;">
          <input type="checkbox" id="bomb-hide-time" checked> Ẩn số giây
        </label>
        <button class="btn-action red" onclick="toggleBombTimer()" id="bomb-btn">🔥 CHÂM NGÒI NỔ!</button>
      </div>
      <div class="canvas-box">
        <canvas id="bomb-canvas" class="game-canvas" width="760" height="340"></canvas>
      </div>
    </section>

    <!-- 7. XÚC XẮC, XU & BÀI -->
    <section id="view-tabletop" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">🎲 Xúc Xắc, Đồng Xu & Bài Tây 3D</div></div>
      <div class="controls-bar">
        <select id="tb-type">
          <option value="dice6">🎲 Xúc xắc 6 mặt</option>
          <option value="dice20">🐉 Xúc xắc D20</option>
          <option value="coin">🪙 Đồng xu Vàng</option>
          <option value="cards">🃏 Bài Tây 52 Lá</option>
        </select>
        <label>Số lượng: <input type="number" id="tb-qty" value="3" min="1" max="15" style="width:70px;"></label>
        <button class="btn-action green" onclick="rollTabletop()">🎲 LẮC / LẬT NGAY!</button>
      </div>
      <div class="felt-table" id="felt-stage"></div>
      <div class="winner-banner" id="tb-summary-banner" style="display:none;">
        <div class="winner-text" id="tb-summary-text"></div>
      </div>
    </section>

    <!-- 8. CHIA ĐỘI -->
    <section id="view-teams" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">👥 Chia Nhóm / Chia Đội</div></div>
      <div class="controls-bar">
        <select id="team-split-mode">
          <option value="by_teams">Chia theo Tổng số đội</option>
          <option value="by_members">Chia theo Số người mỗi đội</option>
        </select>
        <label>Số lượng: <input type="number" id="team-split-val" value="2" min="1" max="20" style="width:70px;"></label>
        <button class="btn-action" onclick="generateVisualTeams()">⚡ CHIA ĐỘI NGAY</button>
      </div>
      <div class="teams-board" id="teams-board"></div>
    </section>

    <!-- 9. TIỆN ÍCH & RANDOM SỐ -->
    <section id="view-utility" class="view-pane stage-card">
      <div class="stage-top"><div class="stage-title">⚡ Random Số, Mật Khẩu, Mã Màu & UUID</div></div>
      <div class="controls-bar">
        <select id="util-type" onchange="updateUtilFields()">
          <option value="numbers">🔢 Random Số [Min, Max]</option>
          <option value="password">🔐 Mật khẩu bảo mật</option>
          <option value="pin">📲 Mã PIN số</option>
          <option value="color">🎨 Bảng màu HEX / RGB</option>
          <option value="uuid">🆔 Mã UUID v4</option>
        </select>
        <span id="util-num-inputs" style="display:inline-flex; gap:8px;">
          <label>Min: <input type="number" id="u-min" value="1" style="width:85px;"></label>
          <label>Max: <input type="number" id="u-max" value="1000" style="width:90px;"></label>
        </span>
        <span id="util-len-inputs" style="display:none;">
          <label>Độ dài: <input type="number" id="u-len" value="12" min="4" max="64" style="width:70px;"></label>
        </span>
        <label>SL: <input type="number" id="u-count" value="5" min="1" max="50" style="width:70px;"></label>
        <button class="btn-action" onclick="runUtilityGenerator()">✨ KHỞI TẠO</button>
      </div>
      <div class="magic-cards" id="util-results" style="justify-content:flex-start;"></div>
    </section>
  </div>

  <!-- LỊCH SỬ CHUNG -->
  <aside class="history-panel" id="history-panel" style="display:none;">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div style="font-weight:800; font-size:0.96rem;">🕒 Lịch Sử Phòng</div>
      <button class="btn-pill" style="padding:4px 9px; font-size:0.74rem;" onclick="clearAllHistory()">Xoá</button>
    </div>
    <div class="history-list" id="history-list"></div>
  </aside>
</div>

<!-- NGĂN KÉO DANH SÁCH CHUNG -->
<div class="drawer-backdrop" id="roster-backdrop" onclick="if(event.target===this) toggleRosterDrawer()">
  <div class="roster-drawer">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <h3 style="font-size:1.1rem; font-weight:800;">📋 Danh Sách Dùng Chung</h3>
      <button class="btn-pill" onclick="toggleRosterDrawer()">✕ Đóng</button>
    </div>
    <div style="display:flex; gap:8px;">
      <select id="preset-picker" onchange="applyPreset()" style="flex:1;"></select>
      <button class="btn-pill" onclick="saveAsNewPreset()">💾 Lưu mẫu</button>
    </div>
    <textarea id="roster-textarea" style="flex:1;" oninput="syncRosterFromTextarea()"></textarea>
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <span style="font-size:0.84rem; color:var(--text-muted);">Tổng: <b id="drawer-count">0</b> mục</span>
      <button class="btn-action" style="padding:8px 16px;" onclick="toggleRosterDrawer()">✅ Xong</button>
    </div>
  </div>
</div>

<script>
  const FEATURE_LABELS = {
    race: "🦆 Đua Vịt / Xe",
    wheel: "🎡 Vòng Quay",
    cage: "🔮 Lồng Cầu",
    slot: "🎰 Máy Slot",
    hat: "🎩 Mũ Ma Thuật",
    bomb: "💣 Bom Hẹn Giờ",
    tabletop: "🎲 Xúc Xắc & Bài",
    teams: "👥 Chia Đội",
    utility: "⚡ Số & Tiện Ích",
    edit_roster: "✏️ Sửa Danh Sách Chung"
  };

  let state = { presets: {}, active_list: [], history: [], mobile_url: "", users: {}, allow_guest_join: true };
  let session = {
    username: localStorage.getItem("omni_user") || "",
    token: localStorage.getItem("omni_token") || "",
    role: "member",
    allowed_features: Object.keys(FEATURE_LABELS)
  };
  let currentMode = "hub";
  let soundOn = true;
  let audioCtx = null;
  let editingPermUser = "";

  const PALETTE = ["#6366f1", "#ec4899", "#10b981", "#f59e0b", "#06b6d4", "#8b5cf6", "#ef4444", "#14b8a6"];

  // --- WEB AUDIO ---
  function ensureAudio() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) audioCtx = new AC();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  }
  function sfxTone(freq = 600, dur = 0.05, type = "triangle", vol = 0.08) {
    if (!soundOn) return;
    ensureAudio();
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator(), g = audioCtx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
    g.gain.setValueAtTime(vol, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + dur);
    osc.connect(g); g.connect(audioCtx.destination);
    osc.start(); osc.stop(audioCtx.currentTime + dur + 0.01);
  }
  function sfxFanfare() {
    if (!soundOn) return;
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => setTimeout(() => sfxTone(f, 0.22, "sine", 0.11), i * 80));
  }
  function toggleSound() {
    soundOn = !soundOn;
    document.getElementById("soundBtn").textContent = soundOn ? "🔊 Âm thanh" : "🔇 Tắt tiếng";
  }
  function toggleTheme() {
    const isLight = document.body.getAttribute("data-theme") === "light";
    document.body.setAttribute("data-theme", isLight ? "dark" : "light");
    document.getElementById("themeBtn").textContent = isLight ? "☀️ Sáng" : "🌙 Tối";
  }

  function shootConfetti() {
    const c = document.getElementById("confetti-canvas"), ctx = c.getContext("2d");
    c.width = window.innerWidth; c.height = window.innerHeight;
    const parts = Array.from({ length: 70 }, () => ({
      x: c.width * (0.2 + Math.random() * 0.6), y: c.height * 0.4,
      vx: (Math.random() - 0.5) * 14, vy: (Math.random() - 1.1) * 13,
      s: 6 + Math.random() * 6, color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      r: Math.random() * 360, vr: (Math.random() - 0.5) * 12
    }));
    let f = 0;
    function step() {
      ctx.clearRect(0, 0, c.width, c.height);
      parts.forEach(p => {
        p.x += p.vx; p.y += p.vy; p.vy += 0.38; p.r += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate((p.r * Math.PI) / 180);
        ctx.fillStyle = p.color; ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.65); ctx.restore();
      });
      if (++f < 70) requestAnimationFrame(step);
      else ctx.clearRect(0, 0, c.width, c.height);
    }
    step();
  }

  // --- AUTHENTICATION & REAL-TIME HEARTBEAT ENFORCEMENT ---
  async function submitLogin() {
    const username = document.getElementById("login-user").value.trim();
    const pin = document.getElementById("login-pin").value.trim();
    const errEl = document.getElementById("login-error");
    if (!username) {
      errEl.textContent = "Vui lòng nhập tên người dùng!";
      errEl.style.display = "block";
      return;
    }
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, pin })
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.ban_status) {
        showBanOverlay(data.ban_status, data.reason, data.remaining);
        return;
      }
      errEl.textContent = data.error || "Không thể đăng nhập.";
      errEl.style.display = "block";
      return;
    }
    errEl.style.display = "none";
    session.username = data.user.username;
    session.token = data.user.session_token;
    session.role = data.user.role;
    session.allowed_features = data.user.allowed_features;
    localStorage.setItem("omni_user", session.username);
    localStorage.setItem("omni_token", session.token);

    document.getElementById("login-modal").classList.remove("open");
    document.getElementById("ban-modal").classList.remove("open");
    applyUserPermissionsToUI();
    await fetchInitialState();
  }

  function logoutAndRelogin() {
    localStorage.removeItem("omni_user");
    localStorage.removeItem("omni_token");
    session.username = "";
    session.token = "";
    document.getElementById("ban-modal").classList.remove("open");
    document.getElementById("login-modal").classList.add("open");
  }

  function showBanOverlay(status, reason, remainingSec) {
    document.getElementById("login-modal").classList.remove("open");
    const modal = document.getElementById("ban-modal");
    const title = document.getElementById("ban-title");
    const desc = document.getElementById("ban-desc");
    const timerBox = document.getElementById("ban-timer-box");
    const icon = document.getElementById("ban-icon");

    modal.classList.add("open");
    if (status === "kicked") {
      icon.textContent = "🥾";
      title.textContent = "BẠN ĐÃ BỊ MỜI RA KHỎI PHÒNG";
      desc.textContent = reason || "Quản trị viên đã ngắt phiên kết nối của bạn.";
      timerBox.style.display = "none";
    } else if (status === "banned_perm") {
      icon.textContent = "🚫";
      title.textContent = "TÀI KHOẢN BỊ CẤM VĨNH VIỄN";
      desc.textContent = "Lý do: " + (reason || "Vi phạm quy định phòng.");
      timerBox.style.display = "none";
    } else if (status === "banned_temp") {
      icon.textContent = "⏳";
      title.textContent = "TÀI KHOẢN ĐANG BỊ TẠM KHOÁ";
      desc.textContent = "Lý do: " + (reason || "Tạm khoá bởi Quản trị viên.");
      timerBox.style.display = "block";
      const mins = Math.floor(remainingSec / 60);
      const secs = Math.ceil(remainingSec % 60);
      timerBox.textContent = `⏳ Thời gian khoá còn lại: ${mins} phút ${secs} giây`;
    }
  }

  function hasPermission(feat) {
    if (session.role === "owner" || session.role === "admin") return true;
    return session.allowed_features.includes(feat);
  }

  function canTriggerSpin() {
    if (session.role === "viewer") {
      alert("🔒 Tài khoản của bạn đang ở chế độ 'Chỉ xem (Viewer)', không được phép bấm quay!");
      return false;
    }
    return true;
  }

  function applyUserPermissionsToUI() {
    document.getElementById("current-username").textContent = session.username;
    const badge = document.getElementById("current-role-badge");
    badge.textContent = session.role.toUpperCase();
    badge.className = "role-badge role-" + session.role;

    const isAdmin = session.role === "owner" || session.role === "admin";
    document.getElementById("admin-nav-btn").style.display = isAdmin ? "inline-flex" : "none";
    document.getElementById("strip-admin-tab").style.display = isAdmin ? "inline-block" : "none";

    // Khóa các tab & thẻ bị hạn chế
    document.querySelectorAll(".arcade-card[data-feat]").forEach(card => {
      const f = card.getAttribute("data-feat");
      card.classList.toggle("disabled-card", !hasPermission(f));
    });
    document.querySelectorAll(".mode-tab[data-mode]").forEach(tab => {
      const m = tab.getAttribute("data-mode");
      if (m !== "hub" && m !== "admin") {
        tab.classList.toggle("locked-tab", !hasPermission(m));
      }
    });
  }

  async function heartbeatLoop() {
    if (!session.username || !session.token) return;
    try {
      const res = await fetch("/api/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: session.username, token: session.token })
      });
      const data = await res.json();
      if (!res.ok && data.ban_status) {
        showBanOverlay(data.ban_status, data.reason, data.remaining);
        return;
      }
      if (res.ok && data.user) {
        document.getElementById("ban-modal").classList.remove("open");
        const changed =
          session.role !== data.user.role ||
          JSON.stringify(session.allowed_features) !== JSON.stringify(data.user.allowed_features);
        session.role = data.user.role;
        session.allowed_features = data.user.allowed_features;
        if (changed) {
          applyUserPermissionsToUI();
          if (currentMode !== "hub" && currentMode !== "admin" && !hasPermission(currentMode)) {
            alert("⚠️ Quản trị viên vừa thu hồi quyền truy cập công cụ này của bạn!");
            openMode("hub");
          }
        }
        if (data.users && currentMode === "admin") {
          state.users = data.users;
          renderAdminTable();
        }
      }
    } catch (e) {}
  }

  // --- ADMIN MANAGEMENT FUNCTIONS ---
  function renderAdminTable() {
    const tbody = document.getElementById("admin-users-tbody");
    if (!tbody) return;
    document.getElementById("toggle-guest-join").checked = !!state.allow_guest_join;
    const now = Date.now() / 1000;

    tbody.innerHTML = Object.values(state.users).map(u => {
      const isOnline = (now - (u.last_seen || 0)) < 12 && u.status === "active";
      let statusHtml = "";
      if (u.status === "banned_perm") {
        statusHtml = `<span class="status-dot dot-banned"></span><b style="color:#f87171;">Cấm vĩnh viễn</b>`;
      } else if (u.status === "banned_temp") {
        const rem = Math.max(0, Math.ceil((u.ban_until - now) / 60));
        statusHtml = `<span class="status-dot dot-banned"></span><b style="color:#fbbf24;">Khoá (${rem}p)</b>`;
      } else if (u.status === "kicked") {
        statusHtml = `<span class="status-dot dot-offline"></span><span style="color:#94a3b8;">Đã bị Kick</span>`;
      } else {
        statusHtml = `<span class="status-dot ${isOnline ? 'dot-online' : 'dot-offline'}"></span>${isOnline ? 'Đang Online' : 'Offline'}`;
      }

      const isSelfOwner = u.role === "owner";
      const featCount = (u.allowed_features || []).length;

      return `
        <tr>
          <td>
            <div style="font-weight:800;">${u.username}</div>
            <div style="font-size:0.73rem; color:var(--text-muted);">IP: ${u.ip || '-'} | PIN: ${u.pin || '(Trống)'}</div>
          </td>
          <td>
            ${isSelfOwner ? '<span class="role-badge role-owner">OWNER</span>' : `
              <select onchange="adminAction('${u.username}', 'set_role', {role: this.value})" style="padding:4px 8px; font-size:0.8rem;">
                <option value="admin" ${u.role==='admin'?'selected':''}>Admin</option>
                <option value="member" ${u.role==='member'?'selected':''}>Member</option>
                <option value="viewer" ${u.role==='viewer'?'selected':''}>Viewer (Chỉ xem)</option>
              </select>
            `}
          </td>
          <td>${statusHtml}</td>
          <td>
            ${isSelfOwner ? 'Toàn quyền (10/10)' : `
              <button class="btn-xs" onclick="openPermModal('${u.username}')">
                🛠️ Giới hạn (${featCount}/10)
              </button>
            `}
          </td>
          <td>
            ${isSelfOwner ? '<span style="color:var(--text-muted); font-size:0.78rem;">Tài khoản gốc</span>' : `
              <div class="admin-actions-row">
                <button class="btn-xs warn" onclick="adminKickUser('${u.username}')">🥾 Kick</button>
                <button class="btn-xs warn" onclick="adminTempBanUser('${u.username}')">⏳ Cấm thời hạn</button>
                <button class="btn-xs ban" onclick="adminPermBanUser('${u.username}')">🚫 Cấm vĩnh viễn</button>
                ${u.status !== 'active' ? `<button class="btn-xs unban" onclick="adminAction('${u.username}', 'unban', {})">🔓 Mở khoá</button>` : ''}
                <button class="btn-xs" onclick="adminAction('${u.username}', 'delete', {})">🗑️</button>
              </div>
            `}
          </td>
        </tr>
      `;
    }).join("");
  }

  async function adminAction(targetUser, action, extra) {
    const res = await fetch("/api/admin/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor: session.username,
        token: session.token,
        target: targetUser,
        action,
        ...extra
      })
    });
    const data = await res.json();
    if (!res.ok) return alert(data.error || "Lỗi thao tác quản trị");
    state.users = data.users;
    state.allow_guest_join = data.allow_guest_join;
    renderAdminTable();
  }

  function adminAddUser() {
    const name = document.getElementById("new-u-name").value.trim();
    const pin = document.getElementById("new-u-pin").value.trim();
    const role = document.getElementById("new-u-role").value;
    if (!name) return alert("Hãy nhập tên người dùng!");
    adminAction(name, "create_user", { pin, role });
    document.getElementById("new-u-name").value = "";
    document.getElementById("new-u-pin").value = "";
  }

  function adminKickUser(u) {
    const reason = prompt(`Nhập lý do mời "${u}" ra khỏi phòng:`, "Quản trị viên mời bạn ra khỏi phòng.");
    if (reason === null) return;
    adminAction(u, "kick", { reason });
  }

  function adminTempBanUser(u) {
    const minsStr = prompt(`Nhập SỐ PHÚT muốn cấm tạm thời đối với "${u}" (VD: 5, 15, 60, 1440):`, "15");
    if (!minsStr) return;
    const minutes = parseFloat(minsStr);
    if (isNaN(minutes) || minutes <= 0) return alert("Số phút không hợp lệ!");
    const reason = prompt("Nhập lý do tạm khoá:", `Tạm khoá ${minutes} phút bởi Admin.`);
    if (reason === null) return;
    adminAction(u, "ban_temp", { minutes, reason });
  }

  function adminPermBanUser(u) {
    const reason = prompt(`Xác nhận CẤM VĨNH VIỄN "${u}". Nhập lý do cấm:`, "Vi phạm quy định — Cấm vĩnh viễn.");
    if (reason === null) return;
    adminAction(u, "ban_perm", { reason });
  }

  function updateRoomAccessSetting() {
    const allow = document.getElementById("toggle-guest-join").checked;
    adminAction("", "set_guest_join", { allow });
  }

  function openPermModal(username) {
    editingPermUser = username;
    const u = state.users[username];
    if (!u) return;
    document.getElementById("perm-target-user").textContent = username;
    const box = document.getElementById("perm-checkboxes");
    const allowed = u.allowed_features || [];
    box.innerHTML = Object.keys(FEATURE_LABELS).map(key => `
      <label class="perm-item">
        <input type="checkbox" value="${key}" ${allowed.includes(key) ? "checked" : ""}>
        <span>${FEATURE_LABELS[key]}</span>
      </label>
    `).join("");
    document.getElementById("perm-modal").classList.add("open");
  }

  function closePermModal() {
    document.getElementById("perm-modal").classList.remove("open");
  }

  function saveUserPermissions() {
    const checked = Array.from(document.querySelectorAll("#perm-checkboxes input:checked")).map(el => el.value);
    adminAction(editingPermUser, "set_permissions", { allowed_features: checked });
    closePermModal();
  }

  // --- INITIALIZATION & ROSTER ---
  async function fetchInitialState() {
    const res = await fetch("/api/state");
    state = await res.json();
    document.getElementById("roster-textarea").value = state.active_list.join("\n");
    updateRosterCounters();
    renderPresetDropdown();
    renderHistoryList();
    if (state.mobile_url) {
      document.getElementById("mobileBtn").textContent = "📱 " + state.mobile_url.replace("http://", "");
    }
    drawIdleRace();
    drawWheel();
    initCageBalls();
    drawBombCanvas(1, "BẤM CHÂM NGÒI ĐỂ BẮT ĐẦU", false);
  }

  function getCleanRoster() {
    return state.active_list.map(x => x.split("|")[0].trim()).filter(Boolean);
  }

  function syncRosterFromTextarea() {
    if (!hasPermission("edit_roster")) {
      alert("🔒 Bạn đã bị Admin khoá quyền chỉnh sửa Danh Sách Chung!");
      document.getElementById("roster-textarea").value = state.active_list.join("\n");
      return;
    }
    const lines = document.getElementById("roster-textarea").value.split("\n").map(s => s.trim()).filter(Boolean);
    state.active_list = lines;
    updateRosterCounters();
    fetch("/api/roster", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: session.username, token: session.token, items: lines })
    });
    if (currentMode === "race") drawIdleRace();
    if (currentMode === "wheel") drawWheel();
    if (currentMode === "cage") initCageBalls();
  }

  function updateRosterCounters() {
    const count = state.active_list.length;
    document.getElementById("roster-count-badge").textContent = count;
    document.getElementById("drawer-count").textContent = count;
  }

  function toggleRosterDrawer() {
    if (!hasPermission("edit_roster")) {
      return alert("🔒 Quản trị viên đã giới hạn quyền chỉnh sửa Danh Sách Chung của bạn!");
    }
    document.getElementById("roster-backdrop").classList.toggle("open");
  }

  function renderPresetDropdown() {
    const sel = document.getElementById("preset-picker");
    sel.innerHTML = '<option value="">-- Chọn bộ danh sách mẫu --</option>';
    Object.keys(state.presets).forEach(k => {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = `${k} (${state.presets[k].length})`;
      sel.appendChild(opt);
    });
  }

  function applyPreset() {
    const key = document.getElementById("preset-picker").value;
    if (!key || !state.presets[key]) return;
    document.getElementById("roster-textarea").value = state.presets[key].join("\n");
    syncRosterFromTextarea();
  }

  async function saveAsNewPreset() {
    if (!hasPermission("edit_roster")) return alert("🔒 Bạn không có quyền lưu mẫu mới!");
    const name = prompt("Đặt tên cho bộ danh sách này:");
    if (!name) return;
    const res = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), items: state.active_list })
    });
    state = await res.json();
    renderPresetDropdown();
  }

  function openMode(mode) {
    if (mode !== "hub" && mode !== "admin" && !hasPermission(mode)) {
      return alert(`🔒 Chức năng "${FEATURE_LABELS[mode]}" đã bị Quản trị viên khoá đối với tài khoản của bạn!`);
    }
    if (mode === "admin" && !(session.role === "owner" || session.role === "admin")) {
      return alert("🔒 Chỉ Quản trị viên mới có quyền truy cập mục này!");
    }
    currentMode = mode;
    document.querySelectorAll(".view-pane").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".mode-tab").forEach(el => {
      el.classList.toggle("active", el.getAttribute("data-mode") === mode);
    });
    document.getElementById("view-" + mode).classList.add("active");
    document.getElementById("main-wrap").classList.toggle("full-hub", mode === "hub" || mode === "admin");
    document.getElementById("history-panel").style.display = (mode === "hub" || mode === "admin") ? "none" : "block";

    if (mode === "admin") renderAdminTable();
    if (mode === "race") drawIdleRace();
    if (mode === "wheel") drawWheel();
    if (mode === "cage") initCageBalls();
    if (mode === "teams") generateVisualTeams();
  }

  async function logHistory(game, summary, results) {
    const res = await fetch("/api/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game, summary, results, actor: session.username })
    });
    const data = await res.json();
    state.history = data.history;
    renderHistoryList();
  }

  function renderHistoryList() {
    const box = document.getElementById("history-list");
    if (!state.history || !state.history.length) {
      box.innerHTML = '<div style="color:var(--text-muted); font-size:0.82rem;">Chưa có lịch sử.</div>';
      return;
    }
    box.innerHTML = state.history.map(h => `
      <div class="history-card">
        <div class="meta"><span>${h.game}</span><span>${h.time}</span></div>
        <div style="font-size:0.75rem; color:var(--text-muted);">${h.summary}</div>
        <div class="val">${h.results.join(" • ")}</div>
      </div>
    `).join("");
  }

  async function clearAllHistory() {
    if (!canTriggerSpin()) return;
    const res = await fetch("/api/history/clear", { method: "POST" });
    state = await res.json();
    renderHistoryList();
  }

  function copyMobileLink() {
    navigator.clipboard.writeText(state.mobile_url || window.location.href);
    const btn = document.getElementById("mobileBtn");
    const old = btn.textContent;
    btn.textContent = "✅ Đã chép link!";
    setTimeout(() => btn.textContent = old, 1500);
  }

  // --- GAME 1: RACE ---
  let raceRunning = false;
  const THEME_CONFIG = {
    duck: { icon: "🦆", bg1: "#0284c7", bg2: "#0369a1", lane: "rgba(255,255,255,0.18)" },
    car: { icon: "🏎️", bg1: "#1e293b", bg2: "#0f172a", lane: "rgba(250,204,21,0.28)" },
    rocket: { icon: "🚀", bg1: "#090d16", bg2: "#1e1b4b", lane: "rgba(129,140,248,0.2)" },
    horse: { icon: "🐎", bg1: "#15803d", bg2: "#166534", lane: "rgba(255,255,255,0.2)" }
  };

  function drawIdleRace() {
    if (raceRunning) return;
    const items = getCleanRoster().slice(0, 12);
    const theme = THEME_CONFIG[document.getElementById("race-theme").value];
    renderRaceFrame(items.map((name, i) => ({ name, x: 165, boost: false })), theme, 0);
  }

  function renderRaceFrame(racers, theme, tick) {
    const canvas = document.getElementById("race-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height, finishX = W - 95;
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, theme.bg1); grad.addColorStop(1, theme.bg2);
    ctx.fillStyle = grad; ctx.fillRect(0, 0, W, H);

    for (let y = 0; y < H; y += 16) {
      ctx.fillStyle = (y / 16) % 2 === 0 ? "#fff" : "#0f172a";
      ctx.fillRect(finishX, y, 12, 16);
      ctx.fillStyle = (y / 16) % 2 === 0 ? "#0f172a" : "#fff";
      ctx.fillRect(finishX + 12, y, 12, 16);
    }
    if (!racers.length) return;
    const laneH = H / racers.length;
    racers.forEach((r, i) => {
      const yMid = i * laneH + laneH / 2;
      ctx.strokeStyle = theme.lane; ctx.setLineDash([8, 8]);
      ctx.beginPath(); ctx.moveTo(0, (i + 1) * laneH); ctx.lineTo(W, (i + 1) * laneH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "rgba(0,0,0,0.45)"; ctx.fillRect(10, yMid - 13, 135, 26);
      ctx.fillStyle = "#fff"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "left";
      ctx.fillText(`${i + 1}. ${r.name.slice(0, 14)}`, 16, yMid + 5);
      if (r.boost) { ctx.font = "20px sans-serif"; ctx.fillText("🔥", r.x - 24, yMid + 7); }
      ctx.font = `${Math.min(34, Math.max(20, laneH * 0.65))}px sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(theme.icon, r.x, yMid + 9 + Math.sin(tick * 0.25 + i) * 3);
    });
  }

  function startRace() {
    if (!canTriggerSpin() || raceRunning) return;
    const items = getCleanRoster().slice(0, 12);
    if (items.length < 2) return alert("Cần ít nhất 2 tay đua!");
    raceRunning = true;
    const duration = parseInt(document.getElementById("race-duration").value, 10);
    const theme = THEME_CONFIG[document.getElementById("race-theme").value];
    const startX = 165, distance = 880 - 95 - startX;
    const winnerIdx = Math.floor(Math.random() * items.length);
    const racers = items.map((name, i) => ({
      name, x: startX, phase: Math.random() * 6.28, freq: 2 + Math.random() * 2.5,
      finalOffset: i === winnerIdx ? 0 : -(12 + Math.random() * 85), boost: false
    }));
    const t0 = performance.now(); let tick = 0;
    function step(now) {
      const p = Math.min(1, (now - t0) / duration);
      racers.forEach(r => {
        const wave = Math.sin(p * Math.PI * r.freq + r.phase) * (1 - p) * 42;
        const targetX = startX + p * (distance + r.finalOffset) + wave;
        r.boost = (targetX - r.x) > 2.2 && p < 0.95;
        r.x = Math.max(startX, targetX);
      });
      if (++tick % 12 === 0) sfxTone(300 + p * 350, 0.03);
      renderRaceFrame(racers, theme, tick);
      if (p < 1) requestAnimationFrame(step);
      else {
        raceRunning = false;
        const sorted = [...racers].sort((a, b) => b.x - a.x);
        document.getElementById("race-winner-text").textContent = "🏆 Hạng Nhất: " + sorted[0].name;
        document.getElementById("race-winner-banner").style.display = "flex";
        sfxFanfare(); shootConfetti();
        logHistory("🦆 Đua Tốc Độ", `Đua ${racers.length} tay đua`, [sorted[0].name]);
      }
    }
    requestAnimationFrame(step);
  }

  // --- GAME 2: WHEEL ---
  let wheelAngle = 0, wheelSpinning = false;
  function parseWheelSlices() {
    return state.active_list.map(line => {
      if (line.includes("|")) {
        const [n, w] = line.split("|");
        const weight = parseFloat(w);
        return { name: n.trim(), weight: !isNaN(weight) && weight > 0 ? weight : 1 };
      }
      return { name: line.trim(), weight: 1 };
    }).filter(x => x.name);
  }
  function drawWheel() {
    const slices = parseWheelSlices(), canvas = document.getElementById("wheel-canvas");
    if (!canvas || !slices.length) return;
    const ctx = canvas.getContext("2d"), cx = 250, cy = 250, r = 225;
    ctx.clearRect(0, 0, 500, 500);
    const totalW = slices.reduce((s, x) => s + x.weight, 0);
    let a = wheelAngle;
    slices.forEach((s, i) => {
      const arc = (s.weight / totalW) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, r, a, a + arc); ctx.closePath();
      ctx.fillStyle = PALETTE[i % PALETTE.length]; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(a + arc / 2);
      ctx.textAlign = "right"; ctx.fillStyle = "#fff"; ctx.font = "bold 16px sans-serif";
      ctx.fillText(s.name.slice(0, 18), r - 20, 6); ctx.restore();
      a += arc;
    });
    ctx.beginPath(); ctx.moveTo(cx + r - 12, cy); ctx.lineTo(cx + r + 18, cy - 14); ctx.lineTo(cx + r + 18, cy + 14);
    ctx.closePath(); ctx.fillStyle = "#ef4444"; ctx.fill();
  }
  function spinArcadeWheel() {
    if (!canTriggerSpin() || wheelSpinning) return;
    const slices = parseWheelSlices();
    if (slices.length < 2) return alert("Cần ít nhất 2 ô!");
    wheelSpinning = true;
    const totalW = slices.reduce((s, x) => s + x.weight, 0);
    const startA = wheelAngle, delta = (6 + Math.random() * 4) * Math.PI * 2 + Math.random() * 6.28;
    const t0 = performance.now(), dur = 3400;
    function step(now) {
      const t = Math.min(1, (now - t0) / dur);
      wheelAngle = startA + delta * (1 - Math.pow(1 - t, 3.5));
      drawWheel();
      if (t < 1) requestAnimationFrame(step);
      else {
        wheelSpinning = false;
        const norm = ((0 - wheelAngle) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
        let acc = 0, win = slices[0];
        for (const s of slices) {
          const arc = (s.weight / totalW) * Math.PI * 2;
          if (norm >= acc && norm < acc + arc) { win = s; break; }
          acc += arc;
        }
        document.getElementById("wheel-winner-text").textContent = "🎉 Kết quả: " + win.name;
        document.getElementById("wheel-winner-banner").style.display = "flex";
        sfxFanfare(); shootConfetti();
        logHistory("🎡 Vòng Quay", `Quay ${slices.length} ô`, [win.name]);
      }
    }
    requestAnimationFrame(step);
  }

  // --- GAME 3: CAGE ---
  let cageBalls = [], drawnBalls = [], cageAnimId = null, cageAngle = 0;
  function initCageBalls() {
    const src = document.getElementById("cage-source")?.value || "roster";
    const labels = src === "roster" ? getCleanRoster() : Array.from({ length: src === "num50" ? 50 : 99 }, (_, i) => String(i + 1));
    drawnBalls = [];
    cageBalls = labels.slice(0, 60).map((label, i) => ({
      label, x: 260 + (Math.random() - 0.5) * 120, y: 200 + (Math.random() - 0.5) * 120,
      vx: (Math.random() - 0.5) * 5, vy: (Math.random() - 0.5) * 5, color: PALETTE[i % PALETTE.length]
    }));
    if (!cageAnimId) animateCage();
  }
  function animateCage() {
    const canvas = document.getElementById("cage-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d"), cx = 260, cy = 200, R = 148;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    cageAngle += 0.02;
    ctx.strokeStyle = "rgba(148,163,184,0.35)"; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    cageBalls.forEach(b => {
      b.vy += 0.18; b.x += b.vx; b.y += b.vy;
      const dx = b.x - cx, dy = b.y - cy, d = Math.hypot(dx, dy);
      if (d > R - 18) {
        const nx = dx / d, ny = dy / d;
        b.x = cx + nx * (R - 18); b.y = cy + ny * (R - 18);
        b.vx = -b.vx * 0.75 - ny * 3; b.vy = -b.vy * 0.75 + nx * 3;
      }
      ctx.beginPath(); ctx.arc(b.x, b.y, 17, 0, Math.PI * 2);
      ctx.fillStyle = b.color; ctx.fill();
      ctx.fillStyle = "#fff"; ctx.font = "bold 11px sans-serif"; ctx.textAlign = "center";
      ctx.fillText(b.label.slice(0, 5), b.x, b.y + 4);
    });
    ctx.fillStyle = "#fff"; ctx.font = "bold 15px sans-serif"; ctx.textAlign = "left";
    ctx.fillText(`🎱 Đã rút (${drawnBalls.length}):`, 470, 55);
    drawnBalls.slice(0, 8).forEach((db, idx) => {
      ctx.fillText(`#${drawnBalls.length - idx}: ${db.label}`, 470, 90 + idx * 32);
    });
    cageAnimId = requestAnimationFrame(animateCage);
  }
  function drawCageBall() {
    if (!canTriggerSpin()) return;
    if (!cageBalls.length) return alert("Đã hết bóng trong lồng!");
    const picked = cageBalls.splice(Math.floor(Math.random() * cageBalls.length), 1)[0];
    drawnBalls.unshift(picked);
    document.getElementById("cage-winner-text").textContent = "🎱 Bóng vừa rơi: " + picked.label;
    document.getElementById("cage-winner-banner").style.display = "flex";
    sfxFanfare(); shootConfetti();
    logHistory("🔮 Lồng Cầu", "Bật bóng xổ số", [picked.label]);
  }

  // --- GAME 4: SLOT ---
  function pullSlotMachine() {
    if (!canTriggerSpin()) return;
    const mode = document.getElementById("slot-mode").value;
    const pool = mode === "numbers" ? ["0","1","2","3","4","5","6","7","8","9"] : getCleanRoster();
    if (!pool.length) return alert("Danh sách trống!");
    const win = pool[Math.floor(Math.random() * pool.length)];
    const targets = mode === "roster_single" ? [win, win, win] : [
      pool[Math.floor(Math.random() * pool.length)],
      pool[Math.floor(Math.random() * pool.length)],
      pool[Math.floor(Math.random() * pool.length)]
    ];
    [0, 1, 2].forEach(i => {
      const el = document.getElementById("reel-" + i);
      el.classList.remove("locked");
      const timer = setInterval(() => { el.textContent = pool[Math.floor(Math.random() * pool.length)]; }, 65);
      setTimeout(() => {
        clearInterval(timer);
        el.textContent = targets[i];
        el.classList.add("locked");
        sfxTone(400 + i * 160, 0.1);
        if (i === 2) { sfxFanfare(); shootConfetti(); logHistory("🎰 Máy Slot", "Quay 3 trục", [targets.join(" - ")]); }
      }, 700 + i * 600);
    });
  }

  // --- GAME 5: HAT ---
  function drawFromHat() {
    if (!canTriggerSpin()) return;
    const pool = getCleanRoster();
    if (!pool.length) return alert("Danh sách trống!");
    const count = Math.min(pool.length, Math.max(1, parseInt(document.getElementById("hat-count").value, 10) || 1));
    const hat = document.getElementById("hat-emoji");
    hat.classList.add("shaking");
    setTimeout(() => {
      hat.classList.remove("shaking");
      const picked = [...pool].sort(() => Math.random() - 0.5).slice(0, count);
      document.getElementById("hat-results").innerHTML = picked.map(x => `<div class="magic-ticket">✨ ${x}</div>`).join("");
      sfxFanfare(); shootConfetti();
      logHistory("🎩 Mũ Ma Thuật", `Rút ${count} phiếu`, picked);
    }, 550);
  }

  // --- GAME 6: BOMB ---
  let bombTimerId = null;
  function drawBombCanvas(ratio, label, exploded) {
    const canvas = document.getElementById("bomb-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    if (exploded) {
      ctx.fillStyle = "#7f1d1d"; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = "#fef08a"; ctx.font = "900 30px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("💥 BÙM! " + label, W / 2, H / 2);
      return;
    }
    ctx.strokeStyle = "#d97706"; ctx.lineWidth = 8;
    ctx.beginPath(); ctx.moveTo(260, 170); ctx.lineTo(260 + Math.max(0, ratio) * 240, 170); ctx.stroke();
    ctx.beginPath(); ctx.arc(195, 175, 70, 0, Math.PI * 2); ctx.fillStyle = "#1e293b"; ctx.fill();
    ctx.fillStyle = "#fff"; ctx.font = "bold 22px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(label, 195, 183);
  }
  function toggleBombTimer() {
    if (!canTriggerSpin()) return;
    if (bombTimerId) { cancelAnimationFrame(bombTimerId); bombTimerId = null; return; }
    const minS = parseFloat(document.getElementById("bomb-min").value) || 5;
    const maxS = parseFloat(document.getElementById("bomb-max").value) || 12;
    const totalMs = (minS + Math.random() * Math.max(1, maxS - minS)) * 1000;
    const hide = document.getElementById("bomb-hide-time").checked, t0 = performance.now();
    function tick(now) {
      const rem = Math.max(0, totalMs - (now - t0)), ratio = rem / totalMs;
      drawBombCanvas(ratio, hide ? "???" : (rem / 1000).toFixed(1) + "s", false);
      if (rem > 0) bombTimerId = requestAnimationFrame(tick);
      else {
        bombTimerId = null;
        const pool = getCleanRoster();
        const victim = pool.length ? pool[Math.floor(Math.random() * pool.length)] : "Hết giờ!";
        drawBombCanvas(0, "Người dính bom: " + victim, true);
        logHistory("💣 Bom Nổ", "Bom hẹn giờ", [victim]);
      }
    }
    bombTimerId = requestAnimationFrame(tick);
  }

  // --- GAME 7: TABLETOP ---
  const DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"];
  function rollTabletop() {
    if (!canTriggerSpin()) return;
    const type = document.getElementById("tb-type").value;
    const qty = Math.min(15, Math.max(1, parseInt(document.getElementById("tb-qty").value, 10) || 2));
    const stage = document.getElementById("felt-stage");
    if (type === "dice6" || type === "dice20") {
      const sides = type === "dice6" ? 6 : 20;
      const vals = Array.from({ length: qty }, () => 1 + Math.floor(Math.random() * sides));
      stage.innerHTML = vals.map(v => `<div class="dice-3d">${sides === 6 ? DICE_FACES[v - 1] : v}</div>`).join("");
      logHistory("🎲 Xúc Xắc", `${qty} viên D${sides}`, vals.map(String));
    } else if (type === "coin") {
      const flips = Array.from({ length: qty }, () => Math.random() < 0.5 ? "NGỬA" : "SẤP");
      stage.innerHTML = flips.map(f => `<div class="coin-3d">🪙<div>${f}</div></div>`).join("");
      logHistory("🪙 Đồng Xu", `${qty} đồng xu`, flips);
    } else {
      const suits = ["♠", "♣", "♦", "♥"], ranks = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"];
      const deck = []; suits.forEach(s => ranks.forEach(r => deck.push({ r, s, red: s === "♦" || s === "♥" })));
      deck.sort(() => Math.random() - 0.5);
      const drawn = deck.slice(0, qty);
      stage.innerHTML = drawn.map(c => `<div class="playing-card ${c.red?'red':''}"><div>${c.r}${c.s}</div><div style="font-size:2rem;text-align:center;">${c.s}</div></div>`).join("");
      logHistory("🃏 Rút Bài", `${qty} lá bài`, drawn.map(c => c.r + c.s));
    }
    sfxFanfare();
  }

  // --- GAME 8: TEAMS ---
  function generateVisualTeams() {
    if (!canTriggerSpin()) return;
    const members = [...getCleanRoster()].sort(() => Math.random() - 0.5);
    const mode = document.getElementById("team-split-mode").value;
    const val = Math.max(1, parseInt(document.getElementById("team-split-val").value, 10) || 2);
    let teams = [];
    if (mode === "by_teams") {
      const k = Math.min(val, Math.max(1, members.length));
      teams = Array.from({ length: k }, () => []);
      members.forEach((m, i) => teams[i % k].push(m));
    } else {
      for (let i = 0; i < members.length; i += val) teams.push(members.slice(i, i + val));
    }
    document.getElementById("teams-board").innerHTML = teams.map((t, i) => `
      <div class="team-box"><h4><span>🛡️ Đội ${i + 1}</span><span>${t.length} người</span></h4><ul>${t.map(m => `<li>• ${m}</li>`).join("")}</ul></div>
    `).join("");
  }

  // --- GAME 9: UTILITY ---
  function updateUtilFields() {
    const t = document.getElementById("util-type").value;
    document.getElementById("util-num-inputs").style.display = t === "numbers" ? "inline-flex" : "none";
    document.getElementById("util-len-inputs").style.display = (t === "password" || t === "pin") ? "inline-flex" : "none";
  }
  async function runUtilityGenerator() {
    if (!canTriggerSpin()) return;
    const payload = {
      utype: document.getElementById("util-type").value,
      min: document.getElementById("u-min").value,
      max: document.getElementById("u-max").value,
      length: document.getElementById("u-len").value,
      count: document.getElementById("u-count").value,
      unique: true,
      actor: session.username
    };
    const res = await fetch("/api/utility", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    document.getElementById("util-results").innerHTML = data.results.map(item => `<div class="magic-ticket">${item}</div>`).join("");
    sfxFanfare();
    state.history = data.history;
    renderHistoryList();
  }

  window.addEventListener("DOMContentLoaded", () => {
    fetchInitialState();
    if (session.username) {
      document.getElementById("login-user").value = session.username;
    }
    setInterval(heartbeatLoop, 2500);
  });
</script>
</body>
</html>
"""


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class ArcadeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            st = DataManager.load_data()
            st["mobile_url"] = f"http://{get_lan_ip()}:{PORT}"
            self._send_json(st)
        else:
            raw = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        payload = json.loads(body)
        client_ip = self.client_address[0]

        if self.path == "/api/auth/login":
            data = DataManager.load_data()
            username = payload.get("username", "").strip()
            pin = payload.get("pin", "").strip()

            if not username:
                return self._send_json({"error": "Tên người dùng không hợp lệ."}, 400)

            user = data["users"].get(username)
            if not user:
                if not data.get("allow_guest_join", True):
                    return self._send_json(
                        {"error": "Phòng hiện đang đóng tự do. Chỉ tài khoản do Admin tạo mới được vào!"},
                        403,
                    )
                user = {
                    "username": username,
                    "pin": pin,
                    "role": "member",
                    "status": "active",
                    "ban_until": 0,
                    "ban_reason": "",
                    "allowed_features": list(ALL_FEATURES),
                    "session_token": str(uuid.uuid4()),
                    "last_seen": time.time(),
                    "ip": client_ip,
                }
                data["users"][username] = user
            else:
                DataManager.check_and_refresh_ban(user)
                if user.get("status") == "banned_perm":
                    return self._send_json(
                        {"ban_status": "banned_perm", "reason": user.get("ban_reason", "")},
                        403,
                    )
                if user.get("status") == "banned_temp":
                    rem = max(0, float(user.get("ban_until", 0)) - time.time())
                    return self._send_json(
                        {
                            "ban_status": "banned_temp",
                            "reason": user.get("ban_reason", ""),
                            "remaining": rem,
                        },
                        403,
                    )
                if user.get("pin") and user.get("pin") != pin:
                    return self._send_json({"error": "Sai mã PIN / Mật khẩu tài khoản!"}, 401)

                if user.get("status") == "kicked":
                    user["status"] = "active"
                user["session_token"] = str(uuid.uuid4())
                user["last_seen"] = time.time()
                user["ip"] = client_ip

            DataManager.save_data(data)
            return self._send_json({"user": user})

        elif self.path == "/api/heartbeat":
            data = DataManager.load_data()
            username = payload.get("username", "")
            token = payload.get("token", "")
            user = data["users"].get(username)

            if not user:
                return self._send_json(
                    {"ban_status": "kicked", "reason": "Tài khoản của bạn đã bị xoá bởi Admin."},
                    403,
                )

            if DataManager.check_and_refresh_ban(user):
                DataManager.save_data(data)

            if user.get("status") == "kicked" or user.get("session_token") != token:
                return self._send_json(
                    {"ban_status": "kicked", "reason": user.get("ban_reason", "Bạn đã bị mời ra khỏi phòng.")},
                    403,
                )
            if user.get("status") == "banned_perm":
                return self._send_json(
                    {"ban_status": "banned_perm", "reason": user.get("ban_reason", "")},
                    403,
                )
            if user.get("status") == "banned_temp":
                rem = max(0, float(user.get("ban_until", 0)) - time.time())
                return self._send_json(
                    {
                        "ban_status": "banned_temp",
                        "reason": user.get("ban_reason", ""),
                        "remaining": rem,
                    },
                    403,
                )

            user["last_seen"] = time.time()
            user["ip"] = client_ip
            DataManager.save_data(data)

            resp = {"user": user}
            if user.get("role") in ("owner", "admin"):
                resp["users"] = data["users"]
            return self._send_json(resp)

        elif self.path == "/api/admin/action":
            data = DataManager.load_data()
            actor_name = payload.get("actor", "")
            token = payload.get("token", "")
            actor = data["users"].get(actor_name)

            if (
                not actor
                or actor.get("session_token") != token
                or actor.get("role") not in ("owner", "admin")
            ):
                return self._send_json({"error": "Bạn không có quyền Quản trị viên!"}, 403)

            action = payload.get("action")
            target_name = payload.get("target", "").strip()

            if action == "set_guest_join":
                data["allow_guest_join"] = bool(payload.get("allow", True))
                DataManager.save_data(data)
                return self._send_json(
                    {"users": data["users"], "allow_guest_join": data["allow_guest_join"]}
                )

            if action == "create_user":
                if not target_name:
                    return self._send_json({"error": "Thiếu tên user"}, 400)
                data["users"][target_name] = {
                    "username": target_name,
                    "pin": payload.get("pin", "").strip(),
                    "role": payload.get("role", "member"),
                    "status": "active",
                    "ban_until": 0,
                    "ban_reason": "",
                    "allowed_features": list(ALL_FEATURES),
                    "session_token": str(uuid.uuid4()),
                    "last_seen": 0,
                    "ip": "-",
                }
                DataManager.save_data(data)
                return self._send_json(
                    {"users": data["users"], "allow_guest_join": data["allow_guest_join"]}
                )

            target = data["users"].get(target_name)
            if not target:
                return self._send_json({"error": "Không tìm thấy người dùng này."}, 404)
            if target.get("role") == "owner":
                return self._send_json({"error": "Không thể tác động lên tài khoản Owner gốc!"}, 403)

            if action == "set_role":
                new_role = payload.get("role", "member")
                if new_role in ("admin", "member", "viewer"):
                    target["role"] = new_role
            elif action == "set_permissions":
                target["allowed_features"] = payload.get("allowed_features", list(ALL_FEATURES))
            elif action == "kick":
                target["status"] = "kicked"
                target["ban_reason"] = payload.get("reason", "Bị mời ra bởi Admin.")
                target["session_token"] = str(uuid.uuid4())
            elif action == "ban_temp":
                mins = float(payload.get("minutes", 15))
                target["status"] = "banned_temp"
                target["ban_until"] = time.time() + (mins * 60)
                target["ban_reason"] = payload.get("reason", f"Tạm khoá {mins:g} phút.")
                target["session_token"] = str(uuid.uuid4())
            elif action == "ban_perm":
                target["status"] = "banned_perm"
                target["ban_until"] = 0
                target["ban_reason"] = payload.get("reason", "Cấm vĩnh viễn bởi Admin.")
                target["session_token"] = str(uuid.uuid4())
            elif action == "unban":
                target["status"] = "active"
                target["ban_until"] = 0
                target["ban_reason"] = ""
            elif action == "delete":
                del data["users"][target_name]

            DataManager.save_data(data)
            return self._send_json(
                {"users": data["users"], "allow_guest_join": data["allow_guest_join"]}
            )

        elif self.path == "/api/roster":
            data = DataManager.load_data()
            u = data["users"].get(payload.get("username", ""))
            if u and u.get("role") not in ("owner", "admin"):
                if "edit_roster" not in u.get("allowed_features", []):
                    return self._send_json({"error": "Không có quyền sửa danh sách"}, 403)
            data["active_list"] = payload.get("items", [])
            DataManager.save_data(data)
            self._send_json({"ok": True})

        elif self.path == "/api/presets":
            data = DataManager.load_data()
            name = payload.get("name", "").strip()
            if name:
                data["presets"][name] = payload.get("items", [])
                DataManager.save_data(data)
            self._send_json(data)

        elif self.path == "/api/log":
            hist = DataManager.add_history(
                payload.get("game", "Random"),
                payload.get("summary", ""),
                payload.get("results", []),
                payload.get("actor", ""),
            )
            self._send_json({"history": hist})

        elif self.path == "/api/utility":
            summary, results = RandomBackend.generate_utility(payload)
            hist = DataManager.add_history(
                "⚡ Tiện Ích", summary, results, payload.get("actor", "")
            )
            self._send_json({"summary": summary, "results": results, "history": hist})

        elif self.path == "/api/history/clear":
            data = DataManager.load_data()
            data["history"] = []
            DataManager.save_data(data)
            self._send_json(data)


if __name__ == "__main__":
    DataManager.load_data()
    lan_ip = get_lan_ip()
    local_url = f"http://localhost:{PORT}"
    mobile_url = f"http://{lan_ip}:{PORT}"

    print("=" * 66)
    print("👑 OMNIRANDOM ARCADE v4.0 (MULTI-USER & ADMIN CONTROL) READY!")
    print("=" * 66)
    print(f"💻 Trên PC / Laptop   : {local_url}")
    print(f"📱 Trên Điện thoại    : {mobile_url}")
    print(f"🔐 Tài khoản Chủ phòng: Username = {DEFAULT_ADMIN_USER} | Mã PIN = {DEFAULT_ADMIN_PIN}")
    print("-" * 66)

    if os.environ.get("RENDER") is None and os.environ.get("PYTHONANYWHERE_DOMAIN") is None:
        webbrowser.open(local_url)

    with ThreadedHTTPServer(("0.0.0.0", PORT), ArcadeHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nĐã đóng OmniRandom Arcade v4.0.")