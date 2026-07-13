"""Jeli Scoped MCP Server — Security & Governance Layer for Personal Memory Systems."""

__version__ = "0.2.0-alpha"
__author__ = "JP Cruz"
__license__ = "MIT"

from .config import Settings
from .security import APIKeyValidator

__all__ = ["Settings", "APIKeyValidator"]
