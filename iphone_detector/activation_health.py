"""Local startup acknowledgements used only during an explicit app handover."""

import os
from pathlib import Path
import re
import sys
import time

from iphone_detector.paths import DATA_ROOT
from iphone_detector.storage import write_json


def ready(component):
    nonce = os.environ.get("IPA_MANAGER_ACTIVATION_NONCE", "")
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        return
    import psutil
    from iphone_detector import __version__

    write_json(
        DATA_ROOT / "activation-health" / f"{nonce}-{component}.json",
        {
            "nonce": nonce,
            "component": component,
            "pid": os.getpid(),
            "created": psutil.Process().create_time(),
            "at": time.time(),
            "executable": str(Path(sys.executable).resolve()),
            "version": __version__,
        },
    )
