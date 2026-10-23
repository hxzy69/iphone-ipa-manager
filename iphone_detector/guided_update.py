"""Verified side-by-side updates and recoverable normal-user startup migration."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

import psutil

from iphone_detector.paths import DATA_ROOT, FROZEN
from iphone_detector.storage import write_json
from iphone_detector.job_history import read
from iphone_detector.updates import REPOSITORY, version_tuple

STATE = DATA_ROOT / "update-state.json"
PREVIOUS = DATA_ROOT / "previous-version.json"


def download(url, destination, limit):
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "iPhoneIPAManager-Updater"})
    with urlopen(request, timeout=20) as response, destination.open("wb") as output:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or not (
            final.hostname == "github.com"
            or (final.hostname or "").endswith(".githubusercontent.com")
        ):
            raise ValueError("Download redirected outside GitHub.")
        size = 0
        while chunk := response.read(128 * 1024):
            size += len(chunk)
            if size > limit or time.monotonic() - started > 600:
                raise ValueError("Release download exceeded its size or time limit.")
            output.write(chunk)


def prepare(release):
    from iphone_detector.release_integrity import verify_release

    version_tuple(release.version)
    tag = release.url.removeprefix(REPOSITORY + "/releases/tag/")
    if release.url != REPOSITORY + "/releases/tag/" + tag or tag != "v" + release.version:
        raise ValueError("Choose an official release.")
    version = release.version.removesuffix("-preview")
    folder = DATA_ROOT / "updates" / (version + "-" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True)
    names = [
        f"iPhoneIPAManager-{version}-windows-x64.zip",
        f"iPhoneIPAManager-{version}-source.zip",
        "SHA256SUMS.txt",
    ]
    for name, limit in zip(names, (256 * 1024 * 1024, 64 * 1024 * 1024, 16 * 1024)):
        download(REPOSITORY + f"/releases/download/{tag}/{name}", folder / name, limit)
    verify_release(folder)
    destination = folder / "app"
    with zipfile.ZipFile(folder / names[0]) as archive:
        members = archive.infolist()
        if len(members) > 25000 or sum(item.file_size for item in members) > 512 * 1024 * 1024:
            raise ValueError("Release expands beyond the supported size.")
        for item in members:
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Release contains a symbolic link.")
        archive.extractall(destination)
    executable = destination / "iPhoneIPAManager.exe"
    receipt = read(destination / "self-test.json")
    if receipt.get("version") != version:
        raise ValueError("Downloaded runtime version does not match the release.")
    plan = {
        "schema_version": 1,
        "version": version,
        "executable": str(executable),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    write_json(folder / "ready.json", plan)
    write_json(DATA_ROOT / "prepared-update.json", {"plan": str(folder / "ready.json")})
    return folder / "ready.json"


def validate_plan(path):
    plan = read(path)
    executable = Path(plan.get("executable", ""))
    if not executable.is_absolute() or not executable.is_file() or executable.is_symlink():
        raise ValueError("Prepared executable is unavailable.")
    if plan.get("sha256") != hashlib.sha256(executable.read_bytes()).hexdigest():
        raise ValueError("Prepared executable changed after verification.")
    return plan, executable


def start(plan):
    if not FROZEN:
        raise ValueError("Guided activation is available in the standalone EXE.")
    validate_plan(plan)
    state = read(STATE)
    if (
        state.get("phase") in ("starting", "close_gui", "migrating")
        and state.get("at", 0) > time.time() - 600
    ):
        raise ValueError("An update is already being activated.")
    from iphone_detector import job_queue

    with job_queue.guard():
        if job_queue.pending():
            raise ValueError("Finish or cancel queued requests before updating.")
        write_json(STATE, {"phase": "starting", "at": time.time()})
    from iphone_detector.discord_bot import command

    try:
        subprocess.Popen(
            command(["--activate-update", str(plan), "--parent-pid", str(os.getpid())]),
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
        )
    except OSError:
        write_json(
            STATE,
            {
                "phase": "failed",
                "at": time.time(),
                "message": "Could not start the update helper. The current app was preserved.",
            },
        )
        raise


def configure_tasks(old, executable):
    from iphone_detector.startup_task import retarget

    retarget(old, executable, DATA_ROOT)


def check_runtime(executable, version=None):
    from iphone_detector.release_integrity import verify_receipt

    report = DATA_ROOT / "update-runtime-check.json"
    report.unlink(missing_ok=True)
    result = subprocess.run(
        [str(executable), "--self-test", "--report-file", str(report)],
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ValueError("The downloaded runtime failed its local self-test.")
    verify_receipt(read(report), executable.read_bytes(), expected_version=version)


def launch(executable, preferences, gui=True):
    base = [str(executable), "--data-dir", str(DATA_ROOT)]
    modes = [
        args
        for args in (
            ["--discord"] if preferences["discord"] else [],
            ["--remote-daemon"] if preferences["remote"] else [],
            ["--queue-runner"] if preferences["queue"] else [],
        )
        if args
    ]
    if gui:
        modes.append([])
    started = []
    try:
        for args in modes:
            started.append(
                subprocess.Popen(
                    base + args,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                )
            )
    except OSError:
        for process in started:
            try:
                if process.poll() is None:
                    children = psutil.Process(process.pid).children(recursive=True)
                    for child in reversed(children):
                        child.terminate()
                    process.terminate()
            except OSError, psutil.Error:
                pass
        raise


def wait_for_exit(pid, created, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - created) > 0.01 or not process.is_running():
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.2)
    raise ValueError("The previous app did not close. Startup tasks were not migrated.")


def activate(plan_path, parent_pid):
    from iphone_detector.run_lock import RunLock
    from iphone_detector import job_queue

    singleton = RunLock()
    singleton.name = "Local\\iPhoneIPACommunityUpdater"
    if not singleton.acquire():
        return 1
    lock = RunLock()
    old = Path(sys.executable)
    preferences = None
    parent_closed = False
    try:
        plan, executable = validate_plan(plan_path)
        if not parent_pid or not lock.acquire():
            raise ValueError("Wait for active installations or account changes before updating.")
        if job_queue.pending():
            raise ValueError("Finish or cancel queued requests before updating.")
        parent = psutil.Process(parent_pid)
        created = parent.create_time()
        if Path(parent.exe()).resolve() != old.resolve():
            raise ValueError("Update parent does not match this app.")
        check_runtime(executable, plan.get("version"))
        discord = read(DATA_ROOT / "discord.json")
        preferences = {
            "discord": discord.get("startup") is True or "--discord" in parent.cmdline(),
            "remote": read(DATA_ROOT / "remote.json").get("enabled") is True,
            "renewal": read(DATA_ROOT / "auto-refresh.json").get("enabled") is True,
            "queue": bool((DATA_ROOT / "queue").exists()),
        }
        # Keep only processes from this executable and this private data directory.
        peers = []
        guis = []
        for process in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
            args = process.info["cmdline"] or []
            if (
                process.pid not in (os.getpid(), parent_pid)
                and process.info["exe"]
                and Path(process.info["exe"]).resolve() == old.resolve()
                and {arg for arg in args[1:] if arg.startswith("--")} <= {"--data-dir"}
                and ("--data-dir" not in args or str(DATA_ROOT) in args)
            ):
                guis.append((process.pid, process.info["create_time"]))
            if (
                process.pid not in (os.getpid(), parent_pid)
                and process.info["exe"]
                and Path(process.info["exe"]).resolve() == old.resolve()
                and any(
                    flag in args
                    for flag in ("--discord", "--remote-daemon", "--auto-refresh", "--queue-runner")
                )
                and ("--data-dir" not in args or str(DATA_ROOT) in args)
            ):
                peers.append((process.pid, process.info["create_time"]))
                if "--discord" in args:
                    preferences["discord"] = True
        for name in ("discord-stop", "remote-stop"):
            (DATA_ROOT / name).touch()
        for pid, when in peers:
            wait_for_exit(pid, when, timeout=45)
        write_json(STATE, {"phase": "close_gui", "at": time.time()})
        wait_for_exit(parent_pid, created)
        parent_closed = True
        for pid, when in guis:
            wait_for_exit(pid, when)
        write_json(STATE, {"phase": "migrating", "at": time.time()})
        configure_tasks(old, executable)
        write_json(
            PREVIOUS,
            {
                "schema_version": 1,
                "executable": str(old),
                "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
            },
        )
        launch(executable, preferences)
        write_json(STATE, {"phase": "complete", "at": time.time(), "executable": str(executable)})
        return 0
    except Exception as exc:
        if preferences is not None:
            try:
                configure_tasks(executable, old)
                launch(old, preferences, gui=parent_closed)
            except Exception:
                write_json(
                    STATE,
                    {
                        "phase": "repair",
                        "at": time.time(),
                        "message": "Startup migration failed. Open the previous release and re-enable startup from Discord setup.",
                    },
                )
                return 1
        write_json(
            STATE,
            {
                "phase": "failed",
                "at": time.time(),
                "message": str(exc)[:400]
                if isinstance(exc, ValueError)
                else "Update was not completed. The previous executable was preserved; check startup settings before retrying.",
            },
        )
        return 1
    finally:
        lock.close()
        singleton.close()
