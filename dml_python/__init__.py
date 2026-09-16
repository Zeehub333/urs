"""DML — Document Markup Language for printable document templates.

DML files (``*.dml``) live in ``odex/system/<app>/documents/`` and are named
``<source>_<N>.dml`` where ``<source>`` is the parent RML/FMLK base name.
"""
from .compiler import DMLCompiler
from .engine import render_preview_html, PAPER_SIZES, SYSTEM_VARS, get_system_vars

__all__ = ["DMLCompiler", "render_preview_html", "PAPER_SIZES", "SYSTEM_VARS", "get_system_vars"]
