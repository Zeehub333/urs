"""Workspace login / first-superuser tests (no live DB — fake PG cursor)."""
import json
import os
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from urs import views as _v


# sys_users shape as discovered on main_hq_2026 (name, type, nullable, default)
SYS_USERS_COLS = [
    ("id", "integer", "NO", "nextval('sys_users_id_seq'::regclass)"),
    ("username", "character varying", "NO", None),
    ("full_name", "character varying", "NO", None),
    ("email", "character varying", "NO", None),
    ("password", "character varying", "NO", None),
    ("role", "character varying", "NO", None),
    ("phone", "character varying", "YES", None),
    ("company_code", "character varying", "YES", None),
    ("branch_code", "character varying", "YES", None),
    ("is_active", "boolean", "NO", "true"),
    ("created_at", "timestamp without time zone", "YES", "CURRENT_TIMESTAMP"),
]


class _FakeCur:
    """Answers the information_schema / count / insert queries views.py issues."""

    def __init__(self, state):
        self.state = state
        self.executed = []
        self._rows = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "information_schema.tables" in sql:
            self._rows = [(self.state["schema"],)]
        elif "is_nullable" in sql:
            self._rows = list(self.state["fullcols"])
        elif "lower(column_name)" in sql:
            self._rows = [(n.lower(), t) for (n, t, _nu, _df) in self.state["fullcols"]]
        elif "SELECT column_name FROM" in sql:
            self._rows = [(n,) for (n, _t, _nu, _df) in self.state["fullcols"]]
        elif "count(*)" in sql:
            self._rows = [(self.state["count"],)]
        elif sql.strip().upper().startswith("SELECT 1"):
            self._rows = [(1,)] if self.state.get("exists") else []
        elif sql.strip().upper().startswith("INSERT"):
            self.state["insert"] = (sql, params)
            self._rows = []
        elif sql.strip().upper().startswith("UPDATE"):
            self.state["update"] = (sql, params)
            self._rows = []
        elif "LIMIT 1" in sql and "WHERE" in sql:
            _ur = self.state.get("userrow")
            self._rows = [_ur] if _ur is not None else []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, state):
        self.state = state
        self.cur = _FakeCur(state)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def close(self):
        pass


def _fresh_state(**kw):
    st = {"schema": "main_hq_2026", "fullcols": list(SYS_USERS_COLS),
          "count": 0, "exists": False, "insert": None,
          "userrow": None, "update": None}
    st.update(kw)
    return st


def _patched(state):
    """Patch all DB-touching seams of the workspace endpoints."""
    return mock.patch.multiple(
        _v,
        _ws_master_ok=mock.Mock(return_value=True),
        _ws_login_connection=mock.Mock(return_value=({"fake": True}, "sys_users", "")),
        _ws_pg_connect=mock.Mock(side_effect=lambda _obj: _FakeConn(state)),
    )


class RequiredFieldsUnitTests(SimpleTestCase):
    def test_detects_email_and_role_only(self):
        st = _fresh_state()
        cur = _FakeCur(st)
        req = _v._ws_required_user_fields(
            cur, "main_hq_2026", "sys_users",
            skip={"username", "password", "full_name", "is_active"})
        self.assertEqual(sorted(c["name"] for c in req), ["email", "role"])

    def test_nullable_and_defaulted_are_not_required(self):
        st = _fresh_state(fullcols=[
            ("a", "character varying", "YES", None),
            ("b", "integer", "NO", "0"),
            ("c", "text", "NO", None),
        ])
        req = _v._ws_required_user_fields(_FakeCur(st), "s", "t", skip=[])
        self.assertEqual([c["name"] for c in req], ["c"])

    def test_real_columns_map(self):
        st = _fresh_state(fullcols=[("Email", "character varying", "NO", None)])
        self.assertEqual(_v._ws_real_columns(_FakeCur(st), "s", "t"), {"email": "Email"})

    def test_never_raises_on_broken_cursor(self):
        class _Bad:
            def execute(self, *a, **k):
                raise RuntimeError("down")
        self.assertEqual(_v._ws_required_user_fields(_Bad(), "s", "t"), [])
        self.assertEqual(_v._ws_real_columns(_Bad(), "s", "t"), {})

    def test_fields_schema_types(self):
        st = _fresh_state(fullcols=[
            ("email", "character varying", "NO", None),
            ("is_active", "boolean", "NO", "true"),
            ("phone", "character varying", "YES", None),
            ("birth_date", "date", "YES", None),
            ("created_at", "timestamp without time zone", "YES", None),
            ("login_time", "time without time zone", "YES", None),
            ("notes", "text", "YES", None),
            ("age", "integer", "YES", None),
        ])
        fields = _v._ws_user_fields_schema(_FakeCur(st), "s", "t", skip=["created_at"])
        by_name = {f["name"]: f for f in fields}
        self.assertNotIn("created_at", by_name)
        self.assertEqual(by_name["email"]["input_type"], "email")
        self.assertEqual(by_name["is_active"]["input_type"], "checkbox")
        self.assertEqual(by_name["phone"]["input_type"], "tel")
        self.assertEqual(by_name["birth_date"]["input_type"], "date")
        self.assertEqual(by_name["login_time"]["input_type"], "time")
        self.assertEqual(by_name["notes"]["input_type"], "textarea")
        self.assertEqual(by_name["age"]["input_type"], "number")



class SuperuserEndpointTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.dirs = mock.patch("urs.workspace.workspace_dirs",
                               return_value=[SimpleNamespace(name="workspace_1")])
        self.dirs.start()
        self.addCleanup(self.dirs.stop)

    def _post(self, state, payload):
        with _patched(state):
            req = self.rf.post("/api/workspaces/workspace_1/superuser/",
                               data=json.dumps(payload),
                               content_type="application/json")
            return _v.api_workspace_superuser(req, "workspace_1"), state

    def test_users_count_reports_required_when_empty(self):
        st = _fresh_state()
        with _patched(st):
            req = self.rf.get("/api/workspaces/workspace_1/users-count/")
            res = _v.api_workspace_users_count(req, "workspace_1")
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["count"], 0)
        self.assertEqual(sorted(c["name"] for c in body["required"]), ["email", "role"])
        self.assertTrue(len(body.get("fields", [])) > 0)
        field_names = [f["name"] for f in body["fields"]]
        self.assertIn("email", field_names)
        self.assertIn("phone", field_names)

    def test_superuser_type_casting(self):
        st = _fresh_state()
        res, _ = self._post(st, {
            "username": "admin", "password": "secret123", "full_name": "المدير",
            "extra": {"email": "admin", "role": "admin", "is_active": "true", "phone": ""},
        })
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        sql, params = st["insert"]
        # Check that is_active was cast to True (bool)
        self.assertIn(True, params)
        # Check that empty nullable phone was cast to None (SQL NULL)
        self.assertIn(None, params)


    def test_users_count_no_required_when_not_empty(self):
        st = _fresh_state(count=3)
        with _patched(st):
            req = self.rf.get("/api/workspaces/workspace_1/users-count/")
            res = _v.api_workspace_users_count(req, "workspace_1")
        body = json.loads(res.content.decode())
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["required"], [])

    def test_accepts_extra_and_inserts_only_known_columns(self):
        st = _fresh_state()
        res, _ = self._post(st, {
            "username": "admin", "password": "secret123", "full_name": "المدير",
            "extra": {"email": "a@x.co", "role": "admin",
                      "username": "hacker", "nope'); DROP TABLE x;--": "z"},
        })
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body.get("ok"))
        sql, params = st["insert"]
        self.assertIn('"email"', sql)
        self.assertIn('"role"', sql)
        self.assertNotIn("DROP", sql)
        self.assertIn("a@x.co", params)
        self.assertIn("admin", params)
        # password must be hashed, never plaintext
        self.assertNotIn("secret123", params)

    def test_refuses_when_users_exist(self):
        st = _fresh_state(count=2)
        res, _ = self._post(st, {"username": "admin", "password": "secret123",
                                 "extra": {"email": "a@x.co", "role": "admin"}})
        self.assertEqual(res.status_code, 400)
        self.assertIsNone(st["insert"])

    def test_rejects_missing_required_fields(self):
        st = _fresh_state()
        res, _ = self._post(st, {"username": "admin", "password": "secret123"})
        body = json.loads(res.content.decode())
        _req = body.get("required", [])
        _missing = body.get("error", "")
        # When email is auto-composed (username@domain), it should NOT be in missing
        # Only role should be missing since it's explicitly required but not provided
        self.assertEqual(res.status_code, 400)
        # role must be missing (not provided, no auto-compose)
        self.assertIn("role", _missing)
        # email should NOT be missing (auto-composed from username + domain)
        self.assertNotIn("email", _missing)
        self.assertEqual(sorted(c["name"] for c in _req), ["email", "role"])
        self.assertIsNone(st["insert"])


class PasswordHashNamespaceTests(SimpleTestCase):
    """Django-format hashes (pbkdf2_sha256$) must verify via Django's parser;
    std-format hashes (pbkdf2_sha256_std$) via the stdlib branch. A past
    regression aliased the two namespaces and broke all Django-hash logins."""

    def test_django_format_verifies(self):
        from django.contrib.auth.hashers import make_password
        from fmlk_engine.engine import verify_secret, is_hashed_secret
        h = make_password("h123456*")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        self.assertTrue(is_hashed_secret(h))
        self.assertTrue(verify_secret("h123456*", h))
        self.assertFalse(verify_secret("wrong", h))
        self.assertTrue(_v._verify_login_password("h123456*", h, username="admin"))
        self.assertFalse(_v._verify_login_password("wrong", h, username="admin"))

    def test_std_format_verifies(self):
        import hashlib as _hl
        import os as _os
        from fmlk_engine.engine import verify_secret, _STD_HASH_PREFIX, _STD_HASH_ITERS
        salt = _os.urandom(16).hex()
        dk = _hl.pbkdf2_hmac("sha256", "h123456*".encode(), bytes.fromhex(salt), _STD_HASH_ITERS)
        h = "%s%d$%s$%s" % (_STD_HASH_PREFIX, _STD_HASH_ITERS, salt, dk.hex())
        self.assertTrue(verify_secret("h123456*", h))
        self.assertFalse(verify_secret("wrong", h))

    def test_namespaces_stay_distinct(self):
        from fmlk_engine.engine import _STD_HASH_PREFIX
        self.assertNotEqual(_STD_HASH_PREFIX, "pbkdf2_sha256$")

    def test_reference_fastapi_dialect_verifies(self):
        # Reference service: salt = token_hex used as UTF-8 bytes (NOT hex-decoded).
        import hashlib as _hl
        from fmlk_engine.engine import verify_secret
        salt, iters = "c9f978d5bdbedd43ca78757148a29ff8", 600000
        dk = _hl.pbkdf2_hmac("sha256", "h123456*".encode(), salt.encode(), iters).hex()
        h = "pbkdf2_sha256$%d$%s$%s" % (iters, salt, dk)
        self.assertTrue(verify_secret("h123456*", h))
        self.assertFalse(verify_secret("wrong", h))
        self.assertTrue(_v._verify_login_password("h123456*", h, username="admin"))

    def test_db_hash_shape_with_known_password(self):
        # Same shape as the production row (100k iters, hex salt/digest):
        # a hash MADE from a known password must verify end-to-end.
        import hashlib as _hl
        from fmlk_engine.engine import verify_secret
        salt, iters = "ab12cd34ef56ab12cd34ef56ab12cd34", 100000
        dk = _hl.pbkdf2_hmac("sha256", "known-pw-1".encode(), salt.encode(), iters).hex()
        h = "pbkdf2_sha256$%d$%s$%s" % (iters, salt, dk)
        self.assertTrue(verify_secret("known-pw-1", h))
        self.assertFalse(verify_secret("h123456*", h))

    def test_blank_padded_hash_verifies(self):
        # char(n) columns blank-pad — padded Django hash must still verify
        from django.contrib.auth.hashers import make_password
        h = make_password("h123456*") + "   "
        self.assertTrue(_v._verify_login_password("h123456*", h, username="admin"))
        self.assertFalse(_v._verify_login_password("wrong", h, username="admin"))

    def test_hash_itself_is_not_a_password(self):
        # pass-the-hash: entering the stored hash must NOT authenticate
        from django.contrib.auth.hashers import make_password
        import hashlib as _hl
        dh = make_password("h123456*")
        self.assertFalse(_v._verify_login_password(dh, dh, username="admin"))
        self.assertTrue(_v._verify_login_password("h123456*", dh, username="admin"))
        hx = _hl.sha256("h123456*".encode()).hexdigest()
        self.assertFalse(_v._verify_login_password(hx, hx, username="admin"))
        self.assertTrue(_v._verify_login_password("h123456*", hx, username="admin"))
        # legacy plaintext still works
        self.assertTrue(_v._verify_login_password("plain123", "plain123", username="admin"))


class PasswordHashColumnTests(SimpleTestCase):
    """password_hash (and siblings) must be treated as secrets everywhere:
    login columns, engine hashing/masking, designer type aliases."""

    def test_login_columns_include_password_hash(self):
        self.assertIn("password_hash", _v._WS_PASS_COLS)

    def test_engine_secret_names_include_password_hash(self):
        from fmlk_engine.engine import FMLKFormEngine
        self.assertIn("password_hash", FMLKFormEngine.SECRET_NAMES)

    def test_designer_alias_maps_password_hash(self):
        from fmlk_engine.field_types import normalize_input_type, get_type
        self.assertEqual(normalize_input_type("password_hash"), "password")
        self.assertEqual(normalize_input_type("email"), "email")
        self.assertEqual(normalize_input_type("email_field"), "email")
        self.assertEqual(normalize_input_type("mail"), "email")
        em_type = get_type("email")
        self.assertIsNotNone(em_type)
        self.assertIn("email_domain", em_type.get("attrs", []))


class UsersColumnMappingTests(SimpleTestCase):
    def setUp(self):
        from pathlib import Path as _P
        self.rf = RequestFactory()
        self.dirs = mock.patch("urs.workspace.workspace_dirs",
                               return_value=[_P("workspace_1")])
        self.dirs.start()
        self.addCleanup(self.dirs.stop)

    def test_colmap_reads_and_validates(self):
        with mock.patch("urs.workspace.read_conf",
                        return_value={"USERS_USER_COLUMN": "Login_Name",
                                      "USERS_PASSWORD_COLUMN": "pwd_hash"}):
            self.assertEqual(_v._ws_users_colmap("workspace_1"), ("login_name", "pwd_hash"))
        with mock.patch("urs.workspace.read_conf", return_value={}):
            self.assertEqual(_v._ws_users_colmap("workspace_1"), ("", ""))
        with mock.patch("urs.workspace.read_conf",
                        return_value={"USERS_USER_COLUMN": "bad name!",
                                      "USERS_PASSWORD_COLUMN": "password"}):
            self.assertEqual(_v._ws_users_colmap("workspace_1"), ("", "password"))

    def test_handled_cols_honor_pins(self):
        cols = {"username": "varchar", "login": "varchar",
                "password": "varchar", "pwd": "varchar"}
        u, p, _f, _s, _a = _v._ws_user_handled_cols(cols, "login", "pwd")
        self.assertEqual((u, p), ("login", "pwd"))
        u, p, _f, _s, _a = _v._ws_user_handled_cols(cols)
        self.assertEqual((u, p), ("username", "password"))
        u, p, _f, _s, _a = _v._ws_user_handled_cols(cols, "nope", "")
        self.assertEqual((u, p), ("username", "password"))

    def test_write_conf_roundtrip(self):
        import tempfile as _tf
        from pathlib import Path as _P
        with _tf.TemporaryDirectory() as td:
            ws = _P(td)
            (ws / "workspace.conf").write_text(
                "# test\nPRIMARY_CONNECTION=urs_local\nUSERS_TABLE=s.t\n", encoding="utf-8")
            _v._write_ws_conf(ws, "urs_local", "s.t", "active", "login", "pwd_hash")
            from urs import workspace as _wsm
            conf = _wsm.read_conf(ws / "workspace.conf")
            self.assertEqual(conf.get("USERS_USER_COLUMN"), "login")
            self.assertEqual(conf.get("USERS_PASSWORD_COLUMN"), "pwd_hash")
            # clearing drops the keys
            _v._write_ws_conf(ws, "urs_local", "s.t", "active", "", "")
            conf = _wsm.read_conf(ws / "workspace.conf")
            self.assertNotIn("USERS_USER_COLUMN", conf)
            self.assertNotIn("USERS_PASSWORD_COLUMN", conf)

    def test_users_columns_endpoint(self):
        st = _fresh_state()
        with _patched(st), \
                mock.patch.object(_v, "_ws_users_colmap", return_value=("username", "")):
            req = self.rf.get("/api/workspaces/workspace_1/users-columns/")
            res = _v.api_workspace_users_columns(req, "workspace_1")
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        names = {c["name"] for c in body["columns"]}
        self.assertIn("username", names)
        self.assertIn("email", names)
        self.assertEqual(body["user_column"], "username")

    def test_modal_has_mapping_selects(self):
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "templates", "home.html")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('id="wsF_user_column"', src)
        self.assertIn('id="wsF_password_column"', src)
        self.assertIn("wsColsFetch", src)
        self.assertIn("users_user_column", src)


class BranchPasswordContractTests(SimpleTestCase):
    """insert (hashed) / verify (plain) must hold on branch/member saves too:
    password-named columns hash even with a non-password inputType."""

    def test_name_based_detection(self):
        cols = [{"name": "username", "input_type": "text"},
                {"name": "password_hash", "input_type": "text"},
                {"name": "pwd", "input_type": "text"},
                {"name": "notes", "input_type": "textarea"}]
        self.assertEqual(_v._branch_password_cols(None, cols), {"password_hash", "pwd"})

    def test_prep_hashes_plain_and_keeps_hash(self):
        from fmlk_engine.engine import hash_secret as _hs
        already = _hs("h123456*")
        rows = _v._prep_branch_secrets(
            [{"username": "a", "password_hash": "h123456*"},
             {"username": "b", "password_hash": already},
             {"username": "c", "password_hash": "**********"}],
            {"password_hash"})
        self.assertTrue(rows[0]["password_hash"].startswith("pbkdf2_sha256"))
        self.assertNotIn("h123456*", rows[0]["password_hash"])
        self.assertEqual(rows[1]["password_hash"], already)
        self.assertNotIn("password_hash", rows[2])


class LoginDiagResetTests(SimpleTestCase):
    def setUp(self):
        from pathlib import Path as _P
        self.rf = RequestFactory()
        self.dirs = mock.patch("urs.workspace.workspace_dirs",
                               return_value=[_P("workspace_1")])
        self.dirs.start()
        self.addCleanup(self.dirs.stop)

    def _call(self, state, fn, payload, master=True):
        with mock.patch.multiple(
                _v,
                _ws_master_ok=mock.Mock(return_value=master),
                _ws_login_connection=mock.Mock(return_value=({"fake": True}, "sys_users", "")),
                _ws_pg_connect=mock.Mock(side_effect=lambda _obj: _FakeConn(state))):
            req = self.rf.post("/x/", data=json.dumps(payload), content_type="application/json")
            return fn(req, "workspace_1"), state

    def test_hash_kind(self):
        import hashlib as _hl
        from django.contrib.auth.hashers import make_password
        k = _v._ws_hash_kind
        self.assertEqual(k(""), "empty")
        self.assertEqual(k(make_password("x")), "django-hash")
        import os as _os
        from fmlk_engine.engine import _STD_HASH_PREFIX, _STD_HASH_ITERS
        salt = _os.urandom(16).hex()
        dk = _hl.pbkdf2_hmac("sha256", b"x", bytes.fromhex(salt), _STD_HASH_ITERS).hex()
        self.assertEqual(k("%s%d$%s$%s" % (_STD_HASH_PREFIX, _STD_HASH_ITERS, salt, dk)), "std-pbkdf2")
        self.assertEqual(k("pbkdf2_sha256$100000$" + "ab" * 16 + "$" + "cd" * 32), "foreign-hex-pbkdf2")
        self.assertEqual(k("ab" * 32), "hex-digest")
        self.assertEqual(k("plain123"), "plaintext-or-unknown")

    def test_diag_unknown_user(self):
        st = _fresh_state()
        res, _ = self._call(st, _v.api_workspace_login_diag, {"username": "ghost"})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertFalse(body["found"])
        self.assertEqual(body["verdict"], "unknown-user")

    def test_diag_bad_password_shape(self):
        from django.contrib.auth.hashers import make_password
        st = _fresh_state(userrow=("admin", make_password("real-pw-1"), True))
        res, _ = self._call(st, _v.api_workspace_login_diag, {"username": "admin"})
        body = json.loads(res.content.decode())
        self.assertTrue(body["found"])
        self.assertEqual(body["verdict"], "bad-password")
        self.assertEqual(body["hash_format"], "django-hash")
        self.assertTrue(body["code_selftest"])

    def test_diag_duplicates(self):
        from django.contrib.auth.hashers import make_password
        st = _fresh_state(count=2, userrow=("admin", make_password("x"), True))
        res, _ = self._call(st, _v.api_workspace_login_diag, {"username": "admin"})
        body = json.loads(res.content.decode())
        self.assertEqual(body["verdict"], "duplicates")

    def test_diag_inactive(self):
        from django.contrib.auth.hashers import make_password
        st = _fresh_state(userrow=("admin", make_password("x"), False))
        res, _ = self._call(st, _v.api_workspace_login_diag, {"username": "admin"})
        body = json.loads(res.content.decode())
        self.assertEqual(body["verdict"], "inactive")

    def test_diag_requires_master(self):
        st = _fresh_state()
        res, _ = self._call(st, _v.api_workspace_login_diag, {"username": "admin"}, master=False)
        self.assertEqual(res.status_code, 403)

    def test_reset_ok(self):
        from fmlk_engine.engine import verify_secret as _vs
        st = _fresh_state(exists=True)
        res, _ = self._call(st, _v.api_workspace_login_reset,
                            {"username": "admin", "new_password": "h123456*"})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        sql, params = st["update"]
        self.assertIn("UPDATE", sql)
        self.assertTrue(_vs("h123456*", params[0]))
        self.assertNotIn("h123456*", params)

    def test_reset_short_and_unknown(self):
        st = _fresh_state(exists=True)
        res, _ = self._call(st, _v.api_workspace_login_reset,
                            {"username": "admin", "new_password": "123"})
        self.assertEqual(res.status_code, 400)
        self.assertIsNone(st["update"])
        st = _fresh_state(exists=False)
        res, _ = self._call(st, _v.api_workspace_login_reset,
                            {"username": "ghost", "new_password": "h123456*"})
        self.assertEqual(res.status_code, 404)

    def test_modal_has_diag_ui(self):
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "templates", "home.html")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('id="wsDg_user"', src)
        self.assertIn("wsLoginDiag", src)
        self.assertIn("wsLoginReset", src)
        self.assertIn("login-diag", src)


class FormulasMigrateTests(SimpleTestCase):
    def test_generate_shapes(self):
        from rml_python import formulas_migrate as _fm
        g = _fm.generate_ddl("public")
        self.assertTrue(g["total"] > 50)
        self.assertTrue(all(f["sql"].startswith("CREATE OR REPLACE FUNCTION") for f in g["functions"]))
        names = {f["name"] for f in g["functions"]}
        self.assertIn("UPPER", names)
        self.assertIn("IF", names)
        skipped = {s["name"] for s in g["skipped"]}
        self.assertIn("SUM", skipped)
        self.assertIn("XLOOKUP", skipped)
        self.assertEqual(_fm.pg_name("Upper"), "fn_upper")
        one = _fm.function_ddl("s1", "UPPER")
        self.assertIn('"s1"."fn_upper"', one["sql"])
        with self.assertRaises(KeyError):
            _fm.function_ddl("public", "XLOOKUP")

    def test_translate_and_expr(self):
        from rml_python import formulas_migrate as _fm
        self.assertEqual(_fm.translate_formula("=UPPER([name])"), 'UPPER("name")')
        self.assertEqual(_fm.translate_formula("=IF([a], [b], 'x')"), 'IF("a", "b", \'x\')')
        with self.assertRaises(ValueError):
            _fm.translate_formula("=XLOOKUP([a], [b])")
        with self.assertRaises(ValueError):
            _fm.translate_formula("=UPPER([a]); DROP TABLE t")
        self.assertEqual(_fm.expr_to_sql("price * 1.15"), "price * 1.15")
        self.assertEqual(_fm.expr_to_sql("=UPPER([n])"), 'UPPER("n")')
        self.assertEqual(_fm.expr_to_sql("XSQL: SELECT id FROM t"), "( SELECT id FROM t )")
        with self.assertRaises(ValueError):
            _fm.expr_to_sql("XSQL: SELECT XLOOKUP(a) FROM t")
        with self.assertRaises(ValueError):
            _fm.expr_to_sql("")

    def test_validate_call_and_ddl(self):
        from rml_python import formulas_migrate as _fm
        self.assertEqual(_fm.validate_call("=myschema.fn_upper(name)"), "myschema.fn_upper(name)")
        with self.assertRaises(ValueError):
            _fm.validate_call("not a call")
        with self.assertRaises(ValueError):
            _fm.validate_call("fn_x(a); DROP TABLE t")
        meta = _fm.validate_ddl("CREATE OR REPLACE FUNCTION public.fn_a(TEXT) RETURNS TEXT LANGUAGE sql AS $$ SELECT $1 $$;")
        self.assertEqual((meta["schema"], meta["name"]), ("public", "fn_a"))
        with self.assertRaises(ValueError):
            _fm.validate_ddl("DROP TABLE t;")
        with self.assertRaises(ValueError):
            _fm.validate_ddl("CREATE OR REPLACE FUNCTION public.fn_a() RETURNS TEXT AS $$ SELECT 1 $$; DELETE FROM t;")

    def test_rulevars_expression_kinds(self):
        from rml_python import rulevars as _rv
        self.assertEqual(_rv._value_sql("expression", "=UPPER([n])"), 'UPPER("n")')
        self.assertEqual(_rv._value_sql("expression", "price * 2"), "price * 2")
        self.assertEqual(_rv._value_sql("custom_function", "fn_upper(name)"), "fn_upper(name)")
        with self.assertRaises(ValueError):
            _rv._value_sql("custom_function", "hello world")
        with self.assertRaises(ValueError):
            _rv._value_sql("expression", "=XLOOKUP([a])")

    def test_formulas_endpoints(self):
        req = RequestFactory().get("/api/formulas/")
        res = _v.api_formulas_list(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["total"] > 50)
        req = RequestFactory().post("/api/formulas/migrate/",
                                    data=json.dumps({"schema": "public", "functions": ["UPPER", "NOPE"]}),
                                    content_type="application/json")
        res = _v.api_formulas_migrate(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertFalse(body["executed"])
        self.assertEqual(len(body["ddl"]), 1)
        self.assertEqual(body["skipped"][0]["name"], "NOPE")
        # execute without master → 403, no DB touched
        req = RequestFactory().post("/api/formulas/migrate/",
                                    data=json.dumps({"connection": "x", "execute": True}),
                                    content_type="application/json")
        res = _v.api_formulas_migrate(req)
        self.assertEqual(res.status_code, 403)
        req = RequestFactory().post("/api/formulas/ddl/",
                                    data=json.dumps({"connection": "x", "ddl": "DROP TABLE t"}),
                                    content_type="application/json")
        res = _v.api_formulas_ddl(req)
        self.assertEqual(res.status_code, 403)

    def test_autocomplete_endpoint(self):
        from rml_python import formulas_migrate as _fm
        defs = _fm.autocomplete_defaults()
        self.assertTrue(len(defs) > 60)
        self.assertTrue(all({"fn", "tpl", "hint", "kind"} <= set(d) for d in defs))
        kinds = {d["kind"] for d in defs}
        self.assertEqual(kinds, {"formula", "xsql"})
        req = RequestFactory().get("/api/formulas/autocomplete/")
        res = _v.api_formulas_autocomplete(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(len(body["defaults"]) > 60)
        self.assertEqual(body["migrated"], [])
        self.assertEqual(body["custom"], [])
        req = RequestFactory().get("/api/formulas/autocomplete/", {"connection": "nope", "schema": "public"})
        res = _v.api_formulas_autocomplete(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertIn("note", body)

    def test_bare_aliases(self):
        from rml_python import formulas_migrate as _fm
        a = _fm.alias_ddl("public", "IF")
        self.assertEqual(a["name"], "if")
        self.assertEqual(a["alias_of"], "fn_if")
        self.assertIn('"public"."fn_if"($1, $2, $3)', a["sql"])
        self.assertIsNone(_fm.alias_ddl("public", "UPPER"))
        g = _fm.generate_ddl("public", ["IF", "UPPER"])
        self.assertEqual([f["name"] for f in g["functions"]], ["IF", "UPPER"])
        self.assertEqual([a["name"] for a in g["aliases"]], ["if"])
        g2 = _fm.generate_ddl("public", ["IF"], alias=False)
        self.assertEqual(g2["aliases"], [])
        # bare calls pass the expression translator untouched
        self.assertEqual(_fm.translate_formula("=myfunc([x])"), 'myfunc("x")')
        self.assertEqual(_fm.validate_call("myfunc(a, b)"), "myfunc(a, b)")

    def test_migrate_preview_has_aliases(self):
        req = RequestFactory().post("/api/formulas/migrate/",
                                    data=json.dumps({"schema": "public", "functions": ["IF"]}),
                                    content_type="application/json")
        res = _v.api_formulas_migrate(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["aliases"], ["if"])
        self.assertEqual(len(body["ddl"]), 2)

    def test_autocomplete_all_sources(self):
        from rml_python import formulas_migrate as _fm
        natives = _fm.native_entries()
        self.assertTrue(len(natives) > 40)
        self.assertTrue(all({"fn", "tpl", "hint", "kind"} <= set(d) for d in natives))
        self.assertTrue(all(d["kind"] == "native" for d in natives))
        fns = {d["fn"] for d in natives}
        self.assertTrue({"UPPER", "COALESCE", "STRING_AGG", "ROW_NUMBER"} <= fns)
        req = RequestFactory().get("/api/formulas/autocomplete/", {"all": "1"})
        res = _v.api_formulas_autocomplete(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(len(body["natives"]) > 40)
        self.assertTrue(len(body["defaults"]) > 60)
        self.assertEqual(body["migrated"], [])
        self.assertEqual(body["custom"], [])

    def test_wizard_autocomplete_fetch(self):
        import os as _os
        path = "workspace_1/apps/settings/modals/rml_wizard_script.html"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for token in ("fxEqMatches", "fxFnsFetch", "fxFnPool", "fxFnFilter",
                      "FX_FN_CACHE", "/api/formulas/autocomplete/?all=1"):
            self.assertIn(token, src)

    def test_fx_modal_select_all(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_designer.html"), encoding="utf-8") as f:
            src = f.read()
        for token in ("fxToggleAll", "fxSyncAll", 'id="fxAll"', "تحديد الكل"):
            self.assertIn(token, src)

    def test_designer_has_fx_ui(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_designer.html"), encoding="utf-8") as f:
            src = f.read()
        for token in ("fxOpen", "fxMigrate", "fxDdlRun", "/api/formulas/migrate/", "دالة مخصصة"):
            self.assertIn(token, src)


class RmlSortDedupeTests(SimpleTestCase):
    """Sorting must survive duplicated SELECT aliases (MSSQL 209): ORDER BY
    uses the 1-based ordinal of the first matching column; exact-duplicate
    select items are dropped."""

    def _cols(self):
        from types import SimpleNamespace as _NS
        return [_NS(alias="رقم الاكسبرس", name="PublicNumber", expr="T0.PublicNumber"),
                _NS(alias="تاريخ الحوالة", name="TheDate", expr="T0.TheDate"),
                _NS(alias="تاريخ الحوالة", name="TheDate", expr="T0.TheDate"),
                _NS(alias="المحافظة", name="ProvinceName", expr="T5.ProvinceName")]

    def test_order_by_ordinal_first_occurrence(self):
        from rml_python.engine import _build_order_by
        cols = self._cols()
        self.assertEqual(_build_order_by({"column": "تاريخ الحوالة", "direction": "desc"}, cols),
                         " ORDER BY 2 DESC")
        self.assertEqual(_build_order_by([{"column": "المحافظة"}], cols), " ORDER BY 4 ASC")

    def test_order_by_fallback_alias(self):
        from rml_python.engine import _build_order_by
        self.assertEqual(_build_order_by({"column": "ghost", "direction": "xx"}, []),
                         ' ORDER BY "ghost" ASC')

    def test_dedupe_select_items(self):
        from rml_python.engine import _dedupe_select_items
        items = ['"T0"."A" AS "x"', '"T0"."B" AS "y"', '"T0"."A" AS "x"',
                 '"T0"."C" AS "x"']
        self.assertEqual(_dedupe_select_items(items),
                         ['"T0"."A" AS "x"', '"T0"."B" AS "y"', '"T0"."C" AS "x"'])
        self.assertEqual(_dedupe_select_items([]), [])


class RmlRefnameTests(SimpleTestCase):
    """col_refname: @ref resolves to the final row VALUE (derived tables),
    never to a merged expression."""

    def _col(self, **kw):
        from types import SimpleNamespace as _NS
        d = {"id": kw.get("name", ""), "name": "", "alias": "", "expr": "",
             "col_refname": None, "where_clause": None}
        d.update(kw)
        return _NS(**d)

    def _eng(self):
        from rml_python.engine import RMLReportEngine as _E
        return object.__new__(_E)

    def test_refname_plan_and_wrap(self):
        eng = self._eng()
        a = self._col(name="net", alias="الصافي", expr='"T0"."AMOUNT" - "T0"."DISC"', col_refname="net_total")
        b = self._col(name="tax", alias="الضريبة", expr="@net_total * 0.15", col_refname="tax_v")
        c = self._col(name="gross", alias="الإجمالي", expr="@net_total + @tax_v")
        cols = [a, b, c]
        plan = eng._plan_value_refs(cols)
        self.assertIsNotNone(plan)
        self.assertEqual([[x.name for x in lvl] for lvl in plan["levels"]], [["tax"], ["gross"]])
        self.assertEqual([x.name for x in plan["base"]], ["net"])
        sql = eng._wrap_value_levels(
            'SELECT "T0"."AMOUNT" - "T0"."DISC" AS "الصافي"', '"sch"."t" "T0"', "", "",
            " ORDER BY 2 DESC", "", ["الصافي", "الضريبة", "الإجمالي"],
            plan["levels"], plan["resolved"])
        self.assertIn('"الصافي"', sql)
        self.assertIn("ORDER BY 2 DESC", sql)
        self.assertNotIn("@net_total", sql)
        # unknown @tokens → no plan (old behavior preserved)
        d = self._col(name="x", alias="X", expr="@ghost + 1")
        self.assertIsNone(eng._plan_value_refs([d]))

    def test_refname_cycles_and_python(self):
        eng = self._eng()
        a = self._col(name="a", alias="A", expr="@b_r", col_refname="a_r")
        b = self._col(name="b", alias="B", expr="@a_r", col_refname="b_r")
        with self.assertRaises(ValueError):
            eng._plan_value_refs([a, b])
        s = self._col(name="s", alias="S", expr="@py_r + 1")
        p = self._col(name="p", alias="P", expr="XLOOKUP([a], [b])", col_refname="py_r")
        with self.assertRaises(ValueError):
            eng._plan_value_refs([s, p])
        me = self._col(name="m", alias="M", expr="@m_r + 1", col_refname="m_r")
        with self.assertRaises(ValueError):
            eng._plan_value_refs([me])

    def test_refname_with_get_and_bracket(self):
        eng = self._eng()
        p = self._col(name="salary", alias="الراتب", expr="=get(employees.basic_salary)", col_refname="base_sal")
        s = self._col(name="bonus", alias="البونص", expr="[base_sal] * 0.1")
        plan = eng._plan_value_refs([p, s])
        self.assertIsNotNone(plan)
        self.assertEqual([x.name for x in plan["base"]], ["salary"])
        self.assertEqual([[x.name for x in lvl] for lvl in plan["levels"]], [["bonus"]])

    def test_compiler_parses_refname(self):
        from rml_python.compiler import RMLColumn
        import dataclasses
        c = RMLColumn(id="1", name="n", alias="a")
        self.assertIsNone(c.col_refname)
        c2 = dataclasses.replace(c, col_refname="net_total")
        self.assertEqual(c2.col_refname, "net_total")
        self.assertEqual(c2.to_dict()["colRefname"], "net_total")


class RmlPaletteTests(SimpleTestCase):
    def test_xml_roundtrip_visible_width_refname(self):
        import tempfile as _tf
        from pathlib import Path as _P
        xml = ('<rml><rpt_metadata name="t" displayName="T" category="X" schema="S"/>'
               '<columns>'
               '<column id="1" name="a" alias="A" expr="a" col_refname="a_v"/>'
               '<column id="2" name="b" alias="B" expr="b" visible="0" width="220"/>'
               '<column id="3" name="c" alias="C" expr="c"/>'
               '</columns></rml>')
        with _tf.TemporaryDirectory() as td:
            p = _P(td) / "t.rml"
            p.write_text(xml, encoding="utf-8")
            from rml_python.compiler import RMLReportCompiler
            cols = {c.name: c for c in RMLReportCompiler(path=p).columns()}
        self.assertEqual(cols["a"].col_refname, "a_v")
        self.assertFalse(cols["b"].visible)
        self.assertEqual(cols["b"].width, 220)
        self.assertTrue(cols["c"].visible)
        self.assertIsNone(cols["c"].width)

    def test_render_xml_persists_new_attrs(self):
        cols = [{"id": "1", "name": "a", "alias": "A", "expr": "a", "col_refname": "a_v"},
                {"id": "2", "name": "b", "alias": "B", "expr": "b", "visible": False, "width": 220},
                {"id": "3", "name": "c", "alias": "C", "expr": "c"}]
        out = _v._render_rml_xml("t", "T", "fa-x", "X", "S", "", None, [], [], cols,
                                 [], [], "master", None, [], "", [], False, [], 1, "", {})
        self.assertIn('col_refname="a_v"', out)
        self.assertIn('visible="0"', out)
        self.assertIn('width="220"', out)
        self.assertNotIn('visible="0" verbo', out)

    def test_empty_column_and_render_xml_no_db_column_fallback(self):
        from rml_python.compiler import RMLColumn, RMLField
        from rml_python.engine import _build_select
        col = RMLColumn(id="25", name="col_25", alias="عمود 25", expr="")
        fld = RMLField(id="1", name="salary", table_source="emp", connection_id="1")
        sel = _build_select([col], fields=[fld])
        self.assertIn('NULL AS "عمود 25"', sel)
        self.assertNotIn('"col_25"', sel)

        cols = [{"id": "25", "name": "col_25", "alias": "عمود 25", "expr": ""}]
        out = _v._render_rml_xml("t", "T", "fa-x", "X", "S", "", None, [], [], cols,
                                 [], [], "master", None, [], "", [], False, [], 1, "", {})
        self.assertIn('name="col_25"', out)
        self.assertIn('expr=""', out)

    def test_design_preview_validates(self):
        req = RequestFactory().post("/api/apps/settings/rml/design-preview/",
                                    data=json.dumps({}), content_type="application/json")
        res = _v.api_rml_design_preview(req, "settings")
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 400)
        self.assertIn("اتصال", body.get("error", ""))

    def test_wizard_palette_markup(self):
        with open("workspace_1/apps/settings/modals/rml_wizard_script.html", encoding="utf-8") as f:
            src = f.read()
        for token in ("rpt-flex", "rptSideColsHTML", "rptPreviewFetch", "design-preview",
                      "rptDrag", "rptDrop", "rptPropsRender", "col_refname", "rptDetListHTML",
                      "قيمة ✓"):
            self.assertIn(token, src)

    def test_player_width_visibility(self):
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "templates", "report_player.html")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for token in ("colWidthInput", "setColWidth", "colWidthStyle", "colWidths",
                      "rml_widths_", "visible === false"):
            self.assertIn(token, src)


class SuperuserModalMarkupTests(SimpleTestCase):
    def test_home_modal_supports_dynamic_extra_fields(self):
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "templates", "home.html")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("data-ws-su-extra", src)
        self.assertIn("extra", src)


class JsonSourceStoreTests(SimpleTestCase):
    """Pure json_source tests (temp dir as base — no repo writes, no DB)."""

    def setUp(self):
        import tempfile as _tf
        self._td = _tf.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.base = self._td.name

    def _write(self, name, doc):
        import os as _os
        import json as _js
        p = _os.path.join(self.base, name)
        with open(p, "w", encoding="utf-8") as f:
            _js.dump(doc, f, ensure_ascii=False)
        return name

    def test_shapes_and_tables(self):
        from urs import json_source as _j
        self._write("a.json", {"users": [{"id": 1}], "orders": []})
        doc, mode = _j.load("a.json", self.base)
        self.assertEqual(mode, "file")
        self.assertEqual(_j.tables(doc), ["orders", "users"])
        self._write("b.json", [{"id": 1}])
        doc2, _ = _j.load("b.json", self.base)
        self.assertEqual(_j.tables(doc2), ["data"])
        with self.assertRaises(ValueError):
            _j.tables({"a": 1})
        with self.assertRaises(ValueError):
            _j.load("missing.json", self.base)

    def test_source_guards(self):
        from urs import json_source as _j
        self.assertTrue(_j.is_json_source("https://x/y.json"))
        self.assertTrue(_j.is_json_source("data/f.json"))
        self.assertFalse(_j.is_json_source("data/f.csv"))
        self.assertFalse(_j.is_json_source(""))
        with self.assertRaises(ValueError):
            _j.resolve_path("../evil.json", self.base)
        with self.assertRaises(ValueError):
            _j.resolve_path("data.csv", self.base)

    def test_columns_inference(self):
        from urs import json_source as _j
        self._write("c.json", {"t": [
            {"id": 1, "price": 2.5, "ok": True, "day": "2026-01-05", "name": "x"},
            {"id": 2, "price": 3, "ok": False, "day": "2026-01-06", "name": "y", "extra": None},
        ]})
        doc, _ = _j.load("c.json", self.base)
        cols = {c["name"]: c["type"] for c in _j.columns(doc, "t")}
        self.assertEqual(cols["id"], "INTEGER")
        self.assertEqual(cols["price"], "NUMERIC")
        self.assertEqual(cols["ok"], "BOOLEAN")
        self.assertEqual(cols["day"], "DATE")
        self.assertEqual(cols["name"], "TEXT")
        self.assertEqual(cols["extra"], "TEXT")

    def test_preview_filters(self):
        from urs import json_source as _j
        self._write("p.json", {"t": [
            {"id": 1, "name": "ahmad"}, {"id": 2, "name": "Sara"}, {"id": 3, "name": "ahmad x"}]})
        doc, _ = _j.load("p.json", self.base)
        cols, rows = _j.preview(doc, "t", 50, q="AHMAD")
        self.assertEqual(len(rows), 2)
        cols, rows = _j.preview(doc, "t", 50, filters=[{"col": "id", "op": "gt", "val": "1"}])
        self.assertEqual([r["id"] for r in rows], [2, 3])
        cols, rows = _j.preview(doc, "t", 1)
        self.assertEqual(len(rows), 1)

    def test_row_write_roundtrip(self):
        from urs import json_source as _j
        self._write("w.json", {"t": [{"id": 1, "name": "a"}]})
        doc, _ = _j.load("w.json", self.base)
        _j.insert(doc, "t", {"id": 2, "name": "b"})
        self.assertEqual(_j.update(doc, "t", {"id": 1}, {"name": "a2"}), 1)
        self.assertEqual(_j.update(doc, "t", {"id": 9}, {"name": "z"}), 0)
        self.assertEqual(_j.delete(doc, "t", {"id": 2}), 1)
        with self.assertRaises(ValueError):
            _j.update(doc, "t", {}, {"name": "z"})
        _j.save("w.json", doc, self.base)
        doc2, _ = _j.load("w.json", self.base)
        self.assertEqual(doc2, {"t": [{"id": 1, "name": "a2"}]})


class JsonConnectionEndpointTests(SimpleTestCase):
    """JSON connection endpoints with a mocked connection row (no DB)."""

    def setUp(self):
        import tempfile as _tf
        import json as _js
        import os as _os
        self.rf = RequestFactory()
        self._td = _tf.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.doc = {"users": [{"id": 1, "name": "ahmad", "role": "admin"},
                              {"id": 2, "name": "sara", "role": "user"}]}
        with open(_os.path.join(self._td.name, "u.json"), "w", encoding="utf-8") as f:
            _js.dump(self.doc, f, ensure_ascii=False)
        self.fake = SimpleNamespace(id=99, name="json1", host="u.json", port=0,
                                    user="", password="", instance="", engine="json",
                                    conn_type="json", is_local=True, is_queryable=True,
                                    schema="", endpoint="att")
        self._eff = mock.patch.object(_v, "_effective_or_row", return_value=self.fake)
        self._eff.start()
        self.addCleanup(self._eff.stop)
        self._base = mock.patch.object(_v, "BASE_DIR", self._td.name)
        self._base.start()
        self.addCleanup(self._base.stop)

    def test_tables_columns_preview(self):
        req = self.rf.get("/api/connections/99/tables/")
        res = _v.api_connection_tables(req, 99)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["tables"], [{"name": "users", "schema": "", "full": "users"}])
        req = self.rf.get("/api/connections/99/tables/users/columns/")
        res = _v.api_connection_table_columns(req, 99, "users")
        body = json.loads(res.content.decode())
        names = {c["name"] for c in body["columns"]}
        self.assertEqual(names, {"id", "name", "role"})
        req = self.rf.get("/api/connections/99/tables/users/preview/", {"limit": "50", "q": "sara"})
        res = _v.api_connection_table_preview(req, 99, "users")
        body = json.loads(res.content.decode())
        self.assertEqual(body["columns"], ["id", "name", "role"])
        self.assertEqual(body["rows"], [[2, "sara", "user"]])

    def test_row_insert_update_delete(self):
        req = self.rf.post("/api/connections/99/tables/users/rows/insert/",
                           data=json.dumps({"row": {"id": 3, "name": "omar"}}),
                           content_type="application/json")
        res = _v.api_json_row_insert(req, 99, "users")
        self.assertEqual(res.status_code, 200)
        req = self.rf.post("/api/connections/99/tables/users/rows/update/",
                           data=json.dumps({"match": {"id": 3}, "patch": {"role": "user"}}),
                           content_type="application/json")
        res = _v.api_json_row_update(req, 99, "users")
        self.assertEqual(json.loads(res.content.decode())["updated"], 1)
        req = self.rf.post("/api/connections/99/tables/users/rows/delete/",
                           data=json.dumps({"match": {"id": 3}}),
                           content_type="application/json")
        res = _v.api_json_row_delete(req, 99, "users")
        self.assertEqual(json.loads(res.content.decode())["deleted"], 1)
        # persisted back to the file
        from urs import json_source as _j
        doc, _ = _j.load("u.json", self._td.name)
        self.assertEqual(len(doc["users"]), 2)

    def test_rejects_non_json_connection(self):
        self.fake.engine = "postgres"
        req = self.rf.post("/api/connections/99/tables/users/rows/insert/",
                           data=json.dumps({"row": {"id": 1}}),
                           content_type="application/json")
        res = _v.api_json_row_insert(req, 99, "users")
        self.assertEqual(res.status_code, 400)

    def test_test_connection_ok_and_missing(self):
        ok, payload, status = _v._test_connection_obj(self.fake)
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIn("2", payload["note"])
        bad = SimpleNamespace(**{**vars(self.fake), "host": "nope.json"})
        ok, payload, status = _v._test_connection_obj(bad)
        self.assertFalse(ok)
        self.assertEqual(status, 400)

    def test_player_and_wizard_know_json(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "forms_player.html"), encoding="utf-8") as f:
            self.assertIn("CONN_JSON_ENGINES", f.read())
        with open("workspace_1/apps/settings/modals/rml_wizard_script.html", encoding="utf-8") as f:
            self.assertIn("json", f.read())


class UserProfileAndHeaderTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_change_password_endpoint(self):
        from fmlk_engine.engine import hash_secret
        st = _fresh_state(
            fullcols=[("username", "text", "NO", None),
                      ("password", "text", "NO", None),
                      ("full_name", "text", "YES", None)],
            userrow=("admin", hash_secret("secret123"), "Admin Full Name")
        )
        req = self.rf.post("/api/user/change-password/",
                           data=json.dumps({"current_password": "secret123",
                                            "new_password": "newpassword123",
                                            "confirm_password": "newpassword123"}),
                           content_type="application/json")
        req.session = {
            "ws_user": {"username": "admin", "full_name": "Admin Full Name"},
            "workspace": "workspace_1",
            "fiscal_schema": "main_hq_2026",
        }
        with mock.patch.multiple(_v,
                                 _ws_login_connection=mock.Mock(return_value=({"fake": True}, "sys_users", "")),
                                 _ws_pg_connect=mock.Mock(side_effect=lambda _obj: _FakeConn(st))):
            res = _v.api_user_change_password(req)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.content)
            self.assertTrue(data.get("ok"))
            self.assertIsNotNone(st.get("update"))
            sql, params = st["update"]
            self.assertIn("UPDATE", sql)
            self.assertNotEqual(params[0], "newpassword123")
            self.assertTrue(params[0].startswith("pbkdf2_"))

    def test_profile_update_endpoint(self):
        st = _fresh_state(
            fullcols=[("username", "text", "NO", None),
                      ("full_name", "text", "YES", None),
                      ("email", "text", "YES", None)],
            userrow=("admin", "Admin Old", "admin@old.com")
        )
        req = self.rf.post("/api/user/profile/",
                           data=json.dumps({"full_name": "Ahmed Mohamed", "email": "ahmed@example.com"}),
                           content_type="application/json")
        req.session = {
            "ws_user": {"username": "admin", "full_name": "Admin Old"},
            "workspace": "workspace_1",
            "fiscal_schema": "main_hq_2026",
        }
        with mock.patch.multiple(_v,
                                 _ws_login_connection=mock.Mock(return_value=({"fake": True}, "sys_users", "")),
                                 _ws_pg_connect=mock.Mock(side_effect=lambda _obj: _FakeConn(st))):
            res = _v.api_user_profile(req)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.content)
            self.assertEqual(data.get("full_name"), "Ahmed Mohamed")
            self.assertEqual(req.session["ws_user"]["full_name"], "Ahmed Mohamed")


class OracleClientUtilTests(SimpleTestCase):
    def test_oracle_error_hints(self):
        from urs.oracle_util import oracle_error_hint
        h3015 = oracle_error_hint("DPY-3015: password verifier type 0x939 is not supported", user="rpt")
        self.assertIn("0x939", h3015)
        self.assertIn("rpt", h3015)

        h1017 = oracle_error_hint("ORA-01017: invalid username/password", user="rpt", password="pbkdf2_sha256$600000$test")
        self.assertIn("ORA-01017", h1017)
        self.assertIn("hash", h1017)

    def test_fmlk_engine_does_not_hash_connection_passwords(self):
        from fmlk_engine.engine import FMLKFormEngine
        eng = mock.Mock(spec=FMLKFormEngine)
        eng.table = "urs_connection"
        out = FMLKFormEngine._hash_secrets(eng, {"password": "plain_password_123"}, {"password"})
        self.assertEqual(out.get("password"), "plain_password_123")

    def test_connections_fmlk_has_custom_actions(self):
        from pathlib import Path
        from fmlk_engine.compiler import FMLKFormCompiler
        c = FMLKFormCompiler(Path("workspace_2/apps/settings/connections.fmlk"))
        action_names = [a.name for a in c.actions()]
        self.assertIn("test_connection", action_names)
        self.assertIn("test_connection_form", action_names)
        tc = next(a for a in c.actions() if a.name == "test_connection")
        self.assertEqual(tc.label, "اختبار الاتصال")
        self.assertEqual(tc.endpoint, "/api/connections/test/")
        self.assertEqual(tc.level, "record")

    def test_resolve_connection_record_prefers_name_over_conflicting_id(self):
        from urs.views import _resolve_connection_record
        from urs.models import Connection
        with mock.patch("urs.models.Connection.objects.filter") as m_filter:
            m_target = mock.Mock(spec=Connection)
            m_target.id = 16
            m_target.name = "اونكس تعافي"
            m_filter.return_value.first.return_value = m_target
            obj = _resolve_connection_record({"id": 2, "name": "اونكس تعافي"})
            self.assertEqual(obj.name, "اونكس تعافي")
            m_filter.assert_called_with(name="اونكس تعافي")

    def test_resolve_connection_record_with_arabic_aliases(self):
        from urs.views import _resolve_connection_record
        from urs.models import Connection
        with mock.patch("urs.models.Connection.objects.filter") as m_filter:
            m_target = mock.Mock(spec=Connection)
            m_target.name = "اونكس تعافي"
            m_filter.return_value.first.return_value = m_target
            obj = _resolve_connection_record({
                "اسم الاتصال": "اونكس تعافي",
                "نوع المحرك": "oracle",
                "المضيف / IP الجهاز": "172.16.10.100",
                "__pk_id": 2,
            })
            self.assertEqual(obj.name, "اونكس تعافي")
            m_filter.assert_called_with(name="اونكس تعافي")

    def test_password_encryption_algorithms_and_shift(self):
        from fmlk_engine.crypto import list_password_algorithms, encrypt_or_hash_password, verify_password_algorithm
        from fmlk_engine.field_types import get_type, CONFIG_ATTRS
        # 1. Config attributes & registry
        self.assertIn("hash_algo", CONFIG_ATTRS["password"])
        self.assertIn("shift", CONFIG_ATTRS["password"])
        pw_t = get_type("password")
        self.assertIn("hash_algo", pw_t.get("attrs", []))
        self.assertIn("shift", pw_t.get("attrs", []))

        # 2. 20 algorithms count & order
        algos = list_password_algorithms()
        self.assertEqual(len(algos), 20)
        self.assertEqual(algos[0]["key"], "caesar")
        self.assertEqual(algos[-1]["key"], "scrypt")

        # 3. Plaintext ("none" / "لا شيء")
        pw = "MyPass123_@#!"
        self.assertEqual(encrypt_or_hash_password(pw, "none"), pw)
        self.assertTrue(verify_password_algorithm(pw, pw, "none"))

        # 4. Caesar with positive & negative unicode shift
        enc_pos = encrypt_or_hash_password(pw, "caesar", shift=4)
        self.assertTrue(enc_pos.startswith("caesar$4$"))
        self.assertTrue(verify_password_algorithm(pw, enc_pos))
        self.assertFalse(verify_password_algorithm("wrong_password", enc_pos))

        enc_neg = encrypt_or_hash_password(pw, "caesar", shift=-5)
        self.assertTrue(enc_neg.startswith("caesar$-5$"))
        self.assertTrue(verify_password_algorithm(pw, enc_neg))

        # 5. All 20 algorithms encrypt and verify properly
        for a in algos:
            k = a["key"]
            enc = encrypt_or_hash_password(pw, k)
            self.assertTrue(verify_password_algorithm(pw, enc, k), f"Failed for algo {k}")

        # 6. Engine integration
        from fmlk_engine.engine import hash_secret, verify_secret
        eng_enc = hash_secret(pw, algorithm="caesar", shift=7)
        self.assertTrue(eng_enc.startswith("caesar$7$"))
        self.assertTrue(verify_secret(pw, eng_enc))

    def test_custom_actions_roundtrip_and_compiler(self):
        from fmlk_engine.compiler import FMLKFormCompiler
        xml = """<fml>
          <fml_metadata name="test" displayName="Test" table="t"/>
          <fields><field name="id" dataType="INTEGER" primary_key="true"/></fields>
          <custom_actions>
            <action name="test_connection" label="اختبار الاتصال" endpoint="/api/connections/test/" icon="fa-plug-circle-check" badge_color="#059669" level="record"/>
            <action name="conn_stats" label="إحصائيات الاتصال" endpoint="/api/connections/stats/" render="modals/conn_stats.html" icon="fa-chart-simple" badge_color="#4f46e5" level="record"/>
            <action name="export_data" label="تصدير" endpoint="/api/fmlk/export/" icon="fa-file-excel" badge_color="#16a34a" level="view"/>
          </custom_actions>
        </fml>"""
        comp = FMLKFormCompiler.from_string(xml)
        actions = comp.actions()
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0].name, "test_connection")
        self.assertEqual(actions[0].label, "اختبار الاتصال")
        self.assertEqual(actions[0].badge_color, "#059669")
        self.assertEqual(actions[0].level, "record")
        self.assertEqual(actions[1].render, "modals/conn_stats.html")
        self.assertEqual(actions[2].level, "view")

        # Test dictionary export format
        dicts = [a.to_dict() for a in actions]
        self.assertEqual(dicts[0]["endpoint"], "/api/connections/test/")
        self.assertEqual(dicts[1]["render"], "modals/conn_stats.html")
        self.assertEqual(dicts[2]["badge_color"], "#16a34a")

    def test_resolve_connection_schema_isolates_external_connections(self):
        from unittest.mock import patch, MagicMock
        from urs.views import _resolve_connection_schema
        from django.test import RequestFactory
        rf = RequestFactory()

        class DummyConn:
            def __init__(self, id, name, schema):
                self.id = id
                self.name = name
                self.schema = schema

        primary = DummyConn(1, "rex_local", "default_pg")
        external = DummyConn(16, "اونكس تعافي", "IAS20264")

        # Mock workspace context: primary_conn="rex_local", fiscal_schema="ies202601"
        with patch("urs.views._ws_session_context", return_value=("ws1", "ies202601", "rex_local")):
            # 1. External connection MUST preserve its own schema and NOT get corrupted by ies202601
            req = rf.get("/api/connections/16/tables/")
            sch = _resolve_connection_schema(req, external)
            self.assertEqual(sch, "IAS20264")

            # 2. Primary connection receives the workspace fiscal_schema
            sch_p = _resolve_connection_schema(req, primary)
            self.assertEqual(sch_p, "ies202601")

            # 3. Explicit URL param ?schema= overrides
            req_custom = rf.get("/api/connections/16/tables/?schema=CUSTOM_SCH")
            sch_c = _resolve_connection_schema(req_custom, external)
            self.assertEqual(sch_c, "CUSTOM_SCH")

    def test_api_connection_table_values_validation(self):
        from unittest.mock import patch, MagicMock
        from urs.views import api_connection_table_values
        from django.test import RequestFactory
        import json

        rf = RequestFactory()

        # 1. Connection not found -> 404
        with patch("urs.views._effective_or_row", return_value=None):
            req = rf.get("/api/connections/999/tables/ACCOUNT/values/?column=A_CODE")
            resp = api_connection_table_values(req, 999, "ACCOUNT")
            self.assertEqual(resp.status_code, 404)

        # 2. Missing or invalid column name -> 400
        dummy = MagicMock()
        dummy.id = 1
        dummy.engine = "postgres"
        with patch("urs.views._effective_or_row", return_value=dummy):
            # Missing column
            req_no_col = rf.get("/api/connections/1/tables/ACCOUNT/values/")
            resp_no_col = api_connection_table_values(req_no_col, 1, "ACCOUNT")
            self.assertEqual(resp_no_col.status_code, 400)

            # Invalid column (SQL injection pattern)
            req_bad_col = rf.get("/api/connections/1/tables/ACCOUNT/values/?column=bad;drop")
            resp_bad_col = api_connection_table_values(req_bad_col, 1, "ACCOUNT")
            self.assertEqual(resp_bad_col.status_code, 400)

        # 3. Successful values fetch using mock _table_values_obj
        with patch("urs.views._effective_or_row", return_value=dummy), \
             patch("urs.views._resolve_connection_schema", return_value="public"), \
             patch("urs.views._table_values_obj", return_value=["val1", "val2", "val3"]):
            req_ok = rf.get("/api/connections/1/tables/ACCOUNT/values/?column=A_CODE&limit=10")
            resp_ok = api_connection_table_values(req_ok, 1, "ACCOUNT")
            self.assertEqual(resp_ok.status_code, 200)
            data = json.loads(resp_ok.content.decode("utf-8"))
            self.assertEqual(data["values"], ["val1", "val2", "val3"])
            self.assertEqual(data["total"], 3)
            self.assertEqual(data["column"], "A_CODE")