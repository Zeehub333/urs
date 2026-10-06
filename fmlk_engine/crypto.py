"""
FMLK Password Hashing & Cipher Algorithms Registry and Handlers.

Supports:
- "none": Plaintext (no encryption / match as-is).
- 20 historical-to-modern encryption/hashing algorithms:
  1. caesar (إزاحة سيزر / إزاحة يونيكود مخصصة)
  2. atbash (عكس الحروف أبجدياً)
  3. rot13 (إزاحة 13 دورية)
  4. vigenere (تشفير فيجينير متكرر)
  5. morse (ترميز مورس)
  6. base64 (ترميز Base64 القياسي)
  7. crc32 (فحص التكرار الدوري 32 بت)
  8. md5 (خوارزمية MD5 النجمية)
  9. sha1 (تجزئة SHA-1 الآمنة)
  10. ripemd160 (تجزئة RIPEMD-160 الأوروبية)
  11. mysql_old_sha1 (تشفير MySQL القديم PASSWORD)
  12. postgres_md5 (تشفير PostgreSQL md5+user)
  13. sha224 (تجزئة SHA-224)
  14. sha256 (تجزئة SHA-256 القياسية)
  15. sha384 (تجزئة SHA-384 فائقة الدقة)
  16. sha512 (تجزئة SHA-512 عالية الأمان)
  17. sha3_256 (معيار SHA-3 / Keccak-256)
  18. pbkdf2_sha1 (اشتقاق مفاتيح PBKDF2-HMAC-SHA1)
  19. pbkdf2_sha256 (معيار PBKDF2-HMAC-SHA256 المعتمد)
  20. scrypt (اشتقاق مقاوم للأجهزة المخصصة scrypt)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import zlib
from typing import Any, Dict, List, Optional

# ── قائمة الخوارزميات الـ 20 مرتبة تاريخياً من الأقدم إلى الأحدث ──
ALGORITHMS: List[Dict[str, Any]] = [
    {
        "key": "caesar",
        "ar": "1. إزاحة سيزر / يونيكود (Caesar / Shift Cipher - 100 ق.م)",
        "en": "Caesar / Unicode Shift",
        "era": "~100 BC",
        "type": "reversible",
        "description": "استبدال الأحرف بإزاحتها بعدد خانات يونيكود محدد (+ أو -).",
        "supports_shift": True,
        "default_shift": 3,
    },
    {
        "key": "atbash",
        "ar": "2. تشفير أتباش (Atbash Cipher - 500 ق.م)",
        "en": "Atbash Cipher",
        "era": "~500 BC",
        "type": "reversible",
        "description": "عكس ترتيب الحروف الأبجدية (الأول يقابل الأخير).",
        "supports_shift": False,
    },
    {
        "key": "rot13",
        "ar": "3. روت 13 (ROT13 - قيصر بنصف دورة)",
        "en": "ROT13",
        "era": "Classical",
        "type": "reversible",
        "description": "إزاحة 13 حرفاً في الأبجدية، التشفير وفك التشفير بالدالة نفسها.",
        "supports_shift": False,
    },
    {
        "key": "vigenere",
        "ar": "4. تشفير فيجينير (Vigenère Cipher - 1553م)",
        "en": "Vigenère Cipher",
        "era": "1553",
        "type": "reversible",
        "description": "تشفير استبدالي متعدد الأبجديات باستخدام مفتاح متكرر.",
        "supports_shift": False,
    },
    {
        "key": "morse",
        "ar": "5. شفرة مورس (Morse Code - 1836م)",
        "en": "Morse Code",
        "era": "1836",
        "type": "reversible",
        "description": "تمثيل الأحرف بسلسلة من النقاط والشرطات.",
        "supports_shift": False,
    },
    {
        "key": "base64",
        "ar": "6. ترميز بيس 64 (Base64 - RFC 4648)",
        "en": "Base64",
        "era": "1987",
        "type": "reversible",
        "description": "ترميز البيانات الثنائية والنصية إلى 64 محرفاً قابلاً للقراءة.",
        "supports_shift": False,
    },
    {
        "key": "crc32",
        "ar": "7. فحص التكرار الدوري (CRC-32 - 1975م)",
        "en": "CRC-32",
        "era": "1975",
        "type": "digest",
        "description": "كود فحص تكرار بطول 32-بت (8 خانات هيكس).",
        "supports_shift": False,
    },
    {
        "key": "md5",
        "ar": "8. إم دي 5 (MD5 - Rivest 1991م)",
        "en": "MD5",
        "era": "1991",
        "type": "hash",
        "description": "تجزئة 128-بت شائعة الاستخدام تاريخياً في قواعد البيانات.",
        "supports_shift": False,
    },
    {
        "key": "sha1",
        "ar": "9. إس إتش إيه 1 (SHA-1 - NSA 1995م)",
        "en": "SHA-1",
        "era": "1995",
        "type": "hash",
        "description": "تجزئة بطول 160-بت من وكالة الأمن القومي الأمريكية.",
        "supports_shift": False,
    },
    {
        "key": "ripemd160",
        "ar": "10. رايب إم دي (RIPEMD-160 - 1996م)",
        "en": "RIPEMD-160",
        "era": "1996",
        "type": "hash",
        "description": "تجزئة أوروبية بطول 160-بت مستخدمة أيضاً في شبكات البلوكتشين.",
        "supports_shift": False,
    },
    {
        "key": "mysql_old_sha1",
        "ar": "11. ماي إس كيو إل القديم (MySQL PASSWORD 4.1+)",
        "en": "MySQL Old PASSWORD()",
        "era": "2000",
        "type": "hash",
        "description": "تجزئة مزدوجة بصيغة * + UPPER(SHA1(SHA1(pw))).",
        "supports_shift": False,
    },
    {
        "key": "postgres_md5",
        "ar": "12. بوستجريس إم دي 5 (PostgreSQL md5+user)",
        "en": "PostgreSQL md5(password+user)",
        "era": "2001",
        "type": "hash",
        "description": "تجزئة md5(password + username) المعتمدة في مصادقة Postgres.",
        "supports_shift": False,
    },
    {
        "key": "sha224",
        "ar": "13. إس إتش إيه 224 (SHA-224 - 2004م)",
        "en": "SHA-224",
        "era": "2004",
        "type": "hash",
        "description": "نسخة مقتطعة 224-بت من عائلة SHA-2.",
        "supports_shift": False,
    },
    {
        "key": "sha256",
        "ar": "14. إس إتش إيه 256 (SHA-256 - 2001م/2004م)",
        "en": "SHA-256",
        "era": "2001",
        "type": "hash",
        "description": "تجزئة 256-بت آمنة وواسعة الانتشار.",
        "supports_shift": False,
    },
    {
        "key": "sha384",
        "ar": "15. إس إتش إيه 384 (SHA-384 - 2001م)",
        "en": "SHA-384",
        "era": "2001",
        "type": "hash",
        "description": "تجزئة 384-بت عالية الأمان من عائلة SHA-2.",
        "supports_shift": False,
    },
    {
        "key": "sha512",
        "ar": "16. إس إتش إيه 512 (SHA-512 - 2001م)",
        "en": "SHA-512",
        "era": "2001",
        "type": "hash",
        "description": "تجزئة 512-بت قوية جداً من عائلة SHA-2.",
        "supports_shift": False,
    },
    {
        "key": "sha3_256",
        "ar": "17. إس إتش إيه 3 (SHA-3 / Keccak-256 - 2015م)",
        "en": "SHA-3 (Keccak-256)",
        "era": "2015",
        "type": "hash",
        "description": "الجيل الثالث الأحدث من معايير التجزئة المعتمدة من NIST.",
        "supports_shift": False,
    },
    {
        "key": "pbkdf2_sha1",
        "ar": "18. بي بي كي دي إف 2 مع إس إتش إيه 1 (PBKDF2-SHA1 - PKCS#5)",
        "en": "PBKDF2-HMAC-SHA1",
        "era": "2000",
        "type": "kdf",
        "description": "اشتقاق بطيء بالمملح مع تكرار دورات الحماية.",
        "supports_shift": False,
    },
    {
        "key": "pbkdf2_sha256",
        "ar": "19. بي بي كي دي إف 2 مع إس إتش إيه 256 (PBKDF2-SHA256 - الافتراضي الحديث)",
        "en": "PBKDF2-HMAC-SHA256",
        "era": "2010+",
        "type": "kdf",
        "description": "المعيار الذهبي الحالي المعتمد في جانغو ومعايير الأمن.",
        "supports_shift": False,
    },
    {
        "key": "scrypt",
        "ar": "20. إس كربت (scrypt - Percival 2009م / RFC 7914)",
        "en": "scrypt (Memory-Hard)",
        "era": "2009",
        "type": "kdf",
        "description": "دالة اشتقاق حديثة مقاومة لهجمات عتاد التعدين عبر استهلاك الذاكرة.",
        "supports_shift": False,
    },
]

ALGORITHMS_BY_KEY = {a["key"]: a for a in ALGORITHMS}

# جدول مورس
MORSE_CODE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    ' ': '/',
}
MORSE_REVERSE = {v: k for k, v in MORSE_CODE.items()}


def list_password_algorithms() -> List[Dict[str, Any]]:
    """Return all 20 encryption/hashing methods from oldest to newest."""
    return [dict(a) for a in ALGORITHMS]


# ── تطبيق إزاحة اليونيكود (سيزر) ──
def shift_unicode(text: str, shift: int = 3) -> str:
    """Shift each character by shift codepoints (+ or -), wrapping inside Unicode range (0 to 0x10FFFF)."""
    if not text:
        return ""
    try:
        shift = int(shift)
    except Exception:
        shift = 3
    max_cp = 0x110000
    res = []
    for ch in text:
        cp = ord(ch)
        new_cp = (cp + shift) % max_cp
        res.append(chr(new_cp))
    return "".join(res)


def unshift_unicode(text: str, shift: int = 3) -> str:
    """Reverse unicode shift."""
    try:
        shift = int(shift)
    except Exception:
        shift = 3
    return shift_unicode(text, -shift)


# ── تشفير أتباش ──
def atbash_cipher(text: str) -> str:
    res = []
    for ch in text:
        if 'A' <= ch <= 'Z':
            res.append(chr(ord('Z') - (ord(ch) - ord('A'))))
        elif 'a' <= ch <= 'z':
            res.append(chr(ord('z') - (ord(ch) - ord('a'))))
        elif '\u0621' <= ch <= '\u064A':  # حروف عربية
            # إزاحة مرآة الحروف العربية الشائعة من أ إلى ي
            start, end = ord('\u0627'), ord('\u064A')
            cp = ord(ch)
            if start <= cp <= end:
                res.append(chr(end - (cp - start)))
            else:
                res.append(ch)
        else:
            res.append(ch)
    return "".join(res)


# ── تشفير روت 13 ──
def rot13_cipher(text: str) -> str:
    res = []
    for ch in text:
        if 'A' <= ch <= 'Z':
            res.append(chr((ord(ch) - 65 + 13) % 26 + 65))
        elif 'a' <= ch <= 'z':
            res.append(chr((ord(ch) - 97 + 13) % 26 + 97))
        else:
            res.append(ch)
    return "".join(res)


# ── تشفير فيجينير ──
def vigenere_cipher(text: str, key: str = "KEY", decrypt: bool = False) -> str:
    if not text:
        return ""
    key = (key or "KEY").upper()
    res = []
    ki = 0
    for ch in text:
        if 'A' <= ch <= 'Z':
            k_shift = ord(key[ki % len(key)]) - 65
            if decrypt:
                k_shift = -k_shift
            res.append(chr((ord(ch) - 65 + k_shift) % 26 + 65))
            ki += 1
        elif 'a' <= ch <= 'z':
            k_shift = ord(key[ki % len(key)]) - 65
            if decrypt:
                k_shift = -k_shift
            res.append(chr((ord(ch) - 97 + k_shift) % 26 + 97))
            ki += 1
        else:
            res.append(ch)
    return "".join(res)


# ── شفرة مورس ──
def morse_encode(text: str) -> str:
    tokens = []
    for ch in text.upper():
        if ch in MORSE_CODE:
            tokens.append(MORSE_CODE[ch])
        else:
            tokens.append(f"[{ord(ch)}]")
    return " ".join(tokens)


def morse_decode(text: str) -> str:
    words = text.split(" ")
    res = []
    for w in words:
        if not w:
            continue
        if w in MORSE_REVERSE:
            res.append(MORSE_REVERSE[w])
        elif w.startswith("[") and w.endswith("]"):
            try:
                res.append(chr(int(w[1:-1])))
            except Exception:
                res.append("?")
        else:
            res.append("?")
    return "".join(res)


# ── الدالة الرئيسية لتشفير كلمة المرور ──
def encrypt_or_hash_password(plain: str, algorithm: str = "pbkdf2_sha256", shift: int = 3, username: str = "") -> str:
    """
    Apply the chosen encryption/hashing algorithm to a plaintext password.

    algorithm:
      - "none": Return plain text as-is.
      - "caesar": Return caesar$<shift>$<shifted_text>
      - "atbash": Return atbash$<cipher_text>
      - "rot13": Return rot13$<cipher_text>
      - "vigenere": Return vigenere$<cipher_text>
      - "morse": Return morse$<cipher_text>
      - "base64": Return base64$<encoded>
      - "crc32": Return crc32$<8_hex>
      - "md5": Return md5$<salt>$<hex_or_hash>
      - "sha1": Return sha1$<salt>$<hex>
      - "ripemd160": Return ripemd160$<salt>$<hex>
      - "mysql_old_sha1": Return *<40_HEX>
      - "postgres_md5": Return md5<32_hex>
      - "sha224": Return sha224$<salt>$<hex>
      - "sha256": Return sha256$<salt>$<hex>
      - "sha384": Return sha384$<salt>$<hex>
      - "sha512": Return sha512$<salt>$<hex>
      - "sha3_256": Return sha3_256$<salt>$<hex>
      - "pbkdf2_sha1": Django standard pbkdf2_sha1$...
      - "pbkdf2_sha256": Django standard pbkdf2_sha256$...
      - "scrypt": Django / stdlib scrypt$...
    """
    s = "" if plain is None else str(plain)
    if not s:
        return ""
    algo = (algorithm or "pbkdf2_sha256").strip().lower()

    if algo in ("none", "plain", "clear", "plaintext", "لا شيء"):
        return s

    if algo in ("caesar", "shift"):
        try:
            sh = int(shift)
        except Exception:
            sh = 3
        shifted = shift_unicode(s, sh)
        # تشفير النص الناتج بـ base64 لضمان سلامة الأحرف غير المرئية أو المسافات عند الحفظ في قواعد البيانات
        b64_val = base64.b64encode(shifted.encode("utf-8")).decode("ascii")
        return f"caesar${sh}${b64_val}"

    if algo == "atbash":
        return f"atbash${atbash_cipher(s)}"

    if algo == "rot13":
        return f"rot13${rot13_cipher(s)}"

    if algo == "vigenere":
        return f"vigenere${vigenere_cipher(s, 'SECRET')}"

    if algo == "morse":
        return f"morse${morse_encode(s)}"

    if algo == "base64":
        b64 = base64.b64encode(s.encode("utf-8")).decode("ascii")
        return f"base64${b64}"

    if algo == "crc32":
        val = zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF
        return f"crc32${format(val, '08x')}"

    if algo == "md5":
        salt = os.urandom(8).hex()
        dk = hashlib.md5((salt + s).encode("utf-8")).hexdigest()
        return f"md5${salt}${dk}"

    if algo == "sha1":
        salt = os.urandom(8).hex()
        dk = hashlib.sha1((salt + s).encode("utf-8")).hexdigest()
        return f"sha1${salt}${dk}"

    if algo == "ripemd160":
        salt = os.urandom(8).hex()
        try:
            dk = hashlib.new("ripemd160", (salt + s).encode("utf-8")).hexdigest()
        except Exception:
            dk = hashlib.sha256((salt + s).encode("utf-8")).hexdigest()[:40]
        return f"ripemd160${salt}${dk}"

    if algo == "mysql_old_sha1":
        # MySQL PASSWORD() format: '*' + UPPER(SHA1(SHA1(s)))
        inner = hashlib.sha1(s.encode("utf-8")).digest()
        outer = hashlib.sha1(inner).hexdigest().upper()
        return f"*{outer}"

    if algo == "postgres_md5":
        # PostgreSQL md5 format: 'md5' + MD5(password + username)
        u = username or "postgres"
        dk = hashlib.md5((s + u).encode("utf-8")).hexdigest()
        return f"md5{dk}"

    if algo == "sha224":
        salt = os.urandom(8).hex()
        dk = hashlib.sha224((salt + s).encode("utf-8")).hexdigest()
        return f"sha224${salt}${dk}"

    if algo == "sha256":
        salt = os.urandom(8).hex()
        dk = hashlib.sha256((salt + s).encode("utf-8")).hexdigest()
        return f"sha256${salt}${dk}"

    if algo == "sha384":
        salt = os.urandom(8).hex()
        dk = hashlib.sha384((salt + s).encode("utf-8")).hexdigest()
        return f"sha384${salt}${dk}"

    if algo == "sha512":
        salt = os.urandom(8).hex()
        dk = hashlib.sha512((salt + s).encode("utf-8")).hexdigest()
        return f"sha512${salt}${dk}"

    if algo in ("sha3_256", "sha3"):
        salt = os.urandom(8).hex()
        dk = hashlib.sha3_256((salt + s).encode("utf-8")).hexdigest()
        return f"sha3_256${salt}${dk}"

    if algo == "pbkdf2_sha1":
        try:
            from django.contrib.auth.hashers import PBKDF2SHA1PasswordHasher
            return PBKDF2SHA1PasswordHasher().encode(s, os.urandom(12).hex())
        except Exception:
            salt = os.urandom(12).hex()
            dk = hashlib.pbkdf2_hmac("sha1", s.encode("utf-8"), salt.encode("utf-8"), 600000)
            b64dk = base64.b64encode(dk).decode("ascii")
            return f"pbkdf2_sha1$600000${salt}${b64dk}"

    if algo == "scrypt":
        try:
            from django.contrib.auth.hashers import ScryptPasswordHasher
            return ScryptPasswordHasher().encode(s, os.urandom(12).hex())
        except Exception:
            salt = os.urandom(12).hex()
            dk = hashlib.scrypt(s.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1)
            b64dk = base64.b64encode(dk).decode("ascii")
            return f"scrypt$16384${salt}$8$1${b64dk}"

    # الافتراضي: pbkdf2_sha256
    try:
        from django.contrib.auth.hashers import make_password
        return make_password(s)
    except Exception:
        salt = os.urandom(16).hex()
        dk = hashlib.pbkdf2_hmac("sha256", s.encode("utf-8"), bytes.fromhex(salt), 600000)
        return f"pbkdf2_sha256_std$600000${salt}${dk.hex()}"


def _safe_compare(a: Any, b: Any) -> bool:
    """Compare two strings in constant-time using UTF-8 bytes to support arbitrary Unicode."""
    try:
        sa = str(a if a is not None else "").encode("utf-8")
        sb = str(b if b is not None else "").encode("utf-8")
        return hmac.compare_digest(sa, sb)
    except Exception:
        return str(a or "") == str(b or "")


def is_known_hash_secret(value: Any) -> bool:
    """True if value matches any known hash/cipher format."""
    s = str(value or "").strip()
    if not s:
        return False
    _KNOWN_PFX = ("caesar$", "atbash$", "rot13$", "vigenere$", "morse$",
                  "base64$", "crc32$", "md5$", "sha1$", "ripemd160$",
                  "sha224$", "sha256$", "sha384$", "sha512$", "sha3_256$",
                  "pbkdf2_sha1$", "pbkdf2_sha256$", "scrypt$", "pbkdf2_sha256_std$")
    if any(s.startswith(pfx) for pfx in _KNOWN_PFX):
        return True
    if len(s) in (32, 40, 56, 64, 96, 128) and all(c in "0123456789abcdefABCDEF" for c in s):
        return True
    if s.startswith("*") and len(s) == 41:
        return True
    if s.lower().startswith("md5") and len(s) == 35:
        return True
    if "$" in s:
        try:
            from django.contrib.auth.hashers import identify_hasher
            identify_hasher(s)
            return True
        except Exception:
            pass
    return False


def decrypt_if_reversible(stored: str) -> str:
    """
    If stored password is encrypted using a reversible algorithm (caesar, atbash, rot13,
    vigenere, morse, base64), decrypt it to plaintext (for database driver connections).
    """
    s = str(stored or "")
    if not s:
        return ""
    if s.startswith("caesar$"):
        parts = s.split("$", 2)
        if len(parts) == 3:
            try:
                sh = int(parts[1])
            except Exception:
                sh = 3
            val = parts[2]
            try:
                dec_b64 = base64.b64decode(val.encode("ascii")).decode("utf-8")
                return unshift_unicode(dec_b64, sh)
            except Exception:
                return unshift_unicode(val, sh)
    if s.startswith("atbash$"):
        return atbash_cipher(s.split("$", 1)[1])
    if s.startswith("rot13$"):
        return rot13_cipher(s.split("$", 1)[1])
    if s.startswith("vigenere$"):
        return vigenere_cipher(s.split("$", 1)[1], "SECRET", decrypt=True)
    if s.startswith("morse$"):
        return morse_decode(s.split("$", 1)[1])
    if s.startswith("base64$"):
        try:
            return base64.b64decode(s.split("$", 1)[1].encode("ascii")).decode("utf-8")
        except Exception:
            return s
    return s


# ── التحقق والمطابقة عبر كافة الخوارزميات ──
def verify_password_algorithm(plain: str, stored: str, algorithm: str = "", shift: Optional[int] = None, username: str = "") -> bool:
    """
    Check if plain password matches stored value across all 20 algorithms or plaintext.
    Handles auto-detection if algorithm is not specified or stored format is self-describing.
    """
    p, s = str(plain or ""), str(stored or "").strip()
    if not p or not s:
        return False

    algo = (algorithm or "").strip().lower()

    # 1. حالة "لا شيء" (نص صريح)
    if algo in ("none", "plain", "clear", "plaintext", "لا شيء"):
        return _safe_compare(s, p)

    # إذا كانت القيمة المخزنة تطابق المدخل تماماً، نقبل فقط إذا لم تكن تجزئة
    if s == p and not is_known_hash_secret(s):
        return True

    # 2. فحص سيزر
    if s.startswith("caesar$"):
        parts = s.split("$", 2)
        if len(parts) == 3:
            try:
                c_shift = int(parts[1])
            except Exception:
                c_shift = 3
            expected = shift_unicode(p, c_shift)
            val = parts[2]
            try:
                dec_val = base64.b64decode(val.encode("ascii")).decode("utf-8")
                if _safe_compare(dec_val, expected):
                    return True
            except Exception:
                pass
            return _safe_compare(val, expected)
    elif algo in ("caesar", "shift"):
        sh = 3 if shift is None else int(shift)
        expected = shift_unicode(p, sh)
        if _safe_compare(s, expected):
            return True
        try:
            dec_s = base64.b64decode(s.encode("ascii")).decode("utf-8")
            if _safe_compare(dec_s, expected):
                return True
        except Exception:
            pass

    # 3. فحص أتباش
    if s.startswith("atbash$"):
        parts = s.split("$", 1)
        expected = atbash_cipher(p)
        return _safe_compare(parts[1], expected)
    elif algo == "atbash":
        if _safe_compare(s, atbash_cipher(p)):
            return True

    # 4. فحص روت 13
    if s.startswith("rot13$"):
        parts = s.split("$", 1)
        expected = rot13_cipher(p)
        return _safe_compare(parts[1], expected)
    elif algo == "rot13":
        if _safe_compare(s, rot13_cipher(p)):
            return True

    # 5. فحص فيجينير
    if s.startswith("vigenere$"):
        parts = s.split("$", 1)
        expected = vigenere_cipher(p, "SECRET")
        return _safe_compare(parts[1], expected)
    elif algo == "vigenere":
        if _safe_compare(s, vigenere_cipher(p, "SECRET")):
            return True

    # 6. فحص مورس
    if s.startswith("morse$"):
        parts = s.split("$", 1)
        expected = morse_encode(p)
        return _safe_compare(parts[1], expected)
    elif algo == "morse":
        if _safe_compare(s, morse_encode(p)):
            return True

    # 7. فحص Base64
    if s.startswith("base64$"):
        parts = s.split("$", 1)
        try:
            expected = base64.b64encode(p.encode("utf-8")).decode("ascii")
            return _safe_compare(parts[1], expected)
        except Exception:
            return False
    elif algo == "base64":
        try:
            expected = base64.b64encode(p.encode("utf-8")).decode("ascii")
            if _safe_compare(s, expected):
                return True
        except Exception:
            pass

    # 8. فحص CRC-32
    if s.startswith("crc32$"):
        parts = s.split("$", 1)
        val = zlib.crc32(p.encode("utf-8")) & 0xFFFFFFFF
        return _safe_compare(parts[1].lower(), format(val, '08x').lower())
    elif algo == "crc32":
        val = zlib.crc32(p.encode("utf-8")) & 0xFFFFFFFF
        if _safe_compare(s.lower(), format(val, '08x').lower()):
            return True

    # 9. فحص MD5 المملح أو الخام
    if s.startswith("md5$"):
        parts = s.split("$")
        if len(parts) == 3:  # md5$salt$hash
            salt, hval = parts[1], parts[2]
            cand = hashlib.md5((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.md5(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 10. فحص SHA-1
    if s.startswith("sha1$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha1((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 40 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.sha1(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 11. فحص RIPEMD-160
    if s.startswith("ripemd160$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            try:
                cand = hashlib.new("ripemd160", (salt + p).encode("utf-8")).hexdigest()
            except Exception:
                cand = hashlib.sha256((salt + p).encode("utf-8")).hexdigest()[:40]
            return _safe_compare(hval.lower(), cand.lower())

    # 12. فحص MySQL Old PASSWORD
    if s.startswith("*") and len(s) == 41:
        inner = hashlib.sha1(p.encode("utf-8")).digest()
        outer = "*" + hashlib.sha1(inner).hexdigest().upper()
        return _safe_compare(s.upper(), outer)

    # 13. فحص Postgres MD5
    if s.lower().startswith("md5") and len(s) == 35:
        u = username or "postgres"
        expected = "md5" + hashlib.md5((p + u).encode("utf-8")).hexdigest()
        if _safe_compare(s.lower(), expected.lower()):
            return True
        # جرب بدون اسم مستخدم
        if _safe_compare(s.lower(), "md5" + hashlib.md5(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 14. فحص SHA-224
    if s.startswith("sha224$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha224((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 56 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.sha224(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 15. فحص SHA-256
    if s.startswith("sha256$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha256((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.sha256(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 16. فحص SHA-384
    if s.startswith("sha384$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha384((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 96 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.sha384(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 17. فحص SHA-512
    if s.startswith("sha512$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha512((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())
    elif len(s) == 128 and all(c in "0123456789abcdefABCDEF" for c in s):
        if _safe_compare(s.lower(), hashlib.sha512(p.encode("utf-8")).hexdigest().lower()):
            return True

    # 18. فحص SHA3-256
    if s.startswith("sha3_256$"):
        parts = s.split("$")
        if len(parts) == 3:
            salt, hval = parts[1], parts[2]
            cand = hashlib.sha3_256((salt + p).encode("utf-8")).hexdigest()
            return _safe_compare(hval.lower(), cand.lower())

    # 19. فحص PBKDF2-SHA1
    if s.startswith("pbkdf2_sha1$"):
        try:
            from django.contrib.auth.hashers import PBKDF2SHA1PasswordHasher
            if PBKDF2SHA1PasswordHasher().verify(p, s):
                return True
        except Exception:
            pass

    # 20. فحص scrypt
    if s.startswith("scrypt$"):
        try:
            from django.contrib.auth.hashers import ScryptPasswordHasher
            if ScryptPasswordHasher().verify(p, s):
                return True
        except Exception:
            pass
        try:
            parts = s.split("$")
            # scrypt$n$salt$r$p$b64
            if len(parts) == 6:
                _n, _salt, _r, _pp, _b64 = int(parts[1]), parts[2], int(parts[3]), int(parts[4]), parts[5]
                cand_dk = hashlib.scrypt(p.encode("utf-8"), salt=_salt.encode("utf-8"), n=_n, r=_r, p=_pp)
                cand_b64 = base64.b64encode(cand_dk).decode("ascii")
                if _safe_compare(_b64, cand_b64):
                    return True
        except Exception:
            pass

    # 21. التحقق عبر جانغو القياسي / fallback
    try:
        from django.contrib.auth.hashers import check_password
        if check_password(p, s):
            return True
    except Exception:
        pass

    # فحص المقارنة المباشرة كحل أخير (في حال تم حفظ كلمة المرور كنص صريح فقط وليس تجزئة)
    if not is_known_hash_secret(s) and _safe_compare(s, p):
        return True

    return False

