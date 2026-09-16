#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""فتح مستندات my_docs المشفرة (PBKDF2 + AES-256-CBC).

الصيغة (ثنائية): MAGIC(4)=b'MDE1' | salt(16) | iv(16) | ciphertext | hmac(32, SHA256)
الخطوات: قراءة الترويسة ← اشتقاق المفتاح بـ PBKDF2 ← تحقق HMAC ← فك AES ←
عرض آمن (ملف مؤقت + المتصفح الافتراضي).

الاستخدام:
    python open_doc.py <id|مسار الملف> [--password P] [--no-browser] [--keep]

- id: اسم المستند في my_docs (بدون لاحقة)، مثال: python open_doc.py "تقرير_..._2026"
- كلمة المرور: --password أو تلقائياً SECRET_KEY من config/settings.py أو إدخال تفاعلي.
"""
import argparse
import getpass
import hmac
import hashlib
import os
import pathlib
import sys
import tempfile
import webbrowser

MAGIC = b"MDE1"
ROUNDS = 200_000
ROOT = pathlib.Path(__file__).resolve().parent
DOCS = ROOT / "my_docs"


def derive_keys(password: str, salt: bytes):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    raw = PBKDF2HMAC(algorithm=hashes.SHA256(), length=64, salt=salt,
                     iterations=ROUNDS).derive(password.encode("utf-8"))
    return raw[:32], raw[32:]


def decrypt_file(path: pathlib.Path, password: str) -> bytes:
    blob = path.read_bytes()
    if blob[:4] != MAGIC or len(blob) <= 4 + 16 + 16 + 32:
        raise ValueError("ليست صيغة MDE1 صالحة (ربما ملف Fernet قديم — افتحه من التطبيق لترحيله)")
    salt, iv, rest = blob[4:20], blob[20:36], blob[36:]
    ct, tag = rest[:-32], rest[-32:]
    enc_key, mac_key = derive_keys(password, salt)
    good = hmac.new(mac_key, MAGIC + salt + iv + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(good, tag):
        raise ValueError("HMAC غير صالح — الملف معدل أو كلمة المرور خاطئة")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def resolve_target(arg: str) -> pathlib.Path:
    p = pathlib.Path(arg)
    if p.suffix == ".enc" and p.exists():
        return p
    cand = DOCS / f"{arg}.html.enc"
    if cand.exists():
        return cand
    if p.exists():
        return p
    raise FileNotFoundError(f"المستند غير موجود: {arg}")


def get_password(cli_pw: str | None) -> str:
    if cli_pw:
        return cli_pw
    try:
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        django.setup()
        from django.conf import settings
        if getattr(settings, "SECRET_KEY", ""):
            return str(settings.SECRET_KEY)
    except Exception:
        pass
    return getpass.getpass("كلمة المرور: ")


def main() -> int:
    ap = argparse.ArgumentParser(description="فتح مستند my_docs مشفر")
    ap.add_argument("doc", help="id المستند أو مسار ملف .enc")
    ap.add_argument("--password", default=None, help="كلمة المرور (افتراضي: SECRET_KEY)")
    ap.add_argument("--no-browser", action="store_true", help="طباعة مسار الملف المؤقت فقط")
    ap.add_argument("--keep", action="store_true", help="إبقاء الملف المؤقت (افتراضي: يُحذف بعد Enter)")
    args = ap.parse_args()
    try:
        target = resolve_target(args.doc)
        password = get_password(args.password)
        raw = decrypt_file(target, password)
    except Exception as e:
        print(f"خطأ: {e}")
        return 1
    fd, tmp = tempfile.mkstemp(prefix="mydoc_", suffix=".html")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
    except Exception as e:
        print(f"تعذر كتابة المؤقت: {e}")
        return 1
    print(f"فُك التشفير ({len(raw)} بايت) ← {tmp}")
    if args.no_browser:
        return 0
    try:
        webbrowser.open(pathlib.Path(tmp).as_uri())
        print("فُتح في المتصفح الافتراضي.")
    except Exception as e:
        print(f"تعذر الفتح التلقائي: {e}")
    if not args.keep:
        try:
            input("Enter لحذف الملف المؤقت... ")
        except EOFError:
            pass
        try:
            os.unlink(tmp)
            print("حُذف المؤقت.")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
