"""
STML Compiler — parses <tree>/<child>/<node> XML into STMLTree (mirrors cml_engine.compiler).

Loud failures (ValueError, STML[...] code prefix):
- root-not-tree | tree-name-required
- child-*: level out of 1..maxLevels, duplicate level, digits<1
- node-*: label required, depth exceeds maxLevels, level attr != depth,
  code segment wider than level digits
- gpm-required: fmlk="*all" without gpm_model
"""
from __future__ import annotations
import json
import pathlib
import xml.etree.ElementTree as ET
from typing import List, Optional, Union

from .stml_models import STMLLevel, STMLNode, STMLTree


def _fail(code: str, msg: str) -> ValueError:
    return ValueError(f"STML[{code}]: {msg}")


def _bool(v: Optional[str], default: bool = False) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _parse_dist(value: str, where: str) -> List[str]:
    """dist_fmlks="[array]": JSON array or comma-separated list."""
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            arr = json.loads(value)
        except Exception as ex:
            raise _fail("dist-bad-json", f"{where}: dist_fmlks JSON غلط ({ex})")
        if not isinstance(arr, list) or any(not isinstance(x, str) for x in arr):
            raise _fail("dist-bad-json", f"{where}: dist_fmlks لازم مصفوفة نصوص")
        return [x.strip().replace('"', "").replace("'", "") for x in arr if x.strip()]
    return [p.strip() for p in value.split(",") if p.strip()]


class STMLCompiler:
    def __init__(self, path: Optional[Union[str, pathlib.Path]] = None,
                 text: Optional[str] = None):
        if path is None and text is None:
            raise _fail("no-source", "لازم path أو text")
        self.path = str(path) if path is not None else "<text>"
        try:
            if text is not None:
                root = ET.fromstring(text)
            else:
                root = ET.parse(str(path)).getroot()
        except ET.ParseError as ex:
            raise _fail("xml-parse", f"{self.path}: XML غلط ({ex})")
        self._root = root

    # -- public ---------------------------------------------------------
    def tree(self) -> STMLTree:
        root = self._root
        if root.tag != "tree":
            raise _fail("root-not-tree", f"{self.path}: الجذر لازم <tree> مش <{root.tag}>")
        name = (root.get("name") or "").strip()
        if not name:
            raise _fail("tree-name-required", f"{self.path}: <tree name=...> مطلوب")
        try:
            max_levels = int(root.get("maxLevels") or 9)
        except ValueError:
            raise _fail("maxlevels-bad", f"{self.path}: maxLevels لازم رقم")
        if max_levels < 1:
            raise _fail("maxlevels-bad", f"{self.path}: maxLevels لازم >= 1")
        tree = STMLTree(
            name=name,
            label=(root.get("label") or name).strip(),
            max_levels=max_levels,
            serial=_bool(root.get("serial"), True),
            separator=root.get("separator") or "",
            raw_attrs=dict(root.attrib),
        )
        seen_levels = set()
        for el in root:
            if el.tag == "child":
                lv = self._parse_level(el, tree)
                if lv.level in seen_levels:
                    raise _fail("child-dup-level", f"{name}: مستوى مكرر level={lv.level}")
                seen_levels.add(lv.level)
                tree.levels.append(lv)
            elif el.tag == "node":
                tree.nodes.append(self._parse_node(el, tree, depth=1, where=f"{name}"))
            else:
                raise _fail("tree-bad-tag", f"{name}: وسم مرفوض <{el.tag}> (child|node فقط)")
        tree.levels.sort(key=lambda l: l.level)
        return tree

    # -- internals ------------------------------------------------------
    def _gpm_check(self, fmlk: str, gpm: str, where: str) -> None:
        if fmlk == "*all" and not gpm:
            raise _fail("gpm-required",
                        f"{where}: fmlk=\"*all\" يفتح كل النماذج — لازم gpm_model (مصدر صلاحيات ad_users)")

    def _parse_level(self, el: ET.Element, tree: STMLTree) -> STMLLevel:
        lname = (el.get("name") or "").strip()
        label = (el.get("label") or lname).strip()
        if not lname:
            raise _fail("child-name-required", f"{tree.name}: <child name=...> مطلوب")
        try:
            level = int(el.get("level") or 0)
        except ValueError:
            raise _fail("child-level-bad", f"{tree.name}/{lname}: level لازم رقم")
        if level < 1 or level > tree.max_levels:
            raise _fail("child-level-range",
                        f"{tree.name}/{lname}: level={level} خارج 1..{tree.max_levels}")
        try:
            digits = int(el.get("digits") or 3)
        except ValueError:
            raise _fail("child-digits-bad", f"{tree.name}/{lname}: digits لازم رقم")
        if digits < 1:
            raise _fail("child-digits-bad", f"{tree.name}/{lname}: digits لازم >= 1")
        fmlk = (el.get("fmlk") or "").strip()
        gpm = (el.get("gpm_model") or "").strip()
        self._gpm_check(fmlk, gpm, f"{tree.name}/{lname}")
        return STMLLevel(
            name=lname, label=label, level=level, digits=digits,
            model_source=(el.get("model_source") or "").strip(),
            fmlk=fmlk,
            dist_fmlks=_parse_dist(el.get("dist_fmlks") or "", f"{tree.name}/{lname}"),
            gpm_model=gpm,
            raw_attrs=dict(el.attrib),
        )

    def _parse_node(self, el: ET.Element, tree: STMLTree, depth: int, where: str) -> STMLNode:
        if depth > tree.max_levels:
            raise _fail("node-too-deep",
                        f"{where}: عمق {depth} يتجاوز maxLevels={tree.max_levels}")
        label = (el.get("label") or "").strip()
        if not label:
            raise _fail("node-label-required", f"{where}: <node label=...> مطلوب (عمق {depth})")
        if el.get("level") is not None:
            try:
                given = int(el.get("level") or 0)
            except ValueError:
                raise _fail("node-level-bad", f"{where}/{label}: level لازم رقم")
            if given != depth:
                raise _fail("node-level-mismatch",
                            f"{where}/{label}: level={given} لا يساوي العمق {depth}")
        code = (el.get("code") or "").strip()
        digits = None
        if el.get("digits") is not None and str(el.get("digits")).strip() != "":
            try:
                digits = int(str(el.get("digits")).strip())
            except ValueError:
                raise _fail("node-digits-bad", f"{where}/{label}: digits لازم رقم")
            if digits < 1:
                raise _fail("node-digits-bad", f"{where}/{label}: digits لازم >= 1")
        eff_digits = digits if digits else tree.digits_for(depth)
        if code and len(code) > eff_digits:
            raise _fail("node-code-wide",
                        f"{where}/{label}: المقطع '{code}' أطول من digits={eff_digits} (مستوى {depth})")
        fmlk = (el.get("fmlk") or "").strip()
        gpm = (el.get("gpm_model") or "").strip()
        self._gpm_check(fmlk, gpm, f"{where}/{label}")
        _auto_raw = str(el.get("auto") or "").strip().lower()
        auto = True if _auto_raw == "" else _auto_raw in ("1", "true", "yes", "y", "on")
        node = STMLNode(
            label=label,
            code=code,
            level=depth,
            name=(el.get("name") or "").strip(),
            digits=digits,
            auto=auto,
            model_source=(el.get("model_source") or "").strip(),
            fmlk=fmlk,
            dist_fmlks=_parse_dist(el.get("dist_fmlks") or "", f"{where}/{label}"),
            gpm_model=gpm,
            raw_attrs=dict(el.attrib),
        )
        for sub in el:
            if sub.tag != "node":
                raise _fail("node-bad-tag",
                            f"{where}/{label}: وسم مرفوض <{sub.tag}> (node فقط)")
            node.children.append(self._parse_node(sub, tree, depth + 1, f"{where}/{label}"))
        return node


def compile_file(path: Union[str, pathlib.Path]) -> STMLTree:
    return STMLCompiler(path=path).tree()


def compile_text(text: str) -> STMLTree:
    return STMLCompiler(text=text).tree()


def save_file(tree: STMLTree, path: Union[str, pathlib.Path]) -> pathlib.Path:
    """Write branch props back to stml (designer/settings round-trip)."""
    p = pathlib.Path(path)
    p.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + tree.to_xml() + "\n",
                 encoding="utf-8")
    return p
