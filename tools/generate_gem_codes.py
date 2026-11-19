# أكواد الجواهر (v1.4.0) — المولّد الوحيد المعتمد.
# الاستخدام:
#     python3 tools/generate_gem_codes.py <pack> <count>
# مثال:
#     python3 tools/generate_gem_codes.py 500 10   # عشرة أكواد × 500 جوهرة
#
# السرّ يُقرأ من (بالترتيب):
#   1. متغير البيئة GEM_CODE_SECRET
#   2. خيار --secret-file <مسار>
#   3. الملف المحفوظ عند صاحب اللعبة: ~/workspace/csharp-game-server-secret.txt
#
# السرّ نفسه يجب أن يكون على السيرفر الحي (ملف .gem_secret بجانب server.py
# أو متغير بيئة) وإلا رفض السيرفر كل الأكواد بـ 503. لا ترفع السرّ للريبو
# أبداً — الريبو عام، والملف .gem_secret مُستثناة في .gitignore.
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import server  # noqa: E402  (مصدر الحقيقة الوحيد لتوقيع الأكواد)

DEFAULT_SECRET_FILE = os.path.expanduser(
    "~/workspace/csharp-game-server-secret.txt")


def load_secret(secret_file=None):
    """السرّ من البيئة، أو من ملف مُعطى، أو من الملف الافتراضي."""
    secret = os.environ.get("GEM_CODE_SECRET", "").strip()
    if secret:
        return secret
    path = secret_file or DEFAULT_SECRET_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            secret = f.read().strip()
            return secret or None
    except OSError:
        return None


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    secret_file = None
    for i, a in enumerate(argv[1:]):
        if a == "--secret-file" and i + 1 < len(argv[1:]):
            secret_file = argv[1:][i + 1]
    if len(args) != 2:
        print("Usage: python3 tools/generate_gem_codes.py <pack> <count> "
              "[--secret-file <path>]")
        print("  pack: 100 | 500 | 1000   count: 1-1000")
        return 2
    try:
        pack = int(args[0])
        count = int(args[1])
    except ValueError:
        print("ERROR: pack and count must be numbers", file=sys.stderr)
        return 2
    if pack not in server.GEM_PACKS:
        print("ERROR: unknown pack %d (use 100, 500 or 1000)" % pack,
              file=sys.stderr)
        return 2
    if not 1 <= count <= 1000:
        print("ERROR: count must be 1-1000", file=sys.stderr)
        return 2
    secret = load_secret(secret_file)
    if not secret:
        print("ERROR: no gem secret found — set GEM_CODE_SECRET, pass "
              "--secret-file, or keep the secret at %s" % DEFAULT_SECRET_FILE,
              file=sys.stderr)
        return 2
    seen = set()
    for _ in range(count):
        code = server.new_gem_code(secret, pack)
        while code in seen:  # مستحيل عملياً، لكن لا نطبع مكرراً أبداً
            code = server.new_gem_code(secret, pack)
        seen.add(code)
        print(code)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
