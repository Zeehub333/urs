"""app.conf as source of truth for the default DB connection.

Standalone module (stdlib + cryptography only) so both config/settings.py
(import time, apps NOT ready) and urs/views.py can use it without cycles.

app.conf keys (KEY=VALUE, # comments):
  DB_ENGINE postgres|mysql|oracle   DB_HOST, DB_PORT, DB_USER,
  DB_PASS  (PBKDF2$ envelope, see below — legacy plaintext also accepted),
  DB_NAME, DB_SCHEMA

Password envelope: PBKDF2$<rounds>$<salt_hex>$<fernet_token>
  key = PBKDF2-HMAC-SHA256(secret=SECRET_KEY, salt, rounds, 32B) → Fernet.
  Authenticated: tamper/wrong SECRET → decrypt returns '' (never raises).
  SECRET rotation orphans stored envelopes → re-save via setup wizard step 3.
"""
from pathlib import Path

APPCONF_KEYS = ["PYTHON", "HOST", "PORT", "SETTINGS",
                "DB_ENGINE", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASS", "DB_NAME", "DB_SCHEMA",
                "DB_INSTANCENAME"]

DBPASS_ROUNDS = 200_000

ENGINE_BACKENDS = {
    "postgres": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
    "oracle": "django.db.backends.oracle",
    # sqlserver needs an external backend (e.g. django-mssql-backend) → not mapped on purpose
}
ENGINE_PORTS = {"postgres": 5432, "mysql": 3306, "oracle": 1521}


def base_dir():
    return Path(__file__).resolve().parent.parent


def read_appconf(path=None):
    """Parse app.config KEY=VALUE (skip # comments) → dict. Missing file → {}."""
    out = {}
    try:
        text = (Path(path) if path else base_dir() / "app.config").read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in APPCONF_KEYS:
            out[k] = v
    return out


def write_appconf(updates, path=None):
    """Upsert keys into app.config preserving comments/order. Returns bool."""
    updates = {k: str(v) for k, v in (updates or {}).items() if k in APPCONF_KEYS}
    try:
        p = Path(path) if path else base_dir() / "app.config"
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        else:
            lines = ["# Odex ERP - local run configuration",
                     "# Format: KEY=VALUE (no spaces around =). Lines starting with # are comments."]
        seen, out = set(), []
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in updates:
                    out.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            out.append(line)
        for k in APPCONF_KEYS:
            if k in updates and k not in seen:
                out.append(f"{k}={updates[k]}")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _secret_key(secret=None):
    """SECRET_KEY explicitly (settings-import time) or lazily from django.conf."""
    if secret:
        return secret
    try:
        from django.conf import settings as _s
        return getattr(_s, "SECRET_KEY", "") or ""
    except Exception:
        return ""


def _derive_key(secret: str, salt: bytes, rounds: int) -> bytes:
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2
    import base64 as _b64
    kdf = _PBKDF2(algorithm=_hashes.SHA256(), length=32, salt=salt,
                  iterations=int(rounds or DBPASS_ROUNDS))
    return _b64.urlsafe_b64encode(kdf.derive((_secret_key(secret)).encode("utf-8")))


def dbpass_encrypt(plain: str, secret=None) -> str:
    """Encrypt a DB password for app.conf storage. Empty → empty."""
    if not plain:
        return ""
    import os as _os
    from cryptography.fernet import Fernet as _Fernet
    salt = _os.urandom(16)
    token = _Fernet(_derive_key(secret, salt, DBPASS_ROUNDS)).encrypt(str(plain).encode("utf-8"))
    return f"PBKDF2${DBPASS_ROUNDS}${salt.hex()}${token.decode('ascii')}"


def dbpass_decrypt(token: str, secret=None) -> str:
    """Decrypt a PBKDF2$ envelope. Any failure → '' (never raises)."""
    try:
        parts = (token or "").split("$")
        if len(parts) != 4 or parts[0] != "PBKDF2":
            return ""
        rounds, salt_hex, fernet_token = int(parts[1]), parts[2], parts[3]
        if not (1_000 <= rounds <= 2_000_000):
            return ""
        from cryptography.fernet import Fernet as _Fernet
        key = _derive_key(secret, bytes.fromhex(salt_hex), rounds)
        return _Fernet(key).decrypt(fernet_token.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def dbpass_resolve(raw: str, secret=None) -> str:
    """Cleartext from an app.conf value: PBKDF2$ → decrypt, legacy plaintext → passthrough."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("PBKDF2$"):
        return dbpass_decrypt(raw, secret)
    return raw


def get_db_config(secret=None, path=None):
    """Build a Django DATABASES['default'] dict from app.conf DB_*.

    Returns None when app.conf has no DB_HOST (caller keeps its defaults) or the
    engine is unmapped. secret: pass SECRET_KEY explicitly at settings-import time;
    otherwise read lazily from django.conf.settings.
    """
    ac = read_appconf(path)
    host = (ac.get("DB_HOST") or "").strip()
    if not host:
        return None
    engine = ((ac.get("DB_ENGINE") or "postgres").strip().lower())
    backend = ENGINE_BACKENDS.get(engine)
    if not backend:
        return None
    secret = _secret_key(secret)
    try:
        port = int(ac.get("DB_PORT") or ENGINE_PORTS.get(engine, 5432))
    except (TypeError, ValueError):
        port = ENGINE_PORTS.get(engine, 5432)
    return {
        "ENGINE": backend,
        "NAME": (ac.get("DB_NAME") or "").strip() or "urs",
        "USER": (ac.get("DB_USER") or "").strip() or "postgres",
        "PASSWORD": dbpass_resolve(ac.get("DB_PASS") or "", secret),
        "HOST": host,
        "PORT": port,
    }


def has_db_details(path=None):
    """True when app.conf carries a DB_HOST (wizard grays out connection editing)."""
    try:
        return bool((read_appconf(path).get("DB_HOST") or "").strip())
    except Exception:
        return False
