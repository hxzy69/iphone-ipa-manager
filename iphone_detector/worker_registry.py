"""Discover and cancel only our live workers, including GUI and scheduled runs."""

import psutil
import time
import math
import logging

from iphone_detector.storage import write_json
from iphone_detector.job_history import read


def register(job, mode, accounts=None):
    from iphone_detector.settings import get_workflow

    process = psutil.Process()
    write_json(
        job / "worker.json",
        {
            "state": "running",
            "pid": process.pid,
            "process_created": process.create_time(),
            "started_at": time.time(),
            "mode": mode,
            "accounts": [value + 1 for value in accounts]
            if accounts
            else list(range(1, len(get_workflow()) + 1)),
        },
    )


def finish(job):
    value = read(job / "worker.json")
    if value:
        write_json(job / "worker.json", dict(value, state="finished", finished_at=time.time()))


def active_jobs(root):
    active = []
    for folder in (root / "automation-runs", root / "gui-jobs"):
        if not folder.is_dir():
            continue
        for job in folder.iterdir():
            if job.is_symlink() or not job.is_dir():
                continue
            value = read(job / "worker.json")
            if value.get("state") != "running":
                continue
            try:
                if (
                    type(value.get("pid")) is not int
                    or value["pid"] <= 0
                    or type(value.get("process_created")) not in (int, float)
                    or not math.isfinite(value["process_created"])
                    or value["process_created"] <= 0
                    or type(value.get("started_at")) not in (int, float)
                    or not math.isfinite(value["started_at"])
                    or not 0 <= value["started_at"] <= time.time() + 5
                    or not isinstance(value.get("mode"), str)
                    or not isinstance(value.get("accounts"), list)
                    or not value["accounts"]
                    or any(
                        type(number) is not int or not 1 <= number <= 25
                        for number in value["accounts"]
                    )
                ):
                    continue
                process = psutil.Process(value["pid"])
                if (
                    abs(process.create_time() - value["process_created"]) > 0.01
                    or not process.is_running()
                ):
                    continue
            except psutil.Error, OSError, ValueError, KeyError, TypeError:
                continue
            active.append(dict(value, job=job))
    return sorted(active, key=lambda item: item["started_at"], reverse=True)


def stop_active(root):
    jobs = active_jobs(root)
    requested = 0
    for value in jobs:
        try:
            (value["job"] / "stop").touch()
            requested += 1
        except OSError:
            logging.getLogger(__name__).exception(
                "Could not persist cancellation for a worker; continuing with other active runs."
            )
    return requested
