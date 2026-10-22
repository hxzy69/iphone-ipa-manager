"""Explicit profile-only renewal; full IPA installation remains the default."""

from datetime import datetime, timezone
import hashlib
import io
import plistlib
import time
import zipfile


class FullInstallRequired(RuntimeError):
    pass


def profile_expiry(profile):
    value = profile.get("ExpirationDate")
    if not isinstance(value, datetime):
        raise FullInstallRequired("Profile expiration is unavailable")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class Inventory:
    """Job-scoped snapshots; refreshed from verified readback after each account."""

    def __init__(self):
        self.udid = None
        self.apps = None
        self.profiles = None

    def invalidate(self):
        self.apps = self.profiles = None


def expiry_evidence(prepared):
    """A readback proves storage, not that iOS launches with a renewed profile."""

    def utc(value):
        return (
            value.replace(tzinfo=timezone.utc).isoformat()
            if value.tzinfo is None
            else value.astimezone(timezone.utc).isoformat()
        )

    return {
        "expiry_test_not_before": utc(max(before for _, _, before in prepared)),
        "expiry_test_before": utc(min(profile["ExpirationDate"] for _, profile, _ in prepared)),
        "expiration_changes": [
            {
                "app": profile["Entitlements"]["application-identifier"],
                "before": utc(before),
                "after": utc(profile["ExpirationDate"]),
            }
            for _, profile, before in prepared
        ],
        "expiry_test_requires": "Close and reopen each app after all old profiles expire and before renewed profiles expire. Any intervening full reinstall invalidates this test.",
    }


def signing_identity(profile):
    return (
        profile.get("Entitlements"),
        profile.get("TeamIdentifier"),
        sorted(hashlib.sha256(c).hexdigest() for c in profile.get("DeveloperCertificates", [])),
    )


def prepare(path, installed, serial, apps):
    from pymobiledevice3.services.misagent import ProvisioningProfile

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with zipfile.ZipFile(path) as archive:
        main = [
            n
            for n in archive.namelist()
            if n.startswith("Payload/") and n.count("/") == 2 and n.endswith(".app/Info.plist")
        ]
        if len(main) != 1:
            raise FullInstallRequired("Main app is ambiguous")
        info = plistlib.loads(archive.read(main[0]))
        bundle = info.get("CFBundleIdentifier")
        current = apps.get(bundle)
        if not current or any(
            not info.get(key) or current.get(key) != info[key]
            for key in ("CFBundleVersion", "CFBundleShortVersionString")
        ):
            raise FullInstallRequired("App is missing or its version changed")
        names = [
            n
            for n in archive.namelist()
            if n.startswith(main[0].rsplit("/", 1)[0] + "/")
            and n.endswith("/embedded.mobileprovision")
        ]
        if not names:
            raise FullInstallRequired("No signed profiles")
        result, seen = [], set()
        for name in names:
            raw = archive.read(name)
            p = ProvisioningProfile(raw).plist
            app = p.get("Entitlements", {}).get("application-identifier")
            if not app or app in seen or serial not in p.get("ProvisionedDevices", []):
                raise FullInstallRequired("Profile identity or device mismatch")
            seen.add(app)
            profile_bundle = app.split(".", 1)[-1]
            if profile_bundle != bundle and not profile_bundle.startswith(bundle + "."):
                raise FullInstallRequired("Profile belongs to another app")
            if not p.get("DeveloperCertificates") or profile_expiry(p) <= now:
                raise FullInstallRequired("Profile expired or unsigned")
            old = [o for o in installed if signing_identity(o) == signing_identity(p)]
            if not old:
                raise FullInstallRequired("Signing certificate or entitlements changed")
            before = max(profile_expiry(o) for o in old)
            if before <= now:
                raise FullInstallRequired("Installed profile expired; full installation required")
            if profile_expiry(p) <= before:
                raise FullInstallRequired("Profile expiration was not extended")
            result.append((raw, p, before))
        if not any(
            p["Entitlements"]["application-identifier"].split(".", 1)[1] == bundle
            for _, p, _ in result
        ):
            raise FullInstallRequired("Main app profile missing")
        return result


async def refresh(rsd, path, inventory=None):
    """False requests one full install after preflight or a confirmed profile rejection.

    Transport errors, cancellation and uncertain writes still propagate; they never
    trigger a second device operation over an unreliable connection.
    """
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    from pymobiledevice3.services.misagent import MisagentService
    from pymobiledevice3.exceptions import PyMobileDevice3Exception
    from iphone_detector.remote_tunnel import atomic, transfer_progress

    started = time.monotonic()
    inventory = inventory or Inventory()

    def fallback(reason):
        inventory.invalidate()
        atomic(
            path.parent / ("profile-fallback-" + path.stem.split("-")[-1] + ".json"),
            {
                "reason": reason,
                "full_install_required": True,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return False

    if inventory.udid != rsd.udid:
        inventory.invalidate()
        inventory.udid = rsd.udid
    if inventory.apps is None:
        async with InstallationProxyService(rsd) as installer:
            inventory.apps = await installer.lookup(
                {
                    "ApplicationType": "User",
                    "ReturnAttributes": [
                        "CFBundleIdentifier",
                        "CFBundleVersion",
                        "CFBundleShortVersionString",
                    ],
                }
            )
    async with MisagentService(rsd) as service:
        if inventory.profiles is None:
            inventory.profiles = [p.plist for p in await service.copy_all()]
        try:
            prepared = prepare(path, inventory.profiles, rsd.udid, inventory.apps)
        except FullInstallRequired as exc:
            return fallback(str(exc))
        preflight_seconds = time.monotonic() - started
        transfer_started = time.monotonic()
        metrics = {
            "account": int(path.stem.split("-")[-1]),
            "archive_bytes": sum(len(raw) for raw, _, _ in prepared),
            "uploaded_bytes": 0,
        }
        transfer_progress(path, metrics, "refresh")
        # Invalidate before writing so an exception cannot leave a reusable stale snapshot.
        inventory.profiles = None
        for raw, _, _ in prepared:
            try:
                await service.install(io.BytesIO(raw))
            except PyMobileDevice3Exception as exc:
                # This exact base error is raised by misagent only after a device
                # response rejects the profile. Connection subclasses remain fatal.
                if type(exc) is PyMobileDevice3Exception and str(exc).startswith("invalid status:"):
                    return fallback("Device rejected profile renewal")
                raise
            metrics["uploaded_bytes"] += len(raw)
            transfer_progress(path, metrics, "refresh")
        actual_profiles = await service.copy_all()
        actual = {p.plist["UUID"]: p.buf for p in actual_profiles}
        for raw, p, _ in prepared:
            if actual.get(p["UUID"]) != raw:
                return fallback("Profile readback did not verify the renewal")
        inventory.profiles = [p.plist for p in actual_profiles]
        report = {
            "account": int(path.stem.split("-")[-1]),
            "profiles": len(prepared),
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "bytes": metrics["uploaded_bytes"],
            "seconds": round(time.monotonic() - transfer_started, 3),
            "preflight_seconds": round(preflight_seconds, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "profile_readback_verified": True,
            "expiry_behavior_verified": False,
            "expires": [str(p["ExpirationDate"]) for _, p, _ in prepared],
            **expiry_evidence(prepared),
        }
        atomic(path.parent / ("profile-refresh-" + str(report["account"]) + ".json"), report)
        transfer_progress(path, metrics, "completed")
        return True
