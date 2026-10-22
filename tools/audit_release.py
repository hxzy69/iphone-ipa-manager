"""Validate the public source allowlist and reject common private release inputs."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = (
    "assets",
    "third_party",
    "iphone_detector",
    "native",
    "vendor",
    "tools",
    "tests",
    "docs",
    ".github",
)
ROOT_FILES = (
    "ipa_manager.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "requirements-build.txt",
    "requirements-dev.txt",
    "iPhoneIPAManager.spec",
    "config.example.json",
    "README.md",
    "LICENSE",
    "NOTICE.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".gitignore",
    ".gitattributes",
)
EXCLUDED = {
    ".git",
    ".venv",
    "target",
    "build",
    "dist",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
}
PRIVATE_NAMES = {
    "config.json",
    "discord.json",
    "remote.json",
    "remote-state.json",
    "renewal-last-success.json",
    "owner.sid",
    ".env",
}
PRIVATE_SUFFIXES = {".ipa", ".mobileprovision", ".p12", ".pfx", ".pem", ".key", ".log", ".pyc"}
TEXT_SUFFIXES = {
    ".py",
    ".rs",
    ".toml",
    ".json",
    ".lock",
    ".txt",
    ".md",
    ".ps1",
    ".cs",
    ".html",
    ".yml",
    ".spec",
}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "literal bot token": re.compile(
        r"(?:mfa\.[A-Za-z0-9_-]{60,}|[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{30,})"
    ),
    "developer profile path": re.compile(
        r'(?i)C:[/\\]Users[/\\](?!ExampleUser(?:[/\\]|$)|Public(?:[/\\]|$))[^/\\\s"\']+'
    ),
}


def source_files(root=ROOT):
    for name in ROOT_FILES:
        path = root / name
        if path.is_file():
            yield path
    for directory in DIRECTORIES:
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED for part in relative.parts) or path.is_dir():
                continue
            if path.suffix.lower() in {".exe", ".dll", ".zip", ".pdb"}:
                continue
            yield path


def audit(root=ROOT):
    findings = []
    files = list(source_files(root))
    for path in files:
        relative = path.relative_to(root).as_posix()
        if (
            path.is_symlink()
            or path.name in PRIVATE_NAMES
            or path.suffix.lower() in PRIVATE_SUFFIXES
        ):
            findings.append({"file": relative, "reason": "private or unsafe release input"})
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "LICENSE":
            continue
        text = path.read_text(encoding="utf-8-sig")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append({"file": relative, "reason": label})
    config = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
    if config.get("device") or config.get("automation", {}).get("accounts"):
        findings.append(
            {
                "file": "config.example.json",
                "reason": "example contains live account/device defaults",
            }
        )
    return {"ok": not findings, "source_files": len(files), "findings": findings}


if __name__ == "__main__":
    report = audit()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
