"""Inert compatibility for old periodic jobs; manual reads replaced them."""


def collectors():
    return []


def enqueue(endpoint, collector):
    return None


def sweep():
    return {"queued": 0, "skipped": 0, "retired": True}
