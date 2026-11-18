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
