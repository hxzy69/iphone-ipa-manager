"""Verified side-by-side updates and recoverable normal-user startup migration."""

import hashlib
import logging
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

import psutil

from iphone_detector.paths import DATA_ROOT, FROZEN, process_uses_profile
from iphone_detector.storage import write_json
from iphone_detector.job_history import read
from iphone_detector.updates import REPOSITORY, version_tuple

STATE = DATA_ROOT / "update-state.json"
PREVIOUS = DATA_ROOT / "previous-version.json"
LOG = logging.getLogger("iphone-detector.update")


class ReplacementCleanupError(ValueError):
    """Starting the previous runtime is unsafe until its replacement has stopped."""


def stop_replacements(processes):
    failures = False
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            children = []
            failures = True
        for target in [*reversed(children), process]:
            try:
                target.terminate()
                try:
                    target.wait(timeout=10)
                except subprocess.TimeoutExpired, psutil.TimeoutExpired:
                    target.kill()
                    target.wait(timeout=5)
            except ProcessLookupError, psutil.NoSuchProcess:
                continue  # A child can finish between discovery and termination.
            except OSError, psutil.Error, subprocess.TimeoutExpired:
                failures = True
    if failures:
        raise ReplacementCleanupError(
            "Replacement shutdown could not be verified. Close its components locally before repairing startup."
        )


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

    version_tuple(release.version)
    if shutil.disk_usage(Path(DATA_ROOT).anchor).free < 1024**3:
        raise ValueError("Keep at least 1 GiB free to download and prepare an update safely.")
    tag = release.url.removeprefix(REPOSITORY + "/releases/tag/")
    if release.url != REPOSITORY + "/releases/tag/" + tag or tag != "v" + release.version:
        raise ValueError("Choose an official release.")
    version = release.version.removesuffix("-preview")
    folder = DATA_ROOT / "updates" / (version + "-" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True)
    try:
        return _prepare_downloads(folder, version, tag)
    except BaseException:
        # This attempt created the directory. Never accumulate hundreds of MB
        # after a failed/offline download, or touch a previous prepared update.
        try:
            if (
                folder.is_symlink()
                or folder.is_junction()
                or folder.resolve().parent != (DATA_ROOT / "updates").resolve()
            ):
                raise ValueError("Update staging directory was redirected")
            shutil.rmtree(folder)
        except OSError, ValueError:
            LOG.warning("Could not remove the failed update's staging directory.")
        raise


def _prepare_downloads(folder, version, tag):
    from iphone_detector.release_integrity import verify_release

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


def launch(executable, preferences, gui=True, *, require_ack=True):
    base = [str(executable), "--data-dir", str(DATA_ROOT)]
    modes = [
        args
        for args in (
            ["--remote-daemon"] if preferences["remote"] else [],
            ["--discord"] if preferences["discord"] else [],
            ["--queue-runner"] if preferences["queue"] else [],
        )
        if args
    ]
    if gui:
        modes.append([])
    started = []
    nonce = uuid.uuid4().hex
    components = {"--discord": "discord", "--remote-daemon": "remote", "--queue-runner": "queue"}
    deadline = time.monotonic() + (45 if require_ack else 5)

    def wait_for_startup(waiting):
        while waiting:
            for process, args in waiting[:]:
                component = components.get(args[0], "gui") if args else "gui"
                receipt = read(DATA_ROOT / "activation-health" / f"{nonce}-{component}.json")
                if (
                    require_ack
                    and receipt.get("nonce") == nonce
                    and receipt.get("component") == component
                ):
                    actual = psutil.Process(receipt["pid"])
                    if (
                        Path(actual.exe()).resolve() != Path(executable).resolve()
                        or abs(actual.create_time() - receipt["created"]) > 0.01
                        or (
                            actual.pid != process.pid
                            and process.pid not in [p.pid for p in actual.parents()]
                        )
                    ):
                        raise ValueError("Replacement startup identity could not be verified.")
                    waiting.remove((process, args))
                    continue
                if process.poll() is not None:
                    raise ValueError(
                        "A replacement component exited before healthy startup. Rolling back."
                    )
            if not waiting:
                break
            if time.monotonic() >= deadline:
                if not require_ack:
                    break
                raise ValueError("Replacement startup was not acknowledged. Rolling back.")
            time.sleep(0.2)

    try:
        waiting = []
        for args in modes:
            process = subprocess.Popen(
                base + args,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=dict(
                    os.environ,
                    PYINSTALLER_RESET_ENVIRONMENT="1",
                    IPA_MANAGER_ACTIVATION_NONCE=nonce,
                ),
            )
            started.append(process)
            waiting.append((process, args))
            if require_ack and args == ["--remote-daemon"]:
                # Discord and the GUI can start the scheduled tunnel themselves.
                # Establish ownership here before launching those controllers,
                # otherwise a competing daemon can take the singleton and fail
                # this handover's process-identity acknowledgement.
                wait_for_startup(waiting)
        wait_for_startup(waiting)
    except OSError, ValueError, TypeError, KeyError, psutil.Error:
        stop_replacements(started)
        raise
    return started


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
    managed = None
    replacement_processes = []
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
            "queue": bool(job_queue.pending()),
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
                and process_uses_profile(args, DATA_ROOT)
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
                and process_uses_profile(args, DATA_ROOT)
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
        from iphone_detector import desktop_install

        install_root = desktop_install.installation_root().resolve()
        if old.resolve().is_relative_to(install_root):
            previous_install = desktop_install.manifest(install_root)
            if (
                previous_install is None
                or (install_root / previous_install["executable"]).resolve() != old.resolve()
            ):
                raise ValueError(
                    "Open the currently installed version before activating its update."
                )
            managed = (install_root, previous_install)
            executable = desktop_install.install_payload(
                executable.parent, install_root, _lock_held=True
            )
        configure_tasks(old, executable)
        write_json(
            PREVIOUS,
            {
                "schema_version": 1,
                "executable": str(old),
                "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
                "version": __import__("iphone_detector").__version__,
            },
        )
        replacement_processes = launch(
            executable,
            preferences,
            require_ack=version_tuple(plan.get("version", "0.2.1")) >= (0, 2, 1),
        )
        write_json(STATE, {"phase": "complete", "at": time.time(), "executable": str(executable)})
        return 0
    except Exception as exc:
        if isinstance(exc, ReplacementCleanupError):
            write_json(STATE, {"phase": "repair", "at": time.time(), "message": str(exc)})
            return 1
        if preferences is not None:
            try:
                stop_replacements(replacement_processes)
                if managed is not None:
                    desktop_install.restore_installation(*managed)
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
