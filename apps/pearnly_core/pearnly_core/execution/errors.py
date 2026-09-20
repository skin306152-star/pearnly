"""Resource outcomes shared by transports and job runners (no framework dependencies)."""

class Deferred(Exception):
    def __init__(self, reason="capacity", delay=5):
        super().__init__(reason)
        self.reason = reason
        self.delay = max(1, min(300, float(delay)))


class Uncertain(Exception):
    """An external request may have completed; never replay automatically."""


class QueueFull(Exception):
    """Admission rejected before accepting any paid work."""
