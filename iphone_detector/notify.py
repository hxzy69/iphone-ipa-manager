"""Windows notifications under the manager's own AppUserModelID."""

import json
import os
from pathlib import Path
import subprocess
import sys
from iphone_detector.paths import ASSET_ROOT, FROZEN, DATA_ROOT
from iphone_detector.com_runtime import initialize, uninitialize

APP_ID = "iPhoneIPAManager.Community"


def notify(title, message):
    import pythoncom
    from win32com.shell import shell
    from win32com.propsys import propsys, pscon

    com_owned = initialize()
    store = link = None
    try:
        shortcut = (
            Path(os.environ["APPDATA"])
            / "Microsoft/Windows/Start Menu/Programs/iPhone IPA Manager Community.lnk"
        )
        shortcut.parent.mkdir(parents=True, exist_ok=True)
        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
        )
        if shortcut.exists():
            link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(shortcut), 2)  # STGM_READWRITE
        else:
            link.SetPath(sys.executable)
            arguments = [] if FROZEN else [str(ASSET_ROOT / "ipa_manager.py")]
            link.SetArguments(subprocess.list2cmdline(arguments + ["--data-dir", str(DATA_ROOT)]))
        link.SetDescription("iPhone IPA Manager")
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
        store.Commit()
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(shortcut), 1)
    finally:
        store = link = None
        uninitialize(com_owned)
    script = (ASSET_ROOT / "iphone_detector/windows_notify.ps1").read_text(encoding="utf-8")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=dict(
            os.environ, IPA_TOAST=json.dumps({"app": APP_ID, "title": title, "message": message})
        ),
    )
    if result.returncode:
        raise RuntimeError("Windows notification failed: " + result.stderr.strip()[-300:])
    if not result.stdout.strip():
        # A successful process without a receipt cannot confirm delivery.
        return {"submitted": None, "setting": "unknown"}
    return json.loads(result.stdout.strip())
