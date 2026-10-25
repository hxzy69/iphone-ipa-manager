"""Check commercial-release evidence against the exact candidate being shipped.

This is a release checklist, not a security certification. Missing, stale or
different-build acceptance never inherits a pass from an older preview.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

SCENARIOS = (
    "clean_windows_install_update_uninstall",
    "new_user_pairing_and_account_setup",
    "usb_install_and_launch",
    "local_wifi_install_and_refresh",
    "cellular_roaming_and_recovery",
    "reboot_and_overdue_renewal",
    "launch_after_previous_profile_expiry",
    "accessibility_and_setup_walkthrough",
    "commercial_dependency_review",
)


def signature(path):
    script = (
        "$ErrorActionPreference='Stop'; "
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
        "$s=Get-AuthenticodeSignature -LiteralPath $env:IPA_RELEASE_SIGNATURE_FILE; "
        "@{status=[string]$s.Status;thumbprint=[string]$s.SignerCertificate.Thumbprint} "
        "| ConvertTo-Json -Compress"
    )
    value = subprocess.check_output(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, IPA_RELEASE_SIGNATURE_FILE=str(Path(path).resolve())),
        timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW,
        text=True,
        stderr=subprocess.PIPE,
    )
    return json.loads(value)


def assess(executable, installer, audit, evidence, *, now=None, inspect_signature=signature):
    now = now or datetime.now(timezone.utc)
    digest = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for name, path in (("application_signature", executable), ("installer_signature", installer)):
        try:
            result = inspect_signature(path)
            valid = result.get("status") == "Valid" and bool(result.get("thumbprint"))
            add(
                name,
                valid,
                "Valid Authenticode signature." if valid else "Trusted code signing required.",
            )
        except OSError, ValueError, subprocess.SubprocessError:
            add(name, False, "Signature verification failed.")
    try:
        checked = datetime.fromisoformat(audit["checked_at"])
        fresh = 0 <= (now - checked).total_seconds() <= 7 * 86400
    except KeyError, TypeError, ValueError:
        fresh = False
    add(
        "dependency_audit",
        fresh and audit.get("ok") is True and audit.get("findings") == [],
        "Requires a passing dependency audit from the last seven days.",
    )
    acceptance = evidence.get("acceptance", {})
    if not isinstance(acceptance, dict):
        acceptance = {}
    for scenario in SCENARIOS:
        receipt = acceptance.get(scenario, {})
        if not isinstance(receipt, dict):
            receipt = {}
        try:
            age = (now - datetime.fromisoformat(receipt["tested_at"])).total_seconds()
            fresh = 0 <= age <= 30 * 86400
        except KeyError, TypeError, ValueError:
            fresh = False
        add(
            scenario,
            receipt.get("ok") is True
            and receipt.get("exe_sha256") == digest
            and isinstance(receipt.get("evidence"), str)
            and bool(receipt["evidence"].strip())
            and fresh,
            "Requires dated acceptance evidence for this exact executable within 30 days.",
        )
    if audit.get("accepted"):
        reviewed = next(c for c in checks if c["name"] == "commercial_dependency_review")["ok"]
        add(
            "advisory_exceptions_reviewed",
            reviewed,
            "Accepted advisories require commercial review.",
        )
    return {"ok": all(c["ok"] for c in checks), "exe_sha256": digest, "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = assess(
        args.executable,
        args.installer,
        json.loads(args.audit.read_text(encoding="utf-8")),
        json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else {},
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
