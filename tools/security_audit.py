"""Audit public dependency locks against OSV and emit a CycloneDX inventory."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tomllib
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def packages():
    values = []
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.strip().split("==")
            values.append({"package": {"name": name, "ecosystem": "PyPI"}, "version": version})
    graph = tomllib.loads((ROOT / "native/Cargo.lock").read_text(encoding="utf-8"))
    for package in graph["package"]:
        if package.get("source", "").startswith("registry+") or package["name"] in (
            "isideload",
            "apple-codesign-quick",
            "block-buffer",
        ):
            values.append(
                {
                    "package": {"name": package["name"], "ecosystem": "crates.io"},
                    "version": package["version"],
                }
            )
    return values


def audit():
    queries = packages()
    exceptions = json.loads(
        (ROOT / "third_party/security-exceptions.json").read_text(encoding="utf-8")
    )
    today = datetime.now(timezone.utc).date().isoformat()
    findings, accepted = [], []
    for index in range(0, len(queries), 75):
        batch = queries[index : index + 75]
        request = Request(
            "https://api.osv.dev/v1/querybatch",
            data=json.dumps({"queries": batch}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=45) as response:
            payload = response.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("Dependency advisory response exceeded its limit.")
        results = json.loads(payload)["results"]
        if len(results) != len(batch):
            raise ValueError("Dependency advisory response was incomplete.")
        for package, result in zip(batch, results):
            for advisory in result.get("vulns", []):
                value = {
                    "id": advisory["id"],
                    "name": package["package"]["name"],
                    "ecosystem": package["package"]["ecosystem"],
                    "version": package["version"],
                }
                exception = next(
                    (
                        item
                        for item in exceptions
                        if all(item.get(k) == v for k, v in value.items())
                        and item.get("expires", "") >= today
                        and item.get("reason")
                    ),
                    None,
                )
                (accepted if exception else findings).append(
                    dict(
                        value,
                        **(
                            {"reason": exception["reason"], "expires": exception["expires"]}
                            if exception
                            else {}
                        ),
                    )
                )
    return {
        "ok": not findings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database": "https://osv.dev",
        "queried": len(queries),
        "findings": findings,
        "accepted": accepted,
    }


def sbom():
    import re
    from urllib.parse import quote

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]

    def package_url(package):
        ecosystem = "pypi" if package["package"]["ecosystem"] == "PyPI" else "cargo"
        name = package["package"]["name"]
        if ecosystem == "pypi":
            name = re.sub(r"[-_.]+", "-", name).lower()
        return (
            f"pkg:{ecosystem}/{quote(name, safe='-._~')}@{quote(package['version'], safe='-._~')}"
        )

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {"type": "application", "name": "iPhone IPA Manager", "version": version},
        },
        "components": [
            {
                "type": "library",
                "name": p["package"]["name"],
                "version": p["version"],
                "purl": package_url(p),
            }
            for p in packages()
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/dependency-audit.json")
    args = parser.parse_args()
    result = audit()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.report.with_name("sbom.cdx.json").write_text(
        json.dumps(sbom(), indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
