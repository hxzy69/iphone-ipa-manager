"""Durable operational reports. Retrying delivery never retries device work."""

import hashlib
import json
import time
import math

from iphone_detector.storage import write_json


def enqueue(root, key, message):
    try:
        saved = json.loads((root / "discord.json").read_text(encoding="utf-8"))
        from iphone_detector.discord_bot import validate_settings

        audience = validate_settings(saved)
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        return False
    digest = hashlib.sha256(key.encode()).hexdigest()
    path = root / "discord-outbox" / (digest + ".json")
    # Retain the original filename for existing reports. If its event key was
    # already used for another audience, give this audience its own receipt.
    # Never move or redeliver the old audience's private report.
    audience_key = json.dumps(audience, sort_keys=True, separators=(",", ":"))
    alternate = (
        root
        / "discord-outbox"
        / (hashlib.sha256((key + "\0" + audience_key).encode()).hexdigest() + ".json")
    )
    try:
        for candidate in (path, alternate):
            written = write_json(
                candidate,
                {
                    "audience": audience,
                    "message": message[:1800],
                    "created": time.time(),
                    "key": key,
                },
                exclusive=True,
            )
            if written is not False:
                return True
            existing = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(existing, dict) or existing.get("key") != key:
                return False
            if existing.get("audience") == audience:
                return True
    except OSError, ValueError:
        import logging

        logging.getLogger(__name__).exception(
            "Could not persist operational report; local job receipt remains available."
        )
        return False
    return False


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
                or not math.isfinite(value["created"])
                or not 0 <= value["created"] <= time.time() + 5
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
