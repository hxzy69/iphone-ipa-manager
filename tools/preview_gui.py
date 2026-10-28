"""Show the actual desktop layout with sample data and no background services.

Run from the source checkout with ``python tools/preview_gui.py``.
Buttons explain that this is a preview; no accounts, files or devices are changed.
"""

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iphone_detector.gui import App, BACKGROUND  # noqa: E402


class Preview(App):
    def __init__(self, root):
        self.preview = True
        self.root = root
        self.editable = []
        self.accounts = [
            {"email": "video@example.com", "ipa": "Video 2.1"},
            {"email": "music@example.com", "ipa": "Music 4.0"},
            {"email": "stream@example.com", "ipa": "Stream 1.3"},
        ]
        self.config = {"automation": {"ipa_directory": "C:/IPA Library", "accounts": self.accounts}}
        root.title("IPA Manager — GUI preview (sample data)")
        from iphone_detector.dialog_ui import size_window

        size_window(root, 1050, 850, minimum=(820, 640))
        root.configure(bg=BACKGROUND)
        self._styles()
        self._build()
        self.device_label.configure(
            text="Sample iPhone · Pairing and connection details appear here"
        )
        self.badge.configure(text="PREVIEW")
        self.connection_summary.set("Sample status: remote tunnel ready.")
        self.renewal_summary.set("Sample schedule: next refresh in 4 days.")
        self.next_step.set(
            "Sample iPhone selected · 3 accounts. Check setup before your first install."
        )
        self.status.set("Design preview · Sample accounts only. Your running program is unchanged.")
        self.sync_status.set(
            "In the live app, saved changes from Discord and automation appear here."
        )
        self.activity.configure(state="normal")
        self.activity.insert(
            "end", "Activity\nYour installation progress and results will appear here.\n"
        )
        self.activity.configure(state="disabled")
        menu = tk.Menu(root)
        screens = tk.Menu(menu, tearoff=False)
        for title, callback in (
            ("Apple sign-in & verification", self.open_account_dialog),
            ("Discord setup", self.open_discord_setup),
            ("Remote setup", self.open_remote_setup),
            ("Automatic renewal", self.open_renewal),
            ("Activity & recovery", self.open_dashboard),
            ("Import IPA", self.open_ipa_import),
            ("IPA Library", self.open_library),
            ("Setup checklist", self.open_checklist),
            ("Updates & support", self.open_updates),
            ("Choose iPhone", lambda: self.start("devices")),
            ("Install / repair", lambda: self.installer(False)),
            ("Uninstall", lambda: self.installer(True)),
        ):
            screens.add_command(label=title, command=callback)
        menu.add_cascade(label="Preview screens", menu=screens)
        root.configure(menu=menu)

    def notice(self, *args, **kwargs):
        from iphone_detector.dialog_ui import preview_notice

        preview_notice(self.root)
        return False

    save = notice
    sync_saved = notice
    browse_folder = notice
    browse_ipa = notice
    export_support_report = notice
    open_folder = notice
    stop = notice

    def start(self, mode):
        if mode == "devices":
            self.choose_device(
                [
                    {"name": "Sample iPhone", "serial": "SAMPLE-PHONE-1"},
                    {"name": "Another sample iPhone", "serial": "SAMPLE-PHONE-2"},
                ]
            )
        else:
            self.notice()

    def open_account_dialog(self):
        from iphone_detector.account_dialog import AccountDialog

        self.account_dialog = AccountDialog(
            self.root, self.config, lambda number: None, preview=True
        )

    def installer(self, removing):
        from iphone_detector.desktop_install import setup_window

        return setup_window(payload=None if removing else "sample", parent=self.root, preview=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screen",
        action="append",
        choices=(
            "install",
            "uninstall",
            "discord",
            "account",
            "remote",
            "renewal",
            "dashboard",
            "library",
            "import",
            "checklist",
            "updates",
        ),
        default=[],
    )
    options = parser.parse_args()
    root = tk.Tk()
    app = Preview(root)
    screens = {
        "install": lambda: app.installer(False),
        "uninstall": lambda: app.installer(True),
        "discord": app.open_discord_setup,
        "account": app.open_account_dialog,
        "remote": app.open_remote_setup,
        "renewal": app.open_renewal,
        "dashboard": app.open_dashboard,
        "library": app.open_library,
        "import": app.open_ipa_import,
        "checklist": app.open_checklist,
        "updates": app.open_updates,
    }
    for screen in options.screen:
        screens[screen]()
    root.mainloop()
