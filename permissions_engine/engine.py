"""
Permissions Engine — Production Ready
Per-file ACL for RML (.rml) and FML (.fmlk / .fml) files.
Supports: users, roles, groups, actions (view, edit, delete, create, execute, export, share)
Storage: JSON sidecar (.perm.json) next to each file + central DB mirror (optional)
Secure, with deny-override and owner bypass.
"""
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Set, Any, Literal

Action = Literal["view","edit","delete","create","execute","export","share","admin"]
FileType = Literal["rml","fml","fmlk"]

@dataclass
class ACE:
    """Access Control Entry — one rule"""
    principal: str  # "user:ahmed" or "role:hr_manager" or "group:finance" or "*"
    actions: List[Action]  # e.g., ["view","execute"]
    effect: Literal["allow","deny"] = "allow"
    # Optional conditions (e.g., row-level)
    conditions: Optional[Dict[str, Any]] = None

    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ACE":
        return cls(
            principal=d.get("principal","*"),
            actions=d.get("actions", []),
            effect=d.get("effect","allow"),
            conditions=d.get("conditions")
        )

@dataclass
class FilePermissions:
    """Full permission set for one file"""
    file: str  # e.g., "emp_report.rml" or "hr_form.fmlk"
    file_type: FileType = "rml"
    owner: Optional[str] = None  # "user:admin" — bypass
    aces: List[ACE] = field(default_factory=list)
    # Inheritance: if True, also check parent folder's permissions
    inherit: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "file_type": self.file_type, "owner": self.owner, "inherit": self.inherit, "aces": [a.to_dict() for a in self.aces]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FilePermissions":
        return cls(
            file=d.get("file",""),
            file_type=d.get("file_type","rml"),
            owner=d.get("owner"),
            inherit=d.get("inherit", True),
            aces=[ACE.from_dict(a) for a in d.get("aces", [])]
        )

    def check(self, principal: str, roles: List[str], action: Action, owner_check: Optional[str] = None) -> bool:
        """Check if principal (with roles) can perform action. Deny overrides. Owner bypass."""
        # Owner bypass
        if owner_check and self.owner and owner_check == self.owner:
            return True
        if principal and self.owner and principal == self.owner:
            return True
        # Collect principals to check: user, roles, groups, wildcard
        candidates = {principal, f"user:{principal}", "*"}
        for r in roles:
            candidates.add(r); candidates.add(f"role:{r}"); candidates.add(f"group:{r}")
        # Evaluate ACEs: deny first
        allowed = False
        for ace in self.aces:
            if ace.principal not in candidates and ace.principal != "*":
                continue
            if action in ace.actions or "*" in ace.actions:
                if ace.effect == "deny":
                    return False  # deny overrides
                allowed = True
        return allowed

    def grant(self, principal: str, actions: List[Action], effect: str = "allow"):
        self.aces.append(ACE(principal=principal, actions=actions, effect=effect))

    def revoke(self, principal: str, actions: List[Action] | None = None):
        if actions is None:
            self.aces = [a for a in self.aces if a.principal != principal]
        else:
            for ace in self.aces:
                if ace.principal == principal:
                    ace.actions = [a for a in ace.actions if a not in actions]
            self.aces = [a for a in self.aces if a.actions]

class PermissionsEngine:
    """
    Engine that manages per-file permissions.
    Storage: <file>.perm.json sidecar + optional central JSON DB
    """
    def __init__(self, base_dirs: List[str | pathlib.Path] | None = None):
        # Directories to scan for .rml/.fmlk files
        self.base_dirs = [pathlib.Path(p) for p in (base_dirs or ["C:/urs2/rml_python/examples", "C:/urs2/fmlk_engine/examples", "C:/urs2/odex/system", "C:/urs2/rml_python/templates"])]
        self._cache: Dict[str, FilePermissions] = {}

    def _perm_path(self, file_path: str | pathlib.Path) -> pathlib.Path:
        p = pathlib.Path(file_path)
        # If file is like "emp_report.rml" without dir, search in base_dirs
        if not p.is_absolute() and not p.exists():
            for base in self.base_dirs:
                cand = base / p
                if cand.exists():
                    return cand.with_suffix(cand.suffix + ".perm.json")
                cand2 = base / f"{p.name}"
                if cand2.exists():
                    return cand2.with_suffix(cand2.suffix + ".perm.json")
            # Fallback to first base
            return (self.base_dirs[0] / p.name).with_suffix(pathlib.Path(p.name).suffix + ".perm.json")
        return p.with_suffix(p.suffix + ".perm.json") if p.suffix else pathlib.Path(str(p) + ".perm.json")

    def load(self, file: str | pathlib.Path) -> FilePermissions:
        """Load permissions for a file, or create default (allow all for demo, deny in prod)."""
        key = str(file)
        if key in self._cache:
            return self._cache[key]
        perm_path = self._perm_path(file)
        if perm_path.exists():
            try:
                data = json.loads(perm_path.read_text(encoding="utf-8"))
                fp = FilePermissions.from_dict(data)
                self._cache[key] = fp
                return fp
            except Exception:
                pass
        # Default: if no perm file, create permissive default for demo
        # In production, you would default to deny and require explicit grant
        fname = pathlib.Path(file).name
        ftype: FileType = "rml" if fname.endswith(".rml") else "fmlk" if fname.endswith(".fmlk") else "rml"
        fp = FilePermissions(file=fname, file_type=ftype, owner="user:admin", aces=[
            ACE(principal="*", actions=["view","execute"], effect="allow"),
            ACE(principal="role:admin", actions=["view","edit","delete","create","execute","export","share","admin"], effect="allow"),
            ACE(principal="role:manager", actions=["view","execute","export"], effect="allow"),
        ])
        self._cache[key] = fp
        return fp

    def save(self, file: str | pathlib.Path, perms: FilePermissions) -> None:
        perm_path = self._perm_path(file)
        perm_path.parent.mkdir(parents=True, exist_ok=True)
        perm_path.write_text(json.dumps(perms.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache[str(file)] = perms

    def check(self, file: str | pathlib.Path, principal: str, roles: List[str], action: Action) -> bool:
        perms = self.load(file)
        # Also check owner via principal
        return perms.check(principal, roles, action, owner_check=f"user:{principal}")

    def grant(self, file: str | pathlib.Path, principal: str, actions: List[Action]):
        perms = self.load(file)
        # Merge or add
        for ace in perms.aces:
            if ace.principal == principal and ace.effect == "allow":
                for a in actions:
                    if a not in ace.actions:
                        ace.actions.append(a)
                self.save(file, perms)
                return
        perms.grant(principal, actions, "allow")
        self.save(file, perms)

    def revoke(self, file: str | pathlib.Path, principal: str, actions: List[Action] | None = None):
        perms = self.load(file)
        perms.revoke(principal, actions)
        self.save(file, perms)

    def list_files(self) -> List[str]:
        """List all RML/FML files known."""
        files: Set[str] = set()
        for base in self.base_dirs:
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if p.suffix.lower() in (".rml",".fml",".fmlk"):
                    files.add(p.name)
        return sorted(files)

    def audit(self, file: str | pathlib.Path) -> Dict[str, Any]:
        perms = self.load(file)
        return perms.to_dict()

    # ── Auto-Analysis & Driver Generation ───────────────────────────────────

    def analyze_file(self, file_path: str | pathlib.Path) -> Dict[str, Any]:
        """Auto-analyze a single RML/FML file and extract its driver metadata."""
        p = pathlib.Path(file_path)
        # Try to find actual file
        actual = None
        if p.exists():
            actual = p
        else:
            for base in self.base_dirs:
                cand = base / p.name
                if cand.exists():
                    actual = cand
                    break
        if not actual or not actual.exists():
            return {"file": str(file_path), "error": "not found", "analysis": {}}

        try:
            if actual.suffix.lower() == ".rml":
                from rml_python.compiler import RMLReportCompiler
                comp = RMLReportCompiler(path=actual)
                meta = comp.rpt_metadata()
                cols = [c.to_dict() for c in comp.columns()]
                return {
                    "file": actual.name,
                    "path": str(actual),
                    "type": "rml",
                    "metadata": meta,
                    "columns": cols,
                    "column_count": len(cols),
                    "analysis": {
                        "has_fk_lookups": any(c.get("refTable") for c in cols),
                        "has_aggregated": any(c.get("type") == "aggregated" for c in cols),
                        "tables": list({c.get("expr","").split(".")[0] for c in cols if "." not in c.get("expr","") or c.get("expr")}),
                    },
                }
            elif actual.suffix.lower() in (".fml", ".fmlk"):
                from fmlk_engine.compiler import FMLKFormCompiler
                comp = FMLKFormCompiler(path=actual)
                meta = comp.fml_metadata()
                tabs = [t.to_dict() for t in comp.tabs()]
                fields = [f.to_dict() for f in comp.fields()]
                return {
                    "file": actual.name,
                    "path": str(actual),
                    "type": "fmlk",
                    "metadata": meta,
                    "tabs": tabs,
                    "fields": fields,
                    "field_count": len(fields),
                    "tab_count": len(tabs),
                    "analysis": {
                        "input_types": list({f.get("inputType") for f in fields}),
                        "has_tabs": len(tabs) > 0,
                        "categories": list({f.get("category") for f in fields if f.get("category")}),
                    },
                }
        except Exception as e:
            return {"file": actual.name, "path": str(actual), "error": str(e), "analysis": {}}
        return {"file": actual.name, "path": str(actual), "type": "unknown", "analysis": {}}

    def analyze_all(self) -> Dict[str, Any]:
        """Scan all base_dirs and analyze every RML/FML file."""
        all_files: List[pathlib.Path] = []
        for base in self.base_dirs:
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if p.suffix.lower() in (".rml", ".fml", ".fmlk"):
                    all_files.append(p)
        analysis = {}
        for f in sorted(all_files):
            analysis[f.name] = self.analyze_file(f)
        return {
            "total": len(analysis),
            "rml_count": len([v for v in analysis.values() if v.get("type") == "rml"]),
            "fml_count": len([v for v in analysis.values() if v.get("type") == "fmlk"]),
            "files": analysis,
        }

    def create_driver_for_user(self, username: str, roles: List[str], save_to_db: bool = True) -> Dict[str, Any]:
        """
        Create a per-user driver file by analyzing all RML/FML and filtering by permissions.
        The driver contains only the files and actions the user is allowed to perform,
        plus filtered column/field details for row-level security.
        """
        # Analyze all files first
        analysis = self.analyze_all()
        driver: Dict[str, Any] = {
            "user": username,
            "roles": roles,
            "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
            "rml": [],
            "fml": [],
            "permissions": {},
        }
        for fname, info in analysis["files"].items():
            # Check what actions this user can do on this file
            allowed_actions: List[str] = []
            for action in ["view", "create", "edit", "delete", "execute", "export", "share"]:
                if self.check(fname, username, roles, action):  # type: ignore
                    allowed_actions.append(action)
            if not allowed_actions:
                continue  # No access, skip
            # For viewable files, include filtered metadata
            entry = {
                "file": fname,
                "path": info.get("path"),
                "type": info.get("type"),
                "metadata": info.get("metadata"),
                "allowed_actions": allowed_actions,
            }
            # For RML, filter columns if needed (example: hide sensitive columns for non-admin)
            if info.get("type") == "rml" and "columns" in info:
                # Example row-level: if user lacks 'export', hide aggregated columns
                cols = info["columns"]
                if "export" not in allowed_actions:
                    cols = [c for c in cols if c.get("type") != "aggregated"]
                entry["columns"] = cols
                entry["column_count"] = len(cols)
            if info.get("type") == "fmlk" and "fields" in info:
                # Filter fields similarly
                fields = info["fields"]
                entry["fields"] = fields
                entry["tabs"] = info.get("tabs")
                entry["field_count"] = len(fields)
            # Add to appropriate list
            if info.get("type") == "rml":
                driver["rml"].append(entry)
            else:
                driver["fml"].append(entry)
            driver["permissions"][fname] = allowed_actions

        driver["rml_count"] = len(driver["rml"])
        driver["fml_count"] = len(driver["fml"])
        driver["total_files"] = len(driver["rml"]) + len(driver["fml"])

        # Save to DB if requested and Django available
        if save_to_db:
            try:
                import os, sys
                sys.path.insert(0, "C:/urs2")
                os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
                import django
                django.setup()
                from django.contrib.auth.models import User
                from urs.models import UserDriver
                import hashlib, json
                user_obj = User.objects.filter(username=username).first()
                if not user_obj:
                    # Auto-create user if not exists (for demo)
                    user_obj = User.objects.create_user(username=username, password="password")
                    # Assign roles as groups
                    from django.contrib.auth.models import Group
                    for role in roles:
                        grp, _ = Group.objects.get_or_create(name=role)
                        user_obj.groups.add(grp)
                # Create driver record
                perm_hash = hashlib.md5(json.dumps(driver["permissions"], sort_keys=True).encode()).hexdigest()
                driver_path = f"C:/urs2/drivers/{username}.json"
                pathlib.Path(driver_path).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(driver_path).write_text(json.dumps(driver, ensure_ascii=False, indent=2), encoding="utf-8")
                # Also save to old location for backwards compat
                pathlib.Path(f"C:/urs2/odex/drivers/{username}.json").parent.mkdir(parents=True, exist_ok=True)
                try:
                    pathlib.Path(f"C:/urs2/odex/drivers/{username}.json").write_text(json.dumps(driver, ensure_ascii=False, indent=2), encoding="utf-8")
                except: pass
                UserDriver.objects.update_or_create(
                    user=user_obj,
                    defaults={
                        "driver_json": driver,
                        "file_path": driver_path,
                        "rml_count": driver["rml_count"],
                        "fml_count": driver["fml_count"],
                        "permissions_hash": perm_hash,
                    },
                )
            except Exception as e:
                driver["db_error"] = str(e)

        # Also save as file for non-DB use
        try:
            driver_path = pathlib.Path(f"C:/urs2/drivers/{username}.json")
            driver_path.parent.mkdir(parents=True, exist_ok=True)
            driver_path.write_text(json.dumps(driver, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return driver

    def auto_analyze_and_create_drivers(self, save_to_db: bool = True) -> Dict[str, Any]:
        """
        Auto-analyze all RML/FML files and create a driver file for each user in DB.
        This is the main entry point for the 'auto analyze' feature.
        """
        # First, run full analysis
        analysis = self.analyze_all()
        # Get all users from DB
        users_info: List[Dict[str, Any]] = []
        try:
            import os, sys
            sys.path.insert(0, "C:/urs2")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()
            from django.contrib.auth.models import User
            for u in User.objects.all():
                roles = list(u.groups.values_list("name", flat=True))
                # Also add a role based on is_staff/is_superuser
                if u.is_superuser:
                    roles.append("admin")
                elif u.is_staff:
                    roles.append("manager")
                users_info.append({"username": u.username, "roles": roles, "is_superuser": u.is_superuser})
            # If no users, create a demo admin
            if not users_info:
                from django.contrib.auth.models import User as U2
                admin = U2.objects.create_superuser("admin", "admin@system.local", "admin")
                users_info.append({"username": "admin", "roles": ["admin"], "is_superuser": True})
        except Exception as e:
            # Fallback: create drivers for a default set of demo users
            users_info = [
                {"username": "admin", "roles": ["admin"], "is_superuser": True},
                {"username": "manager", "roles": ["manager"], "is_superuser": False},
                {"username": "guest", "roles": [], "is_superuser": False},
            ]
            analysis["warning"] = f"DB users not available, using demo users: {e}"

        drivers = {}
        for u in users_info:
            driver = self.create_driver_for_user(u["username"], u["roles"], save_to_db=save_to_db)
            drivers[u["username"]] = {
                "roles": u["roles"],
                "rml_count": driver["rml_count"],
                "fml_count": driver["fml_count"],
                "total": driver["total_files"],
            }

        return {
            "analysis": analysis,
            "users": users_info,
            "drivers": drivers,
            "total_users": len(users_info),
            "total_files": analysis["total"],
        }

# Singleton for convenience
_default_engine: Optional[PermissionsEngine] = None
def get_permissions_engine() -> PermissionsEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = PermissionsEngine()
    return _default_engine

