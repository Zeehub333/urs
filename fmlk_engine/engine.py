"""
FMLK Form Engine â€” Production Ready
Inputting methods, SQL INSERT/UPDATE/DELETE builders, tabs/categories/positioning, records CRUD.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import re
from .compiler import FMLKFormCompiler, FMLKField
from .calc import eval_calc_row, calc_refs, make_row_getter, CalcError
try:
    from rml_python.oracle_engine import OracleEngine, _q
except ImportError:
    from .oracle_engine import OracleEngine  # type: ignore
    def _q(ident: str) -> str:
        return ".".join(f'"{p.replace(chr(34), chr(34)*2)}"' for p in ident.split("."))

def _valid_table_ident(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))


def _blank_incompatible(field) -> bool:
    """True when binding '' to this column would crash PG (int/date/bool/...).

    Empty strings are only storable in TEXT-ish columns; for the rest the
    builder must skip the column (â†’ NULL/DB default) instead of binding ''.
    Unknown/empty types stay TEXT-ish (legacy behavior preserved).
    """
    try:
        dt = str(getattr(field, "data_type", "") or "").upper().split("(")[0].strip()
    except Exception:
        return False
    if not dt:
        return False
    for _t in ("INT", "INTEGER", "BIGINT", "SMALLINT", "SERIAL", "BIGSERIAL",
               "NUMBER", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "MONEY",
               "BOOLEAN", "BOOL", "DATE", "TIME", "TIMESTAMP", "DATETIME",
               "TIMETZ", "TIMESTAMPTZ", "INTERVAL"):
        if dt == _t or dt.startswith(_t):
            return True
    return False


# â”€â”€ Secret hashing (password inputs are stored HASHED, never plaintext) â”€â”€
# Django hashers first (PBKDF2-HMAC-SHA256, verifiable via check_password);
# stdlib PBKDF2 fallback when Django auth hashers are unavailable.
# NOTE: the std prefix MUST stay distinct from Django's "pbkdf2_sha256$"
# namespace — otherwise Django hashes are mis-parsed as std hashes and
# Django's check_password is never reached (login always fails).
_STD_HASH_PREFIX = "pbkdf2_sha256_std$"
# Iteration count aligned with the reference FastAPI auth service
# (hash_password_pbkdf2). Stored per-hash, so older values keep verifying.
_STD_HASH_ITERS = 600_000


def is_hashed_secret(value: Any) -> bool:
    """True if the value already looks like a password hash (any Django
    hasher format or our stdlib fallback) â€” must NOT be re-hashed."""
    s = str(value or "")
    if not s or "$" not in s:
        return False
    if s.startswith(_STD_HASH_PREFIX):
        return True
    try:
        from django.contrib.auth.hashers import identify_hasher
        identify_hasher(s)
        return True
    except Exception:
        return False


def hash_secret(value: Any) -> str:
    """One-way hash for a new plaintext secret. Idempotent: existing
    hashes (and empty values) pass through unchanged."""
    s = "" if value is None else str(value)
    if s == "" or is_hashed_secret(s):
        return s
    try:
        from django.contrib.auth.hashers import make_password
        return make_password(s)
    except Exception:
        pass
    import hashlib as _hl
    import os as _os
    salt = _os.urandom(16).hex()
    dk = _hl.pbkdf2_hmac("sha256", s.encode("utf-8"), bytes.fromhex(salt), _STD_HASH_ITERS)
    return f"{_STD_HASH_PREFIX}{_STD_HASH_ITERS}${salt}${dk.hex()}"


def _pbkdf2_hex_candidates(password: str, salt: str, iters: int):
    """Possible derived keys for a hex-style pbkdf2_sha256 hash.

    Two dialects exist in the wild for the SAME "pbkdf2_sha256$i$s$hex"
    shape and MUST both be tried:
      - std (this system):  salt hex-DECODED to bytes
      - reference FastAPI auth service (hash_password_pbkdf2): salt as
        UTF-8 bytes (secrets.token_hex output used raw)
    Each candidate is an exact constant-time comparison — trying both
    loses no security; an attacker must still satisfy one full equation.
    """
    import hashlib as _hl
    out = []
    try:
        out.append(_hl.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   bytes.fromhex(salt), int(iters)).hex())
    except Exception:
        pass
    try:
        out.append(_hl.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   str(salt or "").encode("utf-8"), int(iters)).hex())
    except Exception:
        pass
    return out


def verify_secret(value: Any, hashed: Any) -> bool:
    """Check a plaintext candidate against a stored hash (Django or fallback).

    Django's check_password runs first so every Django hasher format
    (pbkdf2_sha256, argon2, bcrypt, ...) verifies via its own parser;
    hex-style pbkdf2_sha256 hashes (ours + the reference FastAPI service)
    verify via _pbkdf2_hex_candidates with constant-time compare
    (the reference uses plain == — timing-unsafe — we do better).
    """
    s, h = str(value or ""), str(hashed or "")
    if not s or not h:
        return False
    try:
        from django.contrib.auth.hashers import check_password
        if check_password(s, h):
            return True
    except Exception:
        pass
    import hmac as _hm
    if h.startswith(_STD_HASH_PREFIX):
        try:
            _, iters, salt, dkhex = h.split("$")
            for _cand in _pbkdf2_hex_candidates(s, salt, iters):
                if _hm.compare_digest(_cand, str(dkhex or "").lower()):
                    return True
            return False
        except Exception:
            return False
    # Foreign hex-style "pbkdf2_sha256$iters$salt$hexdigest" (reference
    # service shape: 32-hex salt, 64/128-hex digest). Django already said
    # no above, so only the dual-encoding PBKDF2 equation can still match.
    try:
        _parts = h.split("$")
        if (len(_parts) == 4 and _parts[0] == "pbkdf2_sha256"
                and _parts[3] and all(ch in "0123456789abcdefABCDEF" for ch in _parts[3])
                and len(_parts[3]) in (64, 128) and 1_000 <= int(_parts[1]) <= 5_000_000):
            for _cand in _pbkdf2_hex_candidates(s, _parts[2], _parts[1]):
                if _hm.compare_digest(_cand, _parts[3].lower()):
                    return True
    except Exception:
        pass
    try:
        from django.contrib.auth.hashers import check_password as _cp2
        return bool(_cp2(s, h))
    except Exception:
        return False


def get_options_source(table: str, column: str, schema: str = "", limit: int = 500, search: str | None = None, display: str | None = None, conn_params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Ù‚ÙŠÙ… Ù…Ù…ÙŠØ²Ø© Ù„Ø¹Ù…ÙˆØ¯ Ø¬Ø¯ÙˆÙ„ (Ù…Ø±Ø¬Ø¹ [table.column]) â€” Ù‚Ø±Ø§Ø¡Ø© ÙÙ‚Ø· Ø¨Ù…Ø¹Ø±ÙØ§Øª Ù…ÙØªØ­Ù‚Ù‚ Ù…Ù†Ù‡Ø§.

    display (Ø§Ø®ØªÙŠØ§Ø±ÙŠ): Ø¹Ù…ÙˆØ¯ Ø§Ù„Ø¹Ø±Ø¶ Ù„Ù„ØªØ³Ù…ÙŠØ© â€” Ø§Ù„Ù‚ÙŠÙ…Ø© Ù…Ù† column ÙˆØ§Ù„ØªØ³Ù…ÙŠØ© Ù…Ù†Ù‡.
    conn_params (Ø§Ø®ØªÙŠØ§Ø±ÙŠ): ÙˆØ³Ø§Ø¦Ø· psycopg2 Ù„Ù„Ø§ØªØµØ§Ù„ Ø§Ù„ØµØ­ÙŠØ­ (ÙˆØ¥Ù„Ø§ Ø§Ù„Ø§Ø­ØªÙŠØ§Ø·ÙŠ Ø§Ù„Ù‚Ø¯ÙŠÙ…).
    """
    if not _valid_table_ident(table) or not _valid_table_ident(column):
        raise ValueError("invalid table/column name")
    disp = (display or "").strip()
    if disp and not _valid_table_ident(disp):
        raise ValueError("invalid display column name")
    sch = schema.strip() if schema and _valid_table_ident(schema) else "public"
    lim = max(1, min(int(limit or 500), 1000))
    import psycopg2
    if conn_params:
        conn = psycopg2.connect(connect_timeout=5, **conn_params)
    else:
        conn = psycopg2.connect(dbname="urs", user="postgres", password="postgres", host="172.16.10.101", port=5432, connect_timeout=5)
    try:
        cur = conn.cursor()
        if disp and disp.lower() != column.lower():
            sql = f"SELECT DISTINCT {_q(column)}, {_q(disp)} FROM {_q(sch)}.{_q(table)} WHERE {_q(column)} IS NOT NULL"
            params: Dict[str, Any] = {}
            if search:
                sql += f" AND (CAST({_q(column)} AS TEXT) ILIKE %(s)s OR CAST({_q(disp)} AS TEXT) ILIKE %(s)s)"
                params["s"] = f"%{search}%"
            sql += f" ORDER BY 2 LIMIT {lim}"
            cur.execute(sql, params)
            out = [{"value": r[0], "label": (str(r[1]) if r[1] is not None else str(r[0]))} for r in cur.fetchall() if r[0] is not None]
        else:
            sql = f"SELECT DISTINCT {_q(column)} FROM {_q(sch)}.{_q(table)} WHERE {_q(column)} IS NOT NULL"
            params = {}
            if search:
                sql += f" AND CAST({_q(column)} AS TEXT) ILIKE %(s)s"
                params["s"] = f"%{search}%"
            sql += f" ORDER BY 1 LIMIT {lim}"
            cur.execute(sql, params)
            out = [{"value": r[0], "label": str(r[0])} for r in cur.fetchall() if r[0] is not None]
        cur.close()
    finally:
        conn.close()
    return out


SQL_FUNCTIONS = [
    # Ø­Ø³Ø§Ø¨ÙŠØ© ÙˆØªØ¬Ù…ÙŠØ¹ â€” ØªÙÙ‚Ø¨Ù„ ÙÙŠ Ø§Ù„ØµÙŠØº Ù…Ø¹ Ù…Ø±Ø§Ø¬Ø¹ [field]
    "ABS", "CEIL", "FLOOR", "ROUND", "TRUNC", "MOD", "POWER", "SQRT",
    "SUM", "AVG", "MIN", "MAX", "COUNT",
    "COALESCE", "NULLIF", "GREATEST", "LEAST",
    "UPPER", "LOWER", "TRIM", "LENGTH", "SUBSTRING", "CONCAT", "REPLACE",
    "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP", "EXTRACT",
    "CASE", "CAST",
]


def formula_refs(expr: str) -> List[str]:
    """Ø§Ø³ØªØ®Ø±Ø§Ø¬ Ø£Ø³Ù…Ø§Ø¡ Ø§Ù„Ø­Ù‚ÙˆÙ„ Ø§Ù„Ù…Ø±Ø¬Ø¹ÙŠØ© Ù…Ù† ØµÙŠØºØ© [field]."""
    if not expr:
        return []
    return re.findall(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", expr)


def formula_to_sql(expr: str) -> str:
    """ØªØ­ÙˆÙŠÙ„ [field] Ø¥Ù„Ù‰ binds :field â€” ØªØ¨Ù‚Ù‰ Ø¯ÙˆØ§Ù„ SQL ÙƒÙ…Ø§ Ù‡ÙŠ."""
    if not expr:
        return ""
    return re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", r":\1", expr)


class FMLKFormEngine:
    """
    Meta-Driven Dynamic FMLK Form Engine.
    Handles:
    - Input methods (text, number, date, select, file, phone, email, etc.)
    - SQL builders (INSERT, UPDATE, DELETE, SELECT)
    - Tabs & categories + field positioning (left/right/full, col_span)
    - Records: list, get, create, update, delete
    - Model designer: PK, nullable/editable, fixed defaults, formula [refs]
    """
    def __init__(self, compiler: FMLKFormCompiler, db_engine: OracleEngine):
        self.compiler = compiler
        self.metadata = compiler.fml_metadata()
        self.fields: List[FMLKField] = compiler.fields()
        self.tabs = compiler.tabs()
        self.db = db_engine
        self._table = self.metadata.get("table") or (self.fields[0].name.split(".")[0] if self.fields and "." in self.fields[0].name else "employees")
        # Map field name -> FMLKField for quick lookup
        self._field_map = {f.name: f for f in self.fields}

    # â”€â”€ Input Methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def input_types(self) -> Dict[str, List[str]]:
        """Group fields by input_type for UI rendering."""
        groups: Dict[str, List[str]] = {}
        for f in self.fields:
            groups.setdefault(f.input_type, []).append(f.name)
        return groups

    def validate(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Validate required + types + Validation Engine (regex/min_length/max_length/min/max). Returns {field: error}.

        Ø§Ù„Ø­Ù‚Ù„ Ø§Ù„Ù…Ø®ÙÙŠ (visibleIf ØºÙŠØ± Ù…Ø­Ù‚Ù‚ Ù„Ù‚ÙŠÙ… data) ÙŠÙØªØ¬Ø§Ù‡Ù„ ØªÙ…Ø§Ù…Ø§Ù‹ â€”
        Ø®Ø§ØµÙŠØ© required ØªØ³Ù‚Ø· Ø­Ø§Ù„ Ø§Ù„Ø¥Ø®ÙØ§Ø¡.
        """
        errors: Dict[str, str] = {}
        data = data or {}
        for f in self.fields:
            try:
                visible = f.is_visible(data) if hasattr(f, "is_visible") else True
            except Exception:
                visible = True
            if not visible:
                continue
            val = data.get(f.name)
            if f.required and not getattr(f, "display_only", False) and (val is None or str(val).strip() == ""):
                if (getattr(f, "formula", None) or "").strip() and str(getattr(f, "calc_mode", "default") or "default").lower() == "computed":
                    continue  # Ù…Ø­Ø³ÙˆØ¨ authoritative: ÙŠÙÙ…Ù„Ø£ Ø­Ø³Ø§Ø¨ÙŠØ§Ù‹ Ù‚Ø¨Ù„/Ø£Ø«Ù†Ø§Ø¡ Ø§Ù„Ø­ÙØ¸
                if bool(getattr(f, "serial", False)):
                    continue  # ØªØ³Ù„Ø³Ù„ÙŠ: ØªÙ…Ù„Ø¤Ù‡ Ø§Ù„Ù‚Ø§Ø¹Ø¯Ø© (ØªØ³Ù„Ø³Ù„) Ø¹Ù†Ø¯ Ø§Ù„Ø¥Ù†Ø´Ø§Ø¡
                errors[f.name] = f"{f.alias} Ù…Ø·Ù„ÙˆØ¨"
                continue  # skip further checks if empty required
            if val is None or str(val).strip() == "":
                continue
            sval = str(val)
            # basic type checks (keep first error)
            if f.input_type == "email" and f.name not in errors:
                _cfg_dom = str(((getattr(f, "config", {}) or {}).get("email_domain")) or "").strip().lstrip("@")
                if "@" not in sval and _cfg_dom:
                    sval = f"{sval}@{_cfg_dom}"
                    data[f.name] = sval
                if "@" not in sval:
                    errors[f.name] = "بريد إلكتروني غير صالح"
                elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sval):
                    errors[f.name] = "صيغة البريد الإلكتروني غير صالحة"
            if f.input_type == "number" and f.name not in errors:
                try: float(val)
                except: errors[f.name] = "ÙŠØ¬Ø¨ Ø£Ù† ÙŠÙƒÙˆÙ† Ø±Ù‚Ù…Ø§Ù‹"
            if f.input_type in ("phone", "phone_number") and f.name not in errors:
                _digits = re.sub(r"\D", "", sval)
                if len(_digits) < 6:
                    errors[f.name] = f"{f.alias} Ø±Ù‚Ù… Ù‡Ø§ØªÙ ØºÙŠØ± ØµØ§Ù„Ø­"
            # â”€â”€ Validation Engine â”€â”€
            rules = f.validation or {}
            # regex / pattern
            pattern = rules.get("pattern") or rules.get("regex")
            if pattern and f.name not in errors:
                try:
                    if not re.fullmatch(pattern, sval):
                        errors[f.name] = f"{f.alias} ØµÙŠØºØ© ØºÙŠØ± ØµØ­ÙŠØ­Ø©"
                except re.error:
                    pass  # invalid regex ignored
            # min_length / max_length (for strings)
            min_len = rules.get("min_length")
            if min_len is not None and f.name not in errors:
                try:
                    if len(sval) < int(min_len):
                        errors[f.name] = f"{f.alias} ÙŠØ¬Ø¨ Ø£Ù„Ø§ ÙŠÙ‚Ù„ Ø¹Ù† {min_len} Ø­Ø±Ù"
                except: pass
            max_len = rules.get("max_length")
            if max_len is not None and f.name not in errors:
                try:
                    if len(sval) > int(max_len):
                        errors[f.name] = f"{f.alias} ÙŠØ¬Ø¨ Ø£Ù„Ø§ ÙŠØ²ÙŠØ¯ Ø¹Ù† {max_len} Ø­Ø±Ù"
                except: pass
            # numeric min / max
            min_v = rules.get("min")
            if min_v is not None and f.name not in errors:
                try:
                    if float(val) < float(min_v):
                        errors[f.name] = f"{f.alias} ÙŠØ¬Ø¨ Ø£Ù† ÙŠÙƒÙˆÙ† â‰¥ {min_v}"
                except: pass
            max_v = rules.get("max")
            if max_v is not None and f.name not in errors:
                try:
                    if float(val) > float(max_v):
                        errors[f.name] = f"{f.alias} ÙŠØ¬Ø¨ Ø£Ù† ÙŠÙƒÙˆÙ† â‰¤ {max_v}"
                except: pass
        return errors

    # â”€â”€ Foreign Keys: dynamic lookup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def get_field_lookup(self, field_name: str, limit: int = 100, search: str | None = None) -> Dict[str, Any]:
        """Fetch reference data for a field with ref_table/ref_fk/ref_display. Used by /api/fmlk/lookup."""
        f = self._field_map.get(field_name)
        if not f:
            raise ValueError(f"Field '{field_name}' not found")
        # static options first (plain strings or {value,label} dicts)
        if f.options:
            opts = [(o if isinstance(o, dict) else {"value": o, "label": o}) for o in f.options]
            if search:
                opts = [o for o in opts if search.lower() in str(o["label"]).lower()]
            return {"field": field_name, "refTable": f.ref_table, "options": opts[:limit], "source": "static"}
        if not f.ref_table:
            raise ValueError(f"Field '{field_name}' has no ref_table / static options")
        fk = f.ref_fk or "id"
        disp = f.ref_display or "name"
        table_q = _q(f.ref_table)
        # handle schema prefix if field name contains schema? keep simple
        sql = f"SELECT {_q(fk)} AS value, {_q(disp)} AS label FROM {table_q}"
        params: Dict[str, Any] = {}
        if search:
            sql += f" WHERE LOWER({_q(disp)}) LIKE :search"
            params["search"] = f"%{search.lower()}%"
        sql += " FETCH NEXT :lim ROWS ONLY"
        params["lim"] = limit
        # Oracle vs generic: try Oracle FETCH, fallback to ROWNUM
        try:
            if not self.db.conn:
                self.db.connect()
            cur = self.db._exec(sql, params)
            try:
                cols = [d[0].lower() for d in cur.description] if cur.description else ["value", "label"]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
            finally:
                try: cur.close()
                except: pass
            return {"field": field_name, "refTable": f.ref_table, "refFk": fk, "refDisplay": disp, "options": rows, "source": "db", "sql": sql}
        except Exception as e:
            # fallback: try ROWNUM for older Oracle or mock
            try:
                sql2 = f"SELECT * FROM (SELECT {_q(fk)} AS value, {_q(disp)} AS label FROM {table_q} WHERE ROWNUM <= :lim)"
                # ignore search for fallback simplicity
                cur = self.db._exec(sql2, {"lim": limit})
                try:
                    cols = [d[0].lower() for d in cur.description] if cur.description else ["value", "label"]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                finally:
                    try: cur.close()
                    except: pass
                return {"field": field_name, "refTable": f.ref_table, "options": rows, "source": "db_fallback", "sql": sql2}
            except Exception as e2:
                raise RuntimeError(f"Lookup failed for {field_name} ({f.ref_table}): {e} / {e2}") from e

    def list_lookups(self) -> List[Dict[str, Any]]:
        """List all FK fields with their ref endpoints â€” for frontend to prefetch."""
        out = []
        for f in self.fields:
            if f.ref_table:
                out.append({"field": f.name, "alias": f.alias, "refTable": f.ref_table, "refFk": f.ref_fk, "refDisplay": f.ref_display, "endpoint": f"/api/fmlk/lookup?field={f.name}"})
        return out

    # â”€â”€ SQL Builders (secure _q, binds) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def apply_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ù…Ù„Ø¡ Ø§Ù„Ù‚ÙŠÙ… Ø§Ù„Ø§ÙØªØ±Ø§Ø¶ÙŠØ©: Ø«Ø§Ø¨ØªØ© Ø£ÙˆÙ„Ø§Ù‹ØŒ Ø«Ù… Ø§Ù„ØµÙŠØº Ù„Ù…Ù† ØªØ±Ùƒ ÙØ§Ø±ØºØ§Ù‹ (ÙŠØ¯ÙˆÙŠ Ø¥Ù† Ù„Ø§ ØµÙŠØºØ©)."""
        out = dict(data or {})
        for f in self.fields:
            v = out.get(f.name)
            empty = v is None or (isinstance(v, str) and v.strip() == "")
            if not empty:
                continue
            if f.default is not None and str(f.default) != "":
                out[f.name] = f.default
            # formula ØªÙØªØ±Ùƒ ÙØ§Ø±ØºØ© Ù„Ù„Ø¥Ø¯Ø®Ø§Ù„ Ø§Ù„ÙŠØ¯ÙˆÙŠ Ù…Ø§ Ù„Ù… ØªÙØ­Ø³Ø¨ ÙÙŠ SQL
        return out

    def _table_columns_info(self) -> Dict[str, Dict[str, Any]]:
        """Ø£Ø¹Ù…Ø¯Ø© Ø§Ù„Ø¬Ø¯ÙˆÙ„ {name: {nullable, has_default, generic}} â€” Ù…Ø®Ø²Ù‘Ù† Ù…Ø¤Ù‚ØªØ§Ù‹ØŒ Ø¢Ù…Ù† Ø§Ù„ÙØ´Ù„."""
        try:
            cache = self.__dict__.setdefault("_cols_info_cache", {})
            key = f"{self.metadata.get('schema')}.{self._table}".lower()
            if key not in cache:
                info: Dict[str, Dict[str, Any]] = {}
                sch = (self.metadata.get("schema") or "").strip()
                tbl = (self._table or "").split(".")[-1]
                try:
                    if not getattr(self.db, "conn", None):
                        self.db.connect()
                except Exception:
                    pass
                try:
                    cur = self.db._exec(
                        "SELECT column_name, is_nullable, column_default, data_type FROM information_schema.columns WHERE table_schema = :sch AND table_name = :tbl",
                        {"sch": sch or "public", "tbl": tbl})
                    try:
                        for r in cur.fetchall():
                            dt = str(r[3] or "").lower().split("(")[0]
                            if dt in ("character varying", "varchar", "character", "char", "text", "name"):
                                gen = "TEXT"
                            elif dt == "boolean":
                                gen = "BOOLEAN"
                            else:
                                gen = "OTHER"
                            info[str(r[0]).lower()] = {"real": str(r[0]), "nullable": str(r[1]).upper() != "NO",
                                                       "has_default": r[2] is not None, "generic": gen}
                    finally:
                        try:
                            cur.close()
                        except Exception:
                            pass
                except Exception:
                    info = {}
                cache[key] = info
            return cache.get(key, {})
        except Exception:
            return {}

    def _table_has_column(self, column: str) -> bool:
        """Ù‡Ù„ ÙŠÙˆØ¬Ø¯ Ø¹Ù…ÙˆØ¯ ÙÙŠ Ø¬Ø¯ÙˆÙ„ Ø§Ù„Ù†Ù…ÙˆØ°Ø¬ØŸ"""
        try:
            return column.lower() in self._table_columns_info()
        except Exception:
            return False

    def _formula_fields(self) -> List[Any]:
        """Fields with a non-empty formula."""
        try:
            return [f for f in (self.fields or []) if (getattr(f, "formula", None) or "").strip()]
        except Exception:
            return []

    def _apply_formulas(self, data: Dict[str, Any], require_refs: bool = False) -> Tuple[Dict[str, Any], set]:
        """Recompute XSQL formulas in Python (authoritative static values).

        computed-mode fields are ALWAYS recomputed (incoming values ignored);
        default-mode (legacy) fields are untouched here (old SQL-embed path).
        With require_refs=True (partial updates) a field is recomputed only
        when all its refs exist in data â€” otherwise the stored value survives.
        Returns (data, failed): failed ones keep the legacy SQL-embed behavior.
        Never raises.
        """
        try:
            data = self.apply_defaults(dict(data or {}))
        except Exception:
            data = dict(data or {})
        failed: set = set()
        try:
            fields = self._formula_fields()
        except Exception:
            return data, failed
        for f in fields:
            try:
                mode = str(getattr(f, "calc_mode", "default") or "default").lower()
            except Exception:
                mode = "default"
            if mode != "computed":
                continue
            fx = (getattr(f, "formula", None) or "").strip()
            if not fx:
                continue
            if require_refs:
                try:
                    refs = calc_refs(fx)
                except Exception:
                    continue
                if any(r not in (data or {}) for r in refs):
                    continue
            try:
                data[f.name] = eval_calc_row(fx, data)
            except Exception:
                failed.add(f.name)
        return data, failed

    def _build_insert(self, data: Dict[str, Any], calc_done: set | frozenset = frozenset()) -> Tuple[str, Dict[str, Any]]:
        """Build INSERT with binds + fixed defaults + formula SQL ([refs] â†’ binds).

        calc_done: fields already evaluated in Python (even to None) â€” their
        static value is stored, never re-embedded as SQL.
        """
        data = self.apply_defaults(data)
        cols: List[str] = []
        binds: List[str] = []
        params: Dict[str, Any] = {}
        try:
            _disp_ins = {getattr(f, "name", "") for f in (self.fields or []) if getattr(f, "display_only", False)}
        except Exception:
            _disp_ins = set()
        try:
            _done = set(calc_done or ())
        except Exception:
            _done = set()
        for f in self.fields:
            if f.name in _disp_ins:
                continue  # Ø¹Ø±Ø¶ ÙÙ‚Ø· â€” Ù„Ø§ ÙŠÙØ®Ø²Ù†
            formula = (getattr(f, "formula", None) or "").strip()
            has_val = f.name in data and not (data[f.name] is None or (isinstance(data[f.name], str) and data[f.name].strip() == ""))
            if f.name in _done and f.name not in _disp_ins:
                # Ù…Ø­Ø³ÙˆØ¨ Ø¨Ø§ÙŠØ«ÙˆÙ†: ÙŠÙØ®Ø²Ù† static (Ø­ØªÙ‰ None â†’ NULL) Ø¨Ù„Ø§ ØªØ¶Ù…ÙŠÙ† SQL
                if f.name in data and data[f.name] is not None:
                    cols.append(_q(f.name))
                    binds.append(f":{f.name}")
                    params[f.name] = data[f.name]
                elif f.name in data:
                    cols.append(_q(f.name))
                    binds.append("NULL")
                else:
                    continue
                continue
            if formula and not has_val:
                # ØµÙŠØºØ© Ø­Ø³Ø§Ø¨ÙŠØ©: ØªÙÙ†ÙØ° ÙÙŠ DB Ø¨ÙƒÙ„ Ø¯ÙˆØ§Ù„ SQL â€” Ù…Ø±Ø§Ø¬Ø¹ [f] ØªØµØ¨Ø­ binds
                cols.append(_q(f.name))
                binds.append(f"({formula_to_sql(formula)})")
                for ref in formula_refs(formula):
                    if ref in data:
                        params[ref] = data[ref]
                continue
            if f.name not in data:
                continue
            if f.name in data:
                if data[f.name] is None:
                    continue  # ÙŠÙØªØ±Ùƒ Ù„Ù…Ù„Ø¡ Ø§Ù„Ø£Ø¹Ù…Ø¯Ø© Ø§Ù„ØªÙ„Ù‚Ø§Ø¦ÙŠ Ø£Ùˆ NULL/Ø§Ù„Ø§ÙØªØ±Ø§Ø¶ÙŠ
                if isinstance(data[f.name], str) and data[f.name].strip() == "" \
                        and _blank_incompatible(f):
                    continue  # ÙØ§Ø±Øº Ù„Ø±Ù‚Ù…ÙŠ/ØªØ§Ø±ÙŠØ®/Ù…Ù†Ø·Ù‚ÙŠ â†’ ÙŠÙØ­Ø°Ù (NULL) Ø¨Ø¯Ù„ invalid input syntax
                cols.append(_q(f.name))
                binds.append(f":{f.name}")
                params[f.name] = data[f.name]
        if not cols:
            raise ValueError("No valid columns for INSERT")
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        # Ø¥ÙƒÙ…Ø§Ù„ Ø£Ø¹Ù…Ø¯Ø© Ø§Ù„Ø¬Ø¯ÙˆÙ„ Ø§Ù„ØºØ§Ø¦Ø¨Ø© Ø¹Ù† Ø§Ù„Ù†Ù…ÙˆØ°Ø¬: Ø·ÙˆØ§Ø¨Ø¹ Ø²Ù…Ù†ÙŠØ© â†’ CURRENT_TIMESTAMPØŒ
        # ÙˆÙ†Øµ NOT NULL Ø¨Ù„Ø§ Ø§ÙØªØ±Ø§Ø¶ÙŠ â†’ '' ØŒ ÙˆÙ…Ù†Ø·Ù‚ÙŠ â†’ FALSE (ÙŠØ¹Ù…Ù„ Ø¹Ù„Ù‰ Postgres Ùˆ Oracle)
        try:
            covered = set()
            for _c in cols:
                covered.add(_c.strip('"').lower())
            for _k, _v in (params or {}).items():
                covered.add(str(_k).lower())
            for _k, _v in (data or {}).items():
                if _v is not None and not (isinstance(_v, str) and _v.strip() == ""):
                    covered.add(str(_k).lower())
            for cname, ci in self._table_columns_info().items():
                if cname in covered or ci.get("nullable") or ci.get("has_default"):
                    continue
                real = ci.get("real") or cname
                if cname in ("created_at", "updated_at"):
                    cols.append(_q(real))
                    binds.append("CURRENT_TIMESTAMP")
                    covered.add(cname)
                elif ci.get("generic") == "TEXT":
                    cols.append(_q(real))
                    binds.append(f":{cname}")
                    params[cname] = ""
                    covered.add(cname)
                elif ci.get("generic") == "BOOLEAN":
                    cols.append(_q(real))
                    binds.append(f":{cname}")
                    params[cname] = False
                    covered.add(cname)
        except Exception:
            pass
        sql = f"INSERT INTO {table_q} ({', '.join(cols)}) VALUES ({', '.join(binds)})"
        return sql, params

    def _build_update(self, pk: Dict[str, Any], data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Build UPDATE with WHERE pk."""
        sets = []
        params: Dict[str, Any] = {}
        try:
            _disp = {getattr(f, "name", "") for f in (self.fields or []) if getattr(f, "display_only", False)}
        except Exception:
            _disp = set()
        try:
            _fmap = {getattr(f, "name", ""): f for f in (self.fields or [])}
        except Exception:
            _fmap = {}
        for k, v in data.items():
            if k in pk:
                continue
            if k in _disp:
                continue  # Ø¹Ø±Ø¶ ÙÙ‚Ø· â€” Ù„Ø§ ÙŠÙÙƒØªØ¨ Ø£Ø¨Ø¯Ø§Ù‹
            if isinstance(v, str) and v.strip() == "" and _blank_incompatible(_fmap.get(k)):
                sets.append(f"{_q(k)}=NULL")  # Ù…Ø³Ø­ Ø±Ù‚Ù…ÙŠ/ØªØ§Ø±ÙŠØ®/Ù…Ù†Ø·Ù‚ÙŠ â†’ NULL Ø¨Ø¯Ù„ invalid input syntax
                continue
            sets.append(f"{_q(k)}=:{k}")
            params[k] = v
        if not sets:
            raise ValueError("No columns to update")
        where_parts = []
        for k, v in pk.items():
            where_parts.append(f"{_q(k)}=:pk_{k}")
            params[f"pk_{k}"] = v
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        sql = f"UPDATE {table_q} SET {', '.join(sets)} WHERE {' AND '.join(where_parts)}"
        return sql, params

    def _build_delete(self, pk: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        where_parts = []
        params: Dict[str, Any] = {}
        for k, v in pk.items():
            where_parts.append(f"{_q(k)}=:pk_{k}")
            params[f"pk_{k}"] = v
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        sql = f"DELETE FROM {table_q} WHERE {' AND '.join(where_parts)}"
        return sql, params

    def _build_select(self, filters: List[Dict[str, Any]] | None = None, order_by: str | None = None, limit: int | None = None, offset: int = 0, include_pk: bool = True) -> Tuple[str, Dict[str, Any]]:
        # Build SELECT with column aliases
        select_parts = []
        for f in self.fields:
            # Handle fk_lookup as subquery
            if f.ref_table and f.ref_fk and f.ref_display:
                sub = f"(SELECT {_q(f.ref_display)} FROM {_q(f.ref_table)} WHERE {_q(f.ref_fk)} = {_q(f.name)})"
                select_parts.append(f"{sub} AS {_q(f.alias)}")
            else:
                # Direct
                if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', f.name):
                    select_parts.append(f"{_q(f.name)} AS {_q(f.alias)}")
                else:
                    select_parts.append(f"{f.name} AS {_q(f.alias)}")
        # Always expose the PK so the UI can address rows (ignored if table has no id column)
        if include_pk:
            select_parts.append(f'{_q("id")} AS {_q("__pk_id")}')
        select_clause = ", ".join(select_parts) if select_parts else "*"
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        # WHERE from filters (same as RML)
        where_sql = ""
        params: Dict[str, Any] = {}
        if filters:
            from rml_python.engine import _build_where
            where_sql, params = _build_where(filters, fields=self.fields)
        order_sql = f" ORDER BY {_q(order_by)}" if order_by else ""
        # Pagination
        paginate = ""
        if limit is not None:
            paginate = " OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY"
            params["off"] = offset
            params["lim"] = limit
        sql = f"SELECT {select_clause} FROM {table_q}{where_sql}{order_sql}{paginate}"
        return sql, params

    # â”€â”€ Records CRUD â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def _missing_pk_error(e: Exception) -> bool:
        # Only fall back when the *id* column itself is missing (not a lookup-table error)
        msg = str(e).lower()
        has_id = '"id"' in msg or "'id'" in msg or " id " in msg or msg.strip().endswith("id")
        return has_id and any(k in msg for k in ("does not exist", "no such column", "invalid identifier", "ora-00904"))

    def list_records(self, filters: List[Dict[str, Any]] | None = None, page: int = 1, page_size: int = 50, order_by: str | None = None) -> Dict[str, Any]:
        """List records with pagination â€” for Records showing."""
        limit = None if page_size == "all" else int(page_size)
        offset = (page - 1) * (limit or 0) if limit else 0
        # Stable order: without ORDER BY the DB returns heap order,
        # so an edited row sinks last â€” default to PK (or first field).
        auto_order = not order_by
        if auto_order:
            try:
                order_by = (self.primary_key_fields() or [None])[0]
            except Exception:
                order_by = None
            if not order_by:
                try:
                    order_by = (getattr(self.fields[0], "name", None) if self.fields else None)
                except Exception:
                    order_by = None
        try:
            sql, params = self._build_select(filters, order_by, limit, offset, include_pk=True)
            # Probe: if the table has no id column, fall back to a PK-less select
            return self._list_records_exec(sql, params, filters, page, limit)
        except Exception as e:
            if self._missing_pk_error(e):
                sql, params = self._build_select(filters, order_by, limit, offset, include_pk=False)
                return self._list_records_exec(sql, params, filters, page, limit)
            if auto_order:
                # Ø¹Ù…ÙˆØ¯ Ø§Ù„ØªØ±ØªÙŠØ¨ Ø§Ù„ØªÙ„Ù‚Ø§Ø¦ÙŠ ØºÙŠØ± Ù…ÙˆØ¬ÙˆØ¯ â€” Ø£Ø¹Ø¯ Ø¨Ø¯ÙˆÙ† ØªØ±ØªÙŠØ¨
                try:
                    sql, params = self._build_select(filters, None, limit, offset, include_pk=True)
                    return self._list_records_exec(sql, params, filters, page, limit)
                except Exception:
                    pass
            raise

    # â”€â”€ Secret masking (passwords never leave the list endpoint in cleartext) â”€â”€
    SECRET_NAMES = {"password", "passwd", "pwd", "password_hash", "pass_hash",
                      "pass", "user_password", "pwd_hash",
                      "secret", "secret_key", "api_key", "token"}

    def _secret_aliases(self) -> List[str]:
        """Lowercased output aliases of secret fields (inputType=password or secret name)."""
        out = []
        for f in (self.fields or []):
            try:
                it = str(getattr(f, "input_type", "") or "").lower()
                nm = str(getattr(f, "name", "") or "").lower()
            except Exception:
                continue
            if it == "password" or nm in self.SECRET_NAMES:
                out.append(str(getattr(f, "alias", "") or getattr(f, "name", "")).lower())
        return out

    def _secret_names(self) -> set:
        """Field names (DB columns) considered secret."""
        out = set()
        for f in (self.fields or []):
            try:
                it = str(getattr(f, "input_type", "") or "").lower()
                nm = str(getattr(f, "name", "") or "").lower()
            except Exception:
                continue
            if it == "password" or nm in self.SECRET_NAMES:
                out.add(getattr(f, "name", ""))
        return {x for x in out if x}

    @staticmethod
    def _drop_masked_secrets(data: Dict[str, Any], secret_names: set) -> Dict[str, Any]:
        """Drop secret fields whose value is a stars-run (display mask, not a real value)."""
        if not data or not secret_names:
            return dict(data or {})
        import re as _re2
        out = {}
        for k, v in (data or {}).items():
            if k in secret_names and isinstance(v, str) and _re2.fullmatch(r"\*+", v or ""):
                continue
            out[k] = v
        return out

    def _hash_secrets(self, data: Dict[str, Any], secret_names: set) -> Dict[str, Any]:
        """Replace new plaintext secrets with HASH (idempotent: hashes/empties pass through)."""
        if not data or not secret_names:
            return dict(data or {})
        tbl = str(getattr(self, "table", "") or "").lower()
        if tbl.endswith("urs_connection"):
            return dict(data or {})
        out = dict(data)
        for k in secret_names:
            if k in out:
                out[k] = hash_secret(out[k])
        return out

    def _mask_secrets(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace secret values with random-length '*' runs (length leaks nothing)."""
        try:
            aliases = self._secret_aliases()
        except Exception:
            return rows
        if not aliases or not rows:
            return rows
        import random as _rnd
        for r in rows:
            if not isinstance(r, dict):
                continue
            for a in aliases:
                if a in r and r[a] not in (None, ""):
                    r[a] = "*" * _rnd.randint(8, 14)
        return rows

    def _list_records_exec(self, sql: str, params: Dict[str, Any], filters: List[Dict[str, Any]] | None, page: int, limit: int | None) -> Dict[str, Any]:
        # Count
        count_sql, count_params = self._build_select(filters, None, None, 0, include_pk=False)
        # Count
        count_sql, count_params = self._build_select(filters, None, None, 0)
        # Transform to COUNT(*)
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        where_sql = ""
        if filters:
            from rml_python.engine import _build_where
            where_sql, _ = _build_where(filters, fields=self.fields)
        count_sql = f"SELECT COUNT(*) as cnt FROM {table_q}{where_sql}"
        # Execute
        try:
            if not self.db.conn:
                self.db.connect()
            cur = self.db._exec(count_sql, {k: v for k, v in params.items() if k not in ("off", "lim")})
            try:
                total = cur.fetchone()[0] if cur.description else 0
            finally:
                try: cur.close()
                except: pass
            cur = self.db._exec(sql, params)
            try:
                cols = [d[0].lower() for d in cur.description] if cur.description else []
                rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
            finally:
                try: cur.close()
                except: pass
            rows = self._mask_secrets(rows)
            return {"rows": rows, "total": total, "page": page, "pageSize": limit or total, "sql": sql, "params": params}
        except Exception as e:
            try:
                if self.db.conn:
                    self.db.conn.rollback()
            except: pass
            raise RuntimeError(f"List records failed: {e}\nSQL: {sql}") from e

    def get_record(self, pk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get single record by PK (e.g., {"id": 1})."""
        # Build SELECT with WHERE pk
        filters = [{"field": k, "op": "equals", "value": v} for k, v in pk.items()]
        res = self.list_records(filters=filters, page=1, page_size=1)
        return res["rows"][0] if res["rows"] else None

    def _serial_names(self) -> set:
        """Serial main fields (auto-numbered, immutable after creation)."""
        try:
            return {f.name for f in (self.fields or []) if bool(getattr(f, "serial", False))}
        except Exception:
            return set()

    def _serial_next(self, field: str) -> int:
        """COUNT(*)+1 for this form's table. Raises (Arabic) on any problem."""
        if not _valid_table_ident(field or ""):
            raise ValueError("invalid field")
        try:
            tbl = getattr(self, "_table", "") or ""
            md = getattr(self, "metadata", None)
            md = dict(md) if isinstance(md, dict) else {}
        except Exception:
            tbl, md = "", {}
        sch = (md.get("schema") or "public").strip() or "public"
        if "." in tbl:
            try:
                sch, tbl = tbl.split(".", 1)
            except Exception:
                pass
        if not tbl or not _valid_table_ident(tbl):
            raise ValueError("invalid table")
        db = getattr(self, "db", None)
        if db is None:
            raise RuntimeError("Ù„Ø§ Ø§ØªØµØ§Ù„ Ù‚Ø§Ø¹Ø¯Ø©")
        try:
            if not getattr(db, "conn", None):
                db.connect()
        except Exception as e:
            raise RuntimeError("ØªØ¹Ø°Ø± Ø§Ù„Ø§ØªØµØ§Ù„ (%s)" % (e,))
        try:
            cur = db._exec('SELECT COUNT(*) FROM "%s"."%s"' % (sch, tbl), {})
            try:
                n = cur.fetchone()[0]
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
            return int(n or 0) + 1
        except Exception as e:
            try:
                if getattr(db, "conn", None):
                    db.conn.rollback()
            except Exception:
                pass
            raise RuntimeError("ØªØ¹Ø°Ø± Ø­Ø³Ø§Ø¨ Ø§Ù„ØªØ³Ù„Ø³Ù„ â€” Ø±Ø­Ù‘Ù„ Ø§Ù„Ù†Ù…ÙˆØ°Ø¬ Ø£ÙˆÙ„Ø§Ù‹ (%s)" % (e,))

    def _apply_serials(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serial mains: fresh COUNT(*)+1 each (authoritative). Never raises."""
        try:
            names = [f.name for f in (self.fields or []) if bool(getattr(f, "serial", False))]
        except Exception:
            return data
        if not names:
            return data
        for _nm in names:
            try:
                data[_nm] = self._serial_next(_nm)
            except Exception:
                pass
        return data

    def create_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create record â€” Add button. Computes formulas + serials (static), validates, stores secrets HASHED."""
        _secrets = self._secret_names()
        data = self._drop_masked_secrets(data, _secrets)
        data = self._apply_serials(data)
        data, _calc_done = self._apply_formulas(data)
        errs = self.validate(data)
        if errs:
            raise ValueError(f"Validation failed: {errs}")
        data = self._hash_secrets(data, _secrets)
        sql, params = self._build_insert(data, _calc_done)
        try:
            if not self.db.conn:
                self.db.connect()
            new_id = None
            try:
                # Postgres: Ø£Ø¹Ø¯ Ø§Ù„Ù…ÙØªØ§Ø­ Ø§Ù„Ù…ÙÙˆÙ„Ù‘Ø¯ Ù„Ø±Ø¨Ø· Ø§Ù„Ø¬Ø¯Ø§ÙˆÙ„ Ø§Ù„Ù…ØªÙØ±Ø¹Ø©
                _pkf = next((f.name for f in self.fields if getattr(f, "primary_key", False)), None)
                if _pkf is None and any(f.name == "id" for f in self.fields):
                    _pkf = "id"
                if _pkf and "RETURNING" not in sql.upper():
                    cur = self.db._exec(sql + f' RETURNING "{_pkf}"', params, commit=True)
                    try:
                        _row = cur.fetchone()
                        if _row:
                            new_id = _row[0]
                    finally:
                        try: cur.close()
                        except: pass
                else:
                    raise RuntimeError("no pk")
            except Exception:
                try:
                    if self.db.conn:
                        self.db.conn.rollback()
                except Exception:
                    pass
                cur = self.db._exec(sql, params, commit=True)
                try:
                    rowcount = cur.rowcount
                finally:
                    try: cur.close()
                    except: pass
                return {"ok": True, "rowcount": rowcount, "sql": sql, "params": params}
            return {"ok": True, "rowcount": 1, "id": new_id, "sql": sql, "params": params}
        except Exception as e:
            try:
                if self.db.conn:
                    self.db.conn.rollback()
            except: pass
            raise RuntimeError(f"Create failed: {e}\nSQL: {sql}") from e

    def update_record(self, pk: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """Update record â€” Editing (stars-runs in secrets mean 'unchanged'; new secrets stored HASHED)."""
        # Validate only provided fields
        _secrets = self._secret_names()
        data = self._drop_masked_secrets(data, _secrets)
        for _sn in self._serial_names():
            data.pop(_sn, None)  # ØªØ³Ù„Ø³Ù„ÙŠ Ø«Ø§Ø¨Øª Ø¨Ø¹Ø¯ Ø§Ù„Ø¥Ù†Ø´Ø§Ø¡ â€” Ù„Ø§ ÙŠÙÙƒØªØ¨ Ø£Ø¨Ø¯Ø§Ù‹
        data, _ = self._apply_formulas(data, require_refs=True)
        errs = self.validate({**pk, **data})
        # Filter to only errors for data fields
        errs = {k: v for k, v in errs.items() if k in data}
        if errs:
            raise ValueError(f"Validation failed: {errs}")
        data = self._hash_secrets(data, _secrets)
        sql, params = self._build_update(pk, data)
        try:
            if not self.db.conn:
                self.db.connect()
            cur = self.db._exec(sql, params, commit=True)
            try:
                rowcount = cur.rowcount
            finally:
                try: cur.close()
                except: pass
            return {"ok": True, "rowcount": rowcount, "sql": sql, "params": params}
        except Exception as e:
            try:
                if self.db.conn:
                    self.db.conn.rollback()
            except: pass
            raise RuntimeError(f"Update failed: {e}\nSQL: {sql}") from e

    def delete_record(self, pk: Dict[str, Any]) -> Dict[str, Any]:
        """Delete record â€” Delete button."""
        sql, params = self._build_delete(pk)
        try:
            if not self.db.conn:
                self.db.connect()
            cur = self.db._exec(sql, params, commit=True)
            try:
                rowcount = cur.rowcount
            finally:
                try: cur.close()
                except: pass
            return {"ok": True, "rowcount": rowcount, "sql": sql, "params": params}
        except Exception as e:
            try:
                if self.db.conn:
                    self.db.conn.rollback()
            except: pass
            raise RuntimeError(f"Delete failed: {e}\nSQL: {sql}") from e

    # â”€â”€ Tabs & Positioning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def tabs(self) -> List[Dict[str, Any]]:
        """Return tabs with their fields grouped."""
        result = []
        for tab in self.compiler.tabs():
            fields = [f.to_dict() for f in self.fields if f.tab == tab.id or f.tab == tab.name]
            # Group by category within tab
            cats: Dict[str, List[Dict]] = {}
            for f in fields:
                cats.setdefault(f["category"] or "Ø¹Ø§Ù…", []).append(f)
            result.append({"tab": tab.to_dict(), "fields": fields, "categories": cats})
        # Handle fields without tab
        untabbed = [f.to_dict() for f in self.fields if not f.tab]
        if untabbed:
            result.append({"tab": {"id": "general", "name": "Ø¹Ø§Ù…", "alias": "Ø¹Ø§Ù…"}, "fields": untabbed, "categories": {"Ø¹Ø§Ù…": untabbed}})
        return result

    def preview_insert_sql(self, data: Dict[str, Any]) -> str:
        sql, params = self._build_insert(data)
        return f"{sql}\n-- Binds: {params}"

    def preview_update_sql(self, pk: Dict[str, Any], data: Dict[str, Any]) -> str:
        sql, params = self._build_update(pk, data)
        return f"{sql}\n-- Binds: {params}"

    def preview_delete_sql(self, pk: Dict[str, Any]) -> str:
        sql, params = self._build_delete(pk)
        return f"{sql}\n-- Binds: {params}"

    def preview_select_sql(self, filters=None, order_by=None, limit=None) -> str:
        sql, params = self._build_select(filters, order_by, limit)
        return f"{sql}\n-- Binds: {params}"

    # â”€â”€ Model designer: DDL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    DATA_TYPE_MAP = {
        "VARCHAR": "VARCHAR(255)", "TEXT": "TEXT", "INTEGER": "INTEGER", "INT": "INTEGER",
        "BIGINT": "BIGINT", "NUMERIC": "NUMERIC(18,2)", "DECIMAL": "NUMERIC(18,2)",
        "FLOAT": "DOUBLE PRECISION", "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN",
        "DATE": "DATE", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP", "DATETIME": "TIMESTAMP",
    }

    def primary_key_fields(self) -> List[str]:
        pks = [f.name for f in self.fields if getattr(f, "primary_key", False)]
        return pks or ["id"]

    def build_create_table_ddl(self, schema: str | None = None, table: str | None = None) -> str:
        """Ø¨Ù†Ø§Ø¡ CREATE TABLE Ù…Ù† ØªØ¹Ø±ÙŠÙ Ø§Ù„Ù…ÙˆØ¯ÙŠÙ„: Ø£Ù†ÙˆØ§Ø¹ + PK + Ù‚ÙŠÙˆØ¯ + Ø§ÙØªØ±Ø§Ø¶ÙŠØ§Øª Ø«Ø§Ø¨ØªØ©."""
        sch = schema or self.metadata.get("schema") or "public"
        tbl = table or self.metadata.get("table") or "custom_model"
        col_defs: List[str] = []
        pk_explicit = [f.name for f in self.fields if getattr(f, "primary_key", False)]
        for f in self.fields:
            if getattr(f, "display_only", False):
                continue  # Ø¹Ø±Ø¶ ÙÙ‚Ø· â€” Ø¨Ù„Ø§ Ø¹Ù…ÙˆØ¯
            dt = (f.data_type or "VARCHAR").upper().split("(")[0].strip()
            pg = self.DATA_TYPE_MAP.get(dt, "TEXT")
            parts = [_q(f.name), pg]
            if getattr(f, "primary_key", False):
                parts.append("PRIMARY KEY" if len(pk_explicit) == 1 else "NOT NULL")
            else:
                if f.required or not getattr(f, "nullable", True):
                    parts.append("NOT NULL")
            if f.default is not None and str(f.default).strip() != "" and not getattr(f, "formula", None):
                d = str(f.default).strip()
                if dt in ("INTEGER", "INT", "BIGINT", "NUMERIC", "DECIMAL", "FLOAT") and re.fullmatch(r"-?\d+(\.\d+)?", d):
                    parts.append(f"DEFAULT {d}")
                elif dt == "BOOLEAN" and d.lower() in ("true", "false", "1", "0"):
                    parts.append(f"DEFAULT {'TRUE' if d.lower() in ('true', '1') else 'FALSE'}")
                else:
                    parts.append(f"DEFAULT '{d.replace(chr(39), chr(39)*2)}'")
            col_defs.append(" ".join(parts))
        pk_clause = ""
        if len(pk_explicit) > 1:
            pk_clause = f",\n  PRIMARY KEY ({', '.join(_q(c) for c in pk_explicit)})"
        has_id = any(f.name == "id" for f in self.fields)
        if not has_id and not pk_explicit:
            col_defs.insert(0, '"id" SERIAL PRIMARY KEY')
        return f'CREATE SCHEMA IF NOT EXISTS {_q(sch)};\nCREATE TABLE IF NOT EXISTS {_q(sch)}.{_q(tbl)} (\n  ' + ",\n  ".join(col_defs) + pk_clause + "\n);"
