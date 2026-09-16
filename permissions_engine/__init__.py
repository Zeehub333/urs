"""
permissions_engine — Per-file permissions for RML/FML
Each .rml / .fmlk file has its own permission matrix (roles/users -> actions).
"""
from .engine import PermissionsEngine, FilePermissions, ACE

__all__ = ["PermissionsEngine", "FilePermissions", "ACE"]
__version__ = "1.0.0"
