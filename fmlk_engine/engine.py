"""
FMLK Form Engine — Production Ready
Inputting methods, SQL INSERT/UPDATE/DELETE builders, tabs/categories/positioning, records CRUD.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import re
from .compiler import FMLKFormCompiler, FMLKField
try:
    from rml_python.oracle_engine import OracleEngine, _q
except ImportError:
    from .oracle_engine import OracleEngine  # type: ignore
    def _q(ident: str) -> str:
        return ".".join(f'"{p.replace(chr(34), chr(34)*2)}"' for p in ident.split("."))

def _valid_table_ident(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))


def get_options_source(table: str, column: str, schema: str = "", limit: int = 500, search: str | None = None, display: str | None = None) -> List[Dict[str, Any]]:
    """قيم مميزة لعمود جدول (مرجع [table.column]) — قراءة فقط بمعرفات مُتحقق منها.

    display (اختياري): عمود العرض للتسمية — القيمة من column والتسمية منه.
    """
    if not _valid_table_ident(table) or not _valid_table_ident(column):
        raise ValueError("invalid table/column name")
    disp = (display or "").strip()
    if disp and not _valid_table_ident(disp):
        raise ValueError("invalid display column name")
    sch = schema.strip() if schema and _valid_table_ident(schema) else "public"
    lim = max(1, min(int(limit or 500), 1000))
    import psycopg2
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
    # حسابية وتجميع — تُقبل في الصيغ مع مراجع [field]
    "ABS", "CEIL", "FLOOR", "ROUND", "TRUNC", "MOD", "POWER", "SQRT",
    "SUM", "AVG", "MIN", "MAX", "COUNT",
    "COALESCE", "NULLIF", "GREATEST", "LEAST",
    "UPPER", "LOWER", "TRIM", "LENGTH", "SUBSTRING", "CONCAT", "REPLACE",
    "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP", "EXTRACT",
    "CASE", "CAST",
]


def formula_refs(expr: str) -> List[str]:
    """استخراج أسماء الحقول المرجعية من صيغة [field]."""
    if not expr:
        return []
    return re.findall(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", expr)


def formula_to_sql(expr: str) -> str:
    """تحويل [field] إلى binds :field — تبقى دوال SQL كما هي."""
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

    # ── Input Methods ─────────────────────────────────────────────────────

    def input_types(self) -> Dict[str, List[str]]:
        """Group fields by input_type for UI rendering."""
        groups: Dict[str, List[str]] = {}
        for f in self.fields:
            groups.setdefault(f.input_type, []).append(f.name)
        return groups

    def validate(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Validate required + types + Validation Engine (regex/min_length/max_length/min/max). Returns {field: error}.

        الحقل المخفي (visibleIf غير محقق لقيم data) يُتجاهل تماماً —
        خاصية required تسقط حال الإخفاء.
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
            if f.required and (val is None or str(val).strip() == ""):
                errors[f.name] = f"{f.alias} مطلوب"
                continue  # skip further checks if empty required
            if val is None or str(val).strip() == "":
                continue
            sval = str(val)
            # basic type checks (keep first error)
            if f.input_type == "email" and "@" not in sval and f.name not in errors:
                errors[f.name] = "بريد إلكتروني غير صالح"
            if f.input_type == "number" and f.name not in errors:
                try: float(val)
                except: errors[f.name] = "يجب أن يكون رقماً"
            # ── Validation Engine ──
            rules = f.validation or {}
            # regex / pattern
            pattern = rules.get("pattern") or rules.get("regex")
            if pattern and f.name not in errors:
                try:
                    if not re.fullmatch(pattern, sval):
                        errors[f.name] = f"{f.alias} صيغة غير صحيحة"
                except re.error:
                    pass  # invalid regex ignored
            # min_length / max_length (for strings)
            min_len = rules.get("min_length")
            if min_len is not None and f.name not in errors:
                try:
                    if len(sval) < int(min_len):
                        errors[f.name] = f"{f.alias} يجب ألا يقل عن {min_len} حرف"
                except: pass
            max_len = rules.get("max_length")
            if max_len is not None and f.name not in errors:
                try:
                    if len(sval) > int(max_len):
                        errors[f.name] = f"{f.alias} يجب ألا يزيد عن {max_len} حرف"
                except: pass
            # numeric min / max
            min_v = rules.get("min")
            if min_v is not None and f.name not in errors:
                try:
                    if float(val) < float(min_v):
                        errors[f.name] = f"{f.alias} يجب أن يكون ≥ {min_v}"
                except: pass
            max_v = rules.get("max")
            if max_v is not None and f.name not in errors:
                try:
                    if float(val) > float(max_v):
                        errors[f.name] = f"{f.alias} يجب أن يكون ≤ {max_v}"
                except: pass
        return errors

    # ── Foreign Keys: dynamic lookup ────────────────────────────────────────
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
        """List all FK fields with their ref endpoints — for frontend to prefetch."""
        out = []
        for f in self.fields:
            if f.ref_table:
                out.append({"field": f.name, "alias": f.alias, "refTable": f.ref_table, "refFk": f.ref_fk, "refDisplay": f.ref_display, "endpoint": f"/api/fmlk/lookup?field={f.name}"})
        return out

    # ── SQL Builders (secure _q, binds) ───────────────────────────────────

    def apply_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """ملء القيم الافتراضية: ثابتة أولاً، ثم الصيغ لمن ترك فارغاً (يدوي إن لا صيغة)."""
        out = dict(data or {})
        for f in self.fields:
            v = out.get(f.name)
            empty = v is None or (isinstance(v, str) and v.strip() == "")
            if not empty:
                continue
            if f.default is not None and str(f.default) != "":
                out[f.name] = f.default
            # formula تُترك فارغة للإدخال اليدوي ما لم تُحسب في SQL
        return out

    def _table_columns_info(self) -> Dict[str, Dict[str, Any]]:
        """أعمدة الجدول {name: {nullable, has_default, generic}} — مخزّن مؤقتاً، آمن الفشل."""
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
        """هل يوجد عمود في جدول النموذج؟"""
        try:
            return column.lower() in self._table_columns_info()
        except Exception:
            return False

    def _build_insert(self, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Build INSERT with binds + fixed defaults + formula SQL ([refs] → binds)."""
        data = self.apply_defaults(data)
        cols: List[str] = []
        binds: List[str] = []
        params: Dict[str, Any] = {}
        for f in self.fields:
            formula = (getattr(f, "formula", None) or "").strip()
            has_val = f.name in data and not (data[f.name] is None or (isinstance(data[f.name], str) and data[f.name].strip() == ""))
            if formula and not has_val:
                # صيغة حسابية: تُنفذ في DB بكل دوال SQL — مراجع [f] تصبح binds
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
                    continue  # يُترك لملء الأعمدة التلقائي أو NULL/الافتراضي
                cols.append(_q(f.name))
                binds.append(f":{f.name}")
                params[f.name] = data[f.name]
        if not cols:
            raise ValueError("No valid columns for INSERT")
        table_q = _q(self._table)
        schema = self.metadata.get("schema")
        if schema and "." not in self._table:
            table_q = f"{_q(schema)}.{table_q}"
        # إكمال أعمدة الجدول الغائبة عن النموذج: طوابع زمنية → CURRENT_TIMESTAMP،
        # ونص NOT NULL بلا افتراضي → '' ، ومنطقي → FALSE (يعمل على Postgres و Oracle)
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
        for k, v in data.items():
            if k in pk:
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
            where_sql, params = _build_where(filters)
        order_sql = f" ORDER BY {_q(order_by)}" if order_by else ""
        # Pagination
        paginate = ""
        if limit is not None:
            paginate = " OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY"
            params["off"] = offset
            params["lim"] = limit
        sql = f"SELECT {select_clause} FROM {table_q}{where_sql}{order_sql}{paginate}"
        return sql, params

    # ── Records CRUD ──────────────────────────────────────────────────────

    @staticmethod
    def _missing_pk_error(e: Exception) -> bool:
        # Only fall back when the *id* column itself is missing (not a lookup-table error)
        msg = str(e).lower()
        has_id = '"id"' in msg or "'id'" in msg or " id " in msg or msg.strip().endswith("id")
        return has_id and any(k in msg for k in ("does not exist", "no such column", "invalid identifier", "ora-00904"))

    def list_records(self, filters: List[Dict[str, Any]] | None = None, page: int = 1, page_size: int = 50, order_by: str | None = None) -> Dict[str, Any]:
        """List records with pagination — for Records showing."""
        limit = None if page_size == "all" else int(page_size)
        offset = (page - 1) * (limit or 0) if limit else 0
        # Stable order: without ORDER BY the DB returns heap order,
        # so an edited row sinks last — default to PK (or first field).
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
                # عمود الترتيب التلقائي غير موجود — أعد بدون ترتيب
                try:
                    sql, params = self._build_select(filters, None, limit, offset, include_pk=True)
                    return self._list_records_exec(sql, params, filters, page, limit)
                except Exception:
                    pass
            raise

    # ── Secret masking (passwords never leave the list endpoint in cleartext) ──
    SECRET_NAMES = {"password", "passwd", "pwd", "secret", "secret_key", "api_key", "token"}

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
            where_sql, _ = _build_where(filters)
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

    def create_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create record — Add button. Validates, builds INSERT, executes."""
        data = self._drop_masked_secrets(data, self._secret_names())
        errs = self.validate(data)
        if errs:
            raise ValueError(f"Validation failed: {errs}")
        sql, params = self._build_insert(data)
        try:
            if not self.db.conn:
                self.db.connect()
            new_id = None
            try:
                # Postgres: أعد المفتاح المُولّد لربط الجداول المتفرعة
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
        """Update record — Editing (stars-runs in secrets mean 'unchanged')."""
        # Validate only provided fields
        data = self._drop_masked_secrets(data, self._secret_names())
        errs = self.validate({**pk, **data})
        # Filter to only errors for data fields
        errs = {k: v for k, v in errs.items() if k in data}
        if errs:
            raise ValueError(f"Validation failed: {errs}")
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
        """Delete record — Delete button."""
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

    # ── Tabs & Positioning ────────────────────────────────────────────────

    def tabs(self) -> List[Dict[str, Any]]:
        """Return tabs with their fields grouped."""
        result = []
        for tab in self.compiler.tabs():
            fields = [f.to_dict() for f in self.fields if f.tab == tab.id or f.tab == tab.name]
            # Group by category within tab
            cats: Dict[str, List[Dict]] = {}
            for f in fields:
                cats.setdefault(f["category"] or "عام", []).append(f)
            result.append({"tab": tab.to_dict(), "fields": fields, "categories": cats})
        # Handle fields without tab
        untabbed = [f.to_dict() for f in self.fields if not f.tab]
        if untabbed:
            result.append({"tab": {"id": "general", "name": "عام", "alias": "عام"}, "fields": untabbed, "categories": {"عام": untabbed}})
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

    # ── Model designer: DDL ─────────────────────────────────────────────
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
        """بناء CREATE TABLE من تعريف الموديل: أنواع + PK + قيود + افتراضيات ثابتة."""
        sch = schema or self.metadata.get("schema") or "public"
        tbl = table or self.metadata.get("table") or "custom_model"
        col_defs: List[str] = []
        pk_explicit = [f.name for f in self.fields if getattr(f, "primary_key", False)]
        for f in self.fields:
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
