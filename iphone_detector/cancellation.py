"""Cancellation shared by the native worker and transport implementations."""


class Cancelled(RuntimeError):
    """A user requested that the current operation stop safely."""
