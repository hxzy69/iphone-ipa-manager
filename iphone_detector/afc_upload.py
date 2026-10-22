"""Bounded parallel upload of disjoint file ranges, with phone-side integrity check."""

import asyncio
import hashlib
from pathlib import Path

CHUNK = 4 * 1024 * 1024


def local_digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha1").digest()


async def upload(rsd, control, path, remote, progress, streams=2):
    from pymobiledevice3.services.afc import AfcService, AfcOpcode, AfcError

    if streams != 2:
        raise ValueError("Only the verified two-stream uploader is supported")
    size = path.stat().st_size
    handle = await control.fopen(remote, "w")
    await control.fclose(handle)

    async def writer(index):
        async with AfcService(rsd) as afc:
            handle = await afc.fopen(remote, "r+")
            try:
                start, end = size * index // streams, size * (index + 1) // streams
                await afc.fseek(handle, start)
                with path.open("rb") as source:
                    source.seek(start)
                    remaining = end - start
                    while remaining:
                        chunk = source.read(min(CHUNK, remaining))
                        if not chunk:
                            raise RuntimeError("Signed archive changed during upload")
                        await afc.fwrite(handle, chunk)
                        remaining -= len(chunk)
                        progress(len(chunk))
            finally:
                await afc.fclose(handle)

    # Cancellation/failure joins both writers before the caller removes the owned file.
    async with asyncio.TaskGroup() as group:
        for index in range(streams):
            group.create_task(writer(index))
    status, actual = await control._send_and_wait(AfcOpcode(0x1D), remote.encode() + b"\0")
    expected = await asyncio.to_thread(local_digest, path)
    info = await control.stat(remote)
    if status != AfcError.SUCCESS or actual != expected or int(info["st_size"]) != size:
        raise RuntimeError(
            "Uploaded IPA failed phone-side integrity verification; installation cancelled"
        )
