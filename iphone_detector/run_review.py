"""Read-only, file-bound plan for manual installs and refreshes."""

import hashlib
import json

from iphone_detector.ipa_library import scan
from iphone_detector.settings import get_workflow
from iphone_detector.ipa_assignments import needs_install


def plan(config, mode="install", accounts=None):
    if mode not in ("install", "refresh"):
        raise ValueError("Choose install or refresh.")
    workflow = get_workflow(config)
    indexes = list(range(len(workflow))) if accounts is None else list(accounts)
    if mode == "refresh" and indexes != list(range(len(workflow))):
        raise ValueError("Profile refresh always reviews all configured accounts.")
    if (
        not indexes
        or len(set(indexes)) != len(indexes)
        or any(type(i) is not int or not 0 <= i < len(workflow) for i in indexes)
    ):
        raise ValueError("Choose configured accounts first.")
    selected_stems = {workflow[index]["ipa"].casefold() for index in indexes}
    entries = {item.path.stem.casefold(): item for item in scan(config, stems=selected_stems)}
    rows = []
    for index in indexes:
        account = workflow[index]
        item = entries.get(account["ipa"].casefold())
        if item is None or item.error:
            raise ValueError(
                "Account %d needs a completed, readable IPA. Check the library." % (index + 1)
            )
        pending = needs_install(config, index + 1, account["ipa"])
        rows.append(
            {
                "account": index + 1,
                "email": account["email"],
                "ipa": item.path.name,
                "app": item.name,
                "version": item.version,
                "bundle_id": item.bundle_id,
                "signature": item.signature,
                "operation": "Full installation (changed assignment)"
                if pending
                else "Full installation"
                if mode == "install"
                else "Profile refresh if eligible; full install if required",
            }
        )
    binding = {
        "mode": mode,
        "rows": rows,
        "directory": config.get("automation", {}).get("ipa_directory"),
        "serial": config.get("device", {}).get("serial"),
    }
    return {
        "mode": mode,
        "rows": rows,
        "token": hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest(),
    }


def verify(review, config):
    if (
        not isinstance(review, dict)
        or review.get("mode") not in ("install", "refresh")
        or not isinstance(review.get("token"), str)
        or not isinstance(review.get("rows"), list)
        or not 1 <= len(review["rows"]) <= 25
        or any(
            not isinstance(row, dict) or type(row.get("account")) is not int
            for row in review["rows"]
        )
    ):
        raise ValueError("This review is invalid. Open a fresh review.")
    current = plan(config, review["mode"], [row["account"] - 1 for row in review["rows"]])
    if current["token"] != review["token"]:
        raise ValueError("Settings or IPA files changed after review. Review the run again.")
    return current


def describe(review):
    lines = ["Review %s — %d account(s)" % (review["mode"], len(review["rows"]))]
    for row in review["rows"]:
        lines.append(
            "Account {account} · {email}\n{app} {version} · {ipa}\n{operation}".format(**row)
        )
    lines.append(
        "Refresh eligibility is checked on the phone. File metadata is not a full archive validation."
    )
    return "\n\n".join(lines)


def verify_scope(review, config, mode, accounts=None):
    verify(review, config)
    expected = list(range(len(get_workflow(config)))) if accounts is None else list(accounts)
    if review["mode"] != mode or [row["account"] - 1 for row in review["rows"]] != expected:
        raise ValueError("The reviewed operation or account selection changed. Review again.")
