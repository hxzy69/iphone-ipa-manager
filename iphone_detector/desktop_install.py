"""Per-user desktop installation; retain private data and remove only owned files."""

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import psutil

from iphone_detector import __version__
from iphone_detector.paths import DATA_ROOT
from iphone_detector.storage import write_json
from iphone_detector.com_runtime import initialize, uninitialize

REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\iPhoneIPAManagerCommunity"


def installation_root():
    return Path(os.environ["LOCALAPPDATA"]) / "Programs/iPhoneIPAManagerCommunity"


def checked_root(root):
    root = Path(root)
    expected = installation_root()
    if root.is_symlink() or root.is_junction() or root.resolve() != expected.resolve():
        raise ValueError("Choose the supported per-user installation folder.")
    if DATA_ROOT.resolve().is_relative_to(root.resolve()):
        raise ValueError("Keep private settings outside the application installation folder.")
    return root.resolve()


def owned_path(root, name):
    relative = Path(name)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(p in ("..", ".") or ":" in p for p in relative.parts)
    ):
        raise ValueError("Installation manifest contains an unsafe path.")
    path = root / relative
    resolved_root = root.resolve()
    if not path.resolve().is_relative_to(resolved_root) or path.resolve() == resolved_root:
        raise ValueError("Installation target is outside its owned folder.")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("Installation contains a redirected path; inspect it locally.")
    return path


def manifest(root):
    path = root / "installation.json"
    if not path.exists():
        if root.exists() and any(root.iterdir()):
            raise ValueError(
                "The installation folder has no ownership record. Preserve it and inspect locally."
            )
        return None
    if path.is_symlink():
        raise ValueError("Installation ownership record is redirected.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("schema_version") != 1
            or not isinstance(value.get("files"), list)
            or not isinstance(value.get("executable"), str)
        ):
            raise ValueError("Invalid installation ownership record")
        if any(not isinstance(name, str) for name in value["files"]):
            raise ValueError("Invalid installation file list")
        for name in [*value["files"], value["executable"]]:
            owned_path(root, name)
        if value["executable"] not in value["files"]:
            raise ValueError("Installed executable is not owned")
        if (
            not isinstance(value.get("data_root"), str)
            or Path(value["data_root"]).resolve() != DATA_ROOT.resolve()
        ):
            raise ValueError("This installation uses another settings profile.")
        return value
    except OSError, ValueError, TypeError, AttributeError:
        raise ValueError(
            "The installation ownership record needs local repair; files were preserved."
        ) from None


def require_idle(root):
    for process in psutil.process_iter(["exe"]):
        executable = process.info["exe"]
        if executable and Path(executable).resolve().is_relative_to(root):
            raise ValueError(
                "Close the installed manager and stop its Discord/tunnel processes before changing this installation."
            )


def shortcut_path():
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft/Windows/Start Menu/Programs/iPhone IPA Manager Community.lnk"
    )


def integrate(root, executable, version=__version__):
    import win32com.client
    import winreg

    com_owned = initialize()
    try:
        link_path = shortcut_path()
        link_path.parent.mkdir(parents=True, exist_ok=True)
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(str(link_path))
        link.TargetPath = str(executable)
        link.Arguments = subprocess.list2cmdline(["--data-dir", str(DATA_ROOT)])
        link.WorkingDirectory = str(executable.parent)
        link.IconLocation = str(executable) + ",0"
        link.Save()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            for name, value in {
                "DisplayName": "iPhone IPA Manager",
                "DisplayVersion": version,
                "InstallLocation": str(root),
                "DisplayIcon": str(executable),
                "UninstallString": subprocess.list2cmdline(
                    [
                        str(executable),
                        "--desktop-uninstall",
                        str(root),
                        "--data-dir",
                        str(DATA_ROOT),
                    ]
                ),
                "Comments": "Per-user installation. Uninstall preserves accounts, pairing, settings and IPA files.",
            }.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            for name in ("NoModify", "NoRepair"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
    finally:
        link = shell = None
        uninitialize(com_owned)


def remove_integration(root):
    import win32com.client
    import winreg

    com_owned = initialize()
    try:
        path = shortcut_path()
        if path.is_file():
            shell = win32com.client.Dispatch("WScript.Shell")
            link = shell.CreateShortcut(str(path))
            if Path(link.TargetPath).resolve().is_relative_to(root):
                path.unlink()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
                location = winreg.QueryValueEx(key, "InstallLocation")[0]
            if Path(location).resolve() == root:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY)
        except FileNotFoundError:
            pass
    finally:
        link = shell = None
        uninitialize(com_owned)


def install_payload(payload, root=None, *, _lock_held=False):
    from iphone_detector.release_integrity import verify_receipt
    from iphone_detector.run_lock import RunLock

    payload = Path(payload).resolve()
    root = checked_root(root or installation_root())
    lock = None if _lock_held else RunLock()
    if lock is not None and not lock.acquire():
        raise ValueError("Wait for active device work before installing.")
    try:
        if not _lock_held:
            require_idle(root)
        previous = manifest(root)
        executable = payload / "iPhoneIPAManager.exe"
        content = executable.read_bytes()
        receipt = json.loads((payload / "self-test.json").read_text(encoding="utf-8"))
        version = receipt.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("The setup payload has an invalid version.")
        verify_receipt(receipt, content, expected_version=version)
        files = [p for p in payload.rglob("*") if p.is_file()]
        if len(files) > 25000 or any(p.is_symlink() or p.is_junction() for p in payload.rglob("*")):
            raise ValueError("The setup payload contains unsupported or redirected files.")
        size = sum(path.stat().st_size for path in files)
        if size > 512 * 1024**2 or shutil.disk_usage(root.anchor).free < size * 2 + 128 * 1024**2:
            raise ValueError("Not enough free space to install safely.")
        digest = hashlib.sha256(content).hexdigest()
        slot = root / "versions" / (version + "-" + digest[:12] + "-" + uuid.uuid4().hex[:8])
        owned_path(root, slot.relative_to(root).as_posix())
        slot.mkdir(parents=True, exist_ok=False)
        copied = [
            (slot / source.relative_to(payload)).relative_to(root).as_posix() for source in files
        ]
        try:
            for source in files:
                target = slot / source.relative_to(payload)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if (
                    hashlib.sha256(target.read_bytes()).digest()
                    != hashlib.sha256(source.read_bytes()).digest()
                ):
                    raise ValueError("A copied installation file failed verification.")
            new_executable = slot / "iPhoneIPAManager.exe"
            if hashlib.sha256(new_executable.read_bytes()).hexdigest() != digest:
                raise ValueError("The installed executable changed during setup.")
            # Each attempt owns a new version folder. Preserve the last good copy.
            value = dict(
                schema_version=1,
                version=version,
                executable=new_executable.relative_to(root).as_posix(),
                files=sorted(set((previous or {}).get("files", []) + copied)),
                data_root=str(DATA_ROOT),
                sha256=digest,
            )
            if previous:
                from iphone_detector.startup_task import retarget

                retarget(root / previous["executable"], new_executable, DATA_ROOT)
            integrate(root, new_executable, version)
            write_json(root / "installation.json", value)
            return new_executable
        except Exception:
            rollback = dict(
                previous or {},
                schema_version=1,
                files=sorted(set((previous or {}).get("files", []) + copied)),
                data_root=str(DATA_ROOT),
            )
            if not previous:
                rollback["executable"] = (
                    (slot / "iPhoneIPAManager.exe").relative_to(root).as_posix()
                )
            write_json(root / "installation.json", rollback)
            if previous:
                from iphone_detector.startup_task import retarget

                retarget(slot / "iPhoneIPAManager.exe", root / previous["executable"], DATA_ROOT)
                integrate(root, root / previous["executable"], previous.get("version", __version__))
            else:
                remove_integration(root)
            raise
    finally:
        if lock is not None:
            lock.close()


def restore_installation(root, previous):
    """Restore the active version while retaining ownership of staged update files."""
    latest = manifest(root)
    restored = dict(
        previous, files=sorted(set(previous["files"] + (latest or {}).get("files", [])))
    )
    write_json(root / "installation.json", restored)
    integrate(root, root / previous["executable"], previous.get("version", __version__))


def uninstall(root):
    from iphone_detector.run_lock import RunLock
    from iphone_detector.startup_task import remove_installation_tasks

    root = checked_root(root)
    lock = RunLock()
    if not lock.acquire():
        raise ValueError("Wait for active device work before uninstalling.")
    try:
        require_idle(root)
        value = manifest(root)
        if value is None:
            return
        # Validate the entire removal list before changing tasks or removing a file.
        paths = [owned_path(root, name) for name in value["files"]]
        remove_installation_tasks(root, DATA_ROOT)
        remove_integration(root)
        for path in paths:
            path.unlink(missing_ok=True)
        (root / "installation.json").unlink()
        for folder in sorted(
            {p for path in paths for p in path.parents if p.is_relative_to(root)},
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                folder.rmdir()  # Unknown files and user data are never recursively removed.
            except OSError:
                pass
    finally:
        lock.close()


def launch_uninstaller(root):
    root = checked_root(root)
    if Path(sys.executable).resolve().is_relative_to(root):
        folder = Path(tempfile.mkdtemp(prefix="ipa-manager-uninstall-"))
        helper = folder / "iPhoneIPAManager.exe"
        shutil.copy2(sys.executable, helper)
        subprocess.Popen(
            [str(helper), "--desktop-uninstall", str(root), "--data-dir", str(DATA_ROOT)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
        )
        return
    setup_window(root=root)


def setup_window(payload=None, root=None, *, parent=None, preview=False):
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk

    window = tk.Toplevel(parent) if parent is not None else tk.Tk()
    removing = payload is None
    window.title("Remove iPhone IPA Manager" if removing else "Install iPhone IPA Manager")
    from iphone_detector.dialog_ui import shell, style_window

    if parent is None:
        style_window(window)
    frame, footer = shell(
        window,
        "Remove application files" if removing else "Install iPhone IPA Manager",
        "For this Windows user only · No administrator prompt",
        width=680,
        height=400,
        close=lambda: None if busy and not preview else window.destroy(),
        scrollable=False,
    )
    ttk.Label(
        frame,
        text=(
            "Removes application files only. Your settings, saved accounts, pairing records and IPA files are kept. Close the manager and stop its background listeners before continuing."
            if removing
            else "Install the app and add a Start menu shortcut. Existing settings and IPA files are kept. If you are replacing an installed version, close it and stop its background listeners first."
        ),
        wraplength=550,
    ).pack(anchor="w")
    status = tk.StringVar(
        value="Sample installation folder" if preview else str(root or installation_root())
    )
    ttk.Label(frame, textvariable=status, wraplength=550).pack(anchor="w", pady=16)
    events = queue.Queue()
    busy = False
    progress = ttk.Progressbar(frame, mode="indeterminate")

    def poll():
        nonlocal busy
        if not window.winfo_exists():
            return
        try:
            executable, error = events.get_nowait()
        except queue.Empty:
            window.after(100, poll)
            return
        busy = False
        progress.stop()
        progress.pack_forget()
        if error:
            status.set(error)
            action.configure(state="normal")
        else:
            status.set(
                "Removed. Your private data and IPA files were kept."
                if removing
                else "Installed. Open iPhone IPA Manager from the Start menu."
            )
            action.configure(state="disabled")
            if executable:
                ttk.Button(
                    frame,
                    text="Open manager",
                    command=lambda: subprocess.Popen(
                        [str(executable), "--data-dir", str(DATA_ROOT)],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                    ),
                ).pack(anchor="w")

    def start():
        nonlocal busy
        if preview:
            busy = True
            action.configure(state="disabled")
            progress.configure(mode="determinate", maximum=100, value=0)
            progress.pack(fill="x", pady=12)

            def demonstrate(value=0):
                nonlocal busy
                if not window.winfo_exists():
                    return
                progress.configure(value=value)
                phase = (
                    (
                        "Checking application files"
                        if value < 25
                        else "Removing application files"
                        if removing
                        else "Copying application files"
                    )
                    if value < 80
                    else "Finishing up"
                )
                status.set(f"PREVIEW · {phase}… {value}%\nNo files are being changed.")
                if value < 100:
                    window.after(180, lambda: demonstrate(value + 1))
                else:
                    busy = False
                    status.set("PREVIEW · Complete. No application files were changed.")
                    action.configure(state="normal", text="Replay sample progress")

            demonstrate()
            return
        busy = True
        progress.pack(fill="x", pady=12)
        progress.start()
        action.configure(state="disabled")
        status.set(
            "Removing owned application files…"
            if removing
            else "Verifying and copying application files…"
        )

        def perform():
            try:
                executable = None if removing else install_payload(payload)
                if removing:
                    uninstall(root)
                events.put((executable, None))
            except Exception as exc:
                events.put(
                    (
                        None,
                        str(exc)
                        if isinstance(exc, (OSError, ValueError))
                        else "Setup could not finish. Files were preserved; retry after closing the installed manager.",
                    )
                )

        threading.Thread(target=perform, daemon=True).start()
        window.after(100, poll)

    action = ttk.Button(
        footer,
        text="Uninstall" if removing else "Install / repair",
        command=start,
        style="Accent.TButton",
    )
    action.pack(side="left")
    if preview:
        window.after(400, start)
    window.protocol("WM_DELETE_WINDOW", lambda: None if busy and not preview else window.destroy())
    if parent is None:
        window.mainloop()
    return window
