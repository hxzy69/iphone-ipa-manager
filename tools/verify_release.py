"""Verify downloaded release archives without executing their contents."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iphone_detector.release_integrity import verify_receipt, verify_members, verify_release

__all__ = ["verify_receipt", "verify_members", "verify_release"]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify_release(parser.parse_args().directory), indent=2))
