"""
STML Engine — serial codes, traversal, permission checks, operations-statement spec.

- Serial code: parent_full + separator + segment, segment zero-padded to the
  level digits (numeric) when serial=1. Missing segments auto-assigned by
  sibling order (1-based). Non-serial trees keep codes as written.
- Permissions (gpm_model contract): rows of
  user_no / fmlk_id|rml_id / api_path / is_allowed. Closed by default.
- Operations statement: spec {forms, qty/amount columns, rows=[]}; row binding
  comes later (dist_fmlks only declare WHICH forms roll up to the node).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Union

from .stml_models import GPMPermRow, STMLNode, STMLTree

import pathlib

QTY_FIELD = "qty"
AMOUNT_FIELD = "amount"
FORMS_DIR = pathlib.Path(__file__).resolve().parent / "forms"


class STMLEngine:
    def __init__(self, tree: STMLTree):
        self.tree = tree
        self._index: Dict[str, STMLNode] = {}
        self._parent: Dict[str, Optional[str]] = {}
        self._assign(self.tree.nodes, parent_full="", parent_code=None, depth=1)

    # -- serial codes ---------------------------------------------------
    def effective_digits(self, node: STMLNode) -> int:
        """Own segment width: node digits override, else level template, else 3."""
        try:
            if node.digits and int(node.digits) >= 1:
                return int(node.digits)
        except Exception:
            pass
        return self.tree.digits_for(node.level or 1)

    def _reindex(self) -> None:
        self._index = {}
        self._parent = {}
        self._assign(self.tree.nodes, "", None, 1, None)

    def _assign(self, nodes: List[STMLNode], parent_full: str,
                parent_code: Optional[str], depth: int, parent: Optional[STMLNode] = None) -> None:
        auto_ok = bool(self.tree.serial and (parent is None or getattr(parent, "auto", True)))
        used: set = set()
        nxt = [0]
        for n in (nodes or []):
            n.level = depth
            digits = self.effective_digits(n)
            seg = (n.code or "").strip()
            if not seg:
                if not auto_ok:
                    raise ValueError(
                        f"STML[manual-code-required]: {n.label}: الترقيم يدوي (auto=0) — أدخل code")
                cand = nxt[0] + 1
                seg = str(cand).zfill(digits)
                while seg in used:
                    cand += 1
                    seg = str(cand).zfill(digits)
                nxt[0] = cand
            elif seg.isdigit() and len(seg) < digits and self.tree.serial:
                seg = seg.zfill(digits)
            if len(seg) > digits:
                raise ValueError(
                    f"STML[node-code-wide]: {n.label}: المقطع '{seg}' أطول من digits={digits}")
            if seg in used:
                raise ValueError(
                    f"STML[dup-segment]: المقطع '{seg}' مكرر تحت نفس الأب ({n.label})")
            used.add(seg)
            n.code = seg
            n.full_code = f"{parent_full}{self.tree.separator}{seg}" if parent_full else seg
            if n.full_code in self._index:
                raise ValueError(
                    f"STML[dup-code]: الرمز '{n.full_code}' مكرر ({n.label})")
            self._index[n.full_code] = n
            self._parent[n.full_code] = parent_code
            self._assign(n.children, n.full_code, n.full_code, depth + 1, n)

    # -- traversal ------------------------------------------------------
    def find(self, code: str) -> Optional[STMLNode]:
        return self._index.get(str(code))

    def children_of(self, code: str) -> List[STMLNode]:
        n = self.find(code)
        return list(n.children) if n else []

    def path_of(self, code: str) -> List[STMLNode]:
        """Root -> node chain."""
        out: List[STMLNode] = []
        cur: Optional[str] = str(code)
        while cur:
            n = self._index.get(cur)
            if n is None:
                break
            out.append(n)
            cur = self._parent.get(cur)
        return list(reversed(out))

    def path_labels(self, code: str, sep: str = " / ") -> str:
        return sep.join(n.label for n in self.path_of(code))

    def siblings_of(self, code: str) -> List[STMLNode]:
        parent = self._parent.get(str(code))
        if parent is None:
            return [n for n in self.tree.nodes if n.full_code != str(code)]
        p = self._index.get(parent)
        return [c for c in p.children if c.full_code != str(code)] if p else []

    def flatten(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        def _walk(n: STMLNode, path: List[str]) -> None:
            rows.append({
                "code": n.full_code, "segment": n.code, "label": n.label,
                "level": n.level, "path": list(path) + [n.label],
                "model_source": n.model_source, "fmlk": n.fmlk,
                "dist_fmlks": list(n.dist_fmlks),
                "gpm_model": n.gpm_model, "locked": n.locked, "has_ops": n.has_ops,
            })
            for c in n.children:
                _walk(c, list(path) + [n.label])

        for top in self.tree.nodes:
            _walk(top, [])
        return rows

    def sequence_for(self, model_source: str) -> List[Dict[str, Any]]:
        """Nodes bound to one model, ordered by serial code (player sequencing)."""
        return [r for r in self.flatten() if r["model_source"] == model_source]

    # -- permissions (gpm_model) ----------------------------------------
    def _resolve(self, node: Union[str, STMLNode]) -> STMLNode:
        n = self.find(node) if isinstance(node, str) else node
        if n is None:
            raise ValueError(f"STML[unknown-node]: النود '{node}' غير موجودة")
        return n

    def check_access(self, node: Union[str, STMLNode], user_no: str,
                     perm_rows: List[Union[GPMPermRow, Dict[str, Any]]],
                     fmlk_id: str = "", rml_id: str = "",
                     api_path: str = "") -> bool:
        """Closed by default: a locked node (fmlk=*all / gpm_model) needs a
        matching row with is_allowed=1. Open nodes (plain fmlk) allow all."""
        n = self._resolve(node)
        if not n.locked and not n.gpm_model:
            return True
        user_no, fmlk_id, rml_id, api_path = (str(user_no or ""), str(fmlk_id or ""),
                                             str(rml_id or ""), str(api_path or ""))
        for raw in perm_rows or []:
            row = raw if isinstance(raw, GPMPermRow) else GPMPermRow.from_dict(raw)
            if row.user_no != user_no:
                continue
            form_hit = ((fmlk_id and row.fmlk_id == fmlk_id)
                        or (rml_id and row.rml_id == rml_id)
                        or (not fmlk_id and not rml_id and not row.fmlk_id and not row.rml_id))
            if not form_hit:
                continue
            if api_path and row.api_path not in ("", "*", api_path):
                continue
            return bool(row.is_allowed)
        return False

    # -- operations statement (كشف العمليات) -----------------------------
    def operations_statement(self, node: Union[str, STMLNode]) -> Dict[str, Any]:
        """Spec only: which forms roll up + expected qty/amount columns.
        rows binding comes later (dist_fmlks declare the WHAT, not the data)."""
        n = self._resolve(node)
        return {
            "node": n.full_code, "label": n.label,
            "path": self.path_labels(n.full_code),
            "forms": list(n.dist_fmlks),
            "qty_field": QTY_FIELD, "amount_field": AMOUNT_FIELD,
            "columns": ["form", "date", QTY_FIELD, AMOUNT_FIELD, "balance"],
            "rows": [],
            "pending_binding": True,
        }

    # -- payloads (player + sidebar) ------------------------------------
    def to_dict(self, deep: bool = True) -> Dict[str, Any]:
        d = self.tree.to_dict(deep=deep)
        d["flat"] = self.flatten()
        return d

    def sidebar_entry(self, filename: str) -> Dict[str, Any]:
        return {
            "file": filename, "name": self.tree.name, "label": self.tree.label,
            "group": "دليل النظام",
            "levels": self.tree.max_levels, "nodes": len(self._index),
        }

    # -- designer / settings ops (branch props persist via save) ---------
    def add_node(self, parent_code: Optional[str] = None, label: str = "",
                 code: str = "", name: str = "", **props) -> Dict[str, Any]:
        """Append a branch/leaf. Returns {code, requested, repaired}.

        Empty code auto-numbers when allowed (tree serial + parent auto);
        a colliding manual code AUTO-REPAIRS to the first free segment
        (same policy); manual mode (auto=0) still requires a free code.
        """
        if not (label or "").strip():
            raise ValueError("STML[add-node]: label مطلوب")
        parent = self.find(parent_code) if parent_code else None
        if parent_code and parent is None:
            raise ValueError(f"STML[unknown-node]: الأب '{parent_code}' غير موجود")
        depth = (parent.level + 1) if parent else 1
        if depth > self.tree.max_levels:
            raise ValueError(
                f"STML[node-too-deep]: عمق {depth} يتجاوز maxLevels={self.tree.max_levels}")
        allowed = {"digits", "auto", "model_source", "fmlk", "dist_fmlks", "gpm_model"}
        for k in props:
            if k not in allowed:
                raise ValueError(f"STML[unknown-prop]: خاصية '{k}' غير معروفة")
        node = STMLNode(label=label.strip(), code=(code or "").strip(),
                        name=(name or "").strip(), level=depth)
        self.set_props_obj(node, props)
        sibs = parent.children if parent else self.tree.nodes
        sibs.append(node)
        requested = node.code
        try:
            self._reindex()
            return {"code": node.full_code, "requested": requested, "repaired": False}
        except ValueError as ex:
            msg = str(ex)
            if requested and ("dup-segment" in msg or "dup-code" in msg
                              or "node-code-wide" in msg) and \
                    self.tree.serial and (parent is None or parent.auto):
                node.code = ""
                try:
                    self._reindex()
                except Exception:
                    sibs.remove(node)
                    raise ex
                return {"code": node.full_code, "requested": requested, "repaired": True}
            sibs.remove(node)
            try:
                self._reindex()
            except Exception:
                pass
            raise

    @staticmethod
    def set_props_obj(node: STMLNode, props: Dict[str, Any]) -> None:
        for k, v in (props or {}).items():
            if k == "digits":
                node.digits = int(v) if v not in (None, "") else None
                if node.digits is not None and node.digits < 1:
                    raise ValueError("STML[bad-prop]: digits لازم >= 1")
            elif k == "auto":
                node.auto = (str(v).strip().lower() in ("1", "true", "yes", "y", "on")
                             if isinstance(v, str) else bool(v))
            elif k == "dist_fmlks":
                if isinstance(v, str):
                    node.dist_fmlks = [p.strip() for p in v.split(",") if p.strip()]
                else:
                    node.dist_fmlks = list(v or [])
            elif k in ("model_source", "fmlk", "gpm_model", "name"):
                setattr(node, k, str(v or "").strip())
            else:
                raise ValueError(f"STML[unknown-prop]: خاصية '{k}' غير معروفة")

    def rename(self, code: str, label: str) -> STMLNode:
        n = self._resolve(code)
        if not (label or "").strip():
            raise ValueError("STML[rename]: label مطلوب")
        n.label = label.strip()
        return n

    def set_props(self, code: str, **props) -> STMLNode:
        n = self._resolve(code)
        self.set_props_obj(n, props)
        self._reindex()
        return n

    def delete(self, code: str, cascade: bool = False) -> int:
        """Delete a node; children require cascade=True. Returns removed count."""
        n = self._resolve(code)
        if n.children and not cascade:
            raise ValueError(
                f"STML[has-children]: '{n.label}' له تفرعات — احذفها أولاً أو مرر cascade=True")
        count = 1 + len(self._descendants(n))
        parent_code = self._parent.get(n.full_code)
        if parent_code is None:
            self.tree.nodes = [x for x in self.tree.nodes if x.full_code != n.full_code]
        else:
            p = self._index.get(parent_code)
            if p is not None:
                p.children = [x for x in p.children if x.full_code != n.full_code]
        self._reindex()
        return count

    def _descendants(self, node: STMLNode) -> List[STMLNode]:
        out = []
        for c in node.children:
            out.append(c)
            out.extend(self._descendants(c))
        return out

    def save(self, path=None) -> str:
        """Persist branch props to the stml file (designer round-trip)."""
        from .stml_compiler import save_file
        p = save_file(self.tree, path or getattr(self, "_path", None) or (self.tree.name + ".stml"))
        return str(p)

    # -- records by serial prefix ----------------------------------------
    def subtree_codes(self, code: str) -> List[str]:
        """Full codes under a node (itself included)."""
        n = self._resolve(code)
        return [n.full_code] + [c.full_code for c in self._descendants(n)]

    def match_records(self, code: str, records, code_field: str = "code") -> list:
        """Form records belonging to a branch: record code starts with the
        node code (e.g. node 3001 -> records 3001001, ...)."""
        pre = str(code or "")
        out = []
        for r in records or []:
            try:
                v = r.get(code_field, "") if isinstance(r, dict) else getattr(r, code_field, "")
            except Exception:
                continue
            if str(v or "").startswith(pre):
                out.append(r)
        return out

    # -- entry forms -------------------------------------------------------
    def resolve_fmlk(self, node) -> str:
        """Entry form path: forms/<name>.fmlk for bare names, as-is otherwise."""
        n = self._resolve(node)
        f = (n.fmlk or "").strip()
        if not f:
            return ""
        if "/" in f or "\\" in f or f.startswith("."):
            return f
        base = f if f.lower().endswith(".fmlk") else f + ".fmlk"
        return str(FORMS_DIR / base)

    def entry_fields(self, node) -> Dict[str, Any]:
        """Entry spec: bound fmlk when set, else default (serial + name)."""
        n = self._resolve(node)
        if (n.fmlk or "").strip():
            return {"mode": "fmlk", "path": self.resolve_fmlk(n), "node": n.full_code}
        return {"mode": "default", "form": str(FORMS_DIR / "node_entry.fmlk"),
                "node": n.full_code,
                "fields": [
                    {"name": "code", "alias": "الرقم التسلسلي",
                     "value": n.full_code, "readonly": True},
                    {"name": "label", "alias": "اسم التفرع", "value": n.label},
                ]}

    # -- central visibility --------------------------------------------------
    def filter_visible(self, user_no: str, perm_rows=None) -> Dict[str, Any]:
        """Pruned tree for a user: open nodes always visible, locked
        (fmlk=*all / gpm) nodes only with access. Central visibility gate."""
        def _vis(n: STMLNode):
            if n.locked:
                try:
                    if not self.check_access(n, user_no, perm_rows or []):
                        return None
                except Exception:
                    return None
            d = n.to_dict(deep=False)
            d["children"] = [c for c in (_vis(k) for k in n.children) if c is not None]
            return d
        return {"name": self.tree.name, "label": self.tree.label,
                "nodes": [x for x in (_vis(n) for n in self.tree.nodes) if x is not None]}
