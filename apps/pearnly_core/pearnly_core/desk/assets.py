"""Content-versioned native hook assets, without changing Frappe's asset loader."""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path


def versioned_asset(app, relative):
    package = Path(find_spec(app).origin).parent
    version = sha256((package / "public" / relative).read_bytes()).hexdigest()[:16]
    return f"/assets/{app}/{relative}?v={version}"
