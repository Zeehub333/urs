"""
odex main — dynamic engines demo (Oracle, SQL Server, ZK, Postgres)
50 dynamic CRUD functions per engine, no static tables/columns.

Usage:
  python -m odex.main --engine postgres --dsn "host=localhost dbname=odex user=postgres password=postgres"
  python -m odex.main --engine oracle --dsn "localhost:1521/XEPDB1" --user odex --password odex
  python -m odex.main --engine sqlserver --conn-str "DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=odex;UID=sa;PWD=Passw0rd;TrustServerCertificate=yes"
  python -m odex.main --engine zk --host 192.168.1.201

Env (python-dotenv) supported: ODEX_ENGINE, ODEX_DSN, ODEX_USER, ODEX_PASSWORD, ODEX_CONN_STR, ODEX_ZK_HOST, etc.
"""
import argparse
import os
import sys
from pathlib import Path

# ensure imports work when run as `python C:\urs2\odex\main.py` (no package)
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

try:
    from engines.oracle import OracleEngine
    from engines.sqlserver import SqlServerEngine
    from engines.postgres import PostgresEngine
    from engines.zk import ZKEngine
except ImportError:
    from odex.engines.oracle import OracleEngine
    from odex.engines.sqlserver import SqlServerEngine
    from odex.engines.postgres import PostgresEngine
    from odex.engines.zk import ZKEngine


def get_engine(args) -> tuple:
    eng = (args.engine or os.getenv("ODEX_ENGINE") or "postgres").lower()
    if eng == "oracle":
        dsn = args.dsn or os.getenv("ODEX_DSN") or "localhost:1521/XEPDB1"
        user = args.user or os.getenv("ODEX_USER") or "odex"
        pw = args.password or os.getenv("ODEX_PASSWORD") or "odex"
        e = OracleEngine(dsn=dsn, user=user, password=pw)
        desc = f"Oracle dsn={dsn} user={user}"
        return e, desc
    if eng == "sqlserver":
        conn_str = args.conn_str or os.getenv("ODEX_CONN_STR") or "DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=odex;UID=sa;PWD=Passw0rd;TrustServerCertificate=yes"
        e = SqlServerEngine(conn_str=conn_str)
        return e, f"SQLServer conn_str={conn_str[:60]}..."
    if eng == "zk":
        host = args.host or os.getenv("ODEX_ZK_HOST") or "192.168.1.201"
        port = int(args.port or os.getenv("ODEX_ZK_PORT") or 4370)
        e = ZKEngine(ip=host, port=port)
        return e, f"ZK {host}:{port}"
    # default postgres
    dsn = args.dsn or os.getenv("ODEX_DSN") or "host=localhost dbname=odex user=postgres password=postgres"
    e = PostgresEngine(dsn=dsn)
    return e, f"Postgres dsn={dsn}"


def demo(engine):
    """Run a suite of dynamic CRUD demos on a temporary table (or 'users' for ZK)."""
    print("\n--- dynamic demo: create -> list -> filter -> update -> aggregate -> export ---")
    # choose a table name that works for all engines; ZK only supports 'users'/'attendance'
    table = "odex_demo" if engine.__class__.__name__ != "ZKEngine" else "users"
    is_zk = engine.__class__.__name__ == "ZKEngine"

    try:
        engine.connect()
        print(f"[connect] {engine.__class__.__name__} connected")

        # 1. create
        if is_zk:
            row = {"user_id": "999", "uid": 999, "name": "Demo User", "privilege": 0, "card": 0}
        else:
            # ensure demo table exists (postgres/oracle/sqlserver)
            try:
                engine.raw_execute(f'CREATE TABLE IF NOT EXISTS "{table}" (id SERIAL PRIMARY KEY, name TEXT, email TEXT)' if "Postgres" in engine.__class__.__name__
                                   else f"CREATE TABLE {table} (id INT IDENTITY PRIMARY KEY, name NVARCHAR(100), email NVARCHAR(100))" if "SqlServer" in engine.__class__.__name__
                                   else f"CREATE TABLE {table} (id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name VARCHAR2(100), email VARCHAR2(100))")
            except Exception as ex:
                print(f"[create table] skip: {ex}")
            row = {"name": "Demo User", "email": "demo@odex.local"}
        engine.create(table, row) if not is_zk else engine.create(table, row)
        print(f"[create] {table} -> {row}")

        # 2. bulk_create
        if not is_zk:
            engine.bulk_create(table, [{"name": f"User{i}", "email": f"user{i}@odex.local"} for i in range(2)])
            print("[bulk_create] 2 rows")

        # 3. count / exists / list_all
        print(f"[count] {engine.count(table)}")
        print(f"[exists] {engine.exists(table, {'name': 'Demo User'}) if not is_zk else engine.exists(table, {'name': 'Demo User'})}")
        rows = engine.list_all(table, limit=5)
        print(f"[list_all] {rows[:2]}")

        # 4. filter / search / where_in / etc.
        print(f"[filter] {engine.filter(table, {'name': 'Demo User'}, limit=5)[:1]}")
        print(f"[search] {engine.search(table, 'name', 'Demo', limit=5)[:1]}")
        print(f"[pluck] {engine.pluck(table, 'name')[:3]}")
        print(f"[distinct] {engine.distinct(table, 'name')[:3]}")

        # 5. aggregate family
        if not is_zk:
            print(f"[group_by name] {engine.group_by(table, 'name')[:2]}")
        print(f"[where_not_null] {len(engine.where_not_null(table, 'name', limit=5))} rows")

        # 6. update / increment / upsert
        if not is_zk:
            first = engine.first(table)
            if first:
                pid = first.get("id") or first.get("ID") or first.get("Id")
                engine.update_by_id(table, pid, {"email": "updated@odex.local"})
                print(f"[update_by_id] id={pid}")
                engine.increment(table, pid, "id", 0)  # demo

        # 7. paginate / order_by_limit / sample
        print(f"[paginate] {engine.paginate(table, page=1, page_size=2)}")
        print(f"[order_by_limit] {engine.order_by_limit(table, 'name', limit=2)[:1]}")
        print(f"[sample] {engine.sample(table, n=2)[:1]}")

        # 8. export
        out = Path(__file__).with_name(f"export_{table}.csv")
        n = engine.export_csv(table, str(out))
        print(f"[export_csv] {n} rows -> {out}")

        # 9. schema
        print(f"[get_schema] {engine.get_schema(table)[:3]}")

    finally:
        # cleanup demo rows (keep table for next run)
        try:
            if not is_zk:
                engine.delete_where(table, {"email": "demo@odex.local"})
                engine.delete_where(table, {"email": "updated@odex.local"})
                print(f"[cleanup] demo rows deleted")
        except Exception as ex:
            print(f"[cleanup] {ex}")
        try:
            engine.disconnect()
            print("[disconnect] ok")
        except Exception:
            pass


def main():
    p = argparse.ArgumentParser(description="odex dynamic engines — 50 CRUD funcs each")
    p.add_argument("--engine", choices=["oracle", "sqlserver", "postgres", "zk"], help="engine to use")
    p.add_argument("--dsn", help="DSN for oracle/postgres")
    p.add_argument("--conn-str", dest="conn_str", help="ODBC connection string for sqlserver")
    p.add_argument("--user", help="oracle user")
    p.add_argument("--password", help="oracle/postgres password")
    p.add_argument("--host", help="zk host ip")
    p.add_argument("--port", type=int, help="zk port")
    p.add_argument("--table", default="odex_demo", help="dynamic table to demo")
    p.add_argument("--list-engines", action="store_true", help="list available engines and exit")
    args = p.parse_args()

    if args.list_engines:
        print("engines: oracle (oracledb), sqlserver (pyodbc), postgres (psycopg2), zk (pyzk)")
        for cls in [OracleEngine, SqlServerEngine, PostgresEngine, ZKEngine]:
            funcs = [m for m in dir(cls) if not m.startswith("_")]
            print(f"  {cls.__name__}: {len(funcs)} funcs")
        return 0

    engine, desc = get_engine(args)
    print(f"engine: {desc} -> {engine.__class__.__name__}")
    # also allow custom table demo without full lifecycle
    if args.table != "odex_demo":
        engine.connect()
        try:
            print(f"table={args.table} count={engine.count(args.table)} schema={engine.get_schema(args.table)}")
        finally:
            engine.disconnect()
        return 0

    demo(engine)
    return 0


if __name__ == "__main__":
    # allow `python main.py` or `python -m odex.main`
    # ensure C:\urs2 is on sys.path when run as file
    if __package__ is None or __package__ == "":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
