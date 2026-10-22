"""Core USB detection of an iPhone on Windows via WMI.

Design notes
------------
* Apple's USB vendor ID is ``05AC``. Any PnP device whose device ID contains
  ``VID_05AC`` is an Apple product, so we filter on that and then keep only
  "mobile" devices (iPhone / iPad / "Apple Mobile Device ...").
* Every iPhone exposes a unique USB serial number (its ECID). It shows up in
  the PnP instance ID, e.g.::

      USB\\VID_05AC&PID_12A8\\0000810100123ABC
      SWD\\WPDBUSENUM\\USB#VID_05AC&PID_12A8#0000810100123ABC&_0#{...}

  Pairing stores that serial, so only YOUR phone triggers events.
* The watcher polls the PnP tree every couple of seconds and diffs the
  presence state. Polling was deliberately chosen over raw WMI device-change
  events because it survives sleep/resume, is easy to debug, and the CPU cost
  of one WMI query every 2 s is negligible.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

LOG = logging.getLogger("iphone-detector")

APPLE_VID = "VID_05AC"
MOBILE_KEYWORDS = ("iphone", "ipad", "ipod", "apple mobile device")

_PID_RE = re.compile(r"PID_([0-9A-Fa-f]{4})")


@dataclass
class AppleDevice:
    """A single Apple PnP entity currently present on the system."""

    name: str
    pnp_id: str
    serial: Optional[str]
    pid: Optional[str]

    def summary(self) -> str:
        return "%s (PID %s, serial %s)" % (self.name or "?", self.pid or "?", self.serial or "?")


def extract_pid(pnp_id: str) -> Optional[str]:
    """Return the 4-hex-digit product ID from a PnP device ID, e.g. '12A8'."""
    match = _PID_RE.search(pnp_id or "")
    return match.group(1).upper() if match else None


def extract_serial(pnp_id: str) -> Optional[str]:
    """Extract the USB serial number from a Windows PnP device ID.

    Handles both common layouts:

    * ``USB\\VID_05AC&PID_12A8\\<serial>``              -> ``<serial>``
    * ``SWD\\WPDBUSENUM\\USB#VID_...#<serial>&_0#{..}``  -> ``<serial>``

    Instance IDs derived from hub/port numbers (they contain ``&`` in the
    tail) are NOT real device serials, so ``None`` is returned for them.
    """
    if not pnp_id:
        return None
    tail = pnp_id.split("\\")[-1]
    if "#" in tail:  # WPDBUSENUM style: USB#VID_..&PID_..#<serial>(&_0)#{guid}
        parts = [p for p in tail.split("#") if p]
        if len(parts) >= 3:
            return parts[2].split("&")[0] or None
        return None
    if "&" in tail:  # port-derived instance ID, not a serial
        return None
    return tail or None


def _get_wmi():
    """Create a WMI connection, with a friendly error if deps are missing."""
    try:
        import wmi  # imported lazily so the pure helpers work on any OS
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'wmi'. Install the requirements first:\n"
            "    pip install -r requirements.txt"
        ) from exc
    return wmi.WMI()


def list_apple_devices() -> List[AppleDevice]:
    """Enumerate PnP entities whose device ID contains Apple's vendor ID."""
    conn = _get_wmi()
    query = (
        "SELECT Name, Description, PNPDeviceID FROM Win32_PnPEntity "
        "WHERE PNPDeviceID LIKE '%%%s%%'" % APPLE_VID
    )
    devices: List[AppleDevice] = []
    for row in conn.query(query):
        pnp_id = row.PNPDeviceID or ""
        name = (getattr(row, "Name", None) or getattr(row, "Description", None) or "").strip()
        devices.append(
            AppleDevice(
                name=name,
                pnp_id=pnp_id,
                serial=extract_serial(pnp_id),
                pid=extract_pid(pnp_id),
            )
        )
    return devices


def is_mobile_device(dev: AppleDevice) -> bool:
    """True for iPhones/iPads/Apple mobile entries, False for keyboards etc."""
    name = dev.name.lower()
    return any(keyword in name for keyword in MOBILE_KEYWORDS)


class IPhoneWatcher:
    """Polls the PnP device tree and reports connect/disconnect transitions.

    Events are delivered through an :class:`~iphone_detector.events.EventDispatcher`
    so that future features (e.g. the OCR automation) can subscribe without
    touching the detection code.
    """

    def __init__(
        self,
        dispatcher,
        target_serial: Optional[str] = None,
        match_any: bool = False,
        poll_interval: float = 2.0,
        emit_startup: bool = False,
    ) -> None:
        if not match_any and not target_serial:
            raise ValueError("Either target_serial or match_any=True is required")
        self.dispatcher = dispatcher
        self.target_serial = (target_serial or "").lower() or None
        self.match_any = match_any
        self.poll_interval = poll_interval
        self.emit_startup = emit_startup
        self._last_seen: Optional[AppleDevice] = None

    # ------------------------------------------------------------------ #
    # State queries                                                      #
    # ------------------------------------------------------------------ #

    def is_connected(self) -> Optional[AppleDevice]:
        """Return the matching device if it is currently attached, else None."""
        for dev in list_apple_devices():
            if not is_mobile_device(dev):
                continue
            if self.match_any:
                return dev
            if dev.serial and dev.serial.lower() == self.target_serial:
                return dev
            if self.target_serial and self.target_serial in dev.pnp_id.lower():
                return dev  # catches child/interface entries embedding the ECID
        return None

    # ------------------------------------------------------------------ #
    # Main loop                                                          #
    # ------------------------------------------------------------------ #

    def run_forever(self, stop_event=None) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        mode = "any Apple mobile device" if self.match_any else "serial %s" % self.target_serial
        LOG.info(
            "Watching for: %s (poll every %.1fs) - press Ctrl+C to stop.", mode, self.poll_interval
        )

        try:
            current = self.is_connected()
        except Exception:
            LOG.exception("Initial device enumeration failed - starting as 'not connected'")
            current = None

        if current is not None:
            self._last_seen = current
            LOG.info("[startup] device already connected: %s", current.summary())
            if self.emit_startup:
                # Treat "already plugged in at startup" as a normal connect so
                # handlers (app launch, later OCR stage) fire in this case too.
                if stop_event is None or not stop_event.is_set():
                    self.dispatcher.emit("connect", self._payload(current))
        was_connected = current is not None

        while True:
            if stop_event is not None:
                if stop_event.wait(self.poll_interval):
                    return
            else:
                time.sleep(self.poll_interval)
            try:
                current = self.is_connected()
            except Exception:
                # Keep the previous state: one failed WMI query must not be
                # reported as a disconnect. We simply retry next cycle.
                LOG.exception("Device enumeration failed - retrying next cycle")
                continue

            if current is not None:
                self._last_seen = current

            if current is not None and not was_connected:
                self.dispatcher.emit("connect", self._payload(current))
            elif current is None and was_connected:
                payload = (
                    self._payload(self._last_seen)
                    if self._last_seen
                    else {"name": "?", "serial": "?"}
                )
                self.dispatcher.emit("disconnect", payload)

            was_connected = current is not None

    @staticmethod
    def _payload(dev: Optional[AppleDevice]) -> dict:
        return {
            "name": dev.name if dev else "?",
            "serial": dev.serial if dev else "?",
            "pid": dev.pid if dev else None,
        }
