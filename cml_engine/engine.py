"""
CMLEngine — applies defaults + enforces business rules on settings values.
Rule types: default | required | readonly | hidden | unique | min | max |
            min_length | max_length | regex(pattern) | equals
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple
from .compiler import CMLCompiler, CMLControl, CMLRule


class CMLEngine:
    def __init__(self, compiler: CMLCompiler):
        self.compiler = compiler
        self.metadata = compiler.cml_metadata()
        self.controls: List[CMLControl] = compiler.controls()
        self.rules: List[CMLRule] = compiler.rules()
        self._by_name = {c.name: c for c in self.controls}

    def apply_defaults(self, values: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(values or {})
        for c in self.controls:
            if (c.name not in out or out[c.name] in (None, "")) and c.default is not None:
                out[c.name] = c.default
        for r in self.rules:
            if r.type == "default" and r.target:
                if r.target not in out or out[r.target] in (None, ""):
                    out[r.target] = r.value
        return out

    def visible_controls(self) -> List[CMLControl]:
        hidden = {r.target for r in self.rules if r.type == "hidden" and r.target}
        return [c for c in self.controls if c.name not in hidden]

    def validate(self, values: Dict[str, Any]) -> Dict[str, str]:
        """Returns {control_name: error_message}."""
        errors: Dict[str, str] = {}
        vals = self.apply_defaults(values or {})
        for c in self.controls:
            v = vals.get(c.name)
            if c.required and (v is None or str(v).strip() == ""):
                errors[c.name] = f"{c.alias} مطلوب"
                continue
            if v is None or str(v).strip() == "":
                continue
            sv = str(v)
            vr = c.validation or {}
            pat = vr.get("pattern")
            if pat:
                try:
                    if not re.fullmatch(pat, sv):
                        errors[c.name] = f"{c.alias} صيغة غير صحيحة"
                        continue
                except re.error:
                    pass
            if vr.get("min_length") is not None:
                try:
                    if len(sv) < int(vr["min_length"]):
                        errors[c.name] = f"{c.alias} يجب ألا يقل عن {vr['min_length']} حرف"
                        continue
                except Exception:
                    pass
            if vr.get("max_length") is not None:
                try:
                    if len(sv) > int(vr["max_length"]):
                        errors[c.name] = f"{c.alias} يجب ألا يزيد عن {vr['max_length']} حرف"
                        continue
                except Exception:
                    pass
        for r in self.rules:
            t = r.target
            if not t or t in errors:
                continue
            v = vals.get(t)
            msg = r.message or f"{t} غير صالح"
            if r.type == "required" and (v is None or str(v).strip() == ""):
                errors[t] = msg
            elif r.type == "min_length" and v is not None and str(v).strip() != "":
                try:
                    if len(str(v)) < int(str(r.value)):
                        errors[t] = msg
                except Exception:
                    pass
            elif r.type == "max_length" and v is not None and str(v).strip() != "":
                try:
                    if len(str(v)) > int(str(r.value)):
                        errors[t] = msg
                except Exception:
                    pass
            elif r.type in ("min",) and v is not None and str(v).strip() != "":
                try:
                    if float(v) < float(str(r.value)):
                        errors[t] = msg
                except Exception:
                    pass
            elif r.type in ("max",) and v is not None and str(v).strip() != "":
                try:
                    if float(v) > float(str(r.value)):
                        errors[t] = msg
                except Exception:
                    pass
            elif r.type in ("regex", "pattern") and v is not None and str(v).strip() != "" and r.value:
                try:
                    if not re.fullmatch(str(r.value), str(v)):
                        errors[t] = msg
                except re.error:
                    pass
            elif r.type == "equals" and v is not None:
                if str(v) != str(r.value):
                    errors[t] = msg
            # unique/readonly enforced at storage layer; reported here as-is when flagged
            elif r.type == "unique":
                pass
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata,
            "controls": [c.to_dict() for c in self.controls],
            "rules": [r.to_dict() for r in self.rules],
        }
