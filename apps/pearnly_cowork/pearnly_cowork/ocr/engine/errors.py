"""Only completed-content failures belong to this hierarchy; IO errors pass through."""


class EngineError(ValueError):
    def __init__(self, code: str, *, stage: str = "", page_number: int = 0, detail: str = ""):
        self.code = code
        self.stage = stage
        self.page_number = page_number
        self.detail = detail
        super().__init__(code)


class ContentError(EngineError):
    """A provider finished, but its output could not satisfy the content contract."""


class DirectFallback(ContentError):
    """Only this error switches the whole file from direct read to Vision."""
