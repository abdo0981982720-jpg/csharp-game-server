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
    POST /api/register {username, password} -> {token, username}
    POST /api/login    {username, password} -> {token, username}
    GET  /api/progress                — يتطلب Authorization: Bearer <token>
    POST /api/progress {xp, stars, levels_completed, badges[], streak}
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
VERSION = "1.1.0"

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
                created_at TEXT NOT NULL
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                xp INTEGER NOT NULL DEFAULT 0,
                stars INTEGER NOT NULL DEFAULT 0,
                levels_completed INTEGER NOT NULL DEFAULT 0,
                badges TEXT NOT NULL DEFAULT '[]',
                streak INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
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
                    today = date.today().isoformat()
                    cur = db.execute(
                        "INSERT INTO users (username, salt, password_hash, token, created_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (username, salt, pw_hash, token, today))
                    db.execute(
                        "INSERT INTO progress (user_id, updated_at) VALUES (?, ?)",
                        (cur.lastrowid, today))
                    db.commit()
                return 201, {"ok": True, "token": token, "username": username}

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
                                     "badges": [], "streak": 0}
                    return 200, {"xp": row["xp"], "stars": row["stars"],
                                 "levels_completed": row["levels_completed"],
                                 "badges": json.loads(row["badges"]),
                                 "streak": row["streak"]}
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
                    db.execute(
                        """INSERT INTO progress (user_id, xp, stars, levels_completed, badges, streak, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             xp=excluded.xp, stars=excluded.stars,
                             levels_completed=excluded.levels_completed,
                             badges=excluded.badges, streak=excluded.streak,
                             updated_at=excluded.updated_at""",
                        (uid, merged["xp"], merged["stars"], merged["levels_completed"],
                         json.dumps(merged["badges"], ensure_ascii=False),
                         merged["streak"], date.today().isoformat()))
                    db.commit()
                    return 200, {"ok": True, **merged}
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
