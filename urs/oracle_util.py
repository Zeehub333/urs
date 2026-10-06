"""Oracle Client utilities — thick mode auto-detection, DPY-3015 resolution, and connection helpers."""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path
from typing import Any, Optional

_oracle_thick_done = False
_oracle_thick_failed = False
_oracle_thick_err = ""


def find_oracle_client_lib_dir() -> Optional[str]:
    """Auto-detect directory containing Oracle Client libraries (oci.dll on Windows / libclntsh on Linux)."""
    candidates: list[Path] = []

    # 1. Environment variables
    for ev in ("ORACLE_HOME", "OCI_LIB_DIR", "TNS_ADMIN"):
        v = os.environ.get(ev)
        if v and os.path.exists(v):
            candidates.append(Path(v))
            candidates.append(Path(v) / "bin")

    # 2. Windows WinGet package directories
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.exists():
            try:
                for p in winget_dir.glob("*InstantClient*/**/oci.dll"):
                    candidates.append(p.parent)
            except Exception:
                pass

    # 3. Common drive root directories (C:, D:, etc.)
    for drive in ("C:", "D:"):
        for pattern in (r"\instantclient*", r"\oracle\instantclient*", r"\app\*\product\*\client*"):
            try:
                for p in glob.glob(drive + pattern):
                    candidates.append(Path(p))
                    candidates.append(Path(p) / "bin")
            except Exception:
                pass

    # 4. PATH entries
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p and os.path.exists(p):
            candidates.append(Path(p))

    lib_name = "oci.dll" if sys.platform == "win32" else ("libclntsh.dylib" if sys.platform == "darwin" else "libclntsh.so")
    for c in candidates:
        try:
            if (c / lib_name).exists():
                return str(c)
        except Exception:
            continue
    return None


def ensure_oracle_client(config_dir: Optional[str | Path] = None) -> bool:
    """Initialize Oracle Client thick mode if possible. Safe to call multiple times."""
    global _oracle_thick_done, _oracle_thick_failed, _oracle_thick_err
    if _oracle_thick_done:
        return True
    try:
        import oracledb
        if not getattr(oracledb, "is_thin_mode", lambda: True)():
            _oracle_thick_done = True
            return True

        lib_dir = find_oracle_client_lib_dir()
        cfg_path = None
        if config_dir:
            cfg_path = Path(config_dir)
        else:
            cfg_path = Path(__file__).resolve().parent.parent / "oracle_config"
        try:
            cfg_path.mkdir(parents=True, exist_ok=True)
            sqlnet = cfg_path / "sqlnet.ora"
            if not sqlnet.exists():
                sqlnet.write_text("SQLNET.AUTHENTICATION_SERVICES=(NONE)\n", encoding="utf-8")
        except Exception:
            cfg_path = None

        cfg_str = str(cfg_path) if cfg_path and cfg_path.exists() else None

        init_kwargs = {}
        if lib_dir:
            init_kwargs["lib_dir"] = lib_dir
        if cfg_str:
            init_kwargs["config_dir"] = cfg_str

        try:
            oracledb.init_oracle_client(**init_kwargs)
        except Exception:
            # Fallback without config_dir if config_dir had issues
            if "config_dir" in init_kwargs:
                init_kwargs.pop("config_dir", None)
                oracledb.init_oracle_client(**init_kwargs)
            else:
                raise

        _oracle_thick_done = True
        return True
    except Exception as e:
        msg = str(e)
        if "already been initialized" in msg:
            _oracle_thick_done = True
            return True
        _oracle_thick_failed = True
        _oracle_thick_err = msg
        return False


def is_thick_mode() -> bool:
    """True if python-oracledb is currently operating in thick mode."""
    try:
        import oracledb
        return not getattr(oracledb, "is_thin_mode", lambda: True)()
    except Exception:
        return False


def oracle_error_hint(exc: Exception | str, user: str = "", password: str = "") -> str:
    """Returns an actionable Arabic explanation for Oracle database errors."""
    msg = str(exc or "")
    if "3015" in msg or "0x939" in msg:
        u = user or "المستخدم"
        return (
            f"مدقق كلمة المرور في أوراكل (0x939) قديم (10g) وغير مدعوم في الوضع الخفيف (thin mode). "
            f"الحل الجذري: ALTER USER {u} IDENTIFIED BY <password>; لتوليد مدقق حديث (11g/12c) "
            f"أو تثبيت Oracle Instant Client لدعم الوضع السميك."
        )
    if "ORA-01017" in msg:
        hint = ""
        if str(password or "").startswith("pbkdf2_sha256$"):
            hint = " (تنبيه: كلمة المرور المحفوظة تم تشفيرها كـ hash سابقاً — يرجى إعادة كتابة كلمة المرور الصريحة في سجل الاتصال وحفظه)"
        return f"فشل المصادقة مع أوراكل: اسم المستخدم أو كلمة المرور غير صحيحة (ORA-01017) (المستخدم: {user or 'غير محدد'}){hint}"
    if "ORA-12638" in msg:
        return (
            "فشل جلب بيانات اعتماد Windows (NTS). "
            "الحلول: 1) أدخل مستخدم وكلمة مرور قاعدة البيانات صراحةً في الاتصال (وليس مصادقة Windows)، "
            "2) اضبط ملف sqlnet.ora بوضع SQLNET.AUTHENTICATION_SERVICES=(NONE)، "
            "3) راجع DBA للتأكد من السماح بالمصادقة بكلمة المرور."
        )
    if "ORA-12541" in msg or "ORA-12514" in msg or "TNS:no listener" in msg:
        return f"تعذر الوصول لسيرفر أوراكل أو خدمة/SID غير موجودة (تحقق من المضيف، المنفذ، واسم الخدمة/SID)."
    return msg
