"""iPhone IPA Manager application services."""

import sys
from pathlib import Path

__version__ = "0.3.4"
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor/python"))
