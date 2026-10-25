"""Exercise install, repair, hidden-task migration and removal in a disposable folder.

Uses the real filesystem, current-user registry, shortcuts and Task Scheduler.
Never opens the owner's profile or registers the application's normal task names.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def smoke(payload):
    from iphone_detector import desktop_install as desktop, startup_task
    import win32com.client
    import winreg

    payload = Path(payload).resolve()
    identifier = uuid.uuid4().hex
    name = "iPhone IPA Manager QA " + identifier
    registry = "Software\\iPhoneIPAManagerTests\\" + identifier
    scheduler = win32com.client.Dispatch("Schedule.Service")
    scheduler.Connect()
    tasks = scheduler.GetFolder("\\")
    with tempfile.TemporaryDirectory(prefix="ipa-desktop-acceptance-") as temporary:
        folder = Path(temporary)
        installation, data = folder / "application", folder / "state"
        data.mkdir()
        private = data / "config.json"
        private.write_text('{"acceptance":"preserve"}', encoding="utf-8")
        checks = []
        with (
            patch.dict(os.environ, IPA_MANAGER_DATA_DIR=str(data)),
            patch.object(desktop, "DATA_ROOT", data),
            patch.object(desktop, "installation_root", return_value=installation),
            patch.object(desktop, "REGISTRY_KEY", registry),
            patch.object(desktop, "shortcut_path", return_value=folder / "manager.lnk"),
            patch.object(startup_task, "NAME", name),
        ):
            try:
                first = desktop.install_payload(payload)
                digest = hashlib.sha256((payload / "iPhoneIPAManager.exe").read_bytes()).hexdigest()
                assert hashlib.sha256(first.read_bytes()).hexdigest() == digest
                checks.append("install-copy-integrity")
                startup_task.configure(
                    True, [str(first), "--data-dir", str(data), "--auto-refresh"], name=name
                )
                saved = tasks.GetTask(name)
                definition = saved.Definition
                definition.Settings.Hidden = True
                tasks.RegisterTaskDefinition(
                    name, definition, 6, definition.Principal.UserId, None, 3
                )
                assert name not in {task.Name for task in tasks.GetTasks(0)}
                assert name in {task.Name for task in tasks.GetTasks(1)}
                arguments = definition.Actions.Item(1).Arguments
                second = desktop.install_payload(payload)
                migrated = tasks.GetTask(name).Definition
                assert Path(migrated.Actions.Item(1).Path) == second
                assert migrated.Actions.Item(1).Arguments == arguments
                assert migrated.Settings.Hidden and migrated.Principal.RunLevel == 0
                assert first.is_file()
                checks.extend(["repair-retains-rollback", "hidden-task-migrated-without-elevation"])
                # Execute only self-test in this isolated profile, never renewal/device work.
                result = subprocess.run(
                    [
                        str(second),
                        "--data-dir",
                        str(data),
                        "--self-test",
                        "--report-file",
                        str(folder / "self-test.json"),
                    ],
                    timeout=90,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                )
                assert result.returncode == 0
                checks.append("installed-runtime-self-test")
                unknown = installation / "user-notes.txt"
                unknown.write_text("preserve", encoding="utf-8")
                desktop.uninstall(installation)
                assert not first.exists() and not second.exists()
                assert name not in {task.Name for task in tasks.GetTasks(1)}
                assert private.read_text(encoding="utf-8") == '{"acceptance":"preserve"}'
                assert unknown.read_text(encoding="utf-8") == "preserve"
                assert not (folder / "manager.lnk").exists()
                try:
                    winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry).Close()
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("Owned registry entry survived uninstall")
                checks.extend(
                    ["hidden-task-removed", "uninstall-preserves-private-and-unowned-files"]
                )
                return {"ok": True, "exe_sha256": digest, "checks": checks, "clean_machine": False}
            finally:
                startup_task.configure(False, [], name=name)
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from iphone_detector.storage import write_json

    receipt = smoke(args.payload)
    write_json(args.report, receipt)
    print(json.dumps(receipt, indent=2))
