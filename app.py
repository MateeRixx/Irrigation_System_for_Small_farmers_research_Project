"""Backward-compatible development entrypoint.

The application lives in ``src/ecovri``. This shim preserves ``python app.py``
and existing integrations that import ``app``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ecovri import app as _application_module

sys.modules[__name__] = _application_module
