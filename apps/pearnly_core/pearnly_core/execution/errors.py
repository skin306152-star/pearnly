"""Resource outcomes shared by transports and job runners (no framework dependencies)."""

class Deferred(Exception):
    def __init__(self, reason="capacity", delay=5):
        super().__init__(reason)
        self.reason = reason
        self.delay = max(1, min(300, float(delay)))


class Uncertain(Exception):
    """An external request may have completed; never replay automatically.

    ``lease_seconds`` is how long the capacity permit is still held after the caller itself gave up on the
    transport: ``0`` when the connection is already closed (reset by the provider, never connected, our own
    stop), a short hold after a read timeout (the provider may still be working), ``None`` (the default) keeps
    the conservative lease for an interruption we cannot see the end of (job timeout, killed process).
    """

    def __init__(self, message="", lease_seconds=None):
        super().__init__(message)
        self.lease_seconds = lease_seconds


class QueueFull(Exception):
    """Admission rejected before accepting any paid work."""
