import datetime
import hashlib
import http.server
import json
import os
import random
import secrets
import socket
import socketserver
import string
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

PORT = int(os.environ.get("PORT", 8080))
DATA_FILE = "random_data.json"
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")

# Tuỳ chọn đồng bộ đám mây (VD: JSONBin.io v3 URL & X-Master-Key) để không mất dữ liệu khi Render restart
CLOUD_SYNC_URL = os.environ.get("CLOUD_SYNC_URL", "").strip()
CLOUD_SYNC_KEY = os.environ.get("CLOUD_SYNC_KEY", "").strip()

DATA_LOCK = threading.RLock()
FAILED_LOGINS = {}  # ip -> [timestamps]
ACTION_RATE = {}    # username -> [timestamps]

ALL_FEATURES = [
    "race",
    "wheel",
    "survival",
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


def hash_pin(pin, salt=None):
    if not pin:
        return "", ""
    if not salt:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{pin}".encode("utf-8")).hexdigest()
    return digest, salt


def verify_pin(pin, stored_hash, salt):
    if not stored_hash:
        return True
    if not pin:
        return False
    check, _ = hash_pin(pin, salt)
    return secrets.compare_digest(check, stored_hash)


def parse_device_label(ua):
    ua_low = (ua or "").lower()
    if "iphone" in ua_low or "ipad" in ua_low:
        return "iOS (iPhone/iPad)"
    if "android" in ua_low:
        return "Android Mobile"
    if "windows" in ua_low:
        return "Windows PC"
    if "macintosh" in ua_low or "mac os" in ua_low:
        return "macOS"
    if "linux" in ua_low:
        return "Linux"
    return "Trình duyệt Web"


class CloudSync:
    """Tuỳ chọn sao lưu JSON ra dịch vụ đám mây bên ngoài (JSONBin / REST API) bằng thư viện chuẩn."""

    @staticmethod
    def pull():
        if not CLOUD_SYNC_URL:
            return None
        try:
            req = urllib.request.Request(CLOUD_SYNC_URL, method="GET")
            if CLOUD_SYNC_KEY:
                req.add_header("X-Master-Key", CLOUD_SYNC_KEY)
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                return raw.get("record", raw) if isinstance(raw, dict) else None
        except Exception as e:
            print(f"[CloudSync Pull Warning]: {e}")
            return None

    @staticmethod
    def push_async(data_snapshot):
        if not CLOUD_SYNC_URL:
            return

        def _worker():
            try:
                body = json.dumps(data_snapshot, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(CLOUD_SYNC_URL, data=body, method="PUT")
                req.add_header("Content-Type", "application/json")
                if CLOUD_SYNC_KEY:
                    req.add_header("X-Master-Key", CLOUD_SYNC_KEY)
                urllib.request.urlopen(req, timeout=6).read()
            except Exception as e:
                print(f"[CloudSync Push Warning]: {e}")

        threading.Thread(target=_worker, daemon=True).start()


class DataManager:
    @staticmethod
    def _default_admin_record():
        p_hash, p_salt = hash_pin(DEFAULT_ADMIN_PIN)
        return {
            "username": DEFAULT_ADMIN_USER,
            "pin_hash": p_hash,
            "pin_salt": p_salt,
            "has_pin": True,
            "role": "owner",
            "status": "active",
            "muted": False,
            "ban_until": 0,
            "ban_reason": "",
            "strikes": 0,
            "pending_warning": None,
            "allowed_features": list(ALL_FEATURES),
            "session_token": str(uuid.uuid4()),
            "last_seen": 0,
            "ip": "127.0.0.1",
            "device": "Server Host",
        }

    @staticmethod
    def _build_initial_data():
        return {
            "roster_version": 1,
            "presets": DEFAULT_PRESETS,
            "active_list": list(DEFAULT_PRESETS["Danh sách Thành viên / Lớp"]),
            "history": [],
            "chat_messages": [],
            "live_event": None,
            "audit_logs": [],
            "users": {DEFAULT_ADMIN_USER: DataManager._default_admin_record()},
            "banned_ips": {},
            "settings": {
                "allow_guest_join": True,
                "room_lockdown": False,
                "chat_enabled": True,
                "auto_ban_on_3_strikes": True,
                "broadcast": None,
            },
        }

    @staticmethod
    def _normalize_data(data):
        if not isinstance(data, dict):
            data = {}
        data.setdefault("roster_version", 1)
        data.setdefault("presets", DEFAULT_PRESETS)
        if not data.get("active_list"):
            data["active_list"] = list(DEFAULT_PRESETS["Danh sách Thành viên / Lớp"])
        data.setdefault("history", [])
        data.setdefault("chat_messages", [])
        data.setdefault("live_event", None)
        data.setdefault("audit_logs", [])
        data.setdefault("users", {})
        data.setdefault("banned_ips", {})
        data.setdefault("settings", {})

        st = data["settings"]
        st.setdefault("allow_guest_join", True)
        st.setdefault("room_lockdown", False)
        st.setdefault("chat_enabled", True)
        st.setdefault("auto_ban_on_3_strikes", True)
        st.setdefault("broadcast", None)

        p_hash, p_salt = hash_pin(DEFAULT_ADMIN_PIN)
        if DEFAULT_ADMIN_USER not in data["users"]:
            data["users"][DEFAULT_ADMIN_USER] = DataManager._default_admin_record()
        else:
            adm = data["users"][DEFAULT_ADMIN_USER]
            adm["role"] = "owner"
            adm["status"] = "active"
            adm["muted"] = False
            adm["allowed_features"] = list(ALL_FEATURES)
            if not verify_pin(DEFAULT_ADMIN_PIN, adm.get("pin_hash", ""), adm.get("pin_salt", "")):
                adm["pin_hash"] = p_hash
                adm["pin_salt"] = p_salt
                adm["has_pin"] = True

        for u in data["users"].values():
            u.setdefault("strikes", 0)
            u.setdefault("muted", False)
            u.setdefault("pending_warning", None)
            u.setdefault("device", "Web")
            u.setdefault("has_pin", bool(u.get("pin_hash")))
            if "pin" in u:
                raw_p = u.pop("pin")
                if raw_p and not u.get("pin_hash"):
                    h, s = hash_pin(raw_p)
                    u["pin_hash"] = h
                    u["pin_salt"] = s
                    u["has_pin"] = True
        return data

    @staticmethod
    def _unlocked_load():
        if not os.path.exists(DATA_FILE):
            cloud_data = CloudSync.pull()
            initial = DataManager._normalize_data(cloud_data) if cloud_data else DataManager._build_initial_data()
            DataManager._unlocked_save(initial, sync_cloud=False)
            return initial

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        return DataManager._normalize_data(data)

    @staticmethod
    def _unlocked_save(data, sync_cloud=True):
        tmp_file = f"{DATA_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, DATA_FILE)
            if sync_cloud:
                CloudSync.push_async(data)
        except Exception as e:
            print(f"[Lỗi lưu file]: {e}")

    @staticmethod
    def sanitize_user_public(u):
        return {
            "username": u["username"],
            "role": u["role"],
            "status": u["status"],
            "muted": bool(u.get("muted", False)),
            "ban_until": u.get("ban_until", 0),
            "ban_reason": u.get("ban_reason", ""),
            "strikes": u.get("strikes", 0),
            "pending_warning": u.get("pending_warning"),
            "allowed_features": u.get("allowed_features", []),
            "last_seen": u.get("last_seen", 0),
            "ip": u.get("ip", "-"),
            "device": u.get("device", "Web"),
            "has_pin": bool(u.get("pin_hash")),
        }

    @staticmethod
    def check_and_refresh_ban(user):
        if user.get("status") == "banned_temp":
            if time.time() >= float(user.get("ban_until", 0)):
                user["status"] = "active"
                user["ban_until"] = 0
                user["ban_reason"] = ""
                return True
        return False

    @staticmethod
    def add_audit(data, actor, action_text, level="info"):
        data["audit_logs"].insert(
            0,
            {
                "id": str(uuid.uuid4())[:8],
                "time": datetime.datetime.now().strftime("%H:%M:%S %d/%m"),
                "actor": actor,
                "text": action_text,
                "level": level,
            },
        )
        data["audit_logs"] = data["audit_logs"][:100]


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
            return f"Random {count} số [{min_v:g} → {max_v:g}]", res

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
            return f"Tạo {count} mã màu HEX/RGB", res

        elif utype == "uuid":
            res = [str(uuid.uuid4()) for _ in range(count)]
            return f"Tạo {count} mã UUID v4", res

        raise ValueError("Loại tiện ích không hợp lệ")


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>OmniRandom Arcade v6.0 — Ultimate Live & Security Edition</title>
<style>
  :root {
    --bg: #060913;
    --bg-grad: radial-gradient(circle at 12% 8%, rgba(99, 102, 241, 0.18), transparent 42%),
               radial-gradient(circle at 88% 92%, rgba(236, 72, 153, 0.14), transparent 45%);
    --surface: rgba(20, 29, 49, 0.86);
    --surface-solid: #141d31;
    --surface-2: rgba(31, 43, 70, 0.75);
    --surface-hover: rgba(49, 66, 104, 0.88);
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #6366f1;
    --primary-glow: rgba(99, 102, 241, 0.45);
    --accent: #f59e0b;
    --success: #10b981;
    --danger: #ef4444;
    --border: rgba(255, 255, 255, 0.11);
    --radius: 18px;
    --shadow: 0 14px 36px rgba(0, 0, 0, 0.48);
    --ease-spring: cubic-bezier(0.22, 1, 0.36, 1);
  }
  [data-theme="light"] {
    --bg: #eef2f9;
    --bg-grad: radial-gradient(circle at 15% 10%, rgba(99, 102, 241, 0.12), transparent 45%),
               radial-gradient(circle at 85% 90%, rgba(245, 158, 11, 0.1), transparent 45%);
    --surface: rgba(255, 255, 255, 0.92);
    --surface-solid: #ffffff;
    --surface-2: rgba(241, 245, 249, 0.92);
    --surface-hover: #e2e8f0;
    --text: #0f172a;
    --text-muted: #64748b;
    --primary: #4f46e5;
    --primary-glow: rgba(79, 70, 229, 0.25);
    --accent: #d97706;
    --border: rgba(15, 23, 42, 0.1);
    --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  }
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-tap-highlight-color: transparent;
  }
  body {
    background-color: var(--bg);
    background-image: var(--bg-grad);
    background-attachment: fixed;
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
    transition: background-color 0.35s var(--ease-spring), color 0.3s ease;
  }

  body.screen-shake {
    animation: screenShake 0.42s cubic-bezier(0.36, 0.07, 0.19, 0.97) both;
  }
  @keyframes screenShake {
    10%, 90% { transform: translate3d(-2px, 1px, 0); }
    20%, 80% { transform: translate3d(3px, -2px, 0); }
    30%, 50%, 70% { transform: translate3d(-5px, 2px, 0); }
    40%, 60% { transform: translate3d(5px, -1px, 0); }
  }

  /* Projector Fullscreen Stage Mode */
  .stage-card.projector-mode {
    position: fixed;
    inset: 0;
    z-index: 95;
    border-radius: 0;
    overflow-y: auto;
    padding: 24px 32px;
    background: var(--surface-solid);
  }

  /* Toast Stack */
  .toast-container {
    position: fixed;
    top: 18px;
    right: 18px;
    z-index: 250;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 370px;
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    background: var(--surface-solid);
    border: 1px solid var(--border);
    border-left: 4px solid var(--primary);
    color: var(--text);
    padding: 13px 16px;
    border-radius: 13px;
    box-shadow: 0 12px 30px rgba(0,0,0,0.48);
    font-size: 0.87rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
    animation: toastIn 0.35s var(--ease-spring);
  }
  .toast.success { border-left-color: var(--success); }
  .toast.warning { border-left-color: var(--accent); }
  .toast.error { border-left-color: var(--danger); }
  .toast.live { border-left-color: #ec4899; }
  @keyframes toastIn {
    from { opacity: 0; transform: translateX(40px) scale(0.92); }
    to { opacity: 1; transform: translateX(0) scale(1); }
  }

  .broadcast-bar {
    background: linear-gradient(90deg, #b45309, #d97706, #b45309);
    color: #fff;
    padding: 9px 18px;
    font-size: 0.88rem;
    font-weight: 800;
    display: none;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-align: center;
    box-shadow: 0 4px 16px rgba(217, 119, 6, 0.35);
    animation: slideDown 0.3s var(--ease-spring);
  }
  .broadcast-bar.critical {
    background: linear-gradient(90deg, #991b1b, #dc2626, #991b1b);
  }
  .lockdown-banner {
    background: rgba(239, 68, 68, 0.16);
    border-bottom: 1px solid rgba(239, 68, 68, 0.4);
    color: #fca5a5;
    padding: 7px 16px;
    font-size: 0.82rem;
    font-weight: 700;
    text-align: center;
    display: none;
  }
  @keyframes slideDown {
    from { opacity: 0; transform: translateY(-100%); }
    to { opacity: 1; transform: translateY(0); }
  }

  header {
    background: var(--surface);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    border-bottom: 1px solid var(--border);
    padding: 12px 22px;
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
    font-size: 1.25rem;
    font-weight: 900;
    letter-spacing: -0.3px;
    background: linear-gradient(135deg, #818cf8, #f472b6, #fbbf24);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    cursor: pointer;
    transition: transform 0.2s var(--ease-spring);
  }
  .brand-logo:hover { transform: scale(1.03); }
  .user-pill {
    background: var(--surface-2);
    border: 1px solid var(--border);
    padding: 5px 12px;
    border-radius: 99px;
    font-size: 0.8rem;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    cursor: pointer;
  }
  .role-badge {
    padding: 2px 8px;
    border-radius: 99px;
    font-size: 0.68rem;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .role-owner { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #0f172a; }
  .role-admin { background: linear-gradient(135deg, #f472b6, #db2777); color: #fff; }
  .role-member { background: linear-gradient(135deg, #34d399, #059669); color: #fff; }
  .role-viewer { background: #64748b; color: #fff; }

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
    padding: 7px 14px;
    border-radius: 99px;
    font-size: 0.82rem;
    font-weight: 700;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.22s var(--ease-spring);
  }
  .btn-pill:hover {
    border-color: var(--primary);
    background: var(--surface-hover);
    transform: translateY(-2px);
  }
  .btn-pill:active { transform: scale(0.96); }
  .btn-pill.highlight {
    background: linear-gradient(135deg, #6366f1, #4f46e5);
    color: #fff;
    border-color: transparent;
    box-shadow: 0 4px 15px var(--primary-glow);
  }
  .btn-pill.danger {
    background: rgba(239, 68, 68, 0.14);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.35);
  }

  .mode-strip {
    background: var(--surface-2);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 8px 22px;
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .mode-tab {
    padding: 8px 14px;
    border-radius: 11px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.84rem;
    font-weight: 700;
    white-space: nowrap;
    cursor: pointer;
    transition: all 0.22s var(--ease-spring);
  }
  .mode-tab:hover {
    color: var(--text);
    background: var(--surface);
    transform: translateY(-1px);
  }
  .mode-tab.active {
    background: var(--surface-solid);
    color: var(--primary);
    border-color: var(--primary);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  }
  .mode-tab.locked-tab {
    opacity: 0.4;
    text-decoration: line-through;
  }

  .main-wrap {
    max-width: 1380px;
    width: 100%;
    margin: 0 auto;
    padding: 22px;
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 330px;
    gap: 22px;
  }
  .main-wrap.full-admin {
    grid-template-columns: 1fr;
  }

  .hub-banner {
    text-align: center;
    margin-bottom: 22px;
    animation: fadeUp 0.45s var(--ease-spring);
  }
  .hub-banner h1 {
    font-size: clamp(1.5rem, 3vw, 2.15rem);
    font-weight: 900;
    margin-bottom: 6px;
  }
  .hub-banner p {
    color: var(--text-muted);
    font-size: 0.92rem;
  }
  .arcade-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(245px, 1fr));
    gap: 16px;
  }
  .arcade-card {
    background: var(--surface);
    backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 19px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 9px;
    box-shadow: var(--shadow);
    position: relative;
    overflow: hidden;
    transition: transform 0.28s var(--ease-spring), border-color 0.25s ease, box-shadow 0.28s ease;
    animation: fadeUp 0.45s var(--ease-spring) backwards;
  }
  .arcade-card:hover {
    transform: translateY(-6px) scale(1.015);
    border-color: var(--primary);
    box-shadow: 0 18px 40px rgba(99, 102, 241, 0.24);
  }
  .arcade-card:hover .arcade-icon {
    transform: scale(1.12) rotate(-4deg);
  }
  .arcade-card.disabled-card {
    opacity: 0.42;
    filter: grayscale(0.8);
  }
  .arcade-icon {
    width: 54px;
    height: 54px;
    border-radius: 15px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.85rem;
    transition: transform 0.28s var(--ease-spring);
  }
  .arcade-card h3 { font-size: 1.06rem; font-weight: 800; }
  .arcade-card p { font-size: 0.83rem; color: var(--text-muted); line-height: 1.45; flex: 1; }
  .arcade-tag {
    align-self: flex-start;
    font-size: 0.7rem;
    font-weight: 800;
    padding: 3px 9px;
    border-radius: 99px;
    background: var(--surface-2);
    color: var(--accent);
  }

  .view-pane { display: none; }
  .view-pane.active {
    display: block;
    animation: viewEnter 0.32s var(--ease-spring);
  }
  @keyframes viewEnter {
    from { opacity: 0; transform: translateY(12px) scale(0.985); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .stage-card {
    background: var(--surface);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 22px;
    box-shadow: var(--shadow);
  }
  .stage-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .stage-title { font-size: 1.24rem; font-weight: 900; }
  .controls-bar {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 16px;
    background: var(--surface-2);
    padding: 12px 15px;
    border-radius: 14px;
    border: 1px solid var(--border);
  }
  select, input[type="number"], input[type="text"], input[type="password"], textarea {
    background: var(--surface-solid);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 9px 12px;
    border-radius: 11px;
    font-size: 0.88rem;
    outline: none;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }
  select:focus, input:focus, textarea:focus {
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-glow);
  }
  .btn-action {
    background: linear-gradient(135deg, #6366f1, #4f46e5);
    color: #fff;
    border: none;
    padding: 10px 20px;
    border-radius: 12px;
    font-size: 0.94rem;
    font-weight: 800;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    box-shadow: 0 6px 18px var(--primary-glow);
    transition: transform 0.18s var(--ease-spring), filter 0.2s ease;
  }
  .btn-action:hover { transform: translateY(-2px); filter: brightness(1.06); }
  .btn-action:active { transform: scale(0.96); }
  .btn-action.orange { background: linear-gradient(135deg, #f59e0b, #d97706); box-shadow: 0 6px 18px rgba(245,158,11,0.35); }
  .btn-action.green { background: linear-gradient(135deg, #10b981, #059669); box-shadow: 0 6px 18px rgba(16,185,129,0.35); }
  .btn-action.red { background: linear-gradient(135deg, #ef4444, #dc2626); box-shadow: 0 6px 18px rgba(239,68,68,0.35); }

  .canvas-box {
    width: 100%;
    background: #060911;
    border-radius: 16px;
    border: 2px solid var(--border);
    overflow: hidden;
    display: flex;
    justify-content: center;
    align-items: center;
    box-shadow: inset 0 0 30px rgba(0,0,0,0.6);
  }
  canvas.game-canvas { width: 100%; height: auto; display: block; }

  .winner-banner {
    margin-top: 16px;
    padding: 15px 20px;
    border-radius: 14px;
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.2), rgba(245, 158, 11, 0.16));
    border: 1.5px solid var(--primary);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    animation: popBounce 0.4s var(--ease-spring);
  }
  .winner-text { font-size: 1.22rem; font-weight: 900; color: var(--accent); }
  @keyframes popBounce {
    0% { opacity: 0; transform: scale(0.86); }
    70% { transform: scale(1.03); }
    100% { opacity: 1; transform: scale(1); }
  }

  /* Survival Elimination Arena */
  .survival-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    justify-content: center;
    padding: 20px;
    background: var(--surface-2);
    border-radius: 16px;
    border: 1px solid var(--border);
    min-height: 220px;
  }
  .survivor-chip {
    padding: 12px 18px;
    border-radius: 13px;
    background: var(--surface-solid);
    border: 2px solid var(--primary);
    font-weight: 800;
    font-size: 1rem;
    transition: all 0.35s var(--ease-spring);
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  }
  .survivor-chip.scanning {
    background: #f59e0b;
    color: #0f172a;
    border-color: #fde047;
    transform: scale(1.12);
    box-shadow: 0 0 24px rgba(245, 158, 11, 0.7);
  }
  .survivor-chip.eliminated {
    opacity: 0.32;
    border-color: #ef4444;
    text-decoration: line-through;
    transform: scale(0.92);
    filter: grayscale(1);
  }
  .survivor-chip.champion {
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: #fff;
    border-color: #fef08a;
    transform: scale(1.18);
    box-shadow: 0 0 35px rgba(245, 158, 11, 0.8);
  }

  /* Slot Machine */
  .slot-machine {
    background: linear-gradient(180deg, #1e1b4b, #090d16);
    border: 4px solid #f59e0b;
    border-radius: 20px;
    padding: 26px 18px;
    text-align: center;
    box-shadow: 0 12px 32px rgba(0,0,0,0.5);
  }
  .slot-reels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 18px 0; }
  .slot-window {
    background: #ffffff;
    color: #0f172a;
    border-radius: 14px;
    border: 4px solid #cbd5e1;
    height: 118px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 10px;
    font-size: clamp(1.05rem, 2.2vw, 1.55rem);
    font-weight: 900;
    box-shadow: inset 0 8px 16px rgba(0,0,0,0.25);
    transition: all 0.25s var(--ease-spring);
  }
  .slot-window.spinning { filter: blur(1.4px); transform: scale(0.97); color: #4f46e5; }
  .slot-window.locked {
    border-color: #10b981; background: #ecfdf5; color: #065f46;
    transform: scale(1.04); box-shadow: 0 0 22px rgba(16, 185, 129, 0.5);
  }

  /* Magic Hat & 3D Flip Tickets */
  .magic-stage {
    min-height: 310px;
    background: radial-gradient(circle at center, #2e1065 0%, #070b14 100%);
    border-radius: 18px;
    padding: 26px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .magic-icon {
    font-size: 5.8rem;
    cursor: pointer;
    user-select: none;
    filter: drop-shadow(0 12px 24px rgba(139, 92, 246, 0.45));
    transition: transform 0.25s var(--ease-spring);
  }
  .magic-icon:hover { transform: scale(1.1) rotate(4deg); }
  .magic-icon.shaking { animation: shakeHard 0.42s infinite; }
  @keyframes shakeHard {
    0%, 100% { transform: translate(0, 0) rotate(0deg) scale(1.08); }
    25% { transform: translate(-11px, -7px) rotate(-12deg) scale(1.08); }
    50% { transform: translate(11px, 5px) rotate(12deg) scale(1.08); }
    75% { transform: translate(-9px, 7px) rotate(-8deg) scale(1.08); }
  }
  .magic-cards { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; margin-top: 20px; }
  .magic-ticket {
    background: linear-gradient(135deg, #fef3c7, #fde68a);
    color: #78350f;
    border: 2px solid #f59e0b;
    padding: 14px 22px;
    border-radius: 13px;
    font-size: 1.15rem;
    font-weight: 900;
    cursor: pointer;
    box-shadow: 0 8px 22px rgba(245, 158, 11, 0.35);
    animation: cardFlipIn 0.45s var(--ease-spring);
    transition: transform 0.2s var(--ease-spring);
  }
  .magic-ticket.unrevealed {
    background: linear-gradient(135deg, #4f46e5, #7c3aed);
    color: #fff;
    border-color: #c4b5fd;
  }
  .magic-ticket:hover { transform: translateY(-3px) scale(1.03); }
  @keyframes cardFlipIn {
    from { opacity: 0; transform: perspective(500px) rotateY(-85deg) scale(0.8); }
    to { opacity: 1; transform: perspective(500px) rotateY(0deg) scale(1); }
  }

  /* Tabletop */
  .felt-table {
    min-height: 270px;
    background: radial-gradient(circle at center, #065f46 0%, #022c22 100%);
    border: 6px solid #78350f;
    border-radius: 18px;
    padding: 22px;
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: center;
    justify-content: center;
  }
  .dice-3d {
    width: 80px; height: 80px; background: #fff; color: #0f172a;
    border-radius: 16px; display: flex; align-items: center; justify-content: center;
    font-size: 1.9rem; font-weight: 900;
    box-shadow: 0 10px 20px rgba(0,0,0,0.4);
    animation: diceTumble 0.48s var(--ease-spring);
  }
  @keyframes diceTumble {
    0% { opacity: 0; transform: translateY(-30px) rotate(-220deg) scale(0.5); }
    100% { opacity: 1; transform: translateY(0) rotate(0deg) scale(1); }
  }
  .coin-3d {
    width: 90px; height: 90px; border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, #fde047, #ca8a04);
    border: 4px solid #fef08a; color: #422006;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-weight: 900; box-shadow: 0 10px 20px rgba(0,0,0,0.4);
    animation: coinSpin 0.6s var(--ease-spring);
  }
  @keyframes coinSpin {
    0% { transform: rotateY(0deg) scale(0.6); }
    100% { transform: rotateY(1080deg) scale(1); }
  }
  .playing-card {
    width: 90px; height: 130px; background: #fff; color: #0f172a;
    border-radius: 11px; padding: 9px; display: flex; flex-direction: column;
    justify-content: space-between; font-weight: 900; font-size: 1.15rem;
    box-shadow: 0 10px 20px rgba(0,0,0,0.4);
    animation: cardFlipIn 0.4s var(--ease-spring);
  }
  .playing-card.red { color: #dc2626; }

  .teams-board { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); gap: 14px; }
  .team-box {
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 14px; padding: 15px;
    animation: fadeUp 0.35s var(--ease-spring);
  }
  .team-box h4 { font-size: 0.98rem; color: var(--accent); margin-bottom: 9px; display: flex; justify-content: space-between; }
  .team-box ul { list-style: none; display: flex; flex-direction: column; gap: 6px; font-size: 0.9rem; font-weight: 600; }

  /* Right Sidebar: Live Chat + History Tabs */
  .side-column {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .side-card {
    background: var(--surface);
    backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    box-shadow: var(--shadow);
  }
  .chat-feed {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 230px;
    overflow-y: auto;
    margin: 10px 0;
    padding-right: 4px;
  }
  .chat-msg {
    background: var(--surface-2);
    padding: 8px 10px;
    border-radius: 10px;
    font-size: 0.82rem;
    line-height: 1.35;
    word-break: break-word;
  }
  .chat-msg .sender { font-weight: 800; color: var(--primary); margin-right: 5px; }
  .chat-msg .sender.owner { color: #fbbf24; }
  .chat-msg .sender.admin { color: #f472b6; }
  .reaction-bar {
    display: flex;
    gap: 6px;
    margin-bottom: 8px;
  }
  .react-btn {
    flex: 1;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 5px;
    font-size: 1rem;
    cursor: pointer;
    transition: transform 0.15s var(--ease-spring);
  }
  .react-btn:hover { transform: scale(1.15); border-color: var(--primary); }
  .history-list { display: flex; flex-direction: column; gap: 8px; max-height: 260px; overflow-y: auto; margin-top: 10px; }
  .history-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 9px; font-size: 0.8rem; }
  .history-card .meta { display: flex; justify-content: space-between; color: var(--text-muted); font-size: 0.7rem; }
  .history-card .val { font-weight: 800; color: var(--accent); margin-top: 2px; }

  /* Admin Center */
  .admin-subgrid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 18px;
  }
  .admin-panel-box {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 15px;
  }
  .admin-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 14px;
    margin-top: 12px;
  }
  table.admin-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.84rem;
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
  .status-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 5px; }
  .dot-online { background: #10b981; box-shadow: 0 0 8px #10b981; }
  .dot-offline { background: #64748b; }
  .dot-banned { background: #ef4444; }
  .strike-pill {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 800;
    background: rgba(245, 158, 11, 0.18);
    color: #fbbf24;
  }
  .admin-actions-row { display: flex; gap: 5px; flex-wrap: wrap; }
  .btn-xs {
    padding: 5px 9px;
    border-radius: 8px;
    font-size: 0.75rem;
    font-weight: 700;
    border: 1px solid var(--border);
    background: var(--surface-solid);
    color: var(--text);
    cursor: pointer;
    transition: transform 0.15s ease, border-color 0.15s ease;
  }
  .btn-xs:hover { border-color: var(--primary); transform: translateY(-1px); }
  .btn-xs.warn { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border-color: rgba(245, 158, 11, 0.35); }
  .btn-xs.ban { background: rgba(239, 68, 68, 0.16); color: #f87171; border-color: rgba(239, 68, 68, 0.35); }
  .btn-xs.unban { background: rgba(16, 185, 129, 0.16); color: #34d399; border-color: rgba(16, 185, 129, 0.35); }

  /* Modals */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(5, 8, 15, 0.85);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    z-index: 120;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 18px;
  }
  .modal-overlay.open {
    display: flex;
    animation: modalFade 0.25s var(--ease-spring);
  }
  @keyframes modalFade { from { opacity: 0; } to { opacity: 1; } }
  .modal-card {
    background: var(--surface-solid);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 24px;
    width: 100%;
    max-width: 470px;
    box-shadow: 0 20px 50px rgba(0,0,0,0.65);
    display: flex;
    flex-direction: column;
    gap: 14px;
    animation: popBounce 0.32s var(--ease-spring);
  }
  .warning-pulse {
    border: 2px solid #f59e0b !important;
    box-shadow: 0 0 40px rgba(245, 158, 11, 0.45) !important;
  }
  .perm-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    background: var(--surface-2);
    padding: 12px;
    border-radius: 12px;
  }
  .perm-item { display: flex; align-items: center; gap: 8px; font-size: 0.84rem; font-weight: 600; cursor: pointer; }
  .duration-row { display: grid; grid-template-columns: 1fr 140px; gap: 10px; }
  .quick-chips { display: flex; flex-wrap: wrap; gap: 6px; }

  /* Drawer */
  .drawer-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.65); backdrop-filter: blur(4px);
    z-index: 105; display: none; justify-content: flex-end;
  }
  .drawer-backdrop.open { display: flex; }
  .roster-drawer {
    width: 100%; max-width: 420px; background: var(--surface-solid); height: 100%;
    padding: 22px; display: flex; flex-direction: column; gap: 14px;
    animation: slideDrawer 0.28s var(--ease-spring);
  }
  @keyframes slideDrawer { from { transform: translateX(100%); } to { transform: translateX(0); } }

  #confetti-canvas { position: fixed; inset: 0; pointer-events: none; z-index: 180; }

  @media (max-width: 1024px) {
    .main-wrap { grid-template-columns: 1fr; padding: 12px; }
    .admin-subgrid { grid-template-columns: 1fr; }
    .slot-reels { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<canvas id="confetti-canvas"></canvas>
<div class="toast-container" id="toast-container"></div>

<!-- BIỂU NGỮ THÔNG BÁO TOÀN PHÒNG -->
<div class="broadcast-bar" id="broadcast-bar">
  <span>📢</span>
  <span id="broadcast-text"></span>
</div>
<div class="lockdown-banner" id="lockdown-banner">
  🔒 PHÒNG ĐANG Ở CHẾ ĐỘ ĐÓNG BĂNG KHẨN CẤP (LOCKDOWN) — Tạm khoá mọi lượt quay của thành viên.
</div>

<!-- MODAL 1: ĐĂNG NHẬP -->
<div class="modal-overlay open" id="login-modal">
  <div class="modal-card">
    <h2 style="font-size:1.35rem; font-weight:900;">🎪 Vào Phòng OmniRandom v6.0</h2>
    <p style="font-size:0.84rem; color:var(--text-muted);">
      Nhập tên của bạn để tham gia. Chủ phòng nhập tài khoản <b>admin</b> kèm mã PIN quản trị.
    </p>
    <div style="display:flex; flex-direction:column; gap:6px;">
      <label style="font-size:0.8rem; font-weight:700; color:var(--text-muted);">Tên người dùng (Username)</label>
      <input type="text" id="login-user" placeholder="VD: Gia Huy hoặc admin" onkeydown="if(event.key==='Enter') submitLogin()">
    </div>
    <div style="display:flex; flex-direction:column; gap:6px;">
      <label style="font-size:0.8rem; font-weight:700; color:var(--text-muted);">Mã PIN / Mật khẩu (Để trống nếu là khách mới)</label>
      <input type="password" id="login-pin" placeholder="Nhập mã PIN..." onkeydown="if(event.key==='Enter') submitLogin()">
    </div>
    <div id="login-error" style="color:#f87171; font-size:0.83rem; font-weight:700; display:none;"></div>
    <button class="btn-action" style="justify-content:center;" onclick="submitLogin()">🚀 Tham Gia Ngay</button>
  </div>
</div>

<!-- MODAL 2: CẢNH CÁO TRỰC TIẾP TỪ ADMIN -->
<div class="modal-overlay" id="warning-modal">
  <div class="modal-card warning-pulse" style="text-align:center;">
    <div style="font-size:3.6rem;">⚠️</div>
    <h2 style="color:#fbbf24; font-size:1.35rem; font-weight:900;">CẢNH CÁO TỪ QUẢN TRỊ VIÊN</h2>
    <div id="warn-strike-badge" style="align-self:center; background:rgba(239,68,68,0.2); color:#fca5a5; padding:5px 14px; border-radius:99px; font-weight:800; font-size:0.85rem;"></div>
    <p style="font-size:0.98rem; line-height:1.5; color:var(--text); background:var(--surface-2); padding:14px; border-radius:12px;" id="warn-msg"></p>
    <button class="btn-action orange" style="justify-content:center;" onclick="acknowledgeWarning()">✅ Tôi đã hiểu và tuân thủ</button>
  </div>
</div>

<!-- MODAL 3: MÀN HÌNH KHOÁ KHI BỊ KICK / BỊ CẤM -->
<div class="modal-overlay" id="ban-modal">
  <div class="modal-card" style="border-color:#ef4444; text-align:center;">
    <div style="font-size:3.5rem;" id="ban-icon">🚫</div>
    <h2 style="color:#f87171; font-size:1.35rem; font-weight:900;" id="ban-title">TRUY CẬP BỊ TỪ CHỐI</h2>
    <p style="font-size:0.92rem; color:var(--text);" id="ban-desc"></p>
    <div id="ban-timer-box" style="background:rgba(239,68,68,0.16); padding:12px; border-radius:10px; font-weight:900; color:#fca5a5; display:none;"></div>
    <button class="btn-pill" style="justify-content:center;" onclick="logoutAndRelogin()">🔄 Đăng nhập tài khoản khác</button>
  </div>
</div>

<!-- MODAL 4: GIỚI HẠN CHỨC NĂNG TỪNG USER -->
<div class="modal-overlay" id="perm-modal" onclick="if(event.target===this) closePermModal()">
  <div class="modal-card">
    <h3 style="font-size:1.12rem; font-weight:800;">🛠️ Giới Hạn Quyền: <span id="perm-target-user" style="color:var(--accent);"></span></h3>
    <div style="display:flex; gap:8px;">
      <button class="btn-xs" onclick="toggleAllPerms(true)">✅ Chọn tất cả</button>
      <button class="btn-xs" onclick="toggleAllPerms(false)">❌ Bỏ chọn hết</button>
    </div>
    <div class="perm-grid" id="perm-checkboxes"></div>
    <div style="display:flex; gap:8px; justify-content:flex-end;">
      <button class="btn-pill" onclick="closePermModal()">Huỷ</button>
      <button class="btn-action" style="padding:8px 16px;" onclick="saveUserPermissions()">💾 Lưu Quyền</button>
    </div>
  </div>
</div>

<!-- MODAL 5: HỘP THOẠI QUẢN TRỊ ĐA NĂNG (THAY THẾ HOÀN TOÀN PROMPT/CONFIRM) -->
<div class="modal-overlay" id="unified-admin-modal" onclick="if(event.target===this) closeUnifiedModal()">
  <div class="modal-card">
    <h3 style="font-size:1.15rem; font-weight:900;" id="uam-title">Thao tác quản trị</h3>
    <p style="font-size:0.83rem; color:var(--text-muted);" id="uam-subtitle"></p>

    <!-- Khối chọn thời gian (chỉ hiện khi Cấm có thời hạn) -->
    <div id="uam-duration-section" style="display:none; flex-direction:column; gap:10px;">
      <div class="quick-chips">
        <button type="button" class="btn-xs" onclick="setUamDuration(15, 'minutes')">15 Phút</button>
        <button type="button" class="btn-xs" onclick="setUamDuration(1, 'hours')">1 Giờ</button>
        <button type="button" class="btn-xs" onclick="setUamDuration(12, 'hours')">12 Giờ</button>
        <button type="button" class="btn-xs" onclick="setUamDuration(1, 'days')">1 Ngày</button>
        <button type="button" class="btn-xs" onclick="setUamDuration(7, 'days')">7 Ngày</button>
        <button type="button" class="btn-xs" onclick="setUamDuration(1, 'years')">1 Năm</button>
      </div>
      <div class="duration-row">
        <input type="number" id="uam-dur-val" value="15" min="1" step="any" placeholder="Số lượng">
        <select id="uam-dur-unit">
          <option value="minutes" selected>Phút</option>
          <option value="hours">Giờ</option>
          <option value="days">Ngày</option>
          <option value="years">Năm</option>
        </select>
      </div>
    </div>

    <!-- Ô nhập nội dung chính (Lý do / PIN mới / Địa chỉ IP) -->
    <div id="uam-input-section" style="display:flex; flex-direction:column; gap:6px;">
      <label style="font-size:0.78rem; font-weight:700; color:var(--text-muted);" id="uam-input-label">Nội dung</label>
      <input type="text" id="uam-input-val" placeholder="Nhập thông tin...">
      <div class="quick-chips" id="uam-reason-chips"></div>
    </div>

    <!-- Checkbox tuỳ chọn phụ (VD: Chặn kèm IP khi cấm vĩnh viễn) -->
    <label id="uam-checkbox-row" style="display:none; align-items:center; gap:8px; font-size:0.84rem; color:#fca5a5; cursor:pointer;">
      <input type="checkbox" id="uam-extra-check">
      <span id="uam-extra-label">Chặn kèm địa chỉ IP của tài khoản này</span>
    </label>

    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:4px;">
      <button class="btn-pill" onclick="closeUnifiedModal()">Huỷ</button>
      <button class="btn-action" id="uam-submit-btn" style="padding:9px 18px;" onclick="submitUnifiedModal()">Xác Nhận</button>
    </div>
  </div>
</div>

<!-- MODAL 6: NHẬP DỮ LIỆU SAO LƯU JSON -->
<div class="modal-overlay" id="import-modal" onclick="if(event.target===this) document.getElementById('import-modal').classList.remove('open')">
  <div class="modal-card">
    <h3 style="font-size:1.12rem; font-weight:900;">📥 Phục Hồi Dữ Liệu Từ Bản Sao Lưu JSON</h3>
    <p style="font-size:0.82rem; color:var(--text-muted);">Dán nội dung file <code>omnirandom_backup.json</code> đã xuất trước đó để khôi phục toàn bộ tài khoản, lịch sử và danh sách mẫu:</p>
    <textarea id="import-json-text" style="min-height:150px; font-family:monospace; font-size:0.78rem;" placeholder='{"users": ..., "presets": ...}'></textarea>
    <div style="display:flex; gap:8px; justify-content:flex-end;">
      <button class="btn-pill" onclick="document.getElementById('import-modal').classList.remove('open')">Huỷ</button>
      <button class="btn-action green" style="padding:8px 16px;" onclick="submitImportBackup()">✅ Khôi Phục Ngay</button>
    </div>
  </div>
</div>

<header>
  <div class="brand-area">
    <div class="brand-logo" onclick="openMode('hub')">🎪 OmniRandom v6.0</div>
    <div class="user-pill" title="Bấm để đổi mã PIN cá nhân" onclick="openSelfChangePin()">
      <span>👤 <b id="current-username">Khách</b></span>
      <span class="role-badge role-member" id="current-role-badge">MEMBER</span>
      <span style="font-size:0.72rem; opacity:0.75;">🔑</span>
    </div>
    <span class="user-pill" style="font-size:0.76rem; color:var(--success); cursor:default;">
      🟢 <b id="online-count-badge">1</b> Online
    </span>
    <button class="btn-pill" onclick="openMode('hub')">🏠 Sảnh</button>
    <button class="btn-pill highlight" id="admin-nav-btn" style="display:none;" onclick="openMode('admin')">👑 Quản Trị & Bảo Mật</button>
  </div>
  <div class="top-actions">
    <button class="btn-pill highlight" id="roster-btn" onclick="toggleRosterDrawer()">
      📋 Danh Sách (<span id="roster-count-badge">0</span>)
    </button>
    <button class="btn-pill" onclick="copyMobileLink()" id="mobileBtn">📱 Link Phòng</button>
    <button class="btn-pill" onclick="toggleSound()" id="soundBtn">🔊 Âm thanh</button>
    <button class="btn-pill" onclick="toggleTheme()" id="themeBtn">☀️ Sáng</button>
    <button class="btn-pill danger" onclick="logoutAndRelogin()">🚪 Đổi User</button>
  </div>
</header>

<div class="mode-strip">
  <button class="mode-tab active" data-mode="hub" onclick="openMode('hub')">🏠 Sảnh</button>
  <button class="mode-tab" data-mode="race" onclick="openMode('race')">🦆 Đua Vịt / Xe</button>
  <button class="mode-tab" data-mode="wheel" onclick="openMode('wheel')">🎡 Vòng Quay</button>
  <button class="mode-tab" data-mode="survival" onclick="openMode('survival')">🏆 Sinh Tồn</button>
  <button class="mode-tab" data-mode="cage" onclick="openMode('cage')">🔮 Lồng Cầu</button>
  <button class="mode-tab" data-mode="slot" onclick="openMode('slot')">🎰 Máy Slot</button>
  <button class="mode-tab" data-mode="hat" onclick="openMode('hat')">🎩 Mũ Ma Thuật</button>
  <button class="mode-tab" data-mode="bomb" onclick="openMode('bomb')">💣 Bom Nổ</button>
  <button class="mode-tab" data-mode="tabletop" onclick="openMode('tabletop')">🎲 Xúc Xắc & Bài</button>
  <button class="mode-tab" data-mode="teams" onclick="openMode('teams')">👥 Chia Đội</button>
  <button class="mode-tab" data-mode="utility" onclick="openMode('utility')">⚡ Số & Tiện Ích</button>
  <button class="mode-tab" data-mode="admin" id="strip-admin-tab" style="display:none;" onclick="openMode('admin')">👑 Trung Tâm Admin</button>
</div>

<div class="main-wrap" id="main-wrap">
  <div>
    <!-- 0. SẢNH TRÒ CHƠI -->
    <section id="view-hub" class="view-pane active">
      <div class="hub-banner">
        <h1>Sảnh Trò Chơi Random Trực Quan</h1>
        <p>Đồng bộ thời gian thực đa thiết bị — Hiệu ứng vật lý mượt mà — Bảo mật phân quyền cấp cao.</p>
      </div>
      <div class="arcade-grid">
        <div class="arcade-card" data-feat="race" onclick="openMode('race')">
          <div class="arcade-icon" style="background:rgba(16,185,129,0.18);">🦆</div>
          <span class="arcade-tag">Độ kịch tính cao</span>
          <h3>Đua Vịt / Siêu Xe / Tên Lửa</h3>
          <p>Các tay đua bơi/chạy bứt tốc ngẫu nhiên kèm tia lửa tăng tốc về vạch đích.</p>
        </div>
        <div class="arcade-card" data-feat="wheel" onclick="openMode('wheel')">
          <div class="arcade-icon" style="background:rgba(99,102,241,0.18);">🎡</div>
          <span class="arcade-tag">Đèn LED & Trọng số</span>
          <h3>Vòng Quay Siêu Cấp</h3>
          <p>Vòng quay có chốt đinh va chạm vật lý, đèn LED nhấp nháy và tự loại người trúng.</p>
        </div>
        <div class="arcade-card" data-feat="survival" onclick="openMode('survival')">
          <div class="arcade-icon" style="background:rgba(245,158,11,0.18);">🏆</div>
          <span class="arcade-tag">Mới • Loại Trực Tiếp</span>
          <h3>Đấu Trường Sinh Tồn</h3>
          <p>Quét tia sáng loại dần từng ứng viên qua mỗi vòng cho đến khi còn 1 nhà vô địch.</p>
        </div>
        <div class="arcade-card" data-feat="cage" onclick="openMode('cage')">
          <div class="arcade-icon" style="background:rgba(245,158,11,0.18);">🔮</div>
          <span class="arcade-tag">Vật lý 2D</span>
          <h3>Lồng Cầu Xổ Số (Bingo)</h3>
          <p>Quay lồng cầu chứa các quả bóng nảy liên tục và bật bóng trúng thưởng.</p>
        </div>
        <div class="arcade-card" data-feat="slot" onclick="openMode('slot')">
          <div class="arcade-icon" style="background:rgba(236,72,153,0.18);">🎰</div>
          <span class="arcade-tag">Motion Blur 3 Trục</span>
          <h3>Máy Kéo Slot 777</h3>
          <p>Kéo cần gạt để 3 trục quay dừng lại lần lượt với hiệu ứng rung Jackpot.</p>
        </div>
        <div class="arcade-card" data-feat="hat" onclick="openMode('hat')">
          <div class="arcade-icon" style="background:rgba(139,92,246,0.18);">🎩</div>
          <span class="arcade-tag">Thẻ Lật 3D Tương Tác</span>
          <h3>Chiếc Mũ Ma Thuật</h3>
          <p>Rút các lá thăm úp mặt từ mũ ảo thuật và bấm lật từng thẻ để tạo bất ngờ.</p>
        </div>
        <div class="arcade-card" data-feat="bomb" onclick="openMode('bomb')">
          <div class="arcade-icon" style="background:rgba(239,68,68,0.18);">💣</div>
          <span class="arcade-tag">Sensory Timer</span>
          <h3>Bom Hẹn Giờ Ngẫu Nhiên</h3>
          <p>Đốt dây cháy chậm xẹt lửa với thời gian ẩn. Nổ trúng ai người đó chịu phạt!</p>
        </div>
        <div class="arcade-card" data-feat="tabletop" onclick="openMode('tabletop')">
          <div class="arcade-icon" style="background:rgba(20,184,166,0.18);">🎲</div>
          <span class="arcade-tag">Boardgame</span>
          <h3>Xúc Xắc 3D, Đồng Xu & Bài Tây</h3>
          <p>Đổ xúc xắc trên bàn nỉ, tung đồng xu vàng 3D và rút bài Tây 52 lá.</p>
        </div>
        <div class="arcade-card" data-feat="teams" onclick="openMode('teams')">
          <div class="arcade-icon" style="background:rgba(59,130,246,0.18);">👥</div>
          <span class="arcade-tag">Chia nhóm cân bằng</span>
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

    <!-- TRUNG TÂM QUẢN TRỊ & BẢO MẬT ADMIN -->
    <section id="view-admin" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">👑 Trung Tâm Quản Trị Người Dùng, Cảnh Báo & Bảo Mật</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn-pill" onclick="exportBackupData()">📤 Xuất Backup JSON</button>
          <button class="btn-pill" onclick="document.getElementById('import-modal').classList.add('open')">📥 Khôi phục JSON</button>
        </div>
      </div>

      <!-- Cài đặt phòng & kỷ luật hàng loạt -->
      <div class="controls-bar" style="justify-content:space-between;">
        <div style="display:flex; gap:14px; flex-wrap:wrap;">
          <label style="display:flex; align-items:center; gap:6px; font-size:0.84rem; cursor:pointer;">
            <input type="checkbox" id="toggle-guest-join" onchange="updateRoomSettings()">
            Cho phép khách tự tạo tên
          </label>
          <label style="display:flex; align-items:center; gap:6px; font-size:0.84rem; cursor:pointer;">
            <input type="checkbox" id="toggle-chat-enabled" onchange="updateRoomSettings()">
            Bật Chat phòng
          </label>
          <label style="display:flex; align-items:center; gap:6px; font-size:0.84rem; cursor:pointer; color:#fbbf24;">
            <input type="checkbox" id="toggle-auto-ban-strike" onchange="updateRoomSettings()">
            Tự cấm 30p khi đủ 3 gậy
          </label>
          <label style="display:flex; align-items:center; gap:6px; font-size:0.84rem; cursor:pointer; color:#f87171;">
            <input type="checkbox" id="toggle-lockdown" onchange="updateRoomSettings()">
            🔒 Khoá khẩn cấp phòng
          </label>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
          <button class="btn-xs warn" onclick="adminAction('', 'kick_all_guests', {})">🥾 Kick tất cả Member</button>
          <button class="btn-xs unban" onclick="adminAction('', 'unban_all', {})">🔓 Mở khoá tất cả</button>
        </div>
      </div>

      <!-- Phát cảnh báo toàn phòng -->
      <div class="controls-bar">
        <strong style="font-size:0.85rem;">📢 Phát thông báo toàn phòng:</strong>
        <select id="bc-level">
          <option value="warning">⚠️ Cảnh báo Vàng</option>
          <option value="critical">🚨 Báo động Đỏ</option>
        </select>
        <input type="text" id="bc-text" placeholder="Nhập nội dung biểu ngữ hiện lên màn hình tất cả mọi người..." style="flex:1; min-width:220px;">
        <button class="btn-action orange" style="padding:8px 14px; font-size:0.84rem;" onclick="sendBroadcast(true)">Phát Ngay</button>
        <button class="btn-pill" onclick="sendBroadcast(false)">Tắt</button>
      </div>

      <!-- Thêm User & Lọc tìm kiếm -->
      <div class="controls-bar" style="justify-content:space-between;">
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
          <strong style="font-size:0.85rem;">➕ Tạo User:</strong>
          <input type="text" id="new-u-name" placeholder="Tên người dùng..." style="width:145px;">
          <input type="text" id="new-u-pin" placeholder="Mã PIN" style="width:110px;">
          <select id="new-u-role">
            <option value="member">Member</option>
            <option value="viewer">Viewer</option>
            <option value="admin">Admin</option>
          </select>
          <button class="btn-action green" style="padding:8px 14px; font-size:0.84rem;" onclick="adminAddUser()">Tạo</button>
        </div>
        <div style="display:flex; gap:8px; align-items:center;">
          <input type="text" id="admin-user-search" oninput="renderAdminCenter()" placeholder="🔍 Tìm tên / IP..." style="width:165px;">
          <select id="admin-user-filter" onchange="renderAdminCenter()">
            <option value="all">Tất cả trạng thái</option>
            <option value="online">Đang Online</option>
            <option value="banned">Đang bị Cấm / Kick</option>
            <option value="warned">Có gậy cảnh cáo</option>
          </select>
        </div>
      </div>

      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Tài khoản & Thiết bị</th>
              <th>Quyền hạn</th>
              <th>Trạng thái & Cảnh cáo</th>
              <th>Chức năng</th>
              <th>Hành động Quản trị & Kỷ luật</th>
            </tr>
          </thead>
          <tbody id="admin-users-tbody"></tbody>
        </table>
      </div>

      <div class="admin-subgrid">
        <div class="admin-panel-box">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <strong style="font-size:0.95rem;">🌐 Danh sách IP bị chặn vĩnh viễn</strong>
            <button class="btn-xs ban" onclick="openBanIpModal()">+ Chặn IP thủ công</button>
          </div>
          <div id="admin-ips-list" style="display:flex; flex-direction:column; gap:8px; max-height:220px; overflow-y:auto;"></div>
        </div>

        <div class="admin-panel-box">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <strong style="font-size:0.95rem;">🛡️ Nhật ký Bảo mật & Quản trị (Audit Log)</strong>
          </div>
          <div id="admin-audit-list" style="display:flex; flex-direction:column; gap:6px; max-height:220px; overflow-y:auto; font-size:0.8rem;"></div>
        </div>
      </div>
    </section>

    <!-- 1. ĐUA VỊT / ĐUA XE -->
    <section id="view-race" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🦆 Đường Đua Ngẫu Nhiên</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-race')">🖥️ Trình Chiếu</button>
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
        <div class="stage-title">🎡 Vòng Quay Siêu Cấp (LED & Chốt Vật Lý)</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-wheel')">🖥️ Trình Chiếu</button>
      </div>
      <div class="controls-bar">
        <button class="btn-action orange" onclick="spinArcadeWheel()">🎡 QUAY NGAY</button>
        <label style="display:flex; align-items:center; gap:6px; font-size:0.86rem; cursor:pointer;">
          <input type="checkbox" id="wheel-auto-remove"> Tự loại ô đã trúng sau khi quay
        </label>
      </div>
      <div class="canvas-box" style="padding:14px;">
        <canvas id="wheel-canvas" width="520" height="520" style="max-width:430px; width:100%; height:auto; cursor:pointer;" onclick="spinArcadeWheel()"></canvas>
      </div>
      <div class="winner-banner" id="wheel-winner-banner" style="display:none;">
        <div class="winner-text" id="wheel-winner-text"></div>
      </div>
    </section>

    <!-- 3. ĐẤU TRƯỜNG SINH TỒN (LAST MAN STANDING) -->
    <section id="view-survival" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🏆 Đấu Trường Sinh Tồn (Loại Dần Tới Người Cuối Cùng)</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-survival')">🖥️ Trình Chiếu</button>
      </div>
      <div class="controls-bar">
        <button class="btn-action red" onclick="eliminateOneRound()" id="surv-elim-btn">⚡ LOẠI 1 NGƯỜI NGẪU NHIÊN</button>
        <button class="btn-pill" onclick="resetSurvivalArena()">🔄 Hồi sinh tất cả (<span id="surv-alive-count">0</span>)</button>
      </div>
      <div class="survival-grid" id="survival-grid"></div>
      <div class="winner-banner" id="survival-banner" style="display:none;">
        <div class="winner-text" id="survival-banner-text"></div>
      </div>
    </section>

    <!-- 4. LỒNG CẦU -->
    <section id="view-cage" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🔮 Lồng Cầu Xổ Số Vật Lý</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-cage')">🖥️ Trình Chiếu</button>
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

    <!-- 5. MÁY SLOT -->
    <section id="view-slot" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🎰 Máy Kéo Slot 3 Trục</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-slot')">🖥️ Trình Chiếu</button>
      </div>
      <div class="controls-bar">
        <select id="slot-mode">
          <option value="roster_single">🎯 Bốc 1 mục (3 trục trùng nhau)</option>
          <option value="roster_triple">🎁 Bốc 3 mục khác nhau</option>
          <option value="numbers">🔢 Quay 3 chữ số (000 - 999)</option>
        </select>
        <button class="btn-action orange" onclick="pullSlotMachine()">🕹️ KÉO CẦN GẠT!</button>
      </div>
      <div class="slot-machine">
        <div style="color:#fde047; font-weight:900; letter-spacing:2px;">★ OMNIRANDOM JACKPOT ★</div>
        <div class="slot-reels">
          <div class="slot-window" id="reel-0">🎲</div>
          <div class="slot-window" id="reel-1">🎲</div>
          <div class="slot-window" id="reel-2">🎲</div>
        </div>
      </div>
    </section>

    <!-- 6. MŨ MA THUẬT & THẺ LẬT 3D -->
    <section id="view-hat" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">🎩 Chiếc Mũ Ma Thuật & Thẻ Lật 3D</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-hat')">🖥️ Trình Chiếu</button>
      </div>
      <div class="controls-bar">
        <select id="hat-theme" onchange="document.getElementById('hat-emoji').textContent=this.value">
          <option value="🎩">🎩 Mũ Ảo Thuật</option>
          <option value="🎁">🎁 Hộp Mù Gacha</option>
          <option value="🧧">🧧 Bao Lì Xì Đỏ</option>
        </select>
        <label>Số phiếu: <input type="number" id="hat-count" value="3" min="1" max="12" style="width:70px;"></label>
        <label style="display:flex; align-items:center; gap:6px; font-size:0.84rem; cursor:pointer;">
          <input type="checkbox" id="hat-face-down" checked> Úp thẻ (Bấm từng thẻ để lật)
        </label>
        <button class="btn-action" onclick="drawFromHat()">✨ LẮC & RÚT THĂM!</button>
        <button class="btn-pill" onclick="revealAllHatCards()">👁️ Lật mở hết</button>
      </div>
      <div class="magic-stage">
        <div class="magic-icon" id="hat-emoji" onclick="drawFromHat()">🎩</div>
        <div class="magic-cards" id="hat-results"></div>
      </div>
    </section>

    <!-- 7. BOM HẸN GIỜ -->
    <section id="view-bomb" class="view-pane stage-card">
      <div class="stage-top">
        <div class="stage-title">💣 Bom Hẹn Giờ Ngẫu Nhiên</div>
        <button class="btn-pill" onclick="toggleProjectorMode('view-bomb')">🖥️ Trình Chiếu</button>
      </div>
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

    <!-- 8. XÚC XẮC, XU & BÀI -->
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
    </section>

    <!-- 9. CHIA ĐỘI -->
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

    <!-- 10. TIỆN ÍCH & RANDOM SỐ -->
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

  <!-- CỘT PHẢI: LIVE ROOM CHAT + LỊCH SỬ -->
  <aside class="side-column" id="side-column">
    <!-- Chat & Cảm Xúc Thời Gian Thực -->
    <div class="side-card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div style="font-weight:800; font-size:0.95rem;">💬 Trò Chuyện & Cảm Xúc</div>
        <button class="btn-xs" id="clear-chat-btn" style="display:none;" onclick="adminAction('', 'clear_chat', {})">Xoá chat</button>
      </div>
      <div class="chat-feed" id="chat-feed"></div>
      <div class="reaction-bar">
        <button class="react-btn" onclick="sendChatOrReact('🔥')">🔥</button>
        <button class="react-btn" onclick="sendChatOrReact('😂')">😂</button>
        <button class="react-btn" onclick="sendChatOrReact('👏')">👏</button>
        <button class="react-btn" onclick="sendChatOrReact('😱')">😱</button>
        <button class="react-btn" onclick="sendChatOrReact('🎉')">🎉</button>
      </div>
      <div style="display:flex; gap:6px;">
        <input type="text" id="chat-input" placeholder="Nhắn tin vào phòng..." style="flex:1; padding:7px 10px;" onkeydown="if(event.key==='Enter') sendChatInput()">
        <button class="btn-action" style="padding:7px 13px; font-size:0.84rem;" onclick="sendChatInput()">Gửi</button>
      </div>
    </div>

    <!-- Lịch sử kết quả -->
    <div class="side-card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div style="font-weight:800; font-size:0.95rem;">🕒 Lịch Sử Phòng</div>
        <button class="btn-xs" onclick="clearAllHistory()">Xoá</button>
      </div>
      <div class="history-list" id="history-list"></div>
    </div>
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
      <button class="btn-pill" onclick="openSavePresetModal()">💾 Lưu mẫu</button>
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
    survival: "🏆 Sinh Tồn",
    cage: "🔮 Lồng Cầu",
    slot: "🎰 Máy Slot",
    hat: "🎩 Mũ Ma Thuật",
    bomb: "💣 Bom Hẹn Giờ",
    tabletop: "🎲 Xúc Xắc & Bài",
    teams: "👥 Chia Đội",
    utility: "⚡ Số & Tiện Ích",
    edit_roster: "✏️ Sửa Danh Sách"
  };

  let state = {
    roster_version: 1,
    presets: {},
    active_list: [],
    history: [],
    chat_messages: [],
    live_event: null,
    audit_logs: [],
    mobile_url: "",
    users: {},
    banned_ips: {},
    settings: { allow_guest_join: true, room_lockdown: false, chat_enabled: true, auto_ban_on_3_strikes: true, broadcast: null }
  };

  let session = {
    username: localStorage.getItem("omni_user") || "",
    token: localStorage.getItem("omni_token") || "",
    role: "member",
    muted: false,
    allowed_features: Object.keys(FEATURE_LABELS)
  };
  let currentMode = "hub";
  let soundOn = true;
  let audioCtx = null;
  let editingPermUser = "";
  let currentWarnId = "";
  let lastLiveEventId = "";
  let uamContext = { type: "", target: "", ip: "" };

  const PALETTE = ["#6366f1", "#ec4899", "#10b981", "#f59e0b", "#06b6d4", "#8b5cf6", "#ef4444", "#14b8a6"];

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatDurationVN(totalSec) {
    totalSec = Math.max(0, Math.floor(totalSec));
    if (totalSec <= 0) return "0 giây";
    const years = Math.floor(totalSec / 31536000);
    totalSec %= 31536000;
    const days = Math.floor(totalSec / 86400);
    totalSec %= 86400;
    const hours = Math.floor(totalSec / 3600);
    totalSec %= 3600;
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;

    const parts = [];
    if (years > 0) parts.push(`${years} năm`);
    if (days > 0) parts.push(`${days} ngày`);
    if (hours > 0) parts.push(`${hours} giờ`);
    if (mins > 0) parts.push(`${mins} phút`);
    if (parts.length === 0 || (years === 0 && days === 0 && hours === 0 && secs > 0)) {
      parts.push(`${secs} giây`);
    }
    return parts.slice(0, 3).join(" ");
  }

  function showToast(msg, type = "info") {
    const container = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    const icons = { info: "ℹ️", success: "✅", warning: "⚠️", error: "🚨", live: "🔴" };
    el.innerHTML = `<span>${icons[type] || "ℹ️"}</span><span>${esc(msg)}</span>`;
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateX(30px)";
      el.style.transition = "all 0.25s ease";
      setTimeout(() => el.remove(), 260);
    }, 3400);
  }

  function triggerScreenShake() {
    document.body.classList.remove("screen-shake");
    void document.body.offsetWidth;
    document.body.classList.add("screen-shake");
  }

  function toggleProjectorMode(sectionId) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    el.classList.toggle("projector-mode");
    const isProj = el.classList.contains("projector-mode");
    showToast(isProj ? "Đã bật chế độ Trình Chiếu toàn màn hình (Esc để thoát)" : "Đã thoát chế độ Trình Chiếu", "info");
  }

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
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => setTimeout(() => sfxTone(f, 0.24, "sine", 0.12), i * 80));
  }
  function sfxWarningAlarm() {
    if (!soundOn) return;
    [880, 620, 880, 620].forEach((f, i) => setTimeout(() => sfxTone(f, 0.18, "sawtooth", 0.14), i * 190));
  }
  function toggleSound() {
    soundOn = !soundOn;
    document.getElementById("soundBtn").textContent = soundOn ? "🔊 Âm thanh" : "🔇 Tắt tiếng";
    showToast(soundOn ? "Đã bật âm thanh hiệu ứng" : "Đã tắt âm thanh", "info");
  }
  function toggleTheme() {
    const isLight = document.body.getAttribute("data-theme") === "light";
    document.body.setAttribute("data-theme", isLight ? "dark" : "light");
    document.getElementById("themeBtn").textContent = isLight ? "☀️ Sáng" : "🌙 Tối";
  }

  function shootConfetti() {
    const c = document.getElementById("confetti-canvas"), ctx = c.getContext("2d");
    c.width = window.innerWidth; c.height = window.innerHeight;
    const parts = Array.from({ length: 85 }, () => ({
      x: c.width * (0.2 + Math.random() * 0.6), y: c.height * 0.38,
      vx: (Math.random() - 0.5) * 16, vy: (Math.random() - 1.15) * 14,
      s: 6 + Math.random() * 6, color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      r: Math.random() * 360, vr: (Math.random() - 0.5) * 14
    }));
    let f = 0;
    function step() {
      ctx.clearRect(0, 0, c.width, c.height);
      parts.forEach(p => {
        p.x += p.vx; p.y += p.vy; p.vy += 0.38; p.r += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate((p.r * Math.PI) / 180);
        ctx.fillStyle = p.color; ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.65); ctx.restore();
      });
      if (++f < 75) requestAnimationFrame(step);
      else ctx.clearRect(0, 0, c.width, c.height);
    }
    step();
  }

  // --- UNIFIED CUSTOM ADMIN MODAL (NO NATIVE PROMPT/CONFIRM) ---
  function openUnifiedModal(cfg) {
    uamContext = { type: cfg.type, target: cfg.target || "", ip: cfg.ip || "" };
    document.getElementById("uam-title").textContent = cfg.title;
    document.getElementById("uam-subtitle").textContent = cfg.subtitle || "";

    const durSec = document.getElementById("uam-duration-section");
    durSec.style.display = cfg.showDuration ? "flex" : "none";
    if (cfg.showDuration) {
      document.getElementById("uam-dur-val").value = "15";
      document.getElementById("uam-dur-unit").value = "minutes";
    }

    const inpSec = document.getElementById("uam-input-section");
    inpSec.style.display = cfg.hideInput ? "none" : "flex";
    if (!cfg.hideInput) {
      document.getElementById("uam-input-label").textContent = cfg.inputLabel || "Nội dung";
      const inp = document.getElementById("uam-input-val");
      inp.value = cfg.defaultValue || "";
      inp.placeholder = cfg.placeholder || "";
    }

    const chipsBox = document.getElementById("uam-reason-chips");
    chipsBox.innerHTML = (cfg.chips || []).map(c =>
      `<button type="button" class="btn-xs" onclick="document.getElementById('uam-input-val').value='${esc(c)}'">${esc(c)}</button>`
    ).join("");

    const chkRow = document.getElementById("uam-checkbox-row");
    chkRow.style.display = cfg.showCheckbox ? "flex" : "none";
    if (cfg.showCheckbox) {
      document.getElementById("uam-extra-check").checked = false;
      document.getElementById("uam-extra-label").textContent = cfg.checkboxLabel || "";
    }

    const btn = document.getElementById("uam-submit-btn");
    btn.textContent = cfg.submitText || "Xác Nhận";
    btn.className = "btn-action " + (cfg.btnColor || "");

    document.getElementById("unified-admin-modal").classList.add("open");
    if (!cfg.hideInput) setTimeout(() => document.getElementById("uam-input-val").focus(), 80);
  }

  function closeUnifiedModal() {
    document.getElementById("unified-admin-modal").classList.remove("open");
  }

  function setUamDuration(val, unit) {
    document.getElementById("uam-dur-val").value = String(val);
    document.getElementById("uam-dur-unit").value = unit;
  }

  async function submitUnifiedModal() {
    const val = document.getElementById("uam-input-val").value.trim();
    const { type, target, ip } = uamContext;

    if (type === "warn_user") {
      if (!val) return showToast("Vui lòng nhập nội dung cảnh cáo!", "warning");
      await adminAction(target, "warn_user", { reason: val });
    } else if (type === "kick") {
      await adminAction(target, "kick", { reason: val || "Quản trị viên mời bạn ra khỏi phòng." });
    } else if (type === "ban_temp") {
      const duration = parseFloat(document.getElementById("uam-dur-val").value);
      const unit = document.getElementById("uam-dur-unit").value;
      if (isNaN(duration) || duration <= 0) return showToast("Thời gian cấm phải lớn hơn 0!", "error");
      await adminAction(target, "ban_temp", { duration, unit, reason: val });
    } else if (type === "ban_perm") {
      const banIp = document.getElementById("uam-extra-check").checked;
      await adminAction(target, "ban_perm", { reason: val || "Cấm vĩnh viễn bởi Admin.", ban_ip: banIp });
    } else if (type === "change_pin") {
      await adminAction(target, "change_pin", { new_pin: val });
    } else if (type === "self_change_pin") {
      const res = await fetch("/api/auth/change_my_pin", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ new_pin: val })
      });
      const d = await res.json();
      if (!res.ok) return showToast(d.error || "Không thể đổi PIN", "error");
      showToast(val ? "Đã cập nhật mã PIN bảo vệ cá nhân!" : "Đã gỡ mã PIN cá nhân!", "success");
    } else if (type === "ban_ip_manual") {
      if (!val) return showToast("Vui lòng nhập địa chỉ IP!", "warning");
      await adminAction("", "ban_ip_manual", { ip: val, reason: "Chặn IP bởi Quản trị viên." });
    } else if (type === "save_preset") {
      if (!val) return showToast("Vui lòng đặt tên bộ danh sách!", "warning");
      const res = await fetch("/api/presets", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name: val, items: state.active_list })
      });
      state = await res.json();
      renderPresetDropdown();
      showToast(`Đã lưu mẫu "${val}"!`, "success");
    }
    closeUnifiedModal();
  }

  // --- AUTHENTICATION & HEARTBEAT ---
  function authHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Username": encodeURIComponent(session.username || ""),
      "X-Token": session.token || ""
    };
  }

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
      sfxWarningAlarm();
      return;
    }
    errEl.style.display = "none";
    session.username = data.user.username;
    session.token = data.token;
    session.role = data.user.role;
    session.muted = !!data.user.muted;
    session.allowed_features = data.user.allowed_features;
    localStorage.setItem("omni_user", session.username);
    localStorage.setItem("omni_token", session.token);

    document.getElementById("login-modal").classList.remove("open");
    document.getElementById("ban-modal").classList.remove("open");
    applyUserPermissionsToUI();
    showToast(`Chào mừng ${session.username} (${session.role.toUpperCase()})!`, "success");
    await fetchInitialState();
  }

  function logoutAndRelogin() {
    localStorage.removeItem("omni_user");
    localStorage.removeItem("omni_token");
    session.username = "";
    session.token = "";
    document.getElementById("ban-modal").classList.remove("open");
    document.getElementById("warning-modal").classList.remove("open");
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
    } else if (status === "ip_banned") {
      icon.textContent = "🛡️";
      title.textContent = "ĐỊA CHỈ IP ĐÃ BỊ CHẶN";
      desc.textContent = "Lý do: " + (reason || "IP của bạn đã bị đưa vào danh sách đen.");
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
      timerBox.textContent = `⏳ Thời gian khoá còn lại: ${formatDurationVN(remainingSec)}`;
    }
  }

  async function acknowledgeWarning() {
    document.getElementById("warning-modal").classList.remove("open");
    await fetch("/api/warning/ack", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ warn_id: currentWarnId })
    });
    currentWarnId = "";
  }

  function hasPermission(feat) {
    if (session.role === "owner" || session.role === "admin") return true;
    return session.allowed_features.includes(feat);
  }

  function canTriggerSpin() {
    if (state.settings?.room_lockdown && session.role !== "owner" && session.role !== "admin") {
      showToast("🔒 Phòng đang bị Admin đóng băng khẩn cấp!", "error");
      return false;
    }
    if (session.role === "viewer") {
      showToast("🔒 Tài khoản của bạn chỉ có quyền Xem (Viewer)!", "warning");
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
    document.getElementById("clear-chat-btn").style.display = isAdmin ? "inline-block" : "none";

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

  function applyRoomStateBanners() {
    const bc = state.settings?.broadcast;
    const bcBar = document.getElementById("broadcast-bar");
    if (bc && bc.text) {
      bcBar.style.display = "flex";
      bcBar.className = "broadcast-bar" + (bc.level === "critical" ? " critical" : "");
      document.getElementById("broadcast-text").textContent = bc.text;
    } else {
      bcBar.style.display = "none";
    }
    document.getElementById("lockdown-banner").style.display = state.settings?.room_lockdown ? "block" : "none";
  }

  async function heartbeatLoop() {
    if (!session.username || !session.token) return;
    try {
      const res = await fetch("/api/heartbeat", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({})
      });
      const data = await res.json();
      if (!res.ok && data.ban_status) {
        showBanOverlay(data.ban_status, data.reason, data.remaining);
        return;
      }
      if (res.ok && data.user) {
        document.getElementById("ban-modal").classList.remove("open");
        document.getElementById("online-count-badge").textContent = data.online_count || 1;
        state.settings = data.settings || state.settings;
        applyRoomStateBanners();

        // Đồng bộ danh sách chung nếu có người cập nhật
        if (data.roster_version && data.roster_version !== state.roster_version && Array.isArray(data.active_list)) {
          state.roster_version = data.roster_version;
          state.active_list = data.active_list;
          document.getElementById("roster-textarea").value = state.active_list.join("\n");
          updateRosterCounters();
          if (currentMode === "race") drawIdleRace();
          if (currentMode === "wheel") drawWheel(0);
          if (currentMode === "survival") resetSurvivalArena();
        }

        // Đồng bộ Live Event (Kết quả quay mới nhất từ người khác trong phòng)
        if (data.live_event && data.live_event.id && data.live_event.id !== lastLiveEventId) {
          lastLiveEventId = data.live_event.id;
          if (data.live_event.actor !== session.username) {
            showToast(`🔴 LIVE: ${data.live_event.text}`, "live");
          }
        }

        // Đồng bộ Chat & History
        if (Array.isArray(data.chat_messages)) {
          state.chat_messages = data.chat_messages;
          renderChatFeed();
        }
        if (Array.isArray(data.history)) {
          state.history = data.history;
          renderHistoryList();
        }

        // Kiểm tra cảnh cáo cá nhân
        const warn = data.user.pending_warning;
        if (warn && warn.id && warn.id !== currentWarnId) {
          currentWarnId = warn.id;
          document.getElementById("warn-msg").textContent = warn.reason;
          document.getElementById("warn-strike-badge").textContent = `Số gậy vi phạm hiện tại: ${data.user.strikes || 0} / 3`;
          document.getElementById("warning-modal").classList.add("open");
          triggerScreenShake();
          sfxWarningAlarm();
        }

        session.muted = !!data.user.muted;
        const changed =
          session.role !== data.user.role ||
          JSON.stringify(session.allowed_features) !== JSON.stringify(data.user.allowed_features);
        session.role = data.user.role;
        session.allowed_features = data.user.allowed_features;
        if (changed) {
          applyUserPermissionsToUI();
          if (currentMode !== "hub" && currentMode !== "admin" && !hasPermission(currentMode)) {
            showToast("⚠️ Quản trị viên vừa thu hồi quyền truy cập công cụ này!", "warning");
            openMode("hub");
          }
        }

        if (data.users) {
          state.users = data.users;
          state.banned_ips = data.banned_ips || {};
          state.audit_logs = data.audit_logs || [];
          if (currentMode === "admin") renderAdminCenter();
        }
      }
    } catch (e) {}
  }

  // --- ADMIN CENTER & ACTIONS ---
  function renderAdminCenter() {
    const tbody = document.getElementById("admin-users-tbody");
    if (!tbody) return;
    document.getElementById("toggle-guest-join").checked = !!state.settings.allow_guest_join;
    document.getElementById("toggle-chat-enabled").checked = !!state.settings.chat_enabled;
    document.getElementById("toggle-lockdown").checked = !!state.settings.room_lockdown;
    document.getElementById("toggle-auto-ban-strike").checked = !!state.settings.auto_ban_on_3_strikes;

    const searchKw = (document.getElementById("admin-user-search")?.value || "").toLowerCase().trim();
    const filterVal = document.getElementById("admin-user-filter")?.value || "all";
    const now = Date.now() / 1000;

    const filteredUsers = Object.values(state.users).filter(u => {
      const isOnline = (now - (u.last_seen || 0)) < 12 && u.status === "active";
      if (searchKw && !u.username.toLowerCase().includes(searchKw) && !(u.ip || "").includes(searchKw)) return false;
      if (filterVal === "online" && !isOnline) return false;
      if (filterVal === "banned" && u.status === "active") return false;
      if (filterVal === "warned" && (u.strikes || 0) <= 0) return false;
      return true;
    });

    tbody.innerHTML = filteredUsers.map(u => {
      const isOnline = (now - (u.last_seen || 0)) < 12 && u.status === "active";
      let statusHtml = "";
      if (u.status === "banned_perm") {
        statusHtml = `<span class="status-dot dot-banned"></span><b style="color:#f87171;">Cấm vĩnh viễn</b>`;
      } else if (u.status === "banned_temp") {
        const remSec = Math.max(0, (u.ban_until || 0) - now);
        statusHtml = `<span class="status-dot dot-banned"></span><b style="color:#fbbf24;">Khoá (${formatDurationVN(remSec)})</b>`;
      } else if (u.status === "kicked") {
        statusHtml = `<span class="status-dot dot-offline"></span><span style="color:#94a3b8;">Đã bị Kick</span>`;
      } else {
        statusHtml = `<span class="status-dot ${isOnline ? 'dot-online' : 'dot-offline'}"></span>${isOnline ? 'Online' : 'Offline'}`;
      }

      const isOwner = u.role === "owner";
      const canManageTarget = session.role === "owner" || (session.role === "admin" && !isOwner && u.role !== "admin");
      const featCount = (u.allowed_features || []).length;
      const safeUser = esc(u.username);

      return `
        <tr>
          <td>
            <div style="font-weight:800;">${safeUser} ${u.has_pin ? '🔐' : ''} ${u.muted ? '🔇' : ''}</div>
            <div style="font-size:0.72rem; color:var(--text-muted);">${esc(u.device)} • IP: ${esc(u.ip)}</div>
          </td>
          <td>
            ${isOwner || !canManageTarget ? `<span class="role-badge role-${esc(u.role)}">${esc(u.role)}</span>` : `
              <select onchange="adminAction('${safeUser}', 'set_role', {role: this.value})" style="padding:4px 8px; font-size:0.78rem;">
                ${session.role === 'owner' ? `<option value="admin" ${u.role==='admin'?'selected':''}>Admin</option>` : ''}
                <option value="member" ${u.role==='member'?'selected':''}>Member</option>
                <option value="viewer" ${u.role==='viewer'?'selected':''}>Viewer</option>
              </select>
            `}
          </td>
          <td>
            <div>${statusHtml}</div>
            <div style="margin-top:4px;">
              <span class="strike-pill">⚠️ Gậy: ${u.strikes || 0}/3</span>
              ${u.strikes > 0 && canManageTarget ? `<button class="btn-xs" style="padding:1px 6px; font-size:0.68rem;" onclick="adminAction('${safeUser}', 'reset_strikes', {})">Xoá gậy</button>` : ''}
            </div>
          </td>
          <td>
            ${isOwner || u.role === 'admin' ? 'Toàn quyền (11/11)' : `
              <button class="btn-xs" onclick="openPermModal('${safeUser}')">🛠️ Giới hạn (${featCount}/11)</button>
            `}
          </td>
          <td>
            ${isOwner ? (session.role === 'owner' ? `<button class="btn-xs" onclick="openChangePinModal('${safeUser}')">🔑 Đổi PIN Owner</button>` : '<span style="color:var(--text-muted); font-size:0.76rem;">Chủ phòng tối cao</span>') : (!canManageTarget ? '<span style="color:var(--text-muted); font-size:0.76rem;">Đồng cấp Admin</span>' : `
              <div class="admin-actions-row">
                <button class="btn-xs warn" onclick="openWarnModal('${safeUser}')">⚠️ Cảnh cáo</button>
                <button class="btn-xs ${u.muted ? 'unban' : 'warn'}" onclick="adminAction('${safeUser}', 'toggle_mute', {})">${u.muted ? '🔊 Mở chat' : '🔇 Cấm chat'}</button>
                <button class="btn-xs warn" onclick="openKickModal('${safeUser}')">🥾 Kick</button>
                <button class="btn-xs warn" onclick="openTempBanModal('${safeUser}')">⏳ Cấm thời hạn</button>
                <button class="btn-xs ban" onclick="openPermBanModal('${safeUser}', '${esc(u.ip)}')">🚫 Cấm vĩnh viễn</button>
                <button class="btn-xs" onclick="openChangePinModal('${safeUser}')">🔑 PIN</button>
                ${u.status !== 'active' ? `<button class="btn-xs unban" onclick="adminAction('${safeUser}', 'unban', {})">🔓 Mở khoá</button>` : ''}
                <button class="btn-xs" onclick="adminAction('${safeUser}', 'delete', {})">🗑️</button>
              </div>
            `)}
          </td>
        </tr>
      `;
    }).join("");

    const ipBox = document.getElementById("admin-ips-list");
    const ipEntries = Object.entries(state.banned_ips || {});
    ipBox.innerHTML = ipEntries.length ? ipEntries.map(([ip, info]) => `
      <div style="display:flex; justify-content:space-between; align-items:center; background:var(--surface-solid); padding:8px 10px; border-radius:9px; border:1px solid var(--border); font-size:0.8rem;">
        <div><b>${esc(ip)}</b> — <span style="color:var(--text-muted);">${esc(info.reason)}</span></div>
        <button class="btn-xs unban" onclick="adminAction('', 'unban_ip', {ip: '${esc(ip)}'})">Gỡ IP</button>
      </div>
    `).join("") : '<div style="color:var(--text-muted); font-size:0.8rem;">Chưa có IP nào bị chặn.</div>';

    const auditBox = document.getElementById("admin-audit-list");
    auditBox.innerHTML = (state.audit_logs || []).map(log => `
      <div style="padding:6px 8px; border-bottom:1px solid var(--border);">
        <span style="color:var(--text-muted);">[${esc(log.time)}]</span>
        <b style="color:var(--accent);">${esc(log.actor)}:</b>
        <span>${esc(log.text)}</span>
      </div>
    `).join("") || '<div style="color:var(--text-muted);">Chưa có nhật ký.</div>';
  }

  async function adminAction(targetUser, action, extra) {
    const res = await fetch("/api/admin/action", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ target: targetUser, action, ...extra })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || "Lỗi thao tác quản trị", "error");
      return;
    }
    state.users = data.users;
    state.banned_ips = data.banned_ips || {};
    state.audit_logs = data.audit_logs || [];
    state.settings = data.settings || state.settings;
    if (Array.isArray(data.chat_messages)) {
      state.chat_messages = data.chat_messages;
      renderChatFeed();
    }
    applyRoomStateBanners();
    renderAdminCenter();
    showToast("Đã thực thi lệnh quản trị!", "success");
  }

  function adminAddUser() {
    const name = document.getElementById("new-u-name").value.trim();
    const pin = document.getElementById("new-u-pin").value.trim();
    const role = document.getElementById("new-u-role").value;
    if (!name) return showToast("Vui lòng nhập tên người dùng!", "warning");
    adminAction(name, "create_user", { pin, role });
    document.getElementById("new-u-name").value = "";
    document.getElementById("new-u-pin").value = "";
  }

  function openWarnModal(u) {
    openUnifiedModal({
      type: "warn_user",
      target: u,
      title: `⚠️ Gửi Cảnh Cáo Tới: ${u}`,
      subtitle: "Thông báo kèm âm thanh cảnh báo sẽ bật ngay lập tức trên màn hình người này (+1 gậy).",
      inputLabel: "Nội dung cảnh cáo",
      defaultValue: "Vui lòng tuân thủ quy định phòng chơi!",
      chips: ["Không spam nút quay!", "Giữ lịch sự khi chat!", "Cảnh báo lần cuối!"],
      submitText: "⚠️ Gửi Cảnh Cáo",
      btnColor: "orange"
    });
  }

  function openKickModal(u) {
    openUnifiedModal({
      type: "kick",
      target: u,
      title: `🥾 Mời Ra Khỏi Phòng: ${u}`,
      subtitle: "Ngắt kết nối ngay lập tức (người này có thể đăng nhập lại sau).",
      inputLabel: "Lý do mời ra khỏi phòng",
      defaultValue: "Quản trị viên mời bạn ra khỏi phòng.",
      chips: ["AFK quá lâu", "Gây rối trật tự", "Dọn phòng"],
      submitText: "🥾 Xác Nhận Kick",
      btnColor: "orange"
    });
  }

  function openTempBanModal(u) {
    openUnifiedModal({
      type: "ban_temp",
      target: u,
      title: `⏳ Cấm Có Thời Hạn: ${u}`,
      subtitle: "Chọn thời lượng và đơn vị thời gian (Phút / Giờ / Ngày / Năm):",
      showDuration: true,
      inputLabel: "Lý do tạm khoá",
      placeholder: "VD: Spam liên tục...",
      chips: ["Spam nút quay", "Ngôn từ không phù hợp", "Phá danh sách chung"],
      submitText: "⏳ Xác Nhận Cấm",
      btnColor: "orange"
    });
  }

  function openPermBanModal(u, ip) {
    const validIp = ip && ip !== "-" && ip !== "127.0.0.1";
    openUnifiedModal({
      type: "ban_perm",
      target: u,
      ip,
      title: `🚫 Cấm Vĩnh Viễn: ${u}`,
      subtitle: "Khoá vĩnh viễn tài khoản này khỏi hệ thống.",
      inputLabel: "Lý do cấm vĩnh viễn",
      defaultValue: "Vi phạm nghiêm trọng quy định phòng.",
      showCheckbox: validIp,
      checkboxLabel: `Chặn vĩnh viễn luôn địa chỉ IP (${ip})`,
      submitText: "🚫 Cấm Vĩnh Viễn",
      btnColor: "red"
    });
  }

  function openChangePinModal(u) {
    openUnifiedModal({
      type: "change_pin",
      target: u,
      title: `🔑 Đặt / Đổi Mã PIN: ${u}`,
      subtitle: "Nhập mã PIN mới (để trống nếu muốn xoá mật khẩu tài khoản này).",
      inputLabel: "Mã PIN mới",
      placeholder: "Nhập mã PIN...",
      submitText: "💾 Lưu Mã PIN"
    });
  }

  function openSelfChangePin() {
    if (!session.username) return;
    openUnifiedModal({
      type: "self_change_pin",
      target: session.username,
      title: `🔑 Đổi Mã PIN Cá Nhân (${session.username})`,
      subtitle: "Cài mã PIN để bảo vệ tên của bạn không bị người khác mạo danh.",
      inputLabel: "Mã PIN cá nhân mới",
      placeholder: "Nhập mã PIN mới...",
      submitText: "💾 Cập Nhật PIN"
    });
  }

  function openBanIpModal() {
    openUnifiedModal({
      type: "ban_ip_manual",
      title: "🌐 Chặn Địa Chỉ IP Thủ Công",
      subtitle: "Mọi thiết bị dùng địa chỉ IP này sẽ bị từ chối truy cập.",
      inputLabel: "Địa chỉ IP (IPv4 / IPv6)",
      placeholder: "VD: 192.168.1.55",
      submitText: "🚫 Chặn IP Ngay",
      btnColor: "red"
    });
  }

  function openSavePresetModal() {
    if (!hasPermission("edit_roster")) return showToast("🔒 Bạn không có quyền lưu mẫu mới!", "error");
    openUnifiedModal({
      type: "save_preset",
      title: "💾 Lưu Bộ Danh Sách Mẫu Mới",
      subtitle: "Lưu danh sách hiện tại vào thư viện mẫu để dùng lại nhanh lần sau.",
      inputLabel: "Tên bộ danh sách",
      placeholder: "VD: Danh sách nhóm 1...",
      submitText: "💾 Lưu Mẫu",
      btnColor: "green"
    });
  }

  function updateRoomSettings() {
    adminAction("", "update_settings", {
      allow_guest_join: document.getElementById("toggle-guest-join").checked,
      chat_enabled: document.getElementById("toggle-chat-enabled").checked,
      room_lockdown: document.getElementById("toggle-lockdown").checked,
      auto_ban_on_3_strikes: document.getElementById("toggle-auto-ban-strike").checked
    });
  }

  function sendBroadcast(active) {
    const text = document.getElementById("bc-text").value.trim();
    const level = document.getElementById("bc-level").value;
    if (active && !text) return showToast("Nhập nội dung thông báo trước khi phát!", "warning");
    adminAction("", "set_broadcast", { active, text, level });
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

  function toggleAllPerms(val) {
    document.querySelectorAll("#perm-checkboxes input[type='checkbox']").forEach(cb => cb.checked = val);
  }

  function closePermModal() {
    document.getElementById("perm-modal").classList.remove("open");
  }

  function saveUserPermissions() {
    const checked = Array.from(document.querySelectorAll("#perm-checkboxes input:checked")).map(el => el.value);
    adminAction(editingPermUser, "set_permissions", { allowed_features: checked });
    closePermModal();
  }

  // --- BACKUP EXPORT / IMPORT ---
  async function exportBackupData() {
    const res = await fetch("/api/admin/export", { headers: authHeaders() });
    if (!res.ok) return showToast("Không thể xuất sao lưu!", "error");
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `omnirandom_backup_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("Đã tải xuống file sao lưu JSON!", "success");
  }

  async function submitImportBackup() {
    const raw = document.getElementById("import-json-text").value.trim();
    if (!raw) return showToast("Vui lòng dán nội dung JSON sao lưu!", "warning");
    try {
      const parsed = JSON.parse(raw);
      const res = await fetch("/api/admin/import", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ backup: parsed })
      });
      const d = await res.json();
      if (!res.ok) return showToast(d.error || "Dữ liệu không hợp lệ", "error");
      document.getElementById("import-modal").classList.remove("open");
      showToast("Đã khôi phục toàn bộ dữ liệu thành công!", "success");
      await fetchInitialState();
      renderAdminCenter();
    } catch (e) {
      showToast("Chuỗi JSON bị lỗi cú pháp!", "error");
    }
  }

  // --- LIVE CHAT & REACTIONS ---
  function renderChatFeed() {
    const feed = document.getElementById("chat-feed");
    if (!feed) return;
    const msgs = state.chat_messages || [];
    feed.innerHTML = msgs.length ? msgs.map(m => `
      <div class="chat-msg">
        <span class="sender ${esc(m.role)}">${esc(m.user)}:</span>
        <span>${esc(m.text)}</span>
        <span style="float:right; font-size:0.68rem; color:var(--text-muted);">${esc(m.time)}</span>
      </div>
    `).join("") : '<div style="color:var(--text-muted); font-size:0.8rem;">Chưa có tin nhắn nào...</div>';
  }

  function sendChatInput() {
    const inp = document.getElementById("chat-input");
    const txt = inp.value.trim();
    if (!txt) return;
    inp.value = "";
    sendChatOrReact(txt);
  }

  async function sendChatOrReact(text) {
    if (session.muted && session.role !== "owner" && session.role !== "admin") {
      return showToast("🔇 Bạn đã bị Quản trị viên cấm chat!", "error");
    }
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ text })
    });
    const d = await res.json();
    if (!res.ok) return showToast(d.error || "Không thể gửi tin nhắn", "error");
    state.chat_messages = d.chat_messages || [];
    renderChatFeed();
    sfxTone(720, 0.03, "sine", 0.04);
  }

  // --- INITIALIZATION & ROSTER ---
  async function fetchInitialState() {
    const res = await fetch("/api/state", { headers: authHeaders() });
    state = await res.json();
    document.getElementById("roster-textarea").value = state.active_list.join("\n");
    updateRosterCounters();
    renderPresetDropdown();
    renderHistoryList();
    renderChatFeed();
    applyRoomStateBanners();
    if (state.mobile_url) {
      document.getElementById("mobileBtn").textContent = "📱 " + state.mobile_url.replace("http://", "");
    }
    drawIdleRace();
    drawWheel(0);
    resetSurvivalArena();
    initCageBalls();
    drawBombCanvas(1, "BẤM CHÂM NGÒI ĐỂ BẮT ĐẦU", false);
  }

  function getCleanRoster() {
    return state.active_list.map(x => x.split("|")[0].trim()).filter(Boolean);
  }

  async function syncRosterFromTextarea() {
    if (!hasPermission("edit_roster")) {
      showToast("🔒 Bạn đã bị khoá quyền chỉnh sửa Danh Sách!", "error");
      document.getElementById("roster-textarea").value = state.active_list.join("\n");
      return;
    }
    const lines = document.getElementById("roster-textarea").value.split("\n").map(s => s.trim()).filter(Boolean);
    state.active_list = lines;
    updateRosterCounters();
    const res = await fetch("/api/roster", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ items: lines })
    });
    const d = await res.json();
    if (d.roster_version) state.roster_version = d.roster_version;
    if (currentMode === "race") drawIdleRace();
    if (currentMode === "wheel") drawWheel(0);
    if (currentMode === "survival") resetSurvivalArena();
    if (currentMode === "cage") initCageBalls();
  }

  function updateRosterCounters() {
    const count = state.active_list.length;
    document.getElementById("roster-count-badge").textContent = count;
    document.getElementById("drawer-count").textContent = count;
  }

  function toggleRosterDrawer() {
    if (!hasPermission("edit_roster")) {
      return showToast("🔒 Quản trị viên đã giới hạn quyền sửa Danh Sách của bạn!", "error");
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
    showToast(`Đã nạp mẫu "${key}"`, "success");
  }

  function openMode(mode) {
    if (mode !== "hub" && mode !== "admin" && !hasPermission(mode)) {
      return showToast(`🔒 Chức năng "${FEATURE_LABELS[mode]}" đã bị Admin khoá với bạn!`, "error");
    }
    if (mode === "admin" && !(session.role === "owner" || session.role === "admin")) {
      return showToast("🔒 Chỉ Quản trị viên mới có quyền truy cập!", "error");
    }
    currentMode = mode;
    document.querySelectorAll(".view-pane").forEach(el => {
      el.classList.remove("active");
      el.classList.remove("projector-mode");
    });
    document.querySelectorAll(".mode-tab").forEach(el => {
      el.classList.toggle("active", el.getAttribute("data-mode") === mode);
    });
    document.getElementById("view-" + mode).classList.add("active");
    document.getElementById("main-wrap").classList.toggle("full-admin", mode === "admin");
    document.getElementById("side-column").style.display = mode === "admin" ? "none" : "flex";

    if (mode === "admin") renderAdminCenter();
    if (mode === "race") drawIdleRace();
    if (mode === "wheel") drawWheel(0);
    if (mode === "survival") resetSurvivalArena();
    if (mode === "cage") initCageBalls();
    if (mode === "teams") generateVisualTeams();
  }

  async function logHistory(game, summary, results) {
    const res = await fetch("/api/log", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ game, summary, results })
    });
    const data = await res.json();
    if (data.history) {
      state.history = data.history;
      renderHistoryList();
    }
  }

  function renderHistoryList() {
    const box = document.getElementById("history-list");
    if (!state.history || !state.history.length) {
      box.innerHTML = '<div style="color:var(--text-muted); font-size:0.8rem;">Chưa có lịch sử.</div>';
      return;
    }
    box.innerHTML = state.history.map(h => `
      <div class="history-card">
        <div class="meta"><span>${esc(h.game)}</span><span>${esc(h.time)}</span></div>
        <div style="font-size:0.74rem; color:var(--text-muted);">${esc(h.summary)}</div>
        <div class="val">${h.results.map(esc).join(" • ")}</div>
      </div>
    `).join("");
  }

  async function clearAllHistory() {
    if (!canTriggerSpin()) return;
    const res = await fetch("/api/history/clear", { method: "POST", headers: authHeaders() });
    if (res.ok) {
      state = await res.json();
      renderHistoryList();
      showToast("Đã xoá lịch sử phòng", "info");
    }
  }

  function copyMobileLink() {
    navigator.clipboard.writeText(window.location.href);
    showToast("Đã sao chép đường link phòng!", "success");
  }

  // --- GAME 1: RACE ---
  let raceRunning = false;
  const THEME_CONFIG = {
    duck: { icon: "🦆", bg1: "#0284c7", bg2: "#0369a1", lane: "rgba(255,255,255,0.2)", trail: "rgba(255,255,255,0.55)" },
    car: { icon: "🏎️", bg1: "#1e293b", bg2: "#090d16", lane: "rgba(250,204,21,0.3)", trail: "rgba(251,191,36,0.6)" },
    rocket: { icon: "🚀", bg1: "#060911", bg2: "#1e1b4b", lane: "rgba(129,140,248,0.22)", trail: "rgba(244,114,182,0.65)" },
    horse: { icon: "🐎", bg1: "#15803d", bg2: "#14532d", lane: "rgba(255,255,255,0.22)", trail: "rgba(217,119,6,0.6)" }
  };

  function drawIdleRace() {
    if (raceRunning) return;
    const items = getCleanRoster().slice(0, 12);
    const theme = THEME_CONFIG[document.getElementById("race-theme").value];
    renderRaceFrame(items.map(name => ({ name, x: 165, boost: false, particles: [] })), theme, 0);
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

      (r.particles || []).forEach(pt => {
        ctx.fillStyle = theme.trail;
        ctx.globalAlpha = pt.life;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, pt.r, 0, Math.PI * 2); ctx.fill();
        ctx.globalAlpha = 1;
      });

      ctx.fillStyle = "rgba(0,0,0,0.48)"; ctx.fillRect(10, yMid - 13, 138, 26);
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
    if (items.length < 2) return showToast("Cần ít nhất 2 tay đua!", "warning");
    raceRunning = true;
    const duration = parseInt(document.getElementById("race-duration").value, 10);
    const theme = THEME_CONFIG[document.getElementById("race-theme").value];
    const startX = 165, distance = 880 - 95 - startX;
    const winnerIdx = Math.floor(Math.random() * items.length);
    const racers = items.map((name, i) => ({
      name, x: startX, phase: Math.random() * 6.28, freq: 2 + Math.random() * 2.5,
      finalOffset: i === winnerIdx ? 0 : -(12 + Math.random() * 85), boost: false, particles: []
    }));
    const t0 = performance.now(); let tick = 0;
    function step(now) {
      const p = Math.min(1, (now - t0) / duration);
      const laneH = 440 / racers.length;
      racers.forEach((r, idx) => {
        const wave = Math.sin(p * Math.PI * r.freq + r.phase) * (1 - p) * 42;
        const targetX = startX + p * (distance + r.finalOffset) + wave;
        r.boost = (targetX - r.x) > 2.2 && p < 0.95;
        r.x = Math.max(startX, targetX);
        if (tick % 2 === 0) {
          r.particles.push({ x: r.x - 14, y: idx * laneH + laneH / 2 + (Math.random() - 0.5) * 10, r: 3 + Math.random() * 3, life: 0.9 });
        }
        r.particles.forEach(pt => { pt.x -= 1.8; pt.life -= 0.06; });
        r.particles = r.particles.filter(pt => pt.life > 0);
      });
      if (++tick % 12 === 0) sfxTone(300 + p * 350, 0.03);
      renderRaceFrame(racers, theme, tick);
      if (p < 1) requestAnimationFrame(step);
      else {
        raceRunning = false;
        const sorted = [...racers].sort((a, b) => b.x - a.x);
        const top3 = sorted.slice(0, 3).map((r, i) => `${["🥇", "🥈", "🥉"][i]} ${r.name}`);
        document.getElementById("race-winner-text").textContent = top3.join("   |   ");
        document.getElementById("race-winner-banner").style.display = "flex";
        sfxFanfare(); shootConfetti(); triggerScreenShake();
        logHistory("🦆 Đua Tốc Độ", `Đua ${racers.length} tay đua`, [sorted[0].name]);
      }
    }
    requestAnimationFrame(step);
  }

  // --- GAME 2: WHEEL (WITH WORKING AUTO-REMOVE WINNER) ---
  let wheelAngle = 0, wheelSpinning = false;
  function parseWheelSlices() {
    return state.active_list.map(line => {
      if (line.includes("|")) {
        const [n, w] = line.split("|");
        const weight = parseFloat(w);
        return { name: n.trim(), weight: !isNaN(weight) && weight > 0 ? weight : 1, raw: line };
      }
      return { name: line.trim(), weight: 1, raw: line };
    }).filter(x => x.name);
  }
  function drawWheel(deflect = 0, ledPhase = 0) {
    const slices = parseWheelSlices(), canvas = document.getElementById("wheel-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d"), cx = 260, cy = 260, r = 225;
    ctx.clearRect(0, 0, 520, 520);
    if (!slices.length) return;

    ctx.beginPath(); ctx.arc(cx, cy, r + 14, 0, Math.PI * 2);
    ctx.fillStyle = "#1e293b"; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = "#f59e0b"; ctx.stroke();

    const totalW = slices.reduce((s, x) => s + x.weight, 0);
    let a = wheelAngle;
    slices.forEach((s, i) => {
      const arc = (s.weight / totalW) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, r, a, a + arc); ctx.closePath();
      ctx.fillStyle = PALETTE[i % PALETTE.length]; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(a + arc / 2);
      ctx.textAlign = "right"; ctx.fillStyle = "#fff"; ctx.font = "bold 16px sans-serif";
      ctx.fillText(s.name.slice(0, 18), r - 22, 6); ctx.restore();
      a += arc;
    });

    for (let i = 0; i < 16; i++) {
      const ba = (i / 16) * Math.PI * 2 + wheelAngle * 0.3;
      const bx = cx + Math.cos(ba) * (r + 7);
      const by = cy + Math.sin(ba) * (r + 7);
      const isLit = (i + ledPhase) % 2 === 0;
      ctx.beginPath(); ctx.arc(bx, by, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = isLit ? "#fde047" : "#475569";
      ctx.fill();
    }

    ctx.beginPath(); ctx.arc(cx, cy, 32, 0, Math.PI * 2);
    ctx.fillStyle = "#0f172a"; ctx.fill(); ctx.lineWidth = 4; ctx.strokeStyle = "#f59e0b"; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "center"; ctx.fillText("QUAY", cx, cy + 5);

    ctx.save();
    ctx.translate(cx + r + 18, cy);
    ctx.rotate(deflect);
    ctx.beginPath(); ctx.moveTo(-34, 0); ctx.lineTo(6, -15); ctx.lineTo(6, 15);
    ctx.closePath(); ctx.fillStyle = "#ef4444"; ctx.fill();
    ctx.restore();
  }
  function spinArcadeWheel() {
    if (!canTriggerSpin() || wheelSpinning) return;
    const slices = parseWheelSlices();
    if (slices.length < 2) return showToast("Cần ít nhất 2 ô trên vòng quay!", "warning");
    wheelSpinning = true;
    const totalW = slices.reduce((s, x) => s + x.weight, 0);
    const startA = wheelAngle, delta = (6 + Math.random() * 4) * Math.PI * 2 + Math.random() * 6.28;
    const t0 = performance.now(), dur = 3600;
    let lastIdx = -1, frame = 0;
    function step(now) {
      const t = Math.min(1, (now - t0) / dur);
      wheelAngle = startA + delta * (1 - Math.pow(1 - t, 3.6));
      const norm = ((0 - wheelAngle) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
      let acc = 0, idx = 0;
      for (let i = 0; i < slices.length; i++) {
        const arc = (slices[i].weight / totalW) * Math.PI * 2;
        if (norm >= acc && norm < acc + arc) { idx = i; break; }
        acc += arc;
      }
      let deflect = 0;
      if (idx !== lastIdx) {
        sfxTone(540 + (1 - t) * 260, 0.04);
        lastIdx = idx;
        deflect = -0.32;
      }
      drawWheel(deflect, Math.floor(++frame / 6));
      if (t < 1) requestAnimationFrame(step);
      else {
        wheelSpinning = false;
        drawWheel(0, 0);
        const win = slices[idx];
        document.getElementById("wheel-winner-text").textContent = "🎉 Kết quả: " + win.name;
        document.getElementById("wheel-winner-banner").style.display = "flex";
        sfxFanfare(); shootConfetti(); triggerScreenShake();
        logHistory("🎡 Vòng Quay", `Quay ${slices.length} ô`, [win.name]);

        // Tự loại ô đã trúng nếu được bật
        if (document.getElementById("wheel-auto-remove").checked && hasPermission("edit_roster")) {
          const idxRaw = state.active_list.indexOf(win.raw);
          if (idxRaw !== -1) {
            state.active_list.splice(idxRaw, 1);
            document.getElementById("roster-textarea").value = state.active_list.join("\n");
            setTimeout(() => syncRosterFromTextarea(), 600);
          }
        }
      }
    }
    requestAnimationFrame(step);
  }

  // --- GAME 3: SURVIVAL ELIMINATION (LAST MAN STANDING) ---
  let survivalPool = []; // [{name, alive}]
  let survivalBusy = false;

  function resetSurvivalArena() {
    survivalPool = getCleanRoster().map(name => ({ name, alive: true }));
    document.getElementById("survival-banner").style.display = "none";
    renderSurvivalGrid(-1);
  }

  function renderSurvivalGrid(highlightIdx) {
    const grid = document.getElementById("survival-grid");
    if (!grid) return;
    const aliveCount = survivalPool.filter(x => x.alive).length;
    document.getElementById("surv-alive-count").textContent = aliveCount;
    grid.innerHTML = survivalPool.map((p, idx) => {
      let cls = "survivor-chip";
      if (!p.alive) cls += " eliminated";
      else if (aliveCount === 1) cls += " champion";
      else if (idx === highlightIdx) cls += " scanning";
      const prefix = !p.alive ? "❌ " : (aliveCount === 1 ? "👑 " : "🛡️ ");
      return `<div class="${cls}">${prefix}${esc(p.name)}</div>`;
    }).join("");
  }

  function eliminateOneRound() {
    if (!canTriggerSpin() || survivalBusy) return;
    const aliveIndices = survivalPool.map((p, i) => p.alive ? i : -1).filter(i => i !== -1);
    if (aliveIndices.length <= 1) {
      return showToast("Đã tìm ra nhà vô địch! Bấm 'Hồi sinh tất cả' để chơi ván mới.", "info");
    }
    survivalBusy = true;
    const victimIdx = aliveIndices[Math.floor(Math.random() * aliveIndices.length)];
    let hops = 0;
    const maxHops = 14 + Math.floor(Math.random() * 8);

    const timer = setInterval(() => {
      const randIdx = aliveIndices[hops % aliveIndices.length];
      renderSurvivalGrid(randIdx);
      sfxTone(460 + hops * 18, 0.04);
      hops++;
      if (hops >= maxHops) {
        clearInterval(timer);
        survivalPool[victimIdx].alive = false;
        survivalBusy = false;
        renderSurvivalGrid(-1);
        triggerScreenShake();
        sfxWarningAlarm();

        const remaining = survivalPool.filter(x => x.alive);
        const banner = document.getElementById("survival-banner");
        const bText = document.getElementById("survival-banner-text");
        banner.style.display = "flex";

        if (remaining.length === 1) {
          bText.textContent = `👑 NHÀ VÔ ĐỊCH SỐNG SÓT CUỐI CÙNG: ${remaining[0].name}!`;
          sfxFanfare(); shootConfetti();
          logHistory("🏆 Đấu Trường Sinh Tồn", "Vô địch cuối cùng", [remaining[0].name]);
        } else {
          bText.textContent = `💥 Đã loại: ${survivalPool[victimIdx].name} (Còn lại ${remaining.length} người)`;
          logHistory("🏆 Sinh Tồn", `Loại 1 người (Còn ${remaining.length})`, [`Loại: ${survivalPool[victimIdx].name}`]);
        }
      }
    }, 85);
  }

  // --- GAME 4: CAGE ---
  let cageBalls = [], drawnBalls = [], cageAnimId = null;
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
    ctx.strokeStyle = "rgba(148,163,184,0.4)"; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    cageBalls.forEach(b => {
      b.vy += 0.18; b.x += b.vx; b.y += b.vy;
      const dx = b.x - cx, dy = b.y - cy, d = Math.hypot(dx, dy);
      if (d > R - 18) {
        const nx = dx / d, ny = dy / d;
        b.x = cx + nx * (R - 18); b.y = cy + ny * (R - 18);
        b.vx = -b.vx * 0.78 - ny * 3.4; b.vy = -b.vy * 0.78 + nx * 3.4;
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
    if (!cageBalls.length) return showToast("Đã hết bóng trong lồng!", "warning");
    const picked = cageBalls.splice(Math.floor(Math.random() * cageBalls.length), 1)[0];
    drawnBalls.unshift(picked);
    document.getElementById("cage-winner-text").textContent = "🎱 Bóng vừa rơi: " + picked.label;
    document.getElementById("cage-winner-banner").style.display = "flex";
    sfxFanfare(); shootConfetti(); triggerScreenShake();
    logHistory("🔮 Lồng Cầu", "Bật bóng xổ số", [picked.label]);
  }

  // --- GAME 5: SLOT ---
  function pullSlotMachine() {
    if (!canTriggerSpin()) return;
    const mode = document.getElementById("slot-mode").value;
    const pool = mode === "numbers" ? ["0","1","2","3","4","5","6","7","8","9"] : getCleanRoster();
    if (!pool.length) return showToast("Danh sách trống!", "warning");
    const win = pool[Math.floor(Math.random() * pool.length)];
    const targets = mode === "roster_single" ? [win, win, win] : [
      pool[Math.floor(Math.random() * pool.length)],
      pool[Math.floor(Math.random() * pool.length)],
      pool[Math.floor(Math.random() * pool.length)]
    ];
    [0, 1, 2].forEach(i => {
      const el = document.getElementById("reel-" + i);
      el.classList.remove("locked");
      el.classList.add("spinning");
      const timer = setInterval(() => { el.textContent = pool[Math.floor(Math.random() * pool.length)]; }, 55);
      setTimeout(() => {
        clearInterval(timer);
        el.classList.remove("spinning");
        el.textContent = targets[i];
        el.classList.add("locked");
        sfxTone(400 + i * 160, 0.1);
        if (i === 2) {
          sfxFanfare(); shootConfetti(); triggerScreenShake();
          logHistory("🎰 Máy Slot", "Quay 3 trục", [targets.join(" - ")]);
        }
      }, 750 + i * 650);
    });
  }

  // --- GAME 6: HAT WITH INTERACTIVE 3D FLIP CARDS ---
  function drawFromHat() {
    if (!canTriggerSpin()) return;
    const pool = getCleanRoster();
    if (!pool.length) return showToast("Danh sách trống!", "warning");
    const count = Math.min(pool.length, Math.max(1, parseInt(document.getElementById("hat-count").value, 10) || 1));
    const faceDown = document.getElementById("hat-face-down").checked;
    const hat = document.getElementById("hat-emoji");
    hat.classList.add("shaking");
    setTimeout(() => {
      hat.classList.remove("shaking");
      const picked = [...pool].sort(() => Math.random() - 0.5).slice(0, count);
      document.getElementById("hat-results").innerHTML = picked.map((x, idx) => {
        if (faceDown) {
          return `<div class="magic-ticket unrevealed" data-val="${esc(x)}" onclick="revealSingleHatCard(this)">❓ Lá Thăm #${idx + 1} (Bấm lật)</div>`;
        }
        return `<div class="magic-ticket">✨ ${esc(x)}</div>`;
      }).join("");
      sfxFanfare();
      if (!faceDown) shootConfetti();
      logHistory("🎩 Mũ Ma Thuật", `Rút ${count} phiếu`, picked);
    }, 550);
  }

  function revealSingleHatCard(el) {
    if (!el.classList.contains("unrevealed")) return;
    const val = el.getAttribute("data-val");
    el.classList.remove("unrevealed");
    el.textContent = "✨ " + val;
    sfxTone(780, 0.12, "sine", 0.12);
    shootConfetti();
  }

  function revealAllHatCards() {
    document.querySelectorAll("#hat-results .magic-ticket.unrevealed").forEach(el => revealSingleHatCard(el));
  }

  // --- GAME 7: BOMB ---
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
    const fuseEnd = 260 + Math.max(0, ratio) * 240;
    ctx.strokeStyle = "#d97706"; ctx.lineWidth = 8;
    ctx.beginPath(); ctx.moveTo(260, 170); ctx.lineTo(fuseEnd, 170); ctx.stroke();
    if (ratio < 1 && ratio > 0) {
      ctx.fillStyle = "#fde047";
      ctx.beginPath(); ctx.arc(fuseEnd, 170, 8 + Math.random() * 6, 0, Math.PI * 2); ctx.fill();
    }
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
        triggerScreenShake(); sfxWarningAlarm();
        logHistory("💣 Bom Nổ", "Bom hẹn giờ", [victim]);
      }
    }
    bombTimerId = requestAnimationFrame(tick);
  }

  // --- GAME 8: TABLETOP ---
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

  // --- GAME 9: TEAMS ---
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
      <div class="team-box"><h4><span>🛡️ Đội ${i + 1}</span><span>${t.length} người</span></h4><ul>${t.map(m => `<li>• ${esc(m)}</li>`).join("")}</ul></div>
    `).join("");
  }

  // --- GAME 10: UTILITY ---
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
      unique: true
    };
    const res = await fetch("/api/utility", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) return showToast(data.error || "Lỗi xử lý", "error");
    document.getElementById("util-results").innerHTML = data.results.map(item => `<div class="magic-ticket">${esc(item)}</div>`).join("");
    sfxFanfare();
    state.history = data.history;
    renderHistoryList();
  }

  window.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      document.querySelectorAll(".projector-mode").forEach(el => el.classList.remove("projector-mode"));
      closeUnifiedModal();
      closePermModal();
    }
  });

  window.addEventListener("DOMContentLoaded", () => {
    fetchInitialState();
    if (session.username) {
      document.getElementById("login-user").value = session.username;
    }
    setInterval(heartbeatLoop, 1800);
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

    def _get_client_ip(self):
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authenticate(self, data):
        uname = urllib.parse.unquote(self.headers.get("X-Username", "")).strip()
        token = self.headers.get("X-Token", "").strip()
        if not uname or not token:
            return None
        user = data["users"].get(uname)
        if not user or user.get("session_token") != token:
            return None
        return user

    def _check_rate_limit(self, key, max_calls=20, window=10):
        now = time.time()
        calls = [t for t in ACTION_RATE.get(key, []) if now - t < window]
        calls.append(now)
        ACTION_RATE[key] = calls
        return len(calls) <= max_calls

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            with DATA_LOCK:
                data = DataManager._unlocked_load()
                safe_users = {
                    k: DataManager.sanitize_user_public(v) for k, v in data["users"].items()
                }
                resp = {
                    "roster_version": data.get("roster_version", 1),
                    "presets": data["presets"],
                    "active_list": data["active_list"],
                    "history": data["history"],
                    "chat_messages": data["chat_messages"],
                    "live_event": data["live_event"],
                    "audit_logs": data["audit_logs"],
                    "users": safe_users,
                    "banned_ips": data["banned_ips"],
                    "settings": data["settings"],
                    "mobile_url": f"http://{get_lan_ip()}:{PORT}",
                }
            self._send_json(resp)
        elif parsed.path == "/api/admin/export":
            with DATA_LOCK:
                data = DataManager._unlocked_load()
                actor = self._authenticate(data)
                if not actor or actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Không có quyền xuất dữ liệu!"}, 403)
                DataManager.add_audit(data, actor["username"], "Xuất bản sao lưu JSON toàn hệ thống")
                DataManager._unlocked_save(data, sync_cloud=False)
                return self._send_json(data)
        else:
            raw = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 2_000_000:
            return self._send_json({"error": "Dữ liệu gửi lên vượt quá giới hạn cho phép."}, 413)
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        client_ip = self._get_client_ip()
        ua = self.headers.get("User-Agent", "")

        with DATA_LOCK:
            data = DataManager._unlocked_load()

            if client_ip in data.get("banned_ips", {}):
                ban_info = data["banned_ips"][client_ip]
                return self._send_json(
                    {"ban_status": "ip_banned", "reason": ban_info.get("reason", "IP bị chặn.")},
                    403,
                )

            if self.path == "/api/auth/login":
                now = time.time()
                # Dọn dẹp bộ nhớ FAILED_LOGINS định kỳ
                if len(FAILED_LOGINS) > 500:
                    FAILED_LOGINS.clear()
                attempts = [t for t in FAILED_LOGINS.get(client_ip, []) if now - t < 60]
                FAILED_LOGINS[client_ip] = attempts
                if len(attempts) >= 5:
                    return self._send_json(
                        {"error": "Quá nhiều lần nhập sai PIN! Vui lòng thử lại sau 60 giây."},
                        429,
                    )

                username = payload.get("username", "").strip()[:32]
                pin = payload.get("pin", "").strip()
                if not username:
                    return self._send_json({"error": "Tên người dùng không hợp lệ."}, 400)

                user = data["users"].get(username)
                if not user:
                    if not data["settings"].get("allow_guest_join", True):
                        return self._send_json(
                            {"error": "Phòng đang khoá khách mới. Chỉ tài khoản do Admin tạo mới được vào!"},
                            403,
                        )
                    p_hash, p_salt = hash_pin(pin)
                    user = {
                        "username": username,
                        "pin_hash": p_hash,
                        "pin_salt": p_salt,
                        "has_pin": bool(pin),
                        "role": "member",
                        "status": "active",
                        "muted": False,
                        "ban_until": 0,
                        "ban_reason": "",
                        "strikes": 0,
                        "pending_warning": None,
                        "allowed_features": list(ALL_FEATURES),
                        "session_token": str(uuid.uuid4()),
                        "last_seen": now,
                        "ip": client_ip,
                        "device": parse_device_label(ua),
                    }
                    data["users"][username] = user
                    DataManager.add_audit(data, username, f"Tham gia phòng từ {client_ip}")
                else:
                    DataManager.check_and_refresh_ban(user)
                    if user.get("status") == "banned_perm":
                        return self._send_json(
                            {"ban_status": "banned_perm", "reason": user.get("ban_reason", "")},
                            403,
                        )
                    if user.get("status") == "banned_temp":
                        rem = max(0, float(user.get("ban_until", 0)) - now)
                        return self._send_json(
                            {
                                "ban_status": "banned_temp",
                                "reason": user.get("ban_reason", ""),
                                "remaining": rem,
                            },
                            403,
                        )
                    if not verify_pin(pin, user.get("pin_hash", ""), user.get("pin_salt", "")):
                        FAILED_LOGINS.setdefault(client_ip, []).append(now)
                        DataManager.add_audit(
                            data, "Bảo mật", f"Sai PIN vào tài khoản '{username}' từ IP {client_ip}", "warn"
                        )
                        DataManager._unlocked_save(data, sync_cloud=False)
                        return self._send_json({"error": "Sai mã PIN / Mật khẩu tài khoản!"}, 401)

                    if user.get("status") == "kicked":
                        user["status"] = "active"
                    user["session_token"] = str(uuid.uuid4())
                    user["last_seen"] = now
                    user["ip"] = client_ip
                    user["device"] = parse_device_label(ua)

                DataManager._unlocked_save(data)
                return self._send_json(
                    {
                        "user": DataManager.sanitize_user_public(user),
                        "token": user["session_token"],
                    }
                )

            elif self.path == "/api/heartbeat":
                uname = urllib.parse.unquote(self.headers.get("X-Username", "")).strip()
                token = self.headers.get("X-Token", "").strip()
                user = data["users"].get(uname)
                if not user:
                    return self._send_json(
                        {"ban_status": "kicked", "reason": "Tài khoản đã bị xoá bởi Admin."}, 403
                    )

                if DataManager.check_and_refresh_ban(user):
                    DataManager._unlocked_save(data)

                if user.get("status") == "kicked" or user.get("session_token") != token:
                    return self._send_json(
                        {"ban_status": "kicked", "reason": user.get("ban_reason", "Bạn đã bị mời ra khỏi phòng.")},
                        403,
                    )
                if user.get("status") == "banned_perm":
                    return self._send_json(
                        {"ban_status": "banned_perm", "reason": user.get("ban_reason", "")}, 403
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

                now = time.time()
                user["last_seen"] = now
                user["ip"] = client_ip
                user["device"] = parse_device_label(ua)
                DataManager._unlocked_save(data, sync_cloud=False)

                online_count = sum(
                    1
                    for u in data["users"].values()
                    if u.get("status") == "active" and (now - u.get("last_seen", 0)) < 12
                )

                resp = {
                    "user": DataManager.sanitize_user_public(user),
                    "online_count": max(1, online_count),
                    "settings": data["settings"],
                    "roster_version": data.get("roster_version", 1),
                    "active_list": data["active_list"],
                    "live_event": data["live_event"],
                    "chat_messages": data["chat_messages"],
                    "history": data["history"][:25],
                }
                if user.get("role") in ("owner", "admin"):
                    resp["users"] = {
                        k: DataManager.sanitize_user_public(v) for k, v in data["users"].items()
                    }
                    resp["banned_ips"] = data["banned_ips"]
                    resp["audit_logs"] = data["audit_logs"]
                return self._send_json(resp)

            actor = self._authenticate(data)
            if not actor:
                return self._send_json({"error": "Phiên đăng nhập hết hạn hoặc không hợp lệ!"}, 401)

            if self.path == "/api/warning/ack":
                actor["pending_warning"] = None
                DataManager._unlocked_save(data)
                return self._send_json({"ok": True})

            elif self.path == "/api/auth/change_my_pin":
                new_p = str(payload.get("new_pin", "")).strip()[:32]
                h, s = hash_pin(new_p)
                actor["pin_hash"] = h
                actor["pin_salt"] = s
                actor["has_pin"] = bool(new_p)
                DataManager.add_audit(data, actor["username"], "Tự cập nhật mã PIN bảo vệ cá nhân")
                DataManager._unlocked_save(data)
                return self._send_json({"ok": True})

            elif self.path == "/api/chat":
                if not data["settings"].get("chat_enabled", True) and actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Quản trị viên đang tạm tắt kênh chat phòng!"}, 403)
                if actor.get("muted") and actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Bạn đang bị cấm chat!"}, 403)
                if not self._check_rate_limit(f"chat:{actor['username']}", max_calls=8, window=10):
                    return self._send_json({"error": "Bạn gửi tin nhắn quá nhanh, vui lòng chậm lại!"}, 429)

                txt = str(payload.get("text", "")).strip()[:180]
                if txt:
                    data["chat_messages"].append(
                        {
                            "id": str(uuid.uuid4())[:8],
                            "time": datetime.datetime.now().strftime("%H:%M"),
                            "user": actor["username"],
                            "role": actor["role"],
                            "text": txt,
                        }
                    )
                    data["chat_messages"] = data["chat_messages"][-50:]
                    DataManager._unlocked_save(data, sync_cloud=False)
                return self._send_json({"chat_messages": data["chat_messages"]})

            elif self.path == "/api/admin/import":
                if actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Không có quyền khôi phục dữ liệu!"}, 403)
                backup = payload.get("backup")
                if not isinstance(backup, dict):
                    return self._send_json({"error": "Định dạng JSON sao lưu không hợp lệ."}, 400)
                restored = DataManager._normalize_data(backup)
                DataManager.add_audit(restored, actor["username"], "Khôi phục dữ liệu từ bản sao lưu JSON", "warn")
                DataManager._unlocked_save(restored)
                return self._send_json({"ok": True})

            elif self.path == "/api/admin/action":
                if actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Không có quyền Quản trị viên!"}, 403)

                action = payload.get("action")
                target_name = payload.get("target", "").strip()

                if action == "update_settings":
                    data["settings"]["allow_guest_join"] = bool(payload.get("allow_guest_join", True))
                    data["settings"]["chat_enabled"] = bool(payload.get("chat_enabled", True))
                    data["settings"]["room_lockdown"] = bool(payload.get("room_lockdown", False))
                    data["settings"]["auto_ban_on_3_strikes"] = bool(
                        payload.get("auto_ban_on_3_strikes", True)
                    )
                    DataManager.add_audit(data, actor["username"], "Cập nhật cài đặt bảo mật phòng")

                elif action == "clear_chat":
                    data["chat_messages"] = []
                    DataManager.add_audit(data, actor["username"], "Xoá toàn bộ tin nhắn phòng")

                elif action == "kick_all_guests":
                    kicked_cnt = 0
                    for u in data["users"].values():
                        if u.get("role") in ("member", "viewer") and u.get("status") == "active":
                            u["status"] = "kicked"
                            u["ban_reason"] = "Quản trị viên đã dọn phòng."
                            u["session_token"] = str(uuid.uuid4())
                            kicked_cnt += 1
                    DataManager.add_audit(data, actor["username"], f"Kick hàng loạt {kicked_cnt} thành viên", "warn")

                elif action == "unban_all":
                    for u in data["users"].values():
                        if u.get("status") != "active":
                            u["status"] = "active"
                            u["ban_until"] = 0
                            u["ban_reason"] = ""
                    data["banned_ips"] = {}
                    DataManager.add_audit(data, actor["username"], "Ân xá & mở khoá toàn bộ tài khoản/IP")

                elif action == "set_broadcast":
                    if payload.get("active"):
                        data["settings"]["broadcast"] = {
                            "text": str(payload.get("text", ""))[:200],
                            "level": payload.get("level", "warning"),
                        }
                        DataManager.add_audit(
                            data, actor["username"], f"Phát thông báo toàn phòng: {payload.get('text')}"
                        )
                    else:
                        data["settings"]["broadcast"] = None

                elif action == "ban_ip_manual":
                    ip = payload.get("ip", "").strip()
                    if ip and ip != "127.0.0.1":
                        data["banned_ips"][ip] = {
                            "reason": payload.get("reason", "Chặn IP bởi Admin"),
                            "by": actor["username"],
                        }
                        DataManager.add_audit(data, actor["username"], f"Chặn vĩnh viễn IP {ip}", "warn")

                elif action == "unban_ip":
                    ip = payload.get("ip", "").strip()
                    data["banned_ips"].pop(ip, None)
                    DataManager.add_audit(data, actor["username"], f"Gỡ chặn IP {ip}")

                elif action == "create_user":
                    if not target_name:
                        return self._send_json({"error": "Thiếu tên user"}, 400)
                    role_req = payload.get("role", "member")
                    if role_req == "admin" and actor.get("role") != "owner":
                        role_req = "member"
                    p_hash, p_salt = hash_pin(payload.get("pin", "").strip())
                    data["users"][target_name] = {
                        "username": target_name,
                        "pin_hash": p_hash,
                        "pin_salt": p_salt,
                        "has_pin": bool(p_hash),
                        "role": role_req,
                        "status": "active",
                        "muted": False,
                        "ban_until": 0,
                        "ban_reason": "",
                        "strikes": 0,
                        "pending_warning": None,
                        "allowed_features": list(ALL_FEATURES),
                        "session_token": str(uuid.uuid4()),
                        "last_seen": 0,
                        "ip": "-",
                        "device": "Chưa đăng nhập",
                    }
                    DataManager.add_audit(data, actor["username"], f"Tạo tài khoản '{target_name}' ({role_req})")

                else:
                    target = data["users"].get(target_name)
                    if not target:
                        return self._send_json({"error": "Không tìm thấy người dùng."}, 404)
                    if target.get("role") == "owner" and action != "change_pin":
                        return self._send_json({"error": "Không thể kỷ luật tài khoản Owner gốc!"}, 403)
                    if target.get("role") == "owner" and actor.get("role") != "owner":
                        return self._send_json({"error": "Chỉ Owner mới được đổi PIN của chính mình!"}, 403)
                    if target.get("role") == "admin" and actor.get("role") != "owner":
                        return self._send_json({"error": "Chỉ Owner mới có quyền quản lý Admin đồng cấp!"}, 403)

                    if action == "set_role":
                        new_role = payload.get("role", "member")
                        if new_role == "admin" and actor.get("role") != "owner":
                            return self._send_json({"error": "Chỉ Owner mới được phong quyền Admin!"}, 403)
                        if new_role in ("admin", "member", "viewer"):
                            target["role"] = new_role
                            DataManager.add_audit(
                                data, actor["username"], f"Đổi quyền '{target_name}' thành {new_role}"
                            )
                    elif action == "toggle_mute":
                        target["muted"] = not bool(target.get("muted", False))
                        DataManager.add_audit(
                            data,
                            actor["username"],
                            f"{'Cấm chat' if target['muted'] else 'Mở chat'} đối với '{target_name}'",
                        )
                    elif action == "set_permissions":
                        target["allowed_features"] = payload.get("allowed_features", list(ALL_FEATURES))
                        DataManager.add_audit(
                            data, actor["username"], f"Cập nhật giới hạn chức năng cho '{target_name}'"
                        )
                    elif action == "change_pin":
                        new_p = payload.get("new_pin", "").strip()
                        h, s = hash_pin(new_p)
                        target["pin_hash"] = h
                        target["pin_salt"] = s
                        target["has_pin"] = bool(new_p)
                        DataManager.add_audit(data, actor["username"], f"Đổi mã PIN của '{target_name}'")
                    elif action == "warn_user":
                        reason = payload.get("reason", "Cảnh cáo vi phạm từ Admin.")
                        target["strikes"] = int(target.get("strikes", 0)) + 1
                        target["pending_warning"] = {
                            "id": str(uuid.uuid4())[:8],
                            "reason": reason,
                        }
                        DataManager.add_audit(
                            data,
                            actor["username"],
                            f"Cảnh cáo '{target_name}' (Gậy {target['strikes']}/3): {reason}",
                            "warn",
                        )
                        if (
                            data["settings"].get("auto_ban_on_3_strikes", True)
                            and target["strikes"] >= 3
                        ):
                            target["status"] = "banned_temp"
                            target["ban_until"] = time.time() + 1800
                            target["ban_reason"] = "Tự động khoá 30 phút do nhận đủ 3 gậy cảnh cáo."
                            target["session_token"] = str(uuid.uuid4())
                    elif action == "reset_strikes":
                        target["strikes"] = 0
                        target["pending_warning"] = None
                    elif action == "kick":
                        target["status"] = "kicked"
                        target["ban_reason"] = payload.get("reason", "Bị mời ra bởi Admin.")
                        target["session_token"] = str(uuid.uuid4())
                        DataManager.add_audit(data, actor["username"], f"Kick '{target_name}'", "warn")
                    elif action == "ban_temp":
                        val = float(payload.get("duration", payload.get("minutes", 15)))
                        unit = payload.get("unit", "minutes")
                        unit_map = {
                            "minutes": (60, "phút"),
                            "hours": (3600, "giờ"),
                            "days": (86400, "ngày"),
                            "years": (31536000, "năm"),
                        }
                        mult, unit_label = unit_map.get(unit, (60, "phút"))
                        if val <= 0:
                            return self._send_json({"error": "Thời gian cấm phải lớn hơn 0!"}, 400)
                        total_seconds = val * mult
                        default_reason = f"Tạm khoá {val:g} {unit_label} bởi Admin."
                        reason = str(payload.get("reason", "")).strip() or default_reason

                        target["status"] = "banned_temp"
                        target["ban_until"] = time.time() + total_seconds
                        target["ban_reason"] = reason
                        target["session_token"] = str(uuid.uuid4())
                        DataManager.add_audit(
                            data,
                            actor["username"],
                            f"Cấm '{target_name}' trong {val:g} {unit_label}",
                            "warn",
                        )
                    elif action == "ban_perm":
                        reason = payload.get("reason", "Cấm vĩnh viễn bởi Admin.")
                        target["status"] = "banned_perm"
                        target["ban_until"] = 0
                        target["ban_reason"] = reason
                        target["session_token"] = str(uuid.uuid4())
                        if payload.get("ban_ip") and target.get("ip") not in ("", "-", "127.0.0.1"):
                            data["banned_ips"][target["ip"]] = {
                                "reason": f"Cấm cùng tài khoản {target_name}: {reason}",
                                "by": actor["username"],
                            }
                        DataManager.add_audit(
                            data, actor["username"], f"Cấm vĩnh viễn '{target_name}'", "warn"
                        )
                    elif action == "unban":
                        target["status"] = "active"
                        target["ban_until"] = 0
                        target["ban_reason"] = ""
                        DataManager.add_audit(data, actor["username"], f"Mở khoá cho '{target_name}'")
                    elif action == "delete":
                        del data["users"][target_name]
                        DataManager.add_audit(data, actor["username"], f"Xoá tài khoản '{target_name}'")

                DataManager._unlocked_save(data)
                safe_users = {
                    k: DataManager.sanitize_user_public(v) for k, v in data["users"].items()
                }
                return self._send_json(
                    {
                        "users": safe_users,
                        "banned_ips": data["banned_ips"],
                        "audit_logs": data["audit_logs"],
                        "chat_messages": data["chat_messages"],
                        "settings": data["settings"],
                    }
                )

            if (
                data["settings"].get("room_lockdown")
                and actor.get("role") not in ("owner", "admin")
            ):
                return self._send_json({"error": "Phòng đang bị đóng băng khẩn cấp!"}, 403)

            if self.path == "/api/roster":
                if actor.get("role") not in ("owner", "admin") and "edit_roster" not in actor.get(
                    "allowed_features", []
                ):
                    return self._send_json({"error": "Không có quyền sửa danh sách"}, 403)
                data["active_list"] = [str(x)[:120] for x in payload.get("items", [])]
                data["roster_version"] = int(data.get("roster_version", 1)) + 1
                DataManager._unlocked_save(data)
                return self._send_json({"ok": True, "roster_version": data["roster_version"]})

            elif self.path == "/api/presets":
                if actor.get("role") not in ("owner", "admin") and "edit_roster" not in actor.get(
                    "allowed_features", []
                ):
                    return self._send_json({"error": "Không có quyền lưu mẫu"}, 403)
                name = payload.get("name", "").strip()[:50]
                if name:
                    data["presets"][name] = [str(x)[:120] for x in payload.get("items", [])]
                    DataManager._unlocked_save(data)
                return self._send_json(
                    {
                        "roster_version": data.get("roster_version", 1),
                        "presets": data["presets"],
                        "active_list": data["active_list"],
                        "history": data["history"],
                        "chat_messages": data["chat_messages"],
                        "settings": data["settings"],
                    }
                )

            elif self.path == "/api/log":
                if actor.get("role") == "viewer":
                    return self._send_json({"error": "Viewer không được ghi lịch sử"}, 403)
                game_title = str(payload.get("game", "Random"))[:40]
                results_list = [str(x)[:120] for x in payload.get("results", [])]
                data["history"].insert(
                    0,
                    {
                        "id": str(uuid.uuid4())[:8],
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "game": f"{game_title} ({actor['username']})",
                        "summary": str(payload.get("summary", ""))[:120],
                        "results": results_list,
                    },
                )
                data["history"] = data["history"][:60]
                data["live_event"] = {
                    "id": str(uuid.uuid4())[:8],
                    "actor": actor["username"],
                    "text": f"{actor['username']} vừa chơi {game_title} → {', '.join(results_list[:3])}",
                }
                DataManager._unlocked_save(data)
                return self._send_json({"history": data["history"]})

            elif self.path == "/api/utility":
                if actor.get("role") == "viewer":
                    return self._send_json({"error": "Viewer không được thao tác"}, 403)
                if actor.get("role") not in ("owner", "admin") and "utility" not in actor.get(
                    "allowed_features", []
                ):
                    return self._send_json({"error": "Chức năng Tiện Ích đang bị khoá!"}, 403)
                summary, results = RandomBackend.generate_utility(payload)
                data["history"].insert(
                    0,
                    {
                        "id": str(uuid.uuid4())[:8],
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "game": f"⚡ Tiện Ích ({actor['username']})",
                        "summary": summary,
                        "results": results,
                    },
                )
                data["history"] = data["history"][:60]
                DataManager._unlocked_save(data)
                return self._send_json(
                    {"summary": summary, "results": results, "history": data["history"]}
                )

            elif self.path == "/api/history/clear":
                if actor.get("role") not in ("owner", "admin"):
                    return self._send_json({"error": "Chỉ Admin mới được xoá lịch sử phòng!"}, 403)
                data["history"] = []
                DataManager._unlocked_save(data)
                return self._send_json({"history": []})


if __name__ == "__main__":
    with DATA_LOCK:
        DataManager._unlocked_load()
    lan_ip = get_lan_ip()
    local_url = f"http://localhost:{PORT}"
    mobile_url = f"http://{lan_ip}:{PORT}"

    print("=" * 68)
    print("🚀 OMNIRANDOM ARCADE v6.0 (ULTIMATE LIVE & SECURITY EDITION) READY!")
    print("=" * 68)
    print(f"💻 Local URL : {local_url}")
    print(f"📱 Mobile IP : {mobile_url}")
    print(f"👑 Owner     : {DEFAULT_ADMIN_USER}")
    print("-" * 68)

    if os.environ.get("RENDER") is None and os.environ.get("PYTHONANYWHERE_DOMAIN") is None:
        webbrowser.open(local_url)

    with ThreadedHTTPServer(("0.0.0.0", PORT), ArcadeHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nĐã đóng OmniRandom Arcade v6.0.")
