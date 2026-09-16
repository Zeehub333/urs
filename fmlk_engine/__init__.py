"""
fmlk_engine — Meta-Driven Dynamic FMLK (Form Markup Language) Form Engine for Oracle
Exposes: FMLKFormCompiler, OracleEngine (reused), FMLKFormEngine
"""
from .compiler import FMLKFormCompiler, FMLKField, FMLKTab, FMLKMetadata
from .engine import FMLKFormEngine

# Reuse OracleEngine from rml_python (secure _q, try...finally)
try:
    from rml_python.oracle_engine import OracleEngine
except ImportError:
    try:
        from rml_python.oracle_engine import OracleEngine
    except:
        OracleEngine = None  # type: ignore

__all__ = ["FMLKFormCompiler", "FMLKField", "FMLKTab", "FMLKMetadata", "FMLKFormEngine", "OracleEngine"]
__version__ = "1.0.0"
