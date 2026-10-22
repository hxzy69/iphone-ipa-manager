"""User-owned logon task: normal permissions, no password, no runtime limit."""

from pathlib import Path
import subprocess

NAME = "iPhone IPA Community Discord"


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
