"""Fast, read-only IPA metadata for the desktop library and Discord.

Reading a manifest is not archive validation. Assignments still go through
discord_inbox.assign_managed, which checks the complete archive under RunLock.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import zipfile

from iphone_detector.ipa_inbox import MAX_MEMBERS, MAX_METADATA, parse_manifest
from iphone_detector.settings import get_workflow


@dataclass(frozen=True)
class LibraryEntry:
    path: Path
    signature: tuple[int, int]
    name: str
    version: str
    bundle_id: str
    accounts: tuple[int, ...] = ()
    error: str = ""

    @property
    def size_mb(self):
        return self.signature[0] / 1048576

    def matches(self, query):
        return (
            query.strip().casefold()
            in " ".join((self.path.name, self.name, self.version, self.bundle_id)).casefold()
        )


def _text(value, fallback=""):
    if not isinstance(value, str):
        return fallback
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:200] or fallback


@lru_cache(maxsize=256)
def _metadata(path, size, modified, changed):
    # Stat values are cache keys; never cache account assignments or credentials.
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ValueError("Too many archive entries")
            manifests = [
                member
                for member in members
                if member.filename.startswith("Payload/")
                and member.filename.endswith(".app/Info.plist")
                and member.filename.count("/") == 2
                and "\\" not in member.filename
            ]
            if len(manifests) != 1:
                raise ValueError("Expected one main app manifest")
            manifest = manifests[0]
            if manifest.file_size > MAX_METADATA:
                raise ValueError("App manifest is too large")
            with archive.open(manifest) as stream:
                raw = stream.read(MAX_METADATA + 1)
            if len(raw) > MAX_METADATA:
                raise ValueError("App manifest is too large")
            info = parse_manifest(raw)
            identifier = _text(info.get("CFBundleIdentifier"))
            version = _text(info.get("CFBundleShortVersionString")) or _text(
                info.get("CFBundleVersion")
            )
            if not identifier or not version:
                raise ValueError("Missing app identifier or version")
            name = _text(info.get("CFBundleDisplayName")) or _text(info.get("CFBundleName"))
            return name, version, identifier, ""
    except OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError, RecursionError:
        return "", "", "", "Cannot read app metadata; finish the transfer or replace this IPA."


def clear_cache():
    """An explicit refresh also re-reads manifests with unchanged timestamps."""
    _metadata.cache_clear()


def scan(config, *, stop=None):
    """List regular managed files, tolerating files removed during a transfer."""
    folder = Path(
        config.get("automation", {}).get("ipa_directory", Path.home() / "Downloads" / "ipas")
    )
    if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
        raise ValueError("Choose an existing local IPA folder in Accounts & apps.")
    workflow = get_workflow(config)
    result = []
    try:
        paths = list(folder.iterdir())
    except OSError as exc:
        raise ValueError(
            "Cannot open the IPA folder. Check that it is available and readable."
        ) from exc
    for path in paths:
        if stop is not None and stop.is_set():
            break
        if path.suffix.lower() != ".ipa":
            continue
        try:
            if path.is_symlink() or path.is_junction() or not path.is_file():
                continue
            stat = path.stat()
            name, version, bundle, error = _metadata(
                str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
            )
            after = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if (
                signature != (after.st_size, after.st_mtime_ns)
                or stat.st_ctime_ns != after.st_ctime_ns
            ):
                name, version, bundle, error = (
                    "",
                    "",
                    "",
                    "File is changing. Refresh after the transfer finishes.",
                )
            accounts = tuple(
                i + 1
                for i, account in enumerate(workflow)
                if account["ipa"].casefold() == path.stem.casefold()
            )
            result.append(
                LibraryEntry(
                    path, signature, name or _text(path.stem), version, bundle, accounts, error
                )
            )
        except OSError:
            continue
    return sorted(result, key=lambda entry: (-entry.signature[1], entry.path.name.casefold()))


def sample_entries():
    """Sample data for the inert GUI preview; never reads a real folder."""
    return [
        LibraryEntry(
            Path("Video 2.1.ipa"), (84 * 1048576, 0), "Video", "2.1", "com.example.video", (1,)
        ),
        LibraryEntry(
            Path("Music 4.0.ipa"), (120 * 1048576, 0), "Music", "4.0", "com.example.music", (2,)
        ),
        LibraryEntry(
            Path("Stream 1.3.ipa"), (65 * 1048576, 0), "Stream", "1.3", "com.example.stream", (3,)
        ),
        LibraryEntry(Path("Video 2.2.ipa"), (86 * 1048576, 0), "Video", "2.2", "com.example.video"),
    ]
