"""Durable atomic JSON replacement shared by independent Windows processes."""

import json
import math
import os
from pathlib import Path
import tempfile
import time


def finite_number(value):
    """Reject malformed JSON numbers without overflowing float conversion."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def write_json(path, value, *, exclusive=False):
    """Replace atomically, or publish once without overwriting an existing receipt.

    On Windows, rename without replacement atomically refuses an existing target.
    A producer racing a delivery acknowledgement cannot reset it to pending.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.stem + "-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(40):
            try:
                if exclusive:
                    temporary.rename(path)
                else:
                    temporary.replace(path)
                break
            except FileExistsError:
                if exclusive:
                    return False
                raise
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.025)
        if exclusive:
            return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
