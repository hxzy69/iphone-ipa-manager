"""Durable operational reports. Retrying delivery never retries device work."""

import hashlib
import json
import time

from iphone_detector.storage import write_json


def enqueue(root, key, message):
    try:
        saved = json.loads((root / "discord.json").read_text(encoding="utf-8"))
        from iphone_detector.discord_bot import validate_settings

        audience = validate_settings(saved)
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        return False
    path = root / "discord-outbox" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
    try:
        if not path.exists():
            write_json(
                path,
                {
                    "audience": audience,
                    "message": message[:1800],
                    "created": time.time(),
                    "key": key,
                },
            )
    except OSError:
        import logging

        logging.getLogger(__name__).exception(
            "Could not persist operational report; local job receipt remains available."
        )
        return False
    return True


def pending(root, settings):
    found = []
    for path in (root / "discord-outbox").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("delivered_at")
                or value.get("audience") != settings
                or not isinstance(value.get("message"), str)
                or not 0 < len(value["message"]) <= 1800
                or type(value.get("created")) not in (int, float)
            ):
                continue
            found.append((path, value))
        except OSError, ValueError:
            continue
    return sorted(found, key=lambda entry: entry[1]["created"])[:10]


async def deliver(root, settings, send):
    """Mark delivered only after Discord acknowledges; leave failures for next poll."""
    import asyncio

    items = await asyncio.to_thread(pending, root, settings)
    for path, value in items:
        await send(value["message"])
        await asyncio.to_thread(write_json, path, dict(value, delivered_at=time.time()))
