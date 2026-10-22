"""Isolated native account authentication and validated account enrollment."""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess

from iphone_detector.paths import ASSET_ROOT
from iphone_detector.run_lock import RunLock


def email_address(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid Apple account email address.")
    return value


def server_url():
    path = Path(os.environ.get("APPDATA", "")) / "me.nabdev.iloader/preferences.json"
    value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    server = value.get("newAnisetteServer", "new.ani.sidestore.io")
    server = (
        server
        if server.startswith("https://")
        else "https://" + server
        if "://" not in server
        else server
    )
    if not server.startswith("https://"):
        raise ValueError(
            "Apple sign-in requires an HTTPS anisette server in the local iloader settings."
        )
    return server


def save_iloader_account(email):
    from iphone_detector.storage import write_json

    path = Path(os.environ["APPDATA"]) / "me.nabdev.iloader/data.json"
    saved = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(saved, dict) or not isinstance(saved.get("ids", []), list):
        raise ValueError(
            "iloader account list needs local repair. Credentials were saved; its list was not overwritten."
        )
    ids = saved.setdefault("ids", [])
    if not any(isinstance(value, str) and value.casefold() == email.casefold() for value in ids):
        ids.append(email)
        write_json(path, saved)


def append_account(email, source, signature):
    from iphone_detector.config import load_config, save_config
    from iphone_detector.settings import get_workflow, MAX_ACCOUNTS
    from iphone_detector.discord_inbox import managed_files
    from iphone_detector.ipa_inbox import validate_ipa
    from iphone_detector.ipa_assignments import mark_pending

    email = email_address(email)
    guard = RunLock()
    if not guard.acquire():
        raise ValueError(
            "A run is active. Wait for it to finish before adding the account to automation."
        )
    try:
        config = load_config() or {}
        workflow = get_workflow(config)
        if len(workflow) >= MAX_ACCOUNTS:
            raise ValueError("This workflow supports up to 25 configured accounts.")
        if any(entry["email"].casefold() == email for entry in workflow):
            raise ValueError("That email is already in /accounts. Use /inbox to change its IPA.")
        source = Path(source)
        if source not in managed_files(config):
            raise ValueError("The selected IPA is no longer in the configured folder.")
        before = source.stat()
        if (before.st_size, before.st_mtime_ns) != signature:
            raise ValueError("The IPA changed. Choose it again.")
        validate_ipa(source)
        after = source.stat()
        if (after.st_size, after.st_mtime_ns) != signature:
            raise ValueError("The IPA changed while checking it. Choose it again.")
        entries = [{"email": entry["email"], "ipa": entry["ipa"]} for entry in workflow]
        entries.append({"email": email, "ipa": source.stem})
        automation = config.setdefault("automation", {})
        automation["accounts"] = entries
        automation["background_mode"] = True
        mark_pending(config, len(workflow), source.stem)
        save_config(config)
        return len(entries)
    finally:
        guard.close()


class LoginSession:
    def __init__(self):
        self.process = None
        self.guard = RunLock()
        self.email = None
        self.challenge = 0
        self.waiting = False
        self.started = False
        self.cancelled = False
        self._closing = False

    async def start(self, email, password):
        if self.started:
            raise ValueError("This sign-in session has already been submitted.")
        self.email = email_address(email)
        if not password or len(password) > 4096:
            raise ValueError("Enter the Apple account password.")
        server = server_url()
        if not self.guard.acquire():
            raise ValueError(
                "An installation or another sign-in is running. Wait for it to finish."
            )
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe"),
                "--account-auth",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                limit=32768,
            )
            if self.cancelled:
                self.process.stdin.close()
                if self.process.returncode is None:
                    self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=10)
                raise ValueError("Sign-in was cancelled before it started.")
            self.started = True
            await self.send({"email": self.email, "password": password, "server": server})
        except BaseException:
            await self.close()
            raise

    async def send(self, value):
        if not self.process or self.process.returncode is not None or self.cancelled:
            raise ValueError("Sign-in has ended. Start /login again.")
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def respond(self, number, value):
        if number != self.challenge or not self.waiting:
            raise ValueError("That verification prompt has expired. Use the newest prompt.")
        self.waiting = False
        try:
            await self.send(value)
        except BaseException:
            self.waiting = True
            raise

    async def events(self):
        while self.process:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=180)
            if not line:
                raise ValueError(
                    "Apple sign-in ended without a verified completion. Start /login again."
                )
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            if value.get("stage") == "challenge":
                self.challenge += 1
                self.waiting = True
            if value.get("stage") == "complete":
                code = await asyncio.wait_for(self.process.wait(), timeout=10)
                if code != 0 or value.get("email") != self.email:
                    raise ValueError("Apple sign-in completion could not be verified.")
            yield value
            if value.get("stage") in ("complete", "failed"):
                return

    async def close(self):
        if self._closing:
            return
        self._closing = True
        self.cancelled = True
        self.waiting = False
        try:
            if self.process:
                if self.process.stdin:
                    self.process.stdin.close()
                if self.process.returncode is None:
                    try:
                        self.process.terminate()
                    except ProcessLookupError:
                        pass
                await asyncio.wait_for(self.process.wait(), timeout=10)
        finally:
            self.guard.close()
