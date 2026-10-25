"""Commercial acceptance cannot be inherited from another build or an old run."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from test_build_controls import tool


class PaidReadinessTests(unittest.TestCase):
    def test_missing_or_unsigned_evidence_never_passes(self):
        module = tool("release_readiness")
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "app.exe"
            exe.write_bytes(b"fixture")
            result = module.assess(
                exe, exe, {}, {}, inspect_signature=lambda p: {"status": "NotSigned"}
            )
        self.assertFalse(result["ok"])
        self.assertTrue(all(not c["ok"] for c in result["checks"]))

    def test_current_evidence_is_bound_to_binary_and_fresh_audit(self):
        module = tool("release_readiness")
        now = datetime.now(timezone.utc)
        audit = {
            "ok": True,
            "checked_at": now.isoformat(),
            "findings": [],
            "accepted": [{"id": "fixture"}],
        }
        receipt = {
            "ok": True,
            "exe_sha256": hashlib.sha256(b"fixture").hexdigest(),
            "tested_at": now.isoformat(),
            "evidence": "QA acceptance report",
        }
        evidence = {"acceptance": {scenario: dict(receipt) for scenario in module.SCENARIOS}}
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "app.exe"
            exe.write_bytes(b"fixture")
            options = {
                "now": now,
                "inspect_signature": lambda p: {"status": "Valid", "thumbprint": "fixture"},
            }
            self.assertTrue(module.assess(exe, exe, audit, evidence, **options)["ok"])
            exe.write_bytes(b"changed")
            self.assertFalse(module.assess(exe, exe, audit, evidence, **options)["ok"])
            exe.write_bytes(b"fixture")
            evidence["acceptance"][module.SCENARIOS[0]]["tested_at"] = "2000-01-01T00:00:00+00:00"
            self.assertFalse(module.assess(exe, exe, audit, evidence, **options)["ok"])

    def test_malformed_or_future_receipts_fail_closed(self):
        module = tool("release_readiness")
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "app.exe"
            exe.write_bytes(b"fixture")
            for receipt in (
                [],
                None,
                {"tested_at": "9999-01-01T00:00:00+00:00"},
                {"tested_at": "2026-10-23"},
            ):
                with self.subTest(receipt=receipt):
                    result = module.assess(
                        exe,
                        exe,
                        {},
                        {"acceptance": {s: receipt for s in module.SCENARIOS}},
                        inspect_signature=lambda p: {},
                    )
                    self.assertFalse(result["ok"])
