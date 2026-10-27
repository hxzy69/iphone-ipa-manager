"""User-owned logon task: normal permissions, no password, no runtime limit."""

from pathlib import Path
import subprocess
from iphone_detector.com_runtime import initialize, uninitialize

NAME = "iPhone IPA Community Discord"


def start_existing(arguments, name=NAME):
    """Start a matching enabled owner task without changing its saved definition.

    A missing/disabled task permits an ordinary child-process fallback. A task
    with another executable, profile, owner or privilege level must not be used.
    """
    import pywintypes
    import win32api
    import win32com.client
    import win32security
    from iphone_detector.bridge_service import current_sid

    if name not in {NAME, "iPhone IPA Community Remote Tunnel"}:
        raise ValueError("Unsupported background startup task.")
    com_owned = initialize()
    scheduler = folder = saved = task = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        try:
            saved = folder.GetTask(name)
        except pywintypes.com_error as exc:
            codes = [exc.hresult]
            if exc.excepinfo and len(exc.excepinfo) > 5:
                codes.append(exc.excepinfo[5])
            if any(type(code) is int and code & 0xFFFFFFFF == 0x80070002 for code in codes):
                return False
            raise OSError("Windows could not inspect the saved background task.") from exc
        task = saved.Definition
        principal = str(task.Principal.UserId)
        if principal.casefold() != current_sid().casefold():
            sid = win32security.LookupAccountName(None, principal)[0]
            principal = win32security.ConvertSidToStringSid(sid)
        if (
            principal.casefold() != current_sid().casefold()
            or task.Principal.RunLevel != 0
            or task.Principal.LogonType != 3
            or task.Actions.Count != 1
        ):
            raise ValueError("The background startup task needs local review.")
        action = task.Actions.Item(1)
        actual = win32api.CommandLineToArgv("app.exe " + action.Arguments)[1:]
        if Path(action.Path).resolve() != Path(arguments[0]).resolve() or actual != arguments[1:]:
            raise ValueError("The background startup task uses another executable or profile.")
        if not saved.Enabled:
            return False
        if saved.State not in (2, 4):  # Already queued/running: never launch a duplicate.
            saved.Run(None)
        return True
    except (pywintypes.error, pywintypes.com_error) as exc:
        raise OSError("Windows could not inspect or start the background task.") from exc
    finally:
        action = task = saved = folder = scheduler = None
        uninitialize(com_owned)


def remove_installation_tasks(root, data_root):
    """Remove only this user's normal-permission actions inside this installation."""
    import win32api
    import win32security
    import win32com.client
    from iphone_detector.bridge_service import current_sid
    from iphone_detector.paths import process_uses_profile

    allowed = {
        NAME,
        "iPhone IPA Community Remote Tunnel",
        "iPhone IPA Community Automatic Refresh",
        "iPhone IPA Community Queue",
    }
    owner = current_sid()
    com_owned = initialize()
    scheduler = folder = saved = task = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        names = []
        # TASK_ENUM_HIDDEN: renewal tasks deliberately hide their console/window.
        # Omitting these leaves old binaries active after update or uninstall.
        for saved in folder.GetTasks(1):
            if saved.Name not in allowed:
                continue
            task = saved.Definition
            principal = str(task.Principal.UserId)
            if principal.casefold() != owner.casefold():
                try:
                    sid = win32security.LookupAccountName(None, principal)[0]
                    if win32security.ConvertSidToStringSid(sid).casefold() != owner.casefold():
                        continue
                except Exception:
                    continue
            if task.Actions.Count != 1:
                continue
            action = task.Actions.Item(1)
            if not Path(action.Path).resolve().is_relative_to(Path(root).resolve()):
                continue
            args = win32api.CommandLineToArgv("app.exe " + action.Arguments)[1:]
            if not process_uses_profile(args, data_root):
                continue
            if task.Principal.RunLevel != 0:
                raise ValueError(
                    "A privileged task needs administrator review; uninstall was not started."
                )
            names.append(saved.Name)
        for name in names:
            folder.DeleteTask(name, 0)
    finally:
        action = task = saved = folder = scheduler = None
        uninitialize(com_owned)


def retarget(old_executable, new_executable, data_root=None):
    """Migrate only owned actions, preserving arguments, triggers and settings."""
    import win32api
    import win32security
    import win32com.client
    from iphone_detector.paths import DATA_ROOT
    from iphone_detector.bridge_service import current_sid

    data_root = Path(data_root or DATA_ROOT).resolve()
    owner = current_sid()
    allowed = {
        NAME,
        "iPhone IPA Community Remote Tunnel",
        "iPhone IPA Community Automatic Refresh",
        "iPhone IPA Community Queue",
    }
    com_owned = initialize()
    scheduler = folder = saved = task = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        definitions = []
        for saved in folder.GetTasks(1):
            if saved.Name not in allowed:
                continue
            task = saved.Definition
            if str(task.Principal.UserId).casefold() != owner.casefold():
                try:
                    sid = win32security.LookupAccountName(None, task.Principal.UserId)[0]
                    if win32security.ConvertSidToStringSid(sid).casefold() != owner.casefold():
                        continue
                except Exception:
                    continue
            if task.Principal.RunLevel != 0 or task.Actions.Count != 1:
                raise ValueError("A startup task needs local review before migration.")
            action = task.Actions.Item(1)
            args = win32api.CommandLineToArgv("app.exe " + action.Arguments)[1:]
            from iphone_detector.paths import process_uses_profile

            if not process_uses_profile(args, data_root):
                continue
            if Path(action.Path).resolve() == Path(new_executable).resolve():
                continue
            if Path(action.Path).resolve() != Path(old_executable).resolve():
                raise ValueError("A startup task belongs to another installation.")
            definitions.append((saved.Name, task))
        for name, task in definitions:
            action = task.Actions.Item(1)
            action.Path = str(new_executable)
            action.WorkingDirectory = str(Path(new_executable).parent)
            folder.RegisterTaskDefinition(name, task, 6, task.Principal.UserId, None, 3)
    finally:
        definitions = []
        action = task = saved = folder = scheduler = None
        uninitialize(com_owned)


def configure(enabled, arguments, name=NAME):
    import win32com.client
    import pywintypes
    from iphone_detector.bridge_service import current_sid

    com_owned = initialize()
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        if not enabled:
            try:
                folder.DeleteTask(name, 0)
            except pywintypes.com_error as exc:
                # IDispatch can wrap ERROR_FILE_NOT_FOUND in DISP_E_EXCEPTION;
                # Task Scheduler puts the actual HRESULT in EXCEPINFO.scode.
                codes = [exc.hresult]
                if exc.excepinfo and len(exc.excepinfo) > 5:
                    codes.append(exc.excepinfo[5])
                if not any(type(code) is int and code & 0xFFFFFFFF == 0x80070002 for code in codes):
                    raise
            return
        owner = current_sid()
        task = scheduler.NewTask(0)
        task.RegistrationInfo.Description = (
            "Private Discord listener for iPhone IPA Manager. Runs only as the signed-in owner."
        )
        task.Principal.UserId = owner
        task.Principal.LogonType = 3  # Interactive token; never store a Windows password.
        task.Principal.RunLevel = 0  # Normal user permissions.
        trigger = task.Triggers.Create(9)  # At logon, limited to this user.
        trigger.UserId = owner
        trigger.Enabled = True
        trigger.Delay = "PT15S"
        action = task.Actions.Create(0)
        action.Path = arguments[0]
        action.Arguments = subprocess.list2cmdline(arguments[1:])
        action.WorkingDirectory = str(Path(arguments[0]).parent)
        task.Settings.Enabled = True
        task.Settings.StartWhenAvailable = True
        task.Settings.DisallowStartIfOnBatteries = False
        task.Settings.StopIfGoingOnBatteries = False
        task.Settings.ExecutionTimeLimit = "PT0S"
        task.Settings.RestartCount = 3
        task.Settings.RestartInterval = "PT1M"
        task.Settings.MultipleInstances = 2  # Ignore a second launch while listening.
        folder.RegisterTaskDefinition(name, task, 6, owner, None, 3)
    except (pywintypes.error, pywintypes.com_error) as exc:
        raise OSError("Windows could not update the background startup task.") from exc
    finally:
        # Release COM proxies while this apartment is still initialized.
        action = trigger = task = folder = scheduler = None
        uninitialize(com_owned)
