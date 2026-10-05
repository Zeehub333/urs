"""JSON document connections (file or URL) — tables/columns/preview + row writes.

Document shapes (top-level JSON):
  {"table": [{row}, ...], ...}   → each key is a table
  [{row}, ...]                   → single table named "data"

A row is a JSON object {column: value}. Column types are inferred from values
(INTEGER / NUMERIC / BOOLEAN / DATE / TIMESTAMP / TEXT).

Security:
  - file sources resolve under ``base_dir`` only (no absolute paths, no ".."
    escapes); URL sources allow http(s) only.
  - file writes are atomic (tmp + replace); URL writes PUT the full document.
  - size cap + timeout guard remote/local reads.

Raises JsonSourceError (a ValueError) on any problem so API views can
return 400 with the message.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os
import re as _re
import urllib.request as _urlreq

JSON_MAX_BYTES = 10 * 1024 * 1024
JSON_TIMEOUT = 10
JSON_SCAN_ROWS = 500
JSON_SINGLE_TABLE = "data"

_URL_RE = _re.compile(r"^https?://", _re.IGNORECASE)
_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


class JsonSourceError(ValueError):
    """Any JSON-source problem (missing file, bad shape, network, ...)."""


def is_url_source(source: str) -> bool:
    return bool(_URL_RE.match(str(source or "").strip()))


def is_json_source(source: str) -> bool:
    """True for http(s) URLs or *.json file paths."""
    s = str(source or "").strip()
    return is_url_source(s) or s.lower().endswith(".json")


def resolve_path(source: str, base_dir) -> str:
    """File source → absolute path under base_dir. Raises JsonSourceError."""
    s = str(source or "").strip()
    if not s:
        raise JsonSourceError("empty JSON source")
    if is_url_source(s):
        raise JsonSourceError("URL source has no local path")
    base = _os.path.abspath(str(base_dir or "."))
    # forbid absolute paths and escapes — everything lives under base_dir
    cand = _os.path.abspath(_os.path.join(base, s))
    if cand != base and not cand.startswith(base + _os.sep):
        raise JsonSourceError("JSON file must be under the project directory")
    if not cand.lower().endswith(".json"):
        raise JsonSourceError("JSON file must end with .json")
    return cand


def load(source: str, base_dir=None, timeout: int = JSON_TIMEOUT):
    """(doc, mode) — mode is 'file' or 'url'. Never returns None doc."""
    s = str(source or "").strip()
    if not s:
        raise JsonSourceError("empty JSON source")
    if is_url_source(s):
        req = _urlreq.Request(s, headers={"Accept": "application/json",
                                          "User-Agent": "urs-json-source/1.0"})
        try:
            with _urlreq.urlopen(req, timeout=timeout or JSON_TIMEOUT) as res:
                raw = res.read(JSON_MAX_BYTES + 1)
        except Exception as e:
            raise JsonSourceError("JSON URL unreachable: %s" % str(e)[:150])
        if len(raw) > JSON_MAX_BYTES:
            raise JsonSourceError("JSON document too large (>%d bytes)" % JSON_MAX_BYTES)
        try:
            doc = _json.loads(raw.decode("utf-8", errors="strict"))
        except Exception:
            raise JsonSourceError("URL did not return valid JSON")
        return doc, "url"
    path = resolve_path(s, base_dir)
    if not _os.path.isfile(path):
        raise JsonSourceError("JSON file not found: %s" % s)
    try:
        if _os.path.getsize(path) > JSON_MAX_BYTES:
            raise JsonSourceError("JSON file too large (>%d bytes)" % JSON_MAX_BYTES)
        with open(path, "r", encoding="utf-8") as f:
            doc = _json.load(f)
    except JsonSourceError:
        raise
    except Exception as e:
        raise JsonSourceError("cannot read JSON file: %s" % str(e)[:150])
    return doc, "file"


def save(source: str, doc, base_dir=None, timeout: int = JSON_TIMEOUT) -> None:
    """Persist a (possibly edited) document back to its source."""
    s = str(source or "").strip()
    if not s:
        raise JsonSourceError("empty JSON source")
    if is_url_source(s):
        payload = _json.dumps(doc, ensure_ascii=False).encode("utf-8")
        req = _urlreq.Request(s, data=payload, method="PUT",
                              headers={"Content-Type": "application/json",
                                       "User-Agent": "urs-json-source/1.0"})
        try:
            with _urlreq.urlopen(req, timeout=timeout or JSON_TIMEOUT) as res:
                if res.status not in (200, 201, 204):
                    raise JsonSourceError("JSON URL rejected write (HTTP %s)" % res.status)
        except JsonSourceError:
            raise
        except Exception as e:
            raise JsonSourceError("JSON URL write failed: %s" % str(e)[:150])
        return
    path = resolve_path(s, base_dir)
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(doc, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, path)
    except Exception as e:
        try:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        except Exception:
            pass
        raise JsonSourceError("cannot write JSON file: %s" % str(e)[:150])


def tables(doc) -> list:
    """Table names in a document."""
    if isinstance(doc, dict):
        if all(isinstance(v, list) for v in doc.values()) and doc:
            return sorted(str(k) for k in doc.keys())
        if isinstance(doc.get("tables"), dict) and all(
                isinstance(v, list) for v in doc["tables"].values()):
            return sorted(str(k) for k in doc["tables"].keys())
        raise JsonSourceError("JSON object must be {table: [rows]}")
    if isinstance(doc, list):
        return [JSON_SINGLE_TABLE]
    raise JsonSourceError("JSON document must be an object {table: [rows]} or a rows array")


def rows_of(doc, table: str) -> list:
    """Row list for one table (each row coerced to dict)."""
    t = str(table or "")
    if isinstance(doc, list):
        if t in ("", JSON_SINGLE_TABLE):
            rows = doc
        else:
            raise JsonSourceError("unknown JSON table: %s" % t)
    elif isinstance(doc, dict):
        inner = doc.get("tables") if isinstance(doc.get("tables"), dict) else doc
        if t not in inner or not isinstance(inner[t], list):
            raise JsonSourceError("unknown JSON table: %s" % t)
        rows = inner[t]
    else:
        raise JsonSourceError("JSON document must be an object {table: [rows]} or a rows array")
    out = []
    for r in rows or []:
        out.append(dict(r) if isinstance(r, dict) else {"value": r})
    return out


def _infer(values: list) -> str:
    seen = set()
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            seen.add("BOOLEAN")
        elif isinstance(v, int):
            seen.add("INTEGER")
        elif isinstance(v, float):
            seen.add("NUMERIC")
        elif isinstance(v, str):
            s = v.strip()
            if _DATE_RE.match(s):
                seen.add("DATE")
            elif _TS_RE.match(s):
                seen.add("TIMESTAMP")
            else:
                try:
                    int(s)
                    seen.add("INTEGER")
                    continue
                except Exception:
                    pass
                try:
                    float(s)
                    seen.add("NUMERIC")
                    continue
                except Exception:
                    pass
                seen.add("TEXT")
        else:
            seen.add("TEXT")
    if not seen:
        return "TEXT"
    if seen == {"INTEGER"}:
        return "INTEGER"
    if seen <= {"INTEGER", "NUMERIC"}:
        return "NUMERIC"
    if len(seen) == 1:
        return next(iter(seen))
    return "TEXT"


def columns(doc, table: str) -> list:
    """[{name, type, db_type}] — union of keys over the first N rows."""
    rows = rows_of(doc, table)
    names = []
    for r in rows[:JSON_SCAN_ROWS]:
        for k in r.keys():
            ks = str(k)
            if ks not in names:
                names.append(ks)
    out = []
    for n in names:
        vals = [r.get(n) for r in rows[:JSON_SCAN_ROWS]]
        ty = _infer(vals)
        out.append({"name": n, "type": ty, "db_type": ty})
    return out


def _match(row: dict, match: dict) -> bool:
    for k, v in (match or {}).items():
        if str(row.get(k) if row.get(k) is not None else "") != str(v if v is not None else ""):
            return False
    return True


def _matches(row: dict, q=None, filters=None) -> bool:
    if q is not None and str(q).strip() != "":
        needle = str(q).strip().lower()
        hay = " ".join("" if v is None else str(v) for v in row.values()).lower()
        if needle not in hay:
            return False
    for flt in filters or []:
        if not isinstance(flt, dict) or not flt.get("col"):
            continue
        col = str(flt["col"])
        val = str(flt.get("val", ""))
        if val.strip() == "":
            continue
        cell = "" if row.get(col) is None else str(row.get(col))
        op = str(flt.get("op") or "contains").lower()
        if op == "equals":
            if cell != val:
                return False
        elif op == "gt":
            try:
                if not (float(cell) > float(val)):
                    return False
            except Exception:
                if not (cell > val):
                    return False
        elif op == "lt":
            try:
                if not (float(cell) < float(val)):
                    return False
            except Exception:
                if not (cell < val):
                    return False
        else:
            if val.lower() not in cell.lower():
                return False
    return True


def preview(doc, table: str, limit: int = 50, q=None, filters=None):
    """(columns, rows) — filtered row dicts, capped."""
    n = max(1, min(int(limit or 50), 200))
    cols = columns(doc, table)
    rows = [r for r in rows_of(doc, table) if _matches(r, q, filters)]
    return cols, rows[:n]


def _clean_row(row) -> dict:
    if not isinstance(row, dict):
        raise JsonSourceError("row must be an object {column: value}")
    out = {}
    for k, v in row.items():
        ks = str(k or "").strip()
        if not ks:
            continue
        out[ks] = v
    if not out:
        raise JsonSourceError("row has no columns")
    return out


def insert(doc, table: str, row: dict):
    """Append a row; returns the stored row. Mutates doc in place."""
    clean = _clean_row(row)
    t = str(table or "")
    if isinstance(doc, list):
        if t not in ("", JSON_SINGLE_TABLE):
            raise JsonSourceError("unknown JSON table: %s" % t)
        doc.append(clean)
        return clean
    if isinstance(doc, dict):
        inner = doc.get("tables") if isinstance(doc.get("tables"), dict) else doc
        if t not in inner or not isinstance(inner[t], list):
            raise JsonSourceError("unknown JSON table: %s" % t)
        inner[t].append(clean)
        return clean
    raise JsonSourceError("JSON document must be an object {table: [rows]} or a rows array")


def update(doc, table: str, match: dict, patch: dict) -> int:
    """Patch rows matching {col: val}; returns changed count."""
    if not isinstance(match, dict) or not match:
        raise JsonSourceError("match {column: value} is required")
    if not isinstance(patch, dict) or not patch:
        raise JsonSourceError("patch {column: value} is required")
    rows = rows_of(doc, table)
    n = 0
    for r in rows:
        if _match(r, match):
            for k, v in patch.items():
                ks = str(k or "").strip()
                if ks:
                    r[ks] = v
            n += 1
    # write back (rows_of returns coerced copies — sync into doc)
    _replace_rows(doc, table, rows)
    return n


def delete(doc, table: str, match: dict) -> int:
    """Delete rows matching {col: val}; returns removed count."""
    if not isinstance(match, dict) or not match:
        raise JsonSourceError("match {column: value} is required")
    rows = rows_of(doc, table)
    kept = [r for r in rows if not _match(r, match)]
    removed = len(rows) - len(kept)
    _replace_rows(doc, table, kept)
    return removed


def _replace_rows(doc, table: str, rows: list) -> None:
    t = str(table or "")
    if isinstance(doc, list):
        if t in ("", JSON_SINGLE_TABLE):
            doc[:] = rows
            return
        raise JsonSourceError("unknown JSON table: %s" % t)
    if isinstance(doc, dict):
        inner = doc.get("tables") if isinstance(doc.get("tables"), dict) else doc
        if t in inner and isinstance(inner[t], list):
            inner[t][:] = rows
            return
        raise JsonSourceError("unknown JSON table: %s" % t)
    raise JsonSourceError("JSON document must be an object {table: [rows]} or a rows array")


def stats(doc) -> dict:
    """{tables, rows} counts for test-connection payloads."""
    try:
        names = tables(doc)
    except JsonSourceError:
        return {"tables": 0, "rows": 0}
    total = 0
    for t in names:
        try:
            total += len(rows_of(doc, t))
        except Exception:
            pass
    return {"tables": len(names), "rows": total}
