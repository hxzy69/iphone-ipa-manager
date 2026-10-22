"""Three-way settings merge: live changes never erase unsaved GUI edits."""

from pathlib import Path
from iphone_detector.settings import get_workflow


def fields(config):
    automation = config.get("automation", {})
    result = {
        "folder": str(automation.get("ipa_directory", Path.home() / "Downloads/ipas")),
        "timeout": str(round(automation.get("install_timeout", 900) / 60)),
        "exit_after": bool(automation.get("close_gui_after_success", False)),
        "wifi_fast": bool(automation.get("wifi_archive_transfer", True)),
        "notify_finish": bool(automation.get("notify_on_finish", True)),
    }
    result.update(
        {"ipa%d" % index: entry["ipa"] + ".ipa" for index, entry in enumerate(get_workflow(config))}
    )
    return result


def merge(baseline, local, remote):
    values, next_baseline, conflicts = {}, {}, []
    for key, saved in remote.items():
        before = baseline[key]
        edited = local[key]
        if edited == before or edited == saved:
            values[key] = next_baseline[key] = saved
        else:
            values[key], next_baseline[key] = edited, before
            if saved != before:
                conflicts.append(key)
    return values, next_baseline, conflicts


def conflict_names(keys):
    labels = {
        "folder": "IPA folder",
        "timeout": "timeout",
        "exit_after": "close after completion",
        "wifi_fast": "fast transfer",
        "notify_finish": "Windows notifications",
    }
    labels.update({"ipa%d" % index: "account %d IPA" % (index + 1) for index in range(25)})
    return ", ".join(labels.get(key, key) for key in keys)
