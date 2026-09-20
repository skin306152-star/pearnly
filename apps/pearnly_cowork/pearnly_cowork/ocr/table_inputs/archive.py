"""Bounded in-memory OOXML preflight. Never extract archive members to disk."""
import io
import posixpath
import re
import zipfile
from xml.parsers import expat

from .model import (MAX_ARCHIVE_MEMBERS, MAX_CELLS, MAX_COLUMNS,
                    MAX_EXPANDED_BYTES, MAX_ROWS, TableInputError)


def _coordinate(value):
    match = re.fullmatch(r"([A-Za-z]{1,5})([1-9][0-9]{0,7})", value or "")
    if not match:
        raise TableInputError("ocr.table_invalid_dimensions")
    column = 0
    for char in match[1].upper():
        column = column * 26 + ord(char) - 64
    row = int(match[2])
    if row > MAX_ROWS:
        raise TableInputError("ocr.table_row_limit")
    if column > MAX_COLUMNS:
        raise TableInputError("ocr.table_column_limit")
    return row, column


class _XmlGuard:
    def __init__(self):
        self.worksheet = False
        self.root_seen = False
        self.worksheet_targets = []
        self.unread_features = set()
        self.in_header_footer = False
        self.max_row = self.max_column = self.row = self.column = 0
        self.dimension = None
        self.cell_count = 0
        self.formula = self.cached_value = self.in_value = False
        self.cell_reference = None
        self.missing_formula_cache = []
        self.parser = expat.ParserCreate(namespace_separator="}")
        self.parser.StartElementHandler = self.start
        self.parser.EndElementHandler = self.end
        self.parser.CharacterDataHandler = self.characters
        self.parser.StartDoctypeDeclHandler = self.declaration
        self.parser.EntityDeclHandler = self.declaration
        self.parser.ExternalEntityRefHandler = self.declaration

    def declaration(self, *args):
        raise TableInputError("ocr.table_xml_declaration_not_allowed")

    def start(self, name, attributes):
        name = name.rsplit("}", 1)[-1]
        if not self.root_seen:
            self.root_seen, self.worksheet = True, name == "worksheet"
        if name == "Relationship" and attributes.get("Type", "").endswith("/worksheet"):
            if attributes.get("TargetMode") == "External":
                raise TableInputError("ocr.table_invalid_archive")
            self.worksheet_targets.append(attributes.get("Target", ""))
        if not self.worksheet:
            return
        if name in {"drawing", "legacyDrawing", "legacyDrawingHF", "picture", "oleObjects", "extLst"}:
            self.unread_features.add(name)
        elif name in {"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"}:
            self.in_header_footer = True
        if name == "dimension" and attributes.get("ref"):
            parts = attributes["ref"].split(":")
            if len(parts) > 2:
                raise TableInputError("ocr.table_invalid_dimensions")
            for part in parts:
                _coordinate(part)
            self.dimension = _coordinate(parts[-1])
        elif name == "row":
            value = attributes.get("r")
            if value is not None and not re.fullmatch(r"[1-9][0-9]{0,7}", value):
                raise TableInputError("ocr.table_invalid_dimensions")
            next_row = int(value) if value is not None else self.row + 1
            if next_row <= self.row:
                raise TableInputError("ocr.table_invalid_dimensions")
            self.row = next_row
            self.column = 0
            self.max_row = max(self.row, self.max_row)
            if self.max_row > MAX_ROWS:
                raise TableInputError("ocr.table_row_limit")
        elif name == "c":
            self.cell_count += 1
            if self.cell_count > MAX_CELLS:
                raise TableInputError("ocr.table_cell_limit")
            self.formula = self.cached_value = self.in_value = False
            self.cell_reference = attributes.get("r")
            previous_column = self.column
            self.column += 1
            if attributes.get("r"):
                row, self.column = _coordinate(attributes["r"])
                if row != self.row:
                    raise TableInputError("ocr.table_invalid_dimensions")
            if self.column <= previous_column:
                raise TableInputError("ocr.table_invalid_dimensions")
            self.max_column = max(self.column, self.max_column)
            if self.max_column > MAX_COLUMNS:
                raise TableInputError("ocr.table_column_limit")
        elif name == "f":
            self.formula = True
        elif name == "v":
            self.in_value = True

    def characters(self, text):
        if self.worksheet and self.in_value and text.strip():
            self.cached_value = True
        if self.worksheet and self.in_header_footer and text.strip():
            self.unread_features.add("headerFooter")

    def end(self, name):
        if not self.worksheet:
            return
        name = name.rsplit("}", 1)[-1]
        if name in {"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"}:
            self.in_header_footer = False
        if name == "v":
            self.in_value = False
        elif name == "c" and self.formula and not self.cached_value:
            self.missing_formula_cache.append({"cell": self.cell_reference, "source_row": self.row,
                                               "source_column": self.column})

    def cells(self):
        if self.dimension:
            rows, columns = self.dimension
            if self.max_row > rows or self.max_column > columns:
                raise TableInputError("ocr.table_invalid_dimensions")
        else:
            rows, columns = self.max_row, self.max_column
        return rows * columns


def check_archive(content):
    """Check declared AND actual bytes, CRC, XML declarations and sheet dimensions."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise TableInputError("ocr.table_archive_member_limit")
            if sum(member.file_size for member in members) > MAX_EXPANDED_BYTES:
                raise TableInputError("ocr.table_archive_size_limit")
            expanded = cells = actual_cells = 0
            worksheets, parsed, required_parts = {}, {}, set()
            for member in members:
                name = member.filename.lower()
                guard = _XmlGuard() if name.endswith((".xml", ".rels")) else None
                with archive.open(member) as stream:
                    while chunk := stream.read(64 * 1024):
                        expanded += len(chunk)
                        if expanded > MAX_EXPANDED_BYTES:
                            raise TableInputError("ocr.table_archive_size_limit")
                        if guard:
                            guard.parser.Parse(chunk, False)
                    if guard:
                        guard.parser.Parse(b"", True)
                if guard:
                    parsed[member.filename] = guard
                    for target in guard.worksheet_targets:
                        base = posixpath.dirname(posixpath.dirname(member.filename))
                        part = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                                 else posixpath.join(base, target))
                        required_parts.add(part)
            # Relationships may target a worksheet without the conventional
            # xl/worksheets path or even without an .xml extension.
            for part in required_parts - parsed.keys():
                guard = _XmlGuard()
                with archive.open(part) as stream:
                    while chunk := stream.read(64 * 1024):
                        guard.parser.Parse(chunk, False)
                    guard.parser.Parse(b"", True)
                parsed[part] = guard
            for part, guard in parsed.items():
                if part in required_parts and not guard.worksheet:
                    raise TableInputError("ocr.table_invalid_archive")
                if guard.worksheet:
                    cells += guard.cells()
                    actual_cells += guard.cell_count
                    if cells > MAX_CELLS or actual_cells > MAX_CELLS:
                        raise TableInputError("ocr.table_cell_limit")
                    worksheets[part] = {"missing_formula_cache": guard.missing_formula_cache,
                                        "unread_features": sorted(guard.unread_features)}
            return worksheets
    except TableInputError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, KeyError, expat.ExpatError) as error:
        raise TableInputError("ocr.table_invalid_archive") from error
