# 🎮 سيرفر لعبة تعلّم C#

سيرفر صغير بلغة Python **بدون أي اعتماديات خارجية** (مكتبة Python القياسية فقط):
حسابات لاعبين، حفظ تقدّم سحابي، لوحة صدارة عالمية، وتحدّي يومي موحّد.

## الملفات

| الملف | الوصف |
|---|---|
| `server.py` | السيرفر كاملاً (HTTP + SQLite + JSON) |
| `wsgi.py` | محوّل WSGI لاستضافة PythonAnywhere |
| `Dockerfile` | صورة Docker جاهزة (python:3.12-slim) |
| `test_server.py` | اختبارات من طرف لطرف (21 اختباراً) |

## التشغيل محلياً

```bash
python3 server.py            # http://127.0.0.1:8000
PORT=9000 python3 server.py  # منفذ مخصص
```

ثم افحص: `curl http://127.0.0.1:8000/api/health`

تشغيل الاختبارات: `python3 test_server.py` (يجب أن تظهر `21 passed, 0 failed`)

## نقاط الاتصال (كلها JSON)

| الطلب | الوصف |
|---|---|
| `GET /api/health` | فحص السيرفر + عدد اللاعبين |
| `POST /api/register` | `{username, password}` ← يعيد `{token, username}` |
| `POST /api/login` | `{username, password}` ← يعيد `{token, username}` |
| `GET /api/progress` | تقدّم اللاعب (يتطلب `Authorization: Bearer <token>`) |
| `POST /api/progress` | `{xp, stars, levels_completed, badges[], streak}` |
| `GET /api/leaderboard?limit=50` | أفضل اللاعبين حسب XP |
| `GET /api/daily` | `{date, daily_id}` — معرّف تحدي اليوم (نفسه للجميع) |

**قاعدة ذهبية:** عند رفع التقدّم يحتفظ السيرفر دائماً بالأعلى لكل حقل
(`max`) وتُدمج الأوسمة — تقدّم اللاعب لا يُستبدل أبداً بتقدّم أقل.

**الأمان:** كلمات المرور تُحفظ كـ PBKDF2-HMAC-SHA256 (100,000 جولة)
مع ملح عشوائي لكل لاعب، والجلسات برموز عشوائية 64 خانة.

## النشر مجاناً

### 1) Render (الأسهل — مجاني، مع HTTPS تلقائياً)

1. ارفع مجلد `csharp-game-server` إلى مستودع GitHub (عام أو خاص).
2. في [render.com](https://render.com): New → Web Service ← اختر المستودع.
3. الإعدادات: Runtime = **Docker** (أو Python مع Start Command `python server.py`)،
   واترك المنفذ كما هو — السيرفر يقرأ `PORT` من البيئة تلقائياً.
4. Deploy. ستحصل على رابط مثل `https://csharp-game.onrender.com`.
5. ضع هذا الرابط في التطبيق: الإعدادات ← رابط السيرفر.

⚠️ الخطة المجانية في Render توقف السيرفر بعد 15 دقيقة خمول،
وأول طلب بعدها يتأخر ~30 ثانية (التطبيق ينتظر 5 ثوانٍ فقط ثم يكمل أوفلاين
— لا مشكلة، سيتصل في المحاولة التالية). وقاعدة البيانات على القرص المؤقت
قد تُمسح عند إعادة النشر — التقدّم المحلي على الهاتف لا يتأثر أبداً.

### 2) Railway (مجاني للبداية — $5 رصيد شهرياً)

1. في [railway.app](https://railway.app): New Project → Deploy from GitHub ← المستودع.
2. Railway يكتشف الـ Dockerfile تلقائياً. بعد النشر: Settings → Networking
   → Generate Domain للحصول على رابط عام `https://...up.railway.app`.
3. ضع الرابط في التطبيق كما في الأعلى.

### 3) PythonAnywhere (مجاني — WSGI)

1. في [pythonanywhere.com](https://www.pythonanywhere.com) (حساب Beginner المجاني):
   ارفع `server.py` و `wsgi.py` إلى `/home/<اسمك>/csharp-game-server/`.
2. Web → Add a new web app → Manual configuration → Python 3.12.
3. في ملف WSGI ضع:
   ```python
   import sys
   sys.path.insert(0, '/home/<اسمك>/csharp-game-server')
   from wsgi import application
   ```
4. Reload. الرابط: `https://<اسمك>.pythonanywhere.com` — ضعه في التطبيق.

## ملاحظات

- قاعدة البيانات ملف SQLite واحد (`csharp_game.db`) يُنشأ تلقائياً.
- لا توجد اعتماديات: `pip install` غير مطلوب أبداً.
- التطبيق يعمل كاملاً بدون سيرفر (وضع أوفلاين) — السيرفر يضيف فقط
  الحسابات والمزامنة ولوحة الصدارة.
