"""Validate and import completed IPA files into the managed directory."""

import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path


def validate_ipa(path):
    """Structural check shared by every Discord/PC assignment path."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            for name in names:
                normalized = name.replace("\\", "/")
                if (
                    normalized.startswith("/")
                    or ".." in normalized.split("/")
                    or ":" in normalized.split("/")[0]
                ):
                    raise ValueError("This IPA contains an unsafe archive path.")
            main = [
                name
                for name in names
                if name.startswith("Payload/")
                and name.endswith(".app/Info.plist")
                and name.count("/") == 2
            ]
            if len(main) != 1:
                raise ValueError("The IPA must contain exactly one main iOS app.")
            if archive.testzip() is not None:
                raise ValueError("The IPA is damaged or incomplete.")
    except zipfile.BadZipFile as exc:
        raise ValueError("The IPA is damaged or incomplete.") from exc


def import_ipa(source, directory):
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
                shutil.copyfileobj(incoming, handle)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("The file is still being received. Wait for the transfer to finish.")
        validate_ipa(temporary)
        # Unique names prevent overwriting an IPA used by another account/job.
        destination = directory / (source.stem[:180] + "-" + uuid.uuid4().hex[:12] + ".ipa")
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
