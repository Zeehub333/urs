"""
Postgres Engine — 35 DYNAMIC data functions

Dynamic table/column, psycopg2 with %s placeholders, quoted identifiers, LIMIT/OFFSET.
"""
from typing import Any, Dict, List, Optional, Tuple
try:
    import psycopg2, psycopg2.extras
except ImportError:
    psycopg2 = None

def _q(ident: str) -> str:
    return ".".join(f'"{p.replace(chr(34), chr(34)*2)}"' for p in ident.split("."))

def _where(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
    if not filters: return "", []
    return " WHERE " + " AND ".join(f"{_q(k)}=%s" for k in filters), list(filters.values())

class PostgresEngine:
    def __init__(self, dsn: str): self.dsn, self.conn = dsn, None
    def connect(self):
        if psycopg2 is None: raise RuntimeError("pip install psycopg2-binary")
        self.conn=psycopg2.connect(self.dsn); self.conn.autocommit=False; return self.conn
    def disconnect(self):
        if self.conn: self.conn.close(); self.conn=None
    def _exec(self, sql: str, params: Tuple=(), commit=False):
        cur=self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute(sql, params)
        if commit: self.conn.commit()
        return cur

    def create(self, table: str, data: Dict[str, Any]) -> int:
        cols=", ".join(_q(k) for k in data); cur=self._exec(f"INSERT INTO {_q(table)} ({cols}) VALUES ({', '.join('%s' for _ in data)}) RETURNING id", tuple(data.values()), True); return cur.fetchone()["id"] if cur.rowcount else 0
    def bulk_create(self, table: str, rows: List[Dict[str, Any]]) -> int:
        if not rows: return 0
        cols=list(rows[0].keys()); colsql=", ".join(_q(c) for c in cols); cur=self.conn.cursor()
        try:
            cur.executemany(f"INSERT INTO {_q(table)} ({colsql}) VALUES ({', '.join('%s' for _ in cols)})", [tuple(r[c] for c in cols) for r in rows]); self.conn.commit(); return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
    def get_by_id(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(pk)}=%s", (pk_value,)); return cur.fetchone()
    def get_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params=_where(filters); cur=self._exec(f"SELECT * FROM {_q(table)}{where} LIMIT 1", tuple(params)); return cur.fetchone()
    def list_all(self, table: str, limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} ORDER BY {_q(order_by)} LIMIT %s OFFSET %s", (limit, offset)); return cur.fetchall()
    def filter(self, table: str, filters: Dict[str, Any], limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        where, params=_where(filters); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} LIMIT %s OFFSET %s", tuple(params)+(limit, offset)); return cur.fetchall()
    def search(self, table: str, field: str, pattern: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} ILIKE %s LIMIT %s", (f"%{pattern}%", limit)); return cur.fetchall()
    def count(self, table: str, filters: Dict[str, Any]=None) -> int:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT COUNT(*) as cnt FROM {_q(table)}{where}", tuple(params)); return cur.fetchone()["cnt"]
    def exists(self, table: str, filters: Dict[str, Any]) -> bool: return self.count(table, filters)>0
    def update_by_id(self, table: str, pk_value: Any, data: Dict[str, Any], pk: str="id") -> int:
        sets=", ".join(f"{_q(k)}=%s" for k in data); cur=self._exec(f"UPDATE {_q(table)} SET {sets} WHERE {_q(pk)}=%s", tuple(data.values())+(pk_value,), True); return cur.rowcount
    def update_where(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> int:
        sets=", ".join(f"{_q(k)}=%s" for k in data); where, wparams=_where(filters); cur=self._exec(f"UPDATE {_q(table)} SET {sets}{where}", tuple(data.values())+tuple(wparams), True); return cur.rowcount
    def upsert(self, table: str, data: Dict[str, Any], unique_field: str) -> int:
        cols=list(data.keys()); colsql=", ".join(_q(c) for c in cols); ph=", ".join("%s" for _ in cols); sets=", ".join(f"{_q(c)}=EXCLUDED.{_q(c)}" for c in cols if c!=unique_field)
        sql=f"INSERT INTO {_q(table)} ({colsql}) VALUES ({ph}) ON CONFLICT ({_q(unique_field)}) DO {'UPDATE SET '+sets if sets else 'NOTHING'}"
        cur=self._exec(sql, tuple(data.values()), True); return cur.rowcount
    def increment(self, table: str, pk_value: Any, field: str, delta: float=1, pk: str="id") -> int:
        cur=self._exec(f"UPDATE {_q(table)} SET {_q(field)}={_q(field)}+%s WHERE {_q(pk)}=%s", (delta, pk_value), True); return cur.rowcount
    def delete_by_id(self, table: str, pk_value: Any, pk: str="id") -> int:
        cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)}=%s", (pk_value,), True); return cur.rowcount
    def delete_where(self, table: str, filters: Dict[str, Any]) -> int:
        where, params=_where(filters); cur=self._exec(f"DELETE FROM {_q(table)}{where}", tuple(params), True); return cur.rowcount
    def truncate(self, table: str) -> int:
        cur=self._exec(f"TRUNCATE TABLE {_q(table)}", (), True); return cur.rowcount
    def distinct(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT DISTINCT {_q(field)} FROM {_q(table)}{where}", tuple(params)); return [r[field] for r in cur.fetchall()]
    def aggregate(self, table: str, func: str, field: str, filters: Dict[str, Any]=None) -> Any:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {func.upper()}({_q(field)}) as val FROM {_q(table)}{where}", tuple(params)); return cur.fetchone()["val"]
    def sum_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "SUM", field, filters)
    def avg_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "AVG", field, filters)
    def min_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MIN", field, filters)
    def max_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MAX", field, filters)
    def group_by(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)}, COUNT(*) as cnt FROM {_q(table)}{where} GROUP BY {_q(field)}", tuple(params)); return cur.fetchall()
    def join_select(self, base_table: str, join_table: str, on: str, filters: Dict[str, Any]=None, limit: int=100) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(base_table)} JOIN {_q(join_table)} ON {on}{where} LIMIT %s", tuple(params)+(limit,)); return cur.fetchall()
    def raw_query(self, sql: str, params: Tuple=()) -> List[Dict[str, Any]]:
        cur=self._exec(sql, params); return cur.fetchall() if cur.description else []
    def raw_execute(self, sql: str, params: Tuple=()) -> int:
        cur=self._exec(sql, params, True); return cur.rowcount
    def paginate(self, table: str, page: int=1, page_size: int=20, filters: Dict[str, Any]=None, order_by: str="id") -> Dict[str, Any]:
        total=self.count(table, filters); offset=(page-1)*page_size; rows=self.filter(table, filters or {}, page_size, offset, order_by); return {"total": total, "page": page, "page_size": page_size, "rows": rows}
    def bulk_update(self, table: str, rows: List[Dict[str, Any]], pk: str="id") -> int:
        total=0
        for r in rows: pk_val=r.pop(pk); total+=self.update_by_id(table, pk_val, r, pk); r[pk]=pk_val
        return total
    def bulk_delete(self, table: str, ids: List[Any], pk: str="id") -> int:
        if not ids: return 0
        ph=", ".join("%s" for _ in ids); cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)} IN ({ph})", tuple(ids), True); return cur.rowcount
    def copy_row(self, table: str, pk_value: Any, overrides: Dict[str, Any]=None, pk: str="id") -> int:
        row=self.get_by_id(table, pk_value, pk)
        if not row: return 0
        row.pop(pk, None); 
        if overrides: row.update(overrides)
        return self.create(table, row)
    def clone_table(self, new_table: str, source_table: str) -> int:
        cur=self._exec(f"CREATE TABLE {_q(new_table)} (LIKE {_q(source_table)} INCLUDING ALL)", (), True); cur=self._exec(f"INSERT INTO {_q(new_table)} SELECT * FROM {_q(source_table)}", (), True); return cur.rowcount
    def rename_column(self, table: str, old: str, new: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} RENAME COLUMN {_q(old)} TO {_q(new)}", (), True); return cur.rowcount
    def add_column(self, table: str, column_def: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} ADD COLUMN {column_def}", (), True); return cur.rowcount
    def drop_column(self, table: str, column: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} DROP COLUMN {_q(column)}", (), True); return cur.rowcount
    def get_schema(self, table: str) -> List[Dict[str, Any]]:
        cur=self._exec("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name=%s", (table,)); return cur.fetchall()
    def bulk_upsert(self, table: str, rows: List[Dict[str, Any]], unique_field: str) -> int: return sum(self.upsert(table, r, unique_field) for r in rows)
    def find_or_create(self, table: str, filters: Dict[str, Any], defaults: Dict[str, Any]=None) -> Dict[str, Any]:
        row=self.get_one(table, filters)
        if row: return row
        data={**filters, **(defaults or {})}; self.create(table, data); return self.get_one(table, filters)
    def first(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} ASC LIMIT 1", tuple(params)); return cur.fetchone()
    def last(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} DESC LIMIT 1", tuple(params)); return cur.fetchone()
    def sample(self, table: str, n: int=5, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY RANDOM() LIMIT %s", tuple(params)+(n,)); return cur.fetchall()
    def pluck(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)} FROM {_q(table)}{where}", tuple(params)); return [r[field] for r in cur.fetchall()]
    def where_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return []
        ph=", ".join("%s" for _ in values); cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IN ({ph}) LIMIT %s", tuple(values)+(limit,)); return cur.fetchall()
    def where_not_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return self.list_all(table, limit)
        ph=", ".join("%s" for _ in values); cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} NOT IN ({ph}) LIMIT %s", tuple(values)+(limit,)); return cur.fetchall()
    def where_between(self, table: str, field: str, low: Any, high: Any, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} BETWEEN %s AND %s LIMIT %s", (low, high, limit)); return cur.fetchall()
    def where_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IS NULL LIMIT %s", (limit,)); return cur.fetchall()
    def where_not_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IS NOT NULL LIMIT %s", (limit,)); return cur.fetchall()
    def order_by_limit(self, table: str, order_by: str, direction: str="ASC", limit: int=100, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} {direction.upper()} LIMIT %s", tuple(params)+(limit,)); return cur.fetchall()
    def lock_for_update(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(pk)}=%s FOR UPDATE", (pk_value,)); return cur.fetchone()
    def explain(self, table: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"EXPLAIN SELECT * FROM {_q(table)}{where}", tuple(params)); return cur.fetchall()
    def export_csv(self, table: str, filepath: str, filters: Dict[str, Any]=None) -> int:
        import csv; rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000)
        if not rows: return 0
        with open(filepath, "w", newline="", encoding="utf-8") as f: w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows); return len(rows)

# ── DB REFLECTION (100) ──
    def list_schemas(self, ) -> list:
        """list all schemas — reflection"""
        sql="""SELECT schema_name FROM information_schema.schemata ORDER BY schema_name"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def list_tables(self, schema: str = None) -> list:
        """list tables — reflection"""
        sql="""SELECT table_name FROM information_schema.tables WHERE table_schema=COALESCE(:schema, table_schema) AND table_type='BASE TABLE'"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def list_views(self, schema: str = None) -> list:
        """list views — reflection"""
        sql="""SELECT table_name FROM information_schema.views WHERE table_schema=COALESCE(:schema, table_schema)"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def list_materialized_views(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT matviewname FROM pg_matviews WHERE schemaname=COALESCE(:schema, schemaname)"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def list_tables_with_type(self, table_type: str = 'TABLE') -> list:
        """list tables with type — reflection"""
        sql="""SELECT table_name FROM information_schema.tables WHERE table_type=:table_type"""
        cur=self._exec(sql, (table_type,))
        return cur.fetchall() if cur.description else []

    def list_temporary_tables(self, ) -> list:
        """list temp tables — reflection"""
        sql="""SELECT table_name FROM information_schema.tables WHERE table_type='LOCAL TEMPORARY'"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def list_system_tables(self, ) -> list:
        """list system tables — reflection"""
        sql="""SELECT tablename FROM pg_tables WHERE schemaname='pg_catalog'"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_table_info(self, table: str, schema: str = None) -> list:
        """get table info — reflection"""
        sql="""SELECT * FROM information_schema.tables WHERE table_name=:table"""
        cur=self._exec(sql, (table, schema,))
        return cur.fetchall() if cur.description else []

    def get_table_columns(self, table: str, schema: str = None) -> list:
        """get columns for table — reflection"""
        sql="""SELECT column_name, data_type FROM information_schema.columns WHERE table_name=:table ORDER BY ordinal_position"""
        cur=self._exec(sql, (table, schema,))
        return cur.fetchall() if cur.description else []

    def get_column_info(self, table: str, column: str, schema: str = None) -> list:
        """get column info — reflection"""
        sql="""SELECT * FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column, schema,))
        return cur.fetchall() if cur.description else []

    def get_column_exists(self, table: str, column: str, schema: str = None) -> list:
        """check column exists — reflection"""
        sql="""SELECT COUNT(*) FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column, schema,))
        return cur.fetchall() if cur.description else []

    def get_column_type(self, table: str, column: str) -> list:
        """get column type — reflection"""
        sql="""SELECT data_type FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column,))
        return cur.fetchall() if cur.description else []

    def get_column_default(self, table: str, column: str) -> list:
        """get column default — reflection"""
        sql="""SELECT column_default FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column,))
        return cur.fetchall() if cur.description else []

    def get_column_nullable(self, table: str, column: str) -> list:
        """check nullable — reflection"""
        sql="""SELECT is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column,))
        return cur.fetchall() if cur.description else []

    def get_column_comment(self, table: str, column: str) -> list:
        """get column comment — reflection"""
        sql="""SELECT col_description(:table::regclass, ordinal_position) FROM information_schema.columns WHERE table_name=:table AND column_name=:column"""
        cur=self._exec(sql, (table, column,))
        return cur.fetchall() if cur.description else []

    def list_primary_keys(self, table: str, schema: str = None) -> list:
        """list pks — reflection"""
        sql="""SELECT conname FROM pg_constraint WHERE contype='p' AND conrelid=:table::regclass"""
        cur=self._exec(sql, (table, schema,))
        return cur.fetchall() if cur.description else []

    def get_primary_key(self, table: str) -> list:
        """get pk — reflection"""
        sql="""SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attnum = ANY(i.indkey) WHERE i.indrelid=:table::regclass AND i.indisprimary"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_foreign_keys(self, table: str = None) -> list:
        """list fks — reflection"""
        sql="""SELECT conname FROM pg_constraint WHERE contype='f'"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_foreign_key(self, table: str, column: str) -> list:
        """get fk — reflection"""
        sql="""SELECT * FROM information_schema.referential_constraints WHERE constraint_name=:column"""
        cur=self._exec(sql, (table, column,))
        return cur.fetchall() if cur.description else []

    def list_unique_constraints(self, table: str = None) -> list:
        """list unique constraints — reflection"""
        sql="""SELECT conname FROM pg_constraint WHERE contype='u'"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_check_constraints(self, table: str = None) -> list:
        """list check constraints — reflection"""
        sql="""SELECT conname FROM pg_constraint WHERE contype='c'"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_not_null_constraints(self, table: str = None) -> list:
        """list not null — reflection"""
        sql="""SELECT column_name FROM information_schema.columns WHERE is_nullable='NO'"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_constraint_info(self, constraint: str) -> list:
        """get constraint info — reflection"""
        sql="""SELECT * FROM information_schema.table_constraints WHERE constraint_name=:constraint"""
        cur=self._exec(sql, (constraint,))
        return cur.fetchall() if cur.description else []

    def list_indexes(self, table: str = None) -> list:
        """list indexes — reflection"""
        sql="""SELECT indexname FROM pg_indexes WHERE tablename=COALESCE(:table, tablename)"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_index_info(self, index: str) -> list:
        """get index info — reflection"""
        sql="""SELECT * FROM pg_indexes WHERE indexname=:index"""
        cur=self._exec(sql, (index,))
        return cur.fetchall() if cur.description else []

    def get_index_columns(self, index: str) -> list:
        """get index columns — reflection"""
        sql="""SELECT attname FROM pg_attribute WHERE attrelid=:index::regclass"""
        cur=self._exec(sql, (index,))
        return cur.fetchall() if cur.description else []

    def list_unique_indexes(self, table: str = None) -> list:
        """list unique indexes — reflection"""
        sql="""SELECT indexname FROM pg_indexes WHERE indexdef LIKE '%UNIQUE%'"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_sequences(self, schema: str = None) -> list:
        """list sequences — reflection"""
        sql="""SELECT sequencename FROM information_schema.sequences"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_sequence_info(self, sequence: str) -> list:
        """get sequence info — reflection"""
        sql="""SELECT * FROM information_schema.sequences WHERE sequence_name=:sequence"""
        cur=self._exec(sql, (sequence,))
        return cur.fetchall() if cur.description else []

    def list_triggers(self, table: str = None) -> list:
        """list triggers — reflection"""
        sql="""SELECT trigger_name FROM information_schema.triggers WHERE event_object_table=COALESCE(:table, event_object_table)"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_trigger_info(self, trigger: str) -> list:
        """get trigger info — reflection"""
        sql="""SELECT * FROM information_schema.triggers WHERE trigger_name=:trigger"""
        cur=self._exec(sql, (trigger,))
        return cur.fetchall() if cur.description else []

    def list_procedures(self, schema: str = None) -> list:
        """list procedures — reflection"""
        sql="""SELECT proname FROM pg_proc WHERE proname LIKE '%%'"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_procedure_info(self, procedure: str) -> list:
        """get procedure info — reflection"""
        sql="""SELECT * FROM pg_proc WHERE proname=:procedure"""
        cur=self._exec(sql, (procedure,))
        return cur.fetchall() if cur.description else []

    def list_functions(self, schema: str = None) -> list:
        """list functions — reflection"""
        sql="""SELECT routine_name FROM information_schema.routines WHERE routine_type='FUNCTION'"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_function_info(self, function: str) -> list:
        """get function info — reflection"""
        sql="""SELECT * FROM information_schema.routines WHERE routine_name=:function"""
        cur=self._exec(sql, (function,))
        return cur.fetchall() if cur.description else []

    def list_packages(self, schema: str = None) -> list:
        """list packages (oracle) — reflection"""
        sql="""SELECT nspname FROM pg_namespace"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_package_info(self, package: str) -> list:
        """get package info — reflection"""
        sql="""SELECT * FROM pg_namespace WHERE nspname=:package"""
        cur=self._exec(sql, (package,))
        return cur.fetchall() if cur.description else []

    def list_synonyms(self, schema: str = None) -> list:
        """list synonyms — reflection"""
        sql="""SELECT * FROM information_schema.views WHERE table_name LIKE '%synonym%'"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_synonym_info(self, synonym: str) -> list:
        """get synonym info — reflection"""
        sql="""SELECT * FROM information_schema.views WHERE table_name=:synonym"""
        cur=self._exec(sql, (synonym,))
        return cur.fetchall() if cur.description else []

    def list_types(self, schema: str = None) -> list:
        """list types — reflection"""
        sql="""SELECT typname FROM pg_type"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_type_info(self, type_name: str) -> list:
        """get type info — reflection"""
        sql="""SELECT * FROM pg_type WHERE typname=:type_name"""
        cur=self._exec(sql, (type_name,))
        return cur.fetchall() if cur.description else []

    def list_domains(self, schema: str = None) -> list:
        """list domains — reflection"""
        sql="""SELECT domain_name FROM information_schema.domains"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_domain_info(self, domain: str) -> list:
        """get domain info — reflection"""
        sql="""SELECT * FROM information_schema.domains WHERE domain_name=:domain"""
        cur=self._exec(sql, (domain,))
        return cur.fetchall() if cur.description else []

    def list_extensions(self, ) -> list:
        """list extensions — reflection"""
        sql="""SELECT extname FROM pg_extension"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_extension_info(self, extension: str) -> list:
        """get extension info — reflection"""
        sql="""SELECT * FROM pg_extension WHERE extname=:extension"""
        cur=self._exec(sql, (extension,))
        return cur.fetchall() if cur.description else []

    def list_roles(self, ) -> list:
        """list roles — reflection"""
        sql="""SELECT rolname FROM pg_roles"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_role_info(self, role: str) -> list:
        """get role info — reflection"""
        sql="""SELECT * FROM pg_roles WHERE rolname=:role"""
        cur=self._exec(sql, (role,))
        return cur.fetchall() if cur.description else []

    def list_users_db(self, ) -> list:
        """list db users — reflection"""
        sql="""SELECT usename FROM pg_user"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_user_info(self, user: str) -> list:
        """get user info — reflection"""
        sql="""SELECT * FROM pg_user WHERE usename=:user"""
        cur=self._exec(sql, (user,))
        return cur.fetchall() if cur.description else []

    def list_privileges(self, ) -> list:
        """list privileges — reflection"""
        sql="""SELECT privilege_type FROM information_schema.role_table_grants"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_table_privileges(self, table: str) -> list:
        """get table privileges — reflection"""
        sql="""SELECT * FROM information_schema.role_table_grants WHERE table_name=:table"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_grants(self, user: str = None) -> list:
        """list grants — reflection"""
        sql="""SELECT * FROM information_schema.role_table_grants WHERE grantee=COALESCE(:user, grantee)"""
        cur=self._exec(sql, (user,))
        return cur.fetchall() if cur.description else []

    def get_grant_info(self, grantee: str) -> list:
        """get grant info — reflection"""
        sql="""SELECT * FROM information_schema.role_table_grants WHERE grantee=:grantee"""
        cur=self._exec(sql, (grantee,))
        return cur.fetchall() if cur.description else []

    def get_database_info(self, ) -> list:
        """get db info — reflection"""
        sql="""SELECT current_database(), pg_database_size(current_database())"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_database_version(self, ) -> list:
        """get version — reflection"""
        sql="""SELECT version()"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_database_size(self, ) -> list:
        """get db size — reflection"""
        sql="""SELECT pg_database_size(current_database())"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_table_size(self, table: str) -> list:
        """get table size — reflection"""
        sql="""SELECT pg_total_relation_size(:table)"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_index_size(self, index: str) -> list:
        """get index size — reflection"""
        sql="""SELECT pg_relation_size(:index)"""
        cur=self._exec(sql, (index,))
        return cur.fetchall() if cur.description else []

    def get_row_count(self, table: str) -> list:
        """get row count — reflection"""
        sql="""SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name=:table"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_estimated_row_count(self, table: str) -> list:
        """get estimated count — reflection"""
        sql="""SELECT reltuples FROM pg_class WHERE relname=:table"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def list_partitions(self, table: str = None) -> list:
        """list partitions — reflection"""
        sql="""SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent=:table::regclass"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_partition_info(self, partition: str) -> list:
        """get partition info — reflection"""
        sql="""SELECT * FROM pg_class WHERE relname=:partition"""
        cur=self._exec(sql, (partition,))
        return cur.fetchall() if cur.description else []

    def list_subpartitions(self, table: str = None) -> list:
        """list subpartitions — reflection"""
        sql="""SELECT * FROM pg_inherits"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_subpartition_info(self, subpartition: str) -> list:
        """get subpartition info — reflection"""
        sql="""SELECT * FROM pg_class WHERE relname=:subpartition"""
        cur=self._exec(sql, (subpartition,))
        return cur.fetchall() if cur.description else []

    def list_tablespaces(self, ) -> list:
        """list tablespaces — reflection"""
        sql="""SELECT spcname FROM pg_tablespace"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_tablespace_info(self, tablespace: str) -> list:
        """get tablespace info — reflection"""
        sql="""SELECT * FROM pg_tablespace WHERE spcname=:tablespace"""
        cur=self._exec(sql, (tablespace,))
        return cur.fetchall() if cur.description else []

    def list_datafiles(self, ) -> list:
        """list datafiles — reflection"""
        sql="""SELECT pg_relation_filepath(oid) FROM pg_class"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_datafile_info(self, datafile: str) -> list:
        """get datafile info — reflection"""
        sql="""SELECT * FROM pg_class WHERE relname=:datafile"""
        cur=self._exec(sql, (datafile,))
        return cur.fetchall() if cur.description else []

    def list_clusters(self, ) -> list:
        """list clusters — reflection"""
        sql="""SELECT relname FROM pg_class WHERE relkind='r'"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def list_db_links(self, ) -> list:
        """list db links — reflection"""
        sql="""SELECT * FROM pg_foreign_server"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_db_link_info(self, db_link: str) -> list:
        """get db link info — reflection"""
        sql="""SELECT * FROM pg_foreign_server WHERE srvname=:db_link"""
        cur=self._exec(sql, (db_link,))
        return cur.fetchall() if cur.description else []

    def list_jobs(self, ) -> list:
        """list jobs — reflection"""
        sql="""SELECT * FROM pg_stat_activity"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_job_info(self, job: str) -> list:
        """get job info — reflection"""
        sql="""SELECT * FROM pg_stat_activity WHERE pid=:job::int"""
        cur=self._exec(sql, (job,))
        return cur.fetchall() if cur.description else []

    def list_queues(self, ) -> list:
        """list queues — reflection"""
        sql="""SELECT * FROM pg_queues"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_queue_info(self, queue: str) -> list:
        """get queue info — reflection"""
        sql="""SELECT * FROM pg_queues WHERE queue_name=:queue"""
        cur=self._exec(sql, (queue,))
        return cur.fetchall() if cur.description else []

    def list_mviews(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT matviewname FROM pg_matviews"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_mview_info(self, mview: str) -> list:
        """get mview info — reflection"""
        sql="""SELECT * FROM pg_matviews WHERE matviewname=:mview"""
        cur=self._exec(sql, (mview,))
        return cur.fetchall() if cur.description else []

    def list_dependencies(self, object_name: str) -> list:
        """list dependencies — reflection"""
        sql="""SELECT * FROM pg_depend WHERE refobjid=:object_name::regclass"""
        cur=self._exec(sql, (object_name,))
        return cur.fetchall() if cur.description else []

    def get_dependency_info(self, object_name: str) -> list:
        """get dependency info — reflection"""
        sql="""SELECT * FROM pg_depend WHERE objid=:object_name::regclass"""
        cur=self._exec(sql, (object_name,))
        return cur.fetchall() if cur.description else []

    def list_invalid_objects(self, schema: str = None) -> list:
        """list invalid objects — reflection"""
        sql="""SELECT relname FROM pg_class WHERE relisvalid=false"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_object_info(self, object_name: str) -> list:
        """get object info — reflection"""
        sql="""SELECT * FROM pg_class WHERE relname=:object_name"""
        cur=self._exec(sql, (object_name,))
        return cur.fetchall() if cur.description else []

    def get_object_type(self, object_name: str) -> list:
        """get object type — reflection"""
        sql="""SELECT relkind FROM pg_class WHERE relname=:object_name"""
        cur=self._exec(sql, (object_name,))
        return cur.fetchall() if cur.description else []

    def list_objects_by_type(self, object_type: str) -> list:
        """list objects by type — reflection"""
        sql="""SELECT relname FROM pg_class WHERE relkind=:object_type"""
        cur=self._exec(sql, (object_type,))
        return cur.fetchall() if cur.description else []

    def get_ddl(self, object_type: str, object_name: str) -> list:
        """get ddl — reflection"""
        sql="""SELECT pg_get_ddl(:object_type, :object_name)"""
        cur=self._exec(sql, (object_type, object_name,))
        return cur.fetchall() if cur.description else []

    def get_create_statement(self, table: str) -> list:
        """get create statement — reflection"""
        sql="""SELECT pg_get_tabledef(:table::regclass)"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def describe_table(self, table: str) -> list:
        """describe table — reflection"""
        sql="""SELECT column_name, data_type FROM information_schema.columns WHERE table_name=:table"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def describe_view(self, view: str) -> list:
        """describe view — reflection"""
        sql="""SELECT view_definition FROM information_schema.views WHERE table_name=:view"""
        cur=self._exec(sql, (view,))
        return cur.fetchall() if cur.description else []

    def list_column_privileges(self, table: str = None) -> list:
        """list column privileges — reflection"""
        sql="""SELECT * FROM information_schema.column_privileges"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_table_comment(self, table: str) -> list:
        """get table comment — reflection"""
        sql="""SELECT obj_description(:table::regclass)"""
        cur=self._exec(sql, (table,))
        return cur.fetchall() if cur.description else []

    def get_view_definition(self, view: str) -> list:
        """get view definition — reflection"""
        sql="""SELECT view_definition FROM information_schema.views WHERE table_name=:view"""
        cur=self._exec(sql, (view,))
        return cur.fetchall() if cur.description else []

    def list_view_columns(self, view: str) -> list:
        """list view columns — reflection"""
        sql="""SELECT column_name FROM information_schema.columns WHERE table_name=:view"""
        cur=self._exec(sql, (view,))
        return cur.fetchall() if cur.description else []

    def get_sequence_next_value(self, sequence: str) -> list:
        """get sequence next value — reflection (nextval takes text, bind is valid)"""
        sql="""SELECT nextval(:sequence)"""
        cur=self._exec(sql, (sequence,))
        try:
            return cur.fetchall() if cur.description else []
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def list_enum_types(self, schema: str = None) -> list:
        """list enum types — reflection"""
        sql="""SELECT typname FROM pg_type WHERE typtype='e'"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

    def get_enum_values(self, enum_name: str) -> list:
        """get enum values — reflection"""
        sql="""SELECT enumlabel FROM pg_enum WHERE enumtypid=:enum_name::regtype"""
        cur=self._exec(sql, (enum_name,))
        return cur.fetchall() if cur.description else []

    def list_collations(self, ) -> list:
        """list collations — reflection"""
        sql="""SELECT collname FROM pg_collation"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_collation_info(self, collation: str) -> list:
        """get collation info — reflection"""
        sql="""SELECT * FROM pg_collation WHERE collname=:collation"""
        cur=self._exec(sql, (collation,))
        return cur.fetchall() if cur.description else []

    def list_operators(self, ) -> list:
        """list operators — reflection"""
        sql="""SELECT oprname FROM pg_operator"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def get_operator_info(self, operator: str) -> list:
        """get operator info — reflection"""
        sql="""SELECT * FROM pg_operator WHERE oprname=:operator"""
        cur=self._exec(sql, (operator,))
        return cur.fetchall() if cur.description else []

    def list_casts(self, ) -> list:
        """list casts — reflection"""
        sql="""SELECT * FROM pg_cast"""
        cur=self._exec(sql, ())
        return cur.fetchall() if cur.description else []

    def reflect_all(self, schema: str = None) -> list:
        """comprehensive reflection — reflection"""
        sql="""SELECT tablename FROM pg_tables WHERE schemaname=COALESCE(:schema, schemaname)"""
        cur=self._exec(sql, (schema,))
        return cur.fetchall() if cur.description else []

