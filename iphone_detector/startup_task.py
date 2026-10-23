"""User-owned logon task: normal permissions, no password, no runtime limit."""

from pathlib import Path
import subprocess

NAME = "iPhone IPA Community Discord"


def retarget(old_executable, new_executable, data_root=None):
    """Migrate only owned actions, preserving arguments, triggers and settings."""
    import pythoncom
    import os
    import win32api
    import win32security
    import win32com.client
    from iphone_detector.paths import DATA_ROOT
    from iphone_detector.bridge_service import current_sid

    data_root = Path(data_root or DATA_ROOT).resolve()
    owner = current_sid()
    default_root = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        / "iPhoneIPAManagerCommunity"
    )

    allowed = {
        NAME,
        "iPhone IPA Community Remote Tunnel",
        "iPhone IPA Community Automatic Refresh",
        "iPhone IPA Community Queue",
    }
    pythoncom.CoInitialize()
    scheduler = folder = saved = task = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        definitions = []
        for saved in folder.GetTasks(0):
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
            try:
                profile = (
                    Path(args[args.index("--data-dir") + 1])
                    if "--data-dir" in args
                    else default_root
                )
            except IndexError:
                raise ValueError("A startup task has invalid data-directory arguments.") from None
            if profile.resolve() != data_root:
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
        pythoncom.CoUninitialize()


def configure(enabled, arguments, name=NAME):
    import pythoncom
    import win32com.client
    import pywintypes
    from iphone_detector.bridge_service import current_sid

    pythoncom.CoInitialize()
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        if not enabled:
            try:
                folder.DeleteTask(name, 0)
            except pywintypes.com_error as exc:
                if (exc.hresult & 0xFFFFFFFF) != 0x80070002:
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
        task.Settings.RestartCount = 999
        task.Settings.RestartInterval = "PT1M"
        task.Settings.MultipleInstances = 2  # Ignore a second launch while listening.
        folder.RegisterTaskDefinition(name, task, 6, owner, None, 3)
    finally:
        # Release COM proxies while this apartment is still initialized.
        action = trigger = task = folder = scheduler = None
        pythoncom.CoUninitialize()
