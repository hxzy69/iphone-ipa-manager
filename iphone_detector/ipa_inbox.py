"""Validate and import completed IPA files into the managed directory."""

import tempfile
import uuid
import zipfile
from pathlib import Path
import plistlib
import re
import stat
from iphone_detector.cancellation import Cancelled

MAX_MEMBERS = 100_000
MAX_EXPANDED = 8 * 1024**3
MAX_MEMBER = 2 * 1024**3
MAX_METADATA = 1024 * 1024


def parse_manifest(raw):
    """Normalize failures from untrusted XML and binary property lists."""
    if len(raw) > MAX_METADATA:
        raise ValueError("The IPA app metadata is too large.")
    try:
        info = plistlib.loads(raw)
    except Exception:
        # plistlib also raises ExpatError and AttributeError for malformed XML
        # dates. Keep parser implementation details out of assignment errors.
        raise ValueError("The IPA contains invalid app metadata.") from None
    if not isinstance(info, dict):
        raise ValueError("The IPA contains invalid app metadata.")
    return info


def validate_ipa(path, *, stop=None):
    """Structural check shared by every Discord/PC assignment path."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_EXPANDED:
                raise ValueError("This IPA exceeds the supported archive size or file count.")
            names = archive.namelist()
            if len(names) != len({n.casefold() for n in names}):
                raise ValueError("This IPA contains duplicate or conflicting archive paths.")
            for member in members:
                if stop is not None:
                    stop.check()
                name = member.orig_filename
                normalized = name.replace("\\", "/")
                if (
                    normalized.startswith("/")
                    or any(part in ("", ".") for part in normalized.removesuffix("/").split("/"))
                    or ".." in normalized.split("/")
                    or ":" in normalized
                    or "\\" in name
                    or any(ord(char) < 32 for char in name)
                    or any(
                        part.endswith((".", " "))
                        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                        for part in normalized.split("/")
                        if part
                    )
                ):
                    raise ValueError("This IPA contains an unsafe archive path.")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(
                        "This IPA contains symbolic links; use an archive without links."
                    )
                if member.flag_bits & 1:
                    raise ValueError("Encrypted IPA archives are not supported.")
                if member.file_size > MAX_MEMBER or (
                    member.file_size > MAX_METADATA
                    and member.file_size > max(1, member.compress_size) * 500
                ):
                    raise ValueError("This IPA exceeds the supported decompression limits.")
            main = [
                name
                for name in names
                if name.startswith("Payload/")
                and name.endswith(".app/Info.plist")
                and name.count("/") == 2
            ]
            if len(main) != 1:
                raise ValueError("The IPA must contain exactly one main iOS app.")
            if archive.getinfo(main[0]).file_size > MAX_METADATA:
                raise ValueError("The IPA app metadata is too large.")
            info = parse_manifest(archive.read(main[0]))
            executable = info.get("CFBundleExecutable")
            bundle = info.get("CFBundleIdentifier")
            if (
                not isinstance(executable, str)
                or not re.fullmatch(r"[^/\\:\x00-\x1f]+", executable)
                or executable in (".", "..")
                or not isinstance(bundle, str)
                or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle)
                or not isinstance(info.get("CFBundleVersion"), str)
            ):
                raise ValueError(
                    "The IPA is missing its app identity, version or executable metadata."
                )
            binary = main[0].rsplit("/", 1)[0] + "/" + executable
            if binary not in names or archive.getinfo(binary).file_size == 0:
                raise ValueError("The IPA app executable is missing or empty.")
            for member in members:
                if stop is not None:
                    stop.check()
                with archive.open(member) as entry:
                    while entry.read(1024 * 1024):
                        if stop is not None:
                            stop.check()
            return {
                "bundle_id": bundle,
                "version": info["CFBundleVersion"],
                "name": info.get("CFBundleDisplayName", info.get("CFBundleName", bundle)),
                "expanded_bytes": sum(m.file_size for m in members),
            }
    except Cancelled:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError) as exc:
        raise ValueError("The IPA is damaged or incomplete.") from exc


def import_ipa(source, directory, *, cancelled=None, progress=None):
    class ImportStop:
        def check(self):
            if cancelled is not None and cancelled.is_set():
                raise Cancelled("IPA import cancelled.")

    stop = ImportStop()
    stop.check()
    source, directory = Path(source), Path(directory)
    if source.suffix.lower() != ".ipa" or not source.is_file():
        raise ValueError("Choose a completed .ipa download.")
    before = source.stat()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as incoming:
                copied = 0
                while chunk := incoming.read(4 * 1024 * 1024):
                    stop.check()
                    handle.write(chunk)
                    copied += len(chunk)
                    if progress is not None:
                        progress(copied, before.st_size)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("The file is still being received. Wait for the transfer to finish.")
        stop.check()
        validate_ipa(temporary, stop=stop)
        stop.check()
        # Unique names prevent overwriting an IPA used by another account/job.
        destination = directory / (source.stem[:180] + "-" + uuid.uuid4().hex[:12] + ".ipa")
        # Windows rename refuses an existing destination; replace would silently
        # overwrite it even if a supposedly unique import name collided.
        temporary.rename(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_assignment(
    source, directory, account, expected, signature, *, source_directory, cancelled=None
):
    """Copy and commit one GUI assignment under the shared settings/device lock.

    Cancellation before the commit removes only this import's unique copy.
    Once saved, the file belongs to the assignment and must survive dialog close.
    """
    from iphone_detector.config import load_config, save_config
    from iphone_detector.settings import get_workflow
    from iphone_detector.ipa_assignments import mark_pending
    from iphone_detector.run_lock import RunLock

    guard = RunLock()
    if not guard.acquire():
        raise RuntimeError(
            "An installation or settings change is active. Retry the import after it finishes."
        )
    destination = None
    committed = False
    try:
        config = load_config() or {}
        workflow = get_workflow(config)
        if (
            type(account) is not int
            or not 0 <= account < len(workflow)
            or any(workflow[account][key] != expected.get(key) for key in ("email", "ipa"))
        ):
            raise ValueError("The selected account changed. Close this import and select it again.")
        directory = Path(directory).resolve()
        saved_directory = Path(
            config.get("automation", {}).get("ipa_directory", Path.home() / "Downloads/ipas")
        ).resolve()
        if directory != saved_directory:
            raise ValueError("The IPA folder changed. Reload saved settings before importing.")
        source = Path(source)
        source_directory = Path(source_directory).resolve()
        if source.is_symlink() or source.resolve().parent != source_directory:
            raise ValueError("Choose a regular IPA file from the selected source folder.")
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns) != signature:
            raise ValueError("The source IPA changed. Select the completed file again.")
        destination = import_ipa(source, directory, cancelled=cancelled)
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("IPA import cancelled before saving the assignment.")
        workflow[account]["ipa"] = destination.stem
        automation = config.setdefault("automation", {})
        automation["accounts"] = [
            {"email": entry["email"], "ipa": entry["ipa"]} for entry in workflow
        ]
        automation["import_directory"] = str(source_directory)
        mark_pending(config, account, destination.stem)
        save_config(config)
        committed = True
        return destination
    finally:
        try:
            if destination is not None and not committed:
                destination.unlink(missing_ok=True)
        finally:
            guard.close()
