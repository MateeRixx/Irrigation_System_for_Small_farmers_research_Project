"""Backward-compatible import shim for the zoning engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ecovri import zoning_engine as _engine_module

sys.modules[__name__] = _engine_module
