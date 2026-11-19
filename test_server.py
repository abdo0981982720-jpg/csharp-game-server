#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""اختبارات السيرفر من طرف لطرف: يشغّل server.py على منفذ اختبار،
ثم يضرب كل نقطة اتصال ويتحقق من النتائج. التشغيل: python3 test_server.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 18099
BASE = "http://127.0.0.1:%d" % PORT

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS  %s" % name)
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, extra))


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main():
    tmpdb = os.path.join(tempfile.mkdtemp(), "test.db")
    env = dict(os.environ, PORT=str(PORT), DB_PATH=tmpdb)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # انتظار بدء السيرفر
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            print("FAIL  server did not start")
            return 1

        # 1. health
        s, b = req("GET", "/api/health")
        check("GET /api/health -> 200 ok", s == 200 and b.get("ok") is True, str(b))

        # 2. register
        s, b = req("POST", "/api/register", {"username": "ahmad", "password": "1234"})
        token_ahmad = b.get("token")
        check("POST /api/register ahmad -> 201 + token",
              s == 201 and bool(token_ahmad), str(b))

        # 3. duplicate register -> 409
        s, b = req("POST", "/api/register", {"username": "ahmad", "password": "9999"})
        check("duplicate register -> 409", s == 409, str(b))

        # 4. bad username -> 400
        s, b = req("POST", "/api/register", {"username": "ab", "password": "1234"})
        check("short username -> 400", s == 400, str(b))

        # 5. wrong password login -> 401
        s, b = req("POST", "/api/login", {"username": "ahmad", "password": "wrong"})
        check("wrong password login -> 401", s == 401, str(b))

        # 6. correct login -> 200 + new token
        s, b = req("POST", "/api/login", {"username": "ahmad", "password": "1234"})
        token_ahmad = b.get("token")
        check("POST /api/login -> 200 + token", s == 200 and bool(token_ahmad), str(b))

        # 7. progress without token -> 401
        s, b = req("GET", "/api/progress")
        check("GET /api/progress without token -> 401", s == 401, str(b))

        # 8. progress with bad token -> 401
        s, b = req("GET", "/api/progress", token="deadbeef")
        check("GET /api/progress bad token -> 401", s == 401, str(b))

        # 9. fresh progress = zeros
        s, b = req("GET", "/api/progress", token=token_ahmad)
        check("fresh progress zeros", s == 200 and b.get("xp") == 0
              and b.get("badges") == [], str(b))

        # 10. post progress
        s, b = req("POST", "/api/progress", token=token_ahmad,
                   body={"xp": 150, "stars": 9, "levels_completed": 3,
                         "badges": ["first", "perfect"], "streak": 2})
        check("POST /api/progress -> merged values", s == 200 and b.get("xp") == 150
              and b.get("stars") == 9 and b.get("levels_completed") == 3
              and b.get("streak") == 2, str(b))

        # 11. THE GOLDEN RULE: posting LOWER progress must NOT lose progress
        s, b = req("POST", "/api/progress", token=token_ahmad,
                   body={"xp": 50, "stars": 2, "levels_completed": 1,
                         "badges": ["daily"], "streak": 0})
        check("lower post keeps max values", s == 200 and b.get("xp") == 150
              and b.get("stars") == 9 and b.get("levels_completed") == 3
              and b.get("streak") == 2, str(b))
        check("badges are unioned", set(b.get("badges", [])) ==
              {"first", "perfect", "daily"}, str(b))

        # 12. GET progress confirms the merge
        s, b = req("GET", "/api/progress", token=token_ahmad)
        check("GET progress after merge", s == 200 and b.get("xp") == 150
              and len(b.get("badges", [])) == 3, str(b))

        # 13. second player with higher XP
        s, b = req("POST", "/api/register", {"username": "sara", "password": "abcd"})
        token_sara = b.get("token")
        check("register sara -> 201", s == 201 and bool(token_sara), str(b))
        s, b = req("POST", "/api/progress", token=token_sara,
                   body={"xp": 900, "stars": 30, "levels_completed": 12,
                         "badges": ["first"], "streak": 5})
        check("sara posts xp 900", s == 200 and b.get("xp") == 900, str(b))

        # 14. leaderboard ordering: sara first, ahmad second
        s, b = req("GET", "/api/leaderboard?limit=50")
        players = b.get("players", [])
        check("leaderboard has 2 players", s == 200 and len(players) == 2, str(b))
        check("leaderboard ordered by XP desc",
              len(players) == 2 and players[0]["username"] == "sara"
              and players[0]["xp"] == 900 and players[0]["rank"] == 1
              and players[1]["username"] == "ahmad"
              and players[1]["rank"] == 2, str(b))

        # 15. leaderboard limit param
        s, b = req("GET", "/api/leaderboard?limit=1")
        check("leaderboard limit=1", s == 200 and len(b.get("players", [])) == 1, str(b))

        # 16. daily endpoint: same id on repeated calls, matches date
        s1, d1 = req("GET", "/api/daily")
        s2, d2 = req("GET", "/api/daily")
        import datetime
        check("GET /api/daily stable + today's date",
              s1 == 200 and d1.get("daily_id") == d2.get("daily_id")
              and d1.get("date") == datetime.date.today().isoformat(), str(d1))

        # 17. unknown endpoint -> 404
        s, b = req("GET", "/api/nope")
        check("unknown endpoint -> 404", s == 404, str(b))

        # 18. health now counts 2 players
        s, b = req("GET", "/api/health")
        check("health players == 2", s == 200 and b.get("players") == 2, str(b))

        # 19. حذف الحساب: لاعب ثالث ينشئ حساباً ويسجّل تقدّماً ثم يحذفه
        s, b = req("POST", "/api/register", {"username": "omar", "password": "xyz9"})
        token_omar = b.get("token")
        check("register omar -> 201", s == 201 and bool(token_omar), str(b))
        s, b = req("POST", "/api/progress", token=token_omar,
                   body={"xp": 500, "stars": 15, "levels_completed": 6,
                         "badges": ["first"], "streak": 3})
        check("omar posts progress", s == 200 and b.get("xp") == 500, str(b))
        s, b = req("GET", "/api/leaderboard?limit=50")
        check("leaderboard lists omar before deletion",
              s == 200 and any(p.get("username") == "omar" for p in b.get("players", [])),
              str(b))

        # 20. حذف بدون رمز -> 401
        s, b = req("DELETE", "/api/account")
        check("DELETE /api/account without token -> 401", s == 401, str(b))

        # 21. حذف برمز خاطئ -> 401
        s, b = req("DELETE", "/api/account", token="deadbeef")
        check("DELETE /api/account bad token -> 401", s == 401, str(b))

        # 22. حذف صحيح -> 200
        s, b = req("DELETE", "/api/account", token=token_omar)
        check("DELETE /api/account -> 200 deleted",
              s == 200 and b.get("ok") is True and b.get("deleted") is True, str(b))

        # 23. الرمز لم يعد صالحاً بعد الحذف
        s, b = req("GET", "/api/progress", token=token_omar)
        check("token invalid after deletion -> 401", s == 401, str(b))

        # 24. الحذف نهائي: لا يمكن تسجيل الدخول بالحساب المحذوف
        s, b = req("POST", "/api/login", {"username": "omar", "password": "xyz9"})
        check("login after deletion -> 401", s == 401, str(b))

        # 25. لوحة الصدارة لم تعد تذكر omar، والعدد عاد إلى 2
        s, b = req("GET", "/api/leaderboard?limit=50")
        players = b.get("players", [])
        check("leaderboard no longer lists omar",
              s == 200 and len(players) == 2
              and all(p.get("username") != "omar" for p in players), str(b))

        # 26. health عاد إلى لاعبَين بعد الحذف
        s, b = req("GET", "/api/health")
        check("health players == 2 after deletion", s == 200 and b.get("players") == 2, str(b))

        # 27. التسجيل يعيد رمز استرجاع بصيغة صحيحة (4 مجموعات × 4 أحرف)
        s, b = req("POST", "/api/register", {"username": "layla", "password": "qwer"})
        code_layla = b.get("recovery_code", "")
        import re as _re
        check("register returns recovery code XXXX-XXXX-XXXX-XXXX",
              s == 201 and bool(_re.match(
                  r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){3}$",
                  code_layla)), str(b))

        # 28. استرجاع برمز خاطئ -> 401
        s, b = req("POST", "/api/recover", {"username": "layla", "code": "AAAA-BBBB-CCCC-DDDD",
                                            "new_password": "newp"})
        check("recover with wrong code -> 401", s == 401, str(b))

        # 29. استرجاع لمستخدم غير موجود -> 401 (نفس الخطأ — لا يُكشف وجود الحساب)
        s, b = req("POST", "/api/recover", {"username": "ghost", "code": code_layla,
                                            "new_password": "newp"})
        check("recover unknown user -> 401", s == 401, str(b))

        # 30. استرجاع صحيح -> 200 + رمز جلسة؛ كلمة المرور القديمة تتوقف
        s, b = req("POST", "/api/recover", {"username": "layla", "code": code_layla,
                                            "new_password": "newpass1"})
        token_layla = b.get("token")
        check("recover with correct code -> 200 + token",
              s == 200 and bool(token_layla), str(b))
        s, b = req("POST", "/api/login", {"username": "layla", "password": "qwer"})
        check("old password stops working after recover", s == 401, str(b))
        s, b = req("POST", "/api/login", {"username": "layla", "password": "newpass1"})
        check("new password works after recover", s == 200 and bool(b.get("token")), str(b))

        # 31. الرمز لا يتغيّر بعد الاستخدام، ويُقبل بلا شرطات وبحروف صغيرة
        plain = code_layla.replace("-", "").lower()
        s, b = req("POST", "/api/recover", {"username": "layla", "code": plain,
                                            "new_password": "newpass2"})
        check("same code reusable, dashes/case-insensitive",
              s == 200 and bool(b.get("token")), str(b))
        s, b = req("POST", "/api/login", {"username": "layla", "password": "newpass2"})
        check("second recovered password works", s == 200 and bool(b.get("token")), str(b))

        # 32. كلمة مرور جديدة قصيرة -> 400
        s, b = req("POST", "/api/recover", {"username": "layla", "code": code_layla,
                                            "new_password": "ab"})
        check("recover with short new password -> 400", s == 400, str(b))

        # 33. توليد رمز استرجاع جديد (v1.3.0): بلا رمز جلسة -> 401
        s, b = req("POST", "/api/recovery-code", {})
        check("POST /api/recovery-code without token -> 401", s == 401, str(b))

        # 34. برمز جلسة خاطئ -> 401
        s, b = req("POST", "/api/recovery-code", {}, token="deadbeef")
        check("POST /api/recovery-code bad token -> 401", s == 401, str(b))

        # 35. مستخدم مسجّل يولّد رمزاً جديداً بصيغة صحيحة ومختلفاً عن القديم
        s, b = req("POST", "/api/login", {"username": "layla", "password": "newpass2"})
        token_layla = b.get("token")
        check("login layla for regeneration", s == 200 and bool(token_layla), str(b))
        s, b = req("POST", "/api/recovery-code", {}, token=token_layla)
        code2 = b.get("recovery_code", "")
        check("POST /api/recovery-code -> 200 + well-formed new code",
              s == 200 and bool(_re.match(
                  r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){3}$",
                  code2)) and code2 != code_layla, str(b))

        # 36. الرمز القديم يتوقف عن العمل بعد التوليد الجديد
        s, b = req("POST", "/api/recover", {"username": "layla", "code": code_layla,
                                            "new_password": "newpass3"})
        check("old recovery code dead after regeneration", s == 401, str(b))

        # 37. الرمز الجديد يعمل للاسترجاع
        s, b = req("POST", "/api/recover", {"username": "layla", "code": code2,
                                            "new_password": "newpass3"})
        check("new recovery code works for recover",
              s == 200 and bool(b.get("token")), str(b))
        s, b = req("POST", "/api/login", {"username": "layla", "password": "newpass3"})
        token_layla = b.get("token")
        check("password after new-code recover works",
              s == 200 and bool(token_layla), str(b))

        # 38. حساب قديم بلا رمز (حالة ما قبل v1.2.0 — recovery_code_hash فارغ):
        # التوليد يمنحه رمزاً يعمل للاسترجاع.
        import sqlite3 as _sqlite3
        conn3 = _sqlite3.connect(tmpdb)
        conn3.execute("UPDATE users SET recovery_code_hash = NULL WHERE username = 'layla'")
        conn3.commit()
        conn3.close()
        s, b = req("POST", "/api/recovery-code", {}, token=token_layla)
        code3 = b.get("recovery_code", "")
        check("legacy account (NULL code) gets a code",
              s == 200 and bool(code3), str(b))
        s, b = req("POST", "/api/recover", {"username": "layla", "code": code3,
                                            "new_password": "newpass4"})
        check("legacy account's new code works for recover",
              s == 200 and bool(b.get("token")), str(b))

        # 39. نجوم المستويات (v1.3.0): POST مع level_stars ثم GET يعيدها
        s, b = req("POST", "/api/progress", token=token_ahmad,
                   body={"xp": 150, "level_stars": {"1": 3, "2": 2, "3": 1}})
        check("POST progress with level_stars -> 200",
              s == 200 and b.get("level_stars") == {"1": 3, "2": 2, "3": 1}, str(b))
        s, b = req("GET", "/api/progress", token=token_ahmad)
        check("GET progress returns level_stars",
              s == 200 and b.get("level_stars") == {"1": 3, "2": 2, "3": 1}, str(b))

        # 40. الأعلى يفوز لكل مستوى عبر مزامنتين مختلفتين
        s, b = req("POST", "/api/progress", token=token_ahmad,
                   body={"level_stars": {"1": 1, "2": 3, "4": 2}})
        check("second POST merges per-level max",
              s == 200 and b.get("level_stars") == {"1": 3, "2": 3, "3": 1, "4": 2},
              str(b))
        s, b = req("GET", "/api/progress", token=token_ahmad)
        check("GET confirms per-level max merge",
              s == 200 and b.get("level_stars") == {"1": 3, "2": 3, "3": 1, "4": 2},
              str(b))

        # 41. حمولة قديمة بلا level_stars ما زالت تعمل وتُبقي المخزّن
        s, b = req("POST", "/api/progress", token=token_ahmad, body={"xp": 150})
        check("legacy POST without level_stars -> 200", s == 200, str(b))
        s, b = req("GET", "/api/progress", token=token_ahmad)
        check("legacy POST preserves stored level_stars",
              s == 200 and b.get("level_stars") == {"1": 3, "2": 3, "3": 1, "4": 2},
              str(b))

        # 42. لاعب جديد: GET يعيد level_stars فارغاً
        s, b = req("POST", "/api/register", {"username": "noura", "password": "zxcv"})
        token_noura = b.get("token")
        check("register noura -> 201", s == 201 and bool(token_noura), str(b))
        s, b = req("GET", "/api/progress", token=token_noura)
        check("fresh user GET level_stars == {}",
              s == 200 and b.get("level_stars") == {}, str(b))

        # 43. الترحيل: جدول progress قديم بلا عمود level_stars — السيرفر
        # يضيف العمود تلقائياً ويعمل عليه.
        import sqlite3 as _s3
        old3_dir = tempfile.mkdtemp()
        old3_db = os.path.join(old3_dir, "old3.db")
        conn3 = _s3.connect(old3_db)
        conn3.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                token TEXT UNIQUE,
                created_at TEXT NOT NULL,
                recovery_code_hash TEXT
            )
        """)
        conn3.execute("""
            CREATE TABLE progress (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                xp INTEGER NOT NULL DEFAULT 0,
                stars INTEGER NOT NULL DEFAULT 0,
                levels_completed INTEGER NOT NULL DEFAULT 0,
                badges TEXT NOT NULL DEFAULT '[]',
                streak INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        conn3.commit()
        conn3.close()
        port3 = PORT + 2
        base3 = "http://127.0.0.1:%d" % port3
        env3 = dict(os.environ, PORT=str(port3), DB_PATH=old3_db)
        proc3 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                                 env=env3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ok3 = False
            for _ in range(50):
                try:
                    urllib.request.urlopen(base3 + "/api/health", timeout=1)
                    ok3 = True
                    break
                except Exception:
                    time.sleep(0.1)
            check("server starts on old progress-table DB", ok3)
            if ok3:
                def req3(method, path, body=None, token=None):
                    data = json.dumps(body).encode("utf-8") if body is not None else None
                    r = urllib.request.Request(base3 + path, data=data, method=method)
                    if data is not None:
                        r.add_header("Content-Type", "application/json")
                    if token:
                        r.add_header("Authorization", "Bearer " + token)
                    try:
                        with urllib.request.urlopen(r, timeout=5) as resp:
                            return resp.status, json.loads(resp.read().decode("utf-8"))
                    except urllib.error.HTTPError as e:
                        try:
                            return e.code, json.loads(e.read().decode("utf-8"))
                        except Exception:
                            return e.code, {}
                s3, b3 = req3("POST", "/api/register",
                              {"username": "migrant2", "password": "1234"})
                tok3 = b3.get("token")
                check("register on migrated DB -> 201", s3 == 201 and bool(tok3), str(b3))
                s3, b3 = req3("POST", "/api/progress", token=tok3,
                              body={"level_stars": {"1": 2, "5": 3}})
                check("POST level_stars works after migration",
                      s3 == 200 and b3.get("level_stars") == {"1": 2, "5": 3}, str(b3))
                s3, b3 = req3("GET", "/api/progress", token=tok3)
                check("GET level_stars works after migration",
                      s3 == 200 and b3.get("level_stars") == {"1": 2, "5": 3}, str(b3))
                conn3b = _s3.connect(old3_db)
                pcols3 = [row[1] for row in
                          conn3b.execute("PRAGMA table_info(progress)").fetchall()]
                conn3b.close()
                check("migration added level_stars column",
                      "level_stars" in pcols3, str(pcols3))
        finally:
            proc3.terminate()
            try:
                proc3.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc3.kill()

        # 44. الترحيل: قاعدة بيانات قديمة (جدول users بلا عمود رمز الاسترجاع)
        # يجب أن يعمل السيرفر عليها ويضيف العمود تلقائياً.
        import sqlite3
        old_dir = tempfile.mkdtemp()
        old_db = os.path.join(old_dir, "old.db")
        conn = sqlite3.connect(old_db)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                token TEXT UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        port2 = PORT + 1
        base2 = "http://127.0.0.1:%d" % port2
        env2 = dict(os.environ, PORT=str(port2), DB_PATH=old_db)
        proc2 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                                 env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ok_start = False
            for _ in range(50):
                try:
                    urllib.request.urlopen(base2 + "/api/health", timeout=1)
                    ok_start = True
                    break
                except Exception:
                    time.sleep(0.1)
            check("server starts on old-schema DB", ok_start)
            if ok_start:
                data = json.dumps({"username": "migrant", "password": "1234"}).encode("utf-8")
                r = urllib.request.Request(base2 + "/api/register", data=data, method="POST")
                r.add_header("Content-Type", "application/json")
                try:
                    with urllib.request.urlopen(r, timeout=5) as resp:
                        rb = json.loads(resp.read().decode("utf-8"))
                        check("register works after migration, returns recovery code",
                              resp.status == 201 and bool(rb.get("recovery_code")), str(rb))
                except urllib.error.HTTPError as e:
                    check("register works after migration, returns recovery code",
                          False, "HTTP %d" % e.code)
                conn2 = sqlite3.connect(old_db)
                cols = [row[1] for row in
                        conn2.execute("PRAGMA table_info(users)").fetchall()]
                conn2.close()
                check("migration added recovery_code_hash column",
                      "recovery_code_hash" in cols, str(cols))
        finally:
            proc2.terminate()
            try:
                proc2.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc2.kill()

        print("\n%d passed, %d failed" % (passed, failed))
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
