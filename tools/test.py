"""Run device-free tests with an isolated profile, never the installed app's data."""

import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "vendor/python"))
    with tempfile.TemporaryDirectory(prefix="ipa-manager-tests-") as directory:
        os.environ["IPA_MANAGER_DATA_DIR"] = str(Path(directory) / "state")
        os.environ["APPDATA"] = str(Path(directory) / "roaming")
        suite = unittest.defaultTestLoader.discover(str(root / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
