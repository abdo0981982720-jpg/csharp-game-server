#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سيرفر لعبة تعلّم C# — واجهة برمجة REST بلا أي اعتماديات خارجية.
Python stdlib فقط (http.server + sqlite3 + json + hashlib).

التشغيل محلياً:
    python3 server.py            # على المنفذ 8000
    PORT=9000 python3 server.py  # منفذ مخصص

نقاط الاتصال (كلها JSON):
    GET  /api/health                  — فحص السيرفر
    POST /api/register {username, password} -> {token, username, recovery_code}
    POST /api/login    {username, password} -> {token, username}
    POST /api/recover  {username, code, new_password} -> {token, username}
           استرجاع كلمة المرور برمز الاسترجاع (بلا بريد — السيرفر المجاني
           لا يرسل SMTP). الرمز يُعطى مرة واحدة عند التسجيل فقط.
    POST /api/recovery-code           — توليد رمز استرجاع جديد (يتطلب Bearer
           <token>)، للحسابات التي سُجّلت قبل v1.2.0 ولم تحصل على رمز أبداً.
           الرمز الجديد يستبدل القديم نهائياً ويُعاد نصه مرة واحدة فقط.
    GET  /api/progress                — يتطلب Authorization: Bearer <token>
           (يعيد أيضاً level_stars: نجوم كل مستوى {"1": 3, ...})
    POST /api/progress {xp, stars, levels_completed, badges[], streak, level_stars}
           يدمج نجوم كل مستوى بالأعلى لكل مستوى على حدة (v1.3.0).
    DELETE /api/account               — حذف الحساب نهائياً (يتطلب Bearer <token>)
    GET  /api/leaderboard?limit=50    — أفضل اللاعبين حسب XP
    GET  /api/daily                   — معرّف تحدي اليوم (نفسه لكل اللاعبين)

قاعدة التقدم الذهبية: السيرفر يحتفظ دائماً بالأعلى (max) لكل حقل،
ولا يستبدل تقدّماً أكبر بتقدّم أصغر أبداً — تقدّم اللاعب لا يضيع.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "csharp_game.db"))
PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
VERSION = "1.3.0"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PBKDF2_ROUNDS = 100_000

_db_lock = threading.Lock()
_db = None


def get_db():
    """اتصال SQLite واحد مشترك (محمي بقفل للخيوط المتعددة)."""
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                token TEXT UNIQUE,
                created_at TEXT NOT NULL,
                recovery_code_hash TEXT
            )
        """)
        # ترحيل آمن للقواعد القديمة (قبل v1.2.0): إضافة عمود رمز الاسترجاع
        cols = [r[1] for r in _db.execute("PRAGMA table_info(users)").fetchall()]
        if "recovery_code_hash" not in cols:
            _db.execute("ALTER TABLE users ADD COLUMN recovery_code_hash TEXT")
        _db.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                xp INTEGER NOT NULL DEFAULT 0,
                stars INTEGER NOT NULL DEFAULT 0,
                levels_completed INTEGER NOT NULL DEFAULT 0,
                badges TEXT NOT NULL DEFAULT '[]',
                streak INTEGER NOT NULL DEFAULT 0,
                level_stars TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
        """)
        # ترحيل آمن للقواعد القديمة (قبل v1.3.0): إضافة عمود نجوم المستويات
        pcols = [r[1] for r in _db.execute("PRAGMA table_info(progress)").fetchall()]
        if "level_stars" not in pcols:
            _db.execute("ALTER TABLE progress ADD COLUMN level_stars TEXT NOT NULL DEFAULT '{}'")
        _db.commit()
    return _db


# ---------- كلمات المرور والرموز ----------

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), PBKDF2_ROUNDS)
    return salt, digest.hex()


def verify_password(password, salt, expected_hex):
    _, digest = hash_password(password, salt)
    return hmac.compare_digest(digest, expected_hex)


def new_token():
    return secrets.token_hex(32)


# ---------- رمز استرجاع كلمة المرور (v1.2.0) ----------
# 16 حرفاً من مجموعة بلا أحرف ملتبسة (لا 0/O ولا 1/I/L)، تُعرض
# بأربع مجموعات مفصولة بشرطات: XXXX-XXXX-XXXX-XXXX. يُخزَّن هاشه
# فقط (PBKDF2 مع ملح خاص به بصيغة "salt:hash")، ويُعطى النص الصريح
# للاعب مرة واحدة عند التسجيل — ولا يتغير عند استخدامه.

RECOVERY_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_recovery_code():
    """يعيد (الرمز للعرض بشرطات، الهاش للتخزين)."""
    raw = "".join(secrets.choice(RECOVERY_CHARSET) for _ in range(16))
    display = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    salt, digest = hash_password(raw)
    return display, salt + ":" + digest


def normalize_recovery_code(code):
    """يقبل الرمز بشرطات أو بدونها وبأي حالة أحرف."""
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def verify_recovery_code(code, stored):
    if not stored or ":" not in stored:
        return False
    salt, expected = stored.split(":", 1)
    return verify_password(normalize_recovery_code(code), salt, expected)


def daily_id_for(d):
    """معرّف تحدي اليوم — مشتق حتمياً من التاريخ، نفسه لكل اللاعبين."""
    h = hashlib.sha256(("csharp-daily-" + d.isoformat()).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 1_000_000_007


# ---------- منطق نقاط الاتصال (دالة نقية: تُستخدم من HTTP ومن WSGI) ----------

def dispatch(method, path, query, headers, body_bytes):
    """تعالج طلباً واحداً وتعيد (رمز الحالة، قاموس JSON)."""
    try:
        body = {}
        if body_bytes:
            try:
                body = json.loads(body_bytes.decode("utf-8"))
                if not isinstance(body, dict):
                    return 400, {"error": "JSON body must be an object"}
            except (ValueError, UnicodeDecodeError):
                return 400, {"error": "Invalid JSON body"}

        if method == "GET" and path == "/api/health":
            with _db_lock:
                n = get_db().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            return 200, {"ok": True, "version": VERSION, "players": n}

        if method == "GET" and path == "/api/daily":
            today = date.today()
            return 200, {"date": today.isoformat(), "daily_id": daily_id_for(today)}

        if method == "GET" and path == "/api/leaderboard":
            limit = 50
            try:
                limit = max(1, min(100, int(query.get("limit", ["50"])[0])))
            except ValueError:
                pass
            with _db_lock:
                rows = get_db().execute(
                    """SELECT u.username, p.xp, p.stars, p.levels_completed
                       FROM users u JOIN progress p ON p.user_id = u.id
                       ORDER BY p.xp DESC, p.levels_completed DESC, p.stars DESC
                       LIMIT ?""", (limit,)).fetchall()
            players = [{"rank": i + 1, "username": r["username"], "xp": r["xp"],
                        "stars": r["stars"], "levels": r["levels_completed"]}
                       for i, r in enumerate(rows)]
            return 200, {"players": players}

        if method == "POST" and path in ("/api/register", "/api/login"):
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            if not USERNAME_RE.match(username):
                return 400, {"error": "username must be 3-20 letters, digits or _"}
            if len(password) < 4:
                return 400, {"error": "password must be at least 4 characters"}

            db = get_db()
            if path == "/api/register":
                with _db_lock:
                    exists = db.execute("SELECT id FROM users WHERE username = ?",
                                        (username,)).fetchone()
                    if exists:
                        return 409, {"error": "username already taken"}
                    salt, pw_hash = hash_password(password)
                    token = new_token()
                    recovery_display, recovery_hash = new_recovery_code()
                    today = date.today().isoformat()
                    cur = db.execute(
                        "INSERT INTO users (username, salt, password_hash, token, created_at,"
                        " recovery_code_hash)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (username, salt, pw_hash, token, today, recovery_hash))
                    db.execute(
                        "INSERT INTO progress (user_id, updated_at) VALUES (?, ?)",
                        (cur.lastrowid, today))
                    db.commit()
                return 201, {"ok": True, "token": token, "username": username,
                             "recovery_code": recovery_display}

            # login
            with _db_lock:
                row = db.execute("SELECT * FROM users WHERE username = ?",
                                 (username,)).fetchone()
                if row is None or not verify_password(password, row["salt"], row["password_hash"]):
                    return 401, {"error": "wrong username or password"}
                token = new_token()
                db.execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
                db.commit()
            return 200, {"ok": True, "token": token, "username": username}

        # استرجاع كلمة المرور برمز الاسترجاع (v1.2.0): لا بريد إلكتروني.
        # يُعاد الخطأ نفسه للمستخدم المجهول والرمز الخاطئ حتى لا يُكشف
        # وجود الحساب. الرمز لا يتغيّر بعد الاستخدام — يبقى صالحاً دائماً.
        if method == "POST" and path == "/api/recover":
            username = str(body.get("username", "")).strip()
            code = str(body.get("code", ""))
            new_password = str(body.get("new_password", ""))
            if not USERNAME_RE.match(username):
                return 400, {"error": "username must be 3-20 letters, digits or _"}
            if len(new_password) < 4:
                return 400, {"error": "password must be at least 4 characters"}
            db = get_db()
            with _db_lock:
                row = db.execute("SELECT * FROM users WHERE username = ?",
                                 (username,)).fetchone()
                if row is None or not verify_recovery_code(
                        code, row["recovery_code_hash"]):
                    return 401, {"error": "wrong username or recovery code"}
                salt, pw_hash = hash_password(new_password)
                token = new_token()
                db.execute("UPDATE users SET salt = ?, password_hash = ?, token = ?"
                           " WHERE id = ?", (salt, pw_hash, token, row["id"]))
                db.commit()
            return 200, {"ok": True, "token": token, "username": username}

        # توليد رمز استرجاع جديد للمستخدم المسجّل دخوله (v1.3.0):
        # للحسابات التي سُجّلت قبل v1.2.0 ولم تحصل على رمز أبداً، ولمن
        # أضاع رمزه. الرمز الجديد يستبدل القديم نهائياً، ويُعاد نصه
        # الصريح مرة واحدة فقط — يُخزَّن هاشه فقط كالمعتاد.
        if method == "POST" and path == "/api/recovery-code":
            token = None
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not token:
                return 401, {"error": "missing Authorization: Bearer <token>"}
            db = get_db()
            with _db_lock:
                user = db.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
                if user is None:
                    return 401, {"error": "invalid token"}
                display, code_hash = new_recovery_code()
                db.execute("UPDATE users SET recovery_code_hash = ? WHERE id = ?",
                           (code_hash, user["id"]))
                db.commit()
            return 200, {"ok": True, "recovery_code": display}

        # حذف الحساب نهائياً (موعد به في سياسة الخصوصية):
        # يحذف صف المستخدم + تقدّمه + جلسته، فلا يعود الرمز صالحاً
        # ولا يظهر اللاعب في لوحة الصدارة بعدها.
        if method == "DELETE" and path == "/api/account":
            token = None
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not token:
                return 401, {"error": "missing Authorization: Bearer <token>"}
            db = get_db()
            with _db_lock:
                user = db.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
                if user is None:
                    return 401, {"error": "invalid token"}
                uid = user["id"]
                db.execute("DELETE FROM progress WHERE user_id = ?", (uid,))
                db.execute("DELETE FROM users WHERE id = ?", (uid,))
                db.commit()
            return 200, {"ok": True, "deleted": True}

        if path == "/api/progress":
            token = None
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not token:
                return 401, {"error": "missing Authorization: Bearer <token>"}
            db = get_db()
            with _db_lock:
                user = db.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
                if user is None:
                    return 401, {"error": "invalid token"}
                uid = user["id"]
                if method == "GET":
                    row = db.execute("SELECT * FROM progress WHERE user_id = ?", (uid,)).fetchone()
                    if row is None:
                        return 200, {"xp": 0, "stars": 0, "levels_completed": 0,
                                     "badges": [], "streak": 0, "level_stars": {}}
                    return 200, {"xp": row["xp"], "stars": row["stars"],
                                 "levels_completed": row["levels_completed"],
                                 "badges": json.loads(row["badges"]),
                                 "streak": row["streak"],
                                 "level_stars": _level_stars(row["level_stars"])}
                if method == "POST":
                    # الدمج: الأعلى دائماً يفوز — لا يُستبدل تقدّم أكبر بأصغر أبداً.
                    row = db.execute("SELECT * FROM progress WHERE user_id = ?", (uid,)).fetchone()
                    cur = {"xp": 0, "stars": 0, "levels_completed": 0,
                           "badges": [], "streak": 0} if row is None else {
                        "xp": row["xp"], "stars": row["stars"],
                        "levels_completed": row["levels_completed"],
                        "badges": json.loads(row["badges"]), "streak": row["streak"]}
                    merged = {
                        "xp": max(cur["xp"], _int(body.get("xp"), 0)),
                        "stars": max(cur["stars"], _int(body.get("stars"), 0)),
                        "levels_completed": max(cur["levels_completed"],
                                                _int(body.get("levels_completed"), 0)),
                        "streak": max(cur["streak"], _int(body.get("streak"), 0)),
                        "badges": sorted(set(cur["badges"]) | set(_badges(body.get("badges")))),
                    }
                    # نجوم كل مستوى (v1.3.0): الأعلى يفوز لكل مستوى على حدة.
                    # حمولة قديمة بلا level_stars تُبقي المخزّن كما هو.
                    cur_stars = _level_stars(row["level_stars"]) if row is not None else {}
                    merged_stars = dict(cur_stars)
                    for key, val in _level_stars(body.get("level_stars")).items():
                        if val > merged_stars.get(key, 0):
                            merged_stars[key] = val
                    db.execute(
                        """INSERT INTO progress (user_id, xp, stars, levels_completed, badges, streak, level_stars, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             xp=excluded.xp, stars=excluded.stars,
                             levels_completed=excluded.levels_completed,
                             badges=excluded.badges, streak=excluded.streak,
                             level_stars=excluded.level_stars,
                             updated_at=excluded.updated_at""",
                        (uid, merged["xp"], merged["stars"], merged["levels_completed"],
                         json.dumps(merged["badges"], ensure_ascii=False),
                         merged["streak"],
                         json.dumps(merged_stars, ensure_ascii=False),
                         date.today().isoformat()))
                    db.commit()
                    return 200, {"ok": True, **merged, "level_stars": merged_stars}
            return 405, {"error": "method not allowed"}

        return 404, {"error": "unknown endpoint: %s %s" % (method, path)}
    except Exception as exc:  # لا نكشف تفاصيل داخلية للعميل
        print("ERROR in dispatch:", repr(exc))
        return 500, {"error": "internal server error"}


def _int(value, default):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _badges(value):
    if isinstance(value, list):
        return [str(b) for b in value if isinstance(b, (str, int))][:50]
    return []


def _level_stars(value):
    """تحويل حقل level_stars (نص JSON أو قاموس) إلى {مستوى: نجوم} نظيف.
    مفاتيح نصية "1".."100" وقيم 0..3 فقط؛ أي شيء آخر يُتجاهل."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, val in value.items():
        try:
            level = int(key)
            stars = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= level <= 100 and 0 <= stars <= 3:
            out[str(level)] = stars
    return out


# ---------- خادم HTTP ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, payload = dispatch(method, parsed.path,
                                   parse_qs(parsed.query), headers, body)
        self._send_json(status, payload)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    get_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("🎮 C# game server v%s — listening on %s:%d" % (VERSION, HOST, PORT))
    print("   DB: %s" % DB_PATH)
    server.serve_forever()


if __name__ == "__main__":
    main()
