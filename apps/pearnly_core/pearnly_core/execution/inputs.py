"""Bounded private input retention while durable OCR waits or retries."""
import hashlib
from pathlib import Path
import shutil
import tempfile

import frappe

from .errors import Deferred


def _directory(name):
    # Names are server-created durable work IDs, never client filesystem paths.
    if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
        raise ValueError("Invalid OCR work identity")
    return Path(frappe.get_site_path("private", "files", "ocr-work", name))


def read(reference, loader, max_bytes):
    name = frappe.flags.get("pearnly_ocr_work")
    if not name:
        return loader()
    directory = _directory(name)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / hashlib.sha256(str(reference).encode()).hexdigest()
    if path.exists():
        if path.stat().st_size > max_bytes:
            raise ValueError("OCR input exceeds configured limit")
        return path.read_bytes()
    if shutil.disk_usage(directory).free < max_bytes + 512 * 1024 * 1024:
        raise Deferred("disk_pressure", 30)
    content = loader()
    if not content:
        return content
    if len(content) > max_bytes:
        raise ValueError("OCR input exceeds configured limit")
    # A killed worker must not leave a partially downloaded input for the next one.
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return content


def remove(name):
    directory = _directory(name)
    try:
        if directory.exists():
            shutil.rmtree(directory)
    except OSError:
        # Storage cleanup cannot change a completed business result.
        frappe.logger("pearnly.capacity").warning("OCR input cleanup deferred")
