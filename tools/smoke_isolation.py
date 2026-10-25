"""Check standalone operation without inherited Python, app data or developer PATH.

This is an isolation smoke test on the current Windows host, not a clean-VM test.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def smoke(executable):
    executable = Path(executable).resolve()
    with tempfile.TemporaryDirectory(prefix="ipa-isolation-") as directory:
        root = Path(directory)
        foreign = root / "foreign-python"
        foreign.mkdir()
        for name in ("discord", "psutil", "win32crypt", "iphone_detector"):
            (foreign / (name + ".py")).write_text(
                "raise RuntimeError('ambient module must not load')\n", encoding="utf-8"
            )
        working = root / "empty-working-directory"
        working.mkdir()
        report = root / "self-test.json"
        windows = Path(os.environ["WINDIR"])
        env = dict(
            os.environ,
            PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
            PYTHONHOME=str(root / "nonexistent-python"),
            PYTHONPATH=str(foreign),
            LOCALAPPDATA=str(root / "local"),
            APPDATA=str(root / "roaming"),
            IPA_MANAGER_DATA_DIR=str(root / "state"),
            PYINSTALLER_RESET_ENVIRONMENT="1",
        )
        result = subprocess.run(
            [str(executable), "--self-test", "--report-file", str(report)],
            cwd=working,
            env=env,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("Standalone isolation check failed")
        receipt = json.loads(report.read_text(encoding="utf-8"))
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        assert (
            receipt["ok"] is True and receipt["frozen"] is True and receipt["exe_sha256"] == digest
        )
        assert not (root / "state/config.json").exists()
        return {
            "ok": True,
            "version": receipt["version"],
            "exe_sha256": digest,
            "packaged_checks": len(receipt["checks"]),
            "ambient_python_ignored": True,
            "developer_path_removed": True,
            "fresh_app_data": True,
            "clean_machine": False,
            "phone_writes": False,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.executable)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
