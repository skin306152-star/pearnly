"""对话结果面板的 Word（.docx）预览：把文档读成纯数据，页面逐段按文字渲染（同 Excel 由服务端读成表格的做法）。

- 读正文：段落（标题级别、对齐、列表编号）、文字的粗体 / 斜体 / 下划线 / 删除线 / 上下标、表格（含合并单元格、表中表）、
  文档里嵌着的图片（只认 PNG / JPEG / GIF / WebP 的文件头，转成 data: 地址）。页眉页脚、批注、脚注、文本框、图表不读。
- 结果里没有 HTML，也没有任何地址：宏不读；超链接只留文字，不带目标；外链图片（TargetMode=External）、域代码
  （`instrText`，例如 HYPERLINK "…"）、隐藏文字、修订里删掉的字都不带出来。页面只把文字当文字显示（2026-10-13 教程第 3 条）。
- 防炸：压缩包条目数、每个部件解压后的实际大小、图片总量、段落数、文字总量、嵌套深度都有上限；部件里出现 DOCTYPE / ENTITY
  一律不读（Office 文档本来就不用，实体展开攻击只能借它）。读不出来报 `Unreadable`，调用方退回下载。
- 纯标准库，不依赖 Frappe、网络与磁盘。
"""
import base64
import io
import posixpath
import re
import zipfile
import zlib
from xml.etree import ElementTree

MAX_ENTRIES = 2000
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES_BYTES = 4 * 1024 * 1024
MAX_BLOCKS = 4000          # 段落与表格（含表格里的）合计
MAX_CHARS = 500_000
MAX_TABLE_ROWS = 1000
MAX_SPAN = 64
MAX_DEPTH = 24             # 内容控件、表中表等容器的嵌套
EMU_PER_PX = 9525

IMAGE_REL = "/image"
OFFICE_DOCUMENT_REL = "/officeDocument"
HEADING = re.compile(r"^heading\s*([1-9])$")
LEVEL_REF = re.compile(r"%([1-9])")
BULLETS = ("•", "◦", "▪")
THAI_DIGITS = str.maketrans("0123456789", "".join(chr(0x0E50 + d) for d in range(10)))  # 泰文数字 U+0E50–U+0E59
ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
         (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
# 段落 / 文字里只是包了一层、内容照常显示的容器
BLOCK_WRAPPERS = frozenset({"customXml", "ins", "moveTo"})
INLINE_WRAPPERS = frozenset({"ins", "moveTo", "smartTag", "customXml", "fldSimple", "bdo", "dir", "sdtContent"})
TOGGLES = {"b": "b", "bCs": "bCs", "i": "i", "iCs": "iCs", "strike": "strike", "dstrike": "strike",
           "vanish": "hidden", "specVanish": "hidden", "webHidden": "hidden"}


class Unreadable(Exception):
    """不是能预览的 Word 文档（坏包、加密、超限、带 DTD……）。"""


def read(content: bytes) -> list[dict]:
    """.docx 内容 → 预览块列表。

    块：`{"type": "paragraph", "runs": [...], "style"?: h1–h6 / title / subtitle, "align"?: center / right / justify,
    "list"?: {"level": 0–8, "marker": "1." / "•"}}`；`{"type": "table", "rows": [{"cells": [{"blocks": [...],
    "colspan"?: n, "rowspan"?: n}], "header"?: true}]}`。
    文字段：`{"text": "...", "bold"? / "italic"? / "underline"? / "strike"? / "sup"? / "sub"? / "link"?: true}`；
    图片：`{"image": "data:image/png;base64,…", "width"?: px, "height"?: px}`。"""
    try:
        return _Reader(_Package(content)).document()
    except Unreadable:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ElementTree.ParseError, zlib.error, EOFError, OSError,
            KeyError, ValueError, NotImplementedError, RuntimeError) as error:  # RuntimeError 含加密包与递归过深
        raise Unreadable(str(error)) from error


def image_type(data: bytes) -> str | None:
    """按文件头认图片类型；其余（SVG、EMF / WMF、TIFF……）不认。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _attr(el, name: str):
    if el is None:
        return None
    for key, value in el.attrib.items():
        if key == name or key.endswith("}" + name):
            return value
    return None


def _child(el, name: str):
    if el is None:
        return None
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _children(el, name: str) -> list:
    return [] if el is None else [child for child in el if _local(child.tag) == name]


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _on(el) -> bool:
    """开关属性：`<w:b/>`、`<w:b w:val="1"/>` 是开，`w:val="0" / false / off / none` 是关。"""
    value = _attr(el, "val")
    return value is None or value.strip().lower() not in ("0", "false", "off", "none")


def _alternate(el):
    """mc:AlternateContent 只取一支：先 Choice，没有才 Fallback（两支是同一内容的两种写法）。"""
    choice = _child(el, "Choice")
    return choice if choice is not None else _child(el, "Fallback")


class _Package:
    def __init__(self, content: bytes):
        self.zip = zipfile.ZipFile(io.BytesIO(content))
        infos = self.zip.infolist()
        if len(infos) > MAX_ENTRIES:
            raise Unreadable("too many parts")
        self.infos = {info.filename: info for info in infos}

    def read(self, name: str, limit: int | None = None) -> bytes | None:
        limit = MAX_PART_BYTES if limit is None else limit
        info = self.infos.get(name)
        if info is None or info.is_dir():
            return None
        if info.file_size > limit:
            raise Unreadable(f"{name} is too large")
        with self.zip.open(info) as handle:
            data = handle.read(limit + 1)  # 声明的大小可能是假的：按实际解压出来的算
        if len(data) > limit:
            raise Unreadable(f"{name} is too large")
        return data

    def xml(self, name: str):
        data = self.read(name)
        if data is None:
            return None
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise Unreadable(f"{name} declares a DTD")
        return ElementTree.fromstring(data)

    def relations(self, part: str) -> dict[str, tuple[str, str]]:
        """部件的关系表 {编号: (类型, 包内路径)}；外部目标（网址、本机路径）不收。"""
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", base + ".rels"))
        out = {}
        for rel in [] if root is None else _children(root, "Relationship"):
            target = _attr(rel, "Target") or ""
            if (_attr(rel, "TargetMode") or "").lower() == "external" or not target or "://" in target:
                continue
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
            out[_attr(rel, "Id") or ""] = (_attr(rel, "Type") or "", path)
        return out


class _Styles:
    def __init__(self, root):
        self.by_id: dict[str, dict] = {}
        self.default_paragraph = None
        for style in _children(root, "style"):
            sid = _attr(style, "styleId") or ""
            kind = _attr(style, "type") or "paragraph"
            self.by_id[sid] = {"kind": kind, "name": (_attr(_child(style, "name"), "val") or "").strip().lower(),
                               "based": _attr(_child(style, "basedOn"), "val"),
                               "ppr": _child(style, "pPr"), "rpr": _child(style, "rPr")}
            if kind == "paragraph" and (_attr(style, "default") or "").lower() in ("1", "true", "on"):
                self.default_paragraph = sid

    def chain(self, sid) -> list[dict]:
        """样式及其 basedOn 祖先（近 → 远），防环。"""
        out, seen = [], set()
        while sid and sid not in seen and sid in self.by_id and len(out) < 20:
            seen.add(sid)
            out.append(self.by_id[sid])
            sid = out[-1]["based"]
        return out


class _Numbering:
    """列表编号：按 numbering.xml 的格式与层级现算（同一抽象列表连续计数，下级随上级重置）。"""

    def __init__(self, root):
        self.abstract: dict[str, dict[int, dict]] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self.counters: dict[str, list] = {}
        self.started: set[str] = set()
        for abstract in _children(root, "abstractNum"):
            levels = {}
            for lvl in _children(abstract, "lvl"):
                levels[_int(_attr(lvl, "ilvl"))] = {
                    "format": _attr(_child(lvl, "numFmt"), "val") or "decimal",
                    "text": _attr(_child(lvl, "lvlText"), "val"),
                    "start": _int(_attr(_child(lvl, "start"), "val"), 1)}
            self.abstract[_attr(abstract, "abstractNumId") or ""] = levels
        for num in _children(root, "num"):
            overrides = {}
            for override in _children(num, "lvlOverride"):
                start = _child(override, "startOverride")
                if start is not None:
                    overrides[_int(_attr(override, "ilvl"))] = _int(_attr(start, "val"), 1)
            self.nums[_attr(num, "numId") or ""] = (_attr(_child(num, "abstractNumId"), "val") or "", overrides)

    def marker(self, num_id: str, level: int) -> str:
        abstract_id, overrides = self.nums.get(num_id, ("", {}))
        levels = self.abstract.get(abstract_id, {})
        spec = levels.get(level, {"format": "bullet", "text": None, "start": 1})
        counts = self.counters.setdefault(abstract_id, [None] * 9)
        if num_id not in self.started:  # 这个编号实例第一次出现：有 startOverride 的层级从指定数重新数
            self.started.add(num_id)
            for lvl, start in overrides.items():
                if 0 <= lvl < 9:
                    counts[lvl] = start - 1
        counts[level] = (counts[level] + 1) if counts[level] is not None else spec["start"]
        for deeper in range(level + 1, 9):
            counts[deeper] = None
        if spec["format"] == "bullet":
            return BULLETS[level % len(BULLETS)]
        if spec["format"] == "none":
            return ""
        text = spec["text"] if spec["text"] is not None else f"%{level + 1}."

        def number(match):
            lvl = int(match.group(1)) - 1
            fmt = levels.get(lvl, spec)
            value = counts[lvl] if counts[lvl] is not None else fmt["start"]
            return _number(value, fmt["format"])

        return LEVEL_REF.sub(number, text)


def _number(value: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter") and value > 0:
        letter = chr(ord("a") + (value - 1) % 26) * ((value - 1) // 26 + 1)
        return letter.upper() if fmt == "upperLetter" else letter
    if fmt in ("lowerRoman", "upperRoman") and 0 < value < 4000:
        out, rest = "", value
        for amount, digits in ROMAN:
            while rest >= amount:
                out, rest = out + digits, rest - amount
        return out.upper() if fmt == "upperRoman" else out
    if fmt == "decimalZero":
        return f"{value:02d}"
    if fmt == "thaiNumbers":
        return str(value).translate(THAI_DIGITS)
    return str(value)


class _Reader:
    def __init__(self, package: _Package):
        self.package = package
        main = next((path for kind, path in package.relations("").values() if kind.endswith(OFFICE_DOCUMENT_REL)),
                    "word/document.xml")
        self.root = package.xml(main)
        if self.root is None or _local(self.root.tag) != "document":
            raise Unreadable("no Word document part")
        self.rels = package.relations(main)
        self.styles = _Styles(self._related("/styles"))
        self.numbering = _Numbering(self._related("/numbering"))
        self.blocks_left = MAX_BLOCKS
        self.chars_left = MAX_CHARS
        self.images_left = MAX_IMAGES_BYTES

    def _related(self, suffix: str):
        for kind, path in self.rels.values():
            if kind.endswith(suffix):
                return self.package.xml(path)
        return None

    def full(self) -> bool:
        return self.blocks_left <= 0 or self.chars_left <= 0

    def document(self) -> list[dict]:
        return self.blocks(_child(self.root, "body"), 0)

    # ── 块：段落、表格 ────────────────────────────────────────────────────────────────────────────
    def blocks(self, parent, depth: int) -> list[dict]:
        out: list[dict] = []
        if parent is None or depth > MAX_DEPTH:
            return out
        for el in parent:
            if self.full():
                break
            name = _local(el.tag)
            if name == "p":
                self.blocks_left -= 1
                out.append(self.paragraph(el, depth))
            elif name == "tbl":
                self.blocks_left -= 1
                out.append(self.table(el, depth))
            elif name == "sdt":
                out.extend(self.blocks(_child(el, "sdtContent"), depth + 1))
            elif name in BLOCK_WRAPPERS:
                out.extend(self.blocks(el, depth + 1))
            elif name == "AlternateContent":
                out.extend(self.blocks(_alternate(el), depth + 1))
        return out

    def paragraph(self, p, depth: int) -> dict:
        ppr = _child(p, "pPr")
        chain = self.styles.chain(_attr(_child(ppr, "pStyle"), "val") or self.styles.default_paragraph)
        block: dict = {"type": "paragraph", "runs": []}
        style = self._heading(ppr, chain)
        if style:
            block["style"] = style
        align = {"center": "center", "right": "right", "end": "right", "both": "justify", "distribute": "justify"}.get(
            next((_attr(_child(props, "jc"), "val") for props in [ppr] + [s["ppr"] for s in chain]
                  if _child(props, "jc") is not None), "") or "")
        if align:
            block["align"] = align
        listed = self._list(ppr, chain)
        if listed:
            block["list"] = listed
        base = [s["rpr"] for s in reversed(chain)]
        self.inline(p, block["runs"], base, False, depth + 1)
        return block

    def _heading(self, ppr, chain) -> str | None:
        direct = _child(ppr, "outlineLvl")
        if direct is not None:
            level = _int(_attr(direct, "val"), 9)
            return f"h{level + 1}" if level < 6 else None
        for style in chain:
            named = HEADING.match(style["name"])
            if named:
                return f"h{min(int(named.group(1)), 6)}"
            if style["name"] in ("title", "subtitle"):
                return style["name"]
            outline = _child(style["ppr"], "outlineLvl")
            if outline is not None:
                level = _int(_attr(outline, "val"), 9)
                return f"h{level + 1}" if level < 6 else None
        return None

    def _list(self, ppr, chain) -> dict | None:
        num_id = level = None
        for props in [ppr] + [s["ppr"] for s in chain]:
            numpr = _child(props, "numPr")
            if numpr is None:
                continue
            if num_id is None and _child(numpr, "numId") is not None:
                num_id = _attr(_child(numpr, "numId"), "val")
            if level is None and _child(numpr, "ilvl") is not None:
                level = _int(_attr(_child(numpr, "ilvl"), "val"))
        if not num_id or num_id == "0":
            return None
        level = min(max(level or 0, 0), 8)
        return {"level": level, "marker": self.numbering.marker(num_id, level)}

    def table(self, tbl, depth: int) -> dict:
        rows: list[dict] = []
        merging: dict[int, dict] = {}  # 网格列 → 往下合并中的单元格
        for tr in self._rows(tbl, depth):
            if len(rows) >= MAX_TABLE_ROWS or self.full():
                break
            trpr = _child(tr, "trPr")
            column = min(_int(_attr(_child(trpr, "gridBefore"), "val")), MAX_SPAN)
            cells: list[dict] = []
            for tc in self._cells(tr, depth):
                tcpr = _child(tc, "tcPr")
                span = min(max(_int(_attr(_child(tcpr, "gridSpan"), "val"), 1), 1), MAX_SPAN)
                vmerge = _child(tcpr, "vMerge")
                restart = vmerge is not None and (_attr(vmerge, "val") or "continue") == "restart"
                above = merging.get(column)
                if vmerge is not None and not restart and above is not None:
                    above["rowspan"] = above.get("rowspan", 1) + 1
                    column += span
                    continue
                cell: dict = {"blocks": self.blocks(tc, depth + 1)}
                if span > 1:
                    cell["colspan"] = span
                if restart:
                    merging[column] = cell
                else:
                    merging.pop(column, None)
                cells.append(cell)
                column += span
            row: dict = {"cells": cells}
            if _child(trpr, "tblHeader") is not None and _on(_child(trpr, "tblHeader")):
                row["header"] = True
            rows.append(row)
        return {"type": "table", "rows": rows}

    def _rows(self, tbl, depth: int):
        for el in tbl:
            name = _local(el.tag)
            if name == "tr":
                yield el
            elif name in ("sdt", "customXml") and depth < MAX_DEPTH:
                yield from self._rows(_child(el, "sdtContent") if name == "sdt" else el, depth + 1)

    def _cells(self, tr, depth: int):
        for el in tr:
            name = _local(el.tag)
            if name == "tc":
                yield el
            elif name in ("sdt", "customXml") and depth < MAX_DEPTH:
                yield from self._cells(_child(el, "sdtContent") if name == "sdt" else el, depth + 1)

    # ── 段落里的文字与图片 ─────────────────────────────────────────────────────────────────────────
    def inline(self, parent, runs: list, base: list, link: bool, depth: int) -> None:
        if parent is None or depth > MAX_DEPTH:
            return
        for el in parent:
            name = _local(el.tag)
            if name == "r":
                self.run(el, runs, base, link, depth)
            elif name == "hyperlink":
                self.inline(el, runs, base, True, depth + 1)
            elif name == "sdt":
                self.inline(_child(el, "sdtContent"), runs, base, link, depth + 1)
            elif name in INLINE_WRAPPERS:
                self.inline(el, runs, base, link, depth + 1)
            elif name == "AlternateContent":
                self.inline(_alternate(el), runs, base, link, depth + 1)
            elif name in ("oMath", "oMathPara"):
                self._text(runs, "".join(t.text or "" for t in el.iter() if _local(t.tag) == "t"), {})
            # del / moveFrom（修订删掉的）、书签、批注范围、校对标记：不显示

    def run(self, r, runs: list, base: list, link: bool, depth: int) -> None:
        rpr = _child(r, "rPr")
        chain = self.styles.chain(_attr(_child(rpr, "rStyle"), "val"))
        fmt = _format(base + [s["rpr"] for s in reversed(chain)] + [rpr])
        if fmt.pop("hidden", False):
            return
        if link:
            fmt["link"] = True
        self._run_content(r, runs, fmt, depth)

    def _run_content(self, parent, runs: list, fmt: dict, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        for el in parent:
            name = _local(el.tag)
            if name == "t":
                self._text(runs, el.text or "", fmt)
            elif name in ("tab", "ptab"):
                self._text(runs, "\t", fmt)
            elif name in ("br", "cr"):
                self._text(runs, "\n", fmt)
            elif name == "noBreakHyphen":
                self._text(runs, "‑", fmt)
            elif name in ("drawing", "pict", "object"):
                self._images(el, runs)
            elif name == "AlternateContent":
                self._run_content(_alternate(el), runs, fmt, depth + 1)
            # instrText（域代码）、delText、fldChar、脚注引用、符号字体字符：不显示

    def _text(self, runs: list, text: str, fmt: dict) -> None:
        if not text or self.chars_left <= 0:
            return
        text = text[:self.chars_left]
        self.chars_left -= len(text)
        last = runs[-1] if runs else None
        if last is not None and "text" in last and {k: v for k, v in last.items() if k != "text"} == fmt:
            last["text"] += text
        else:
            runs.append({"text": text, **fmt})

    def _images(self, el, runs: list) -> None:
        extent = next((e for e in el.iter() if _local(e.tag) == "extent"), None)
        for node in el.iter():
            name = _local(node.tag)
            ref = _attr(node, "embed") if name == "blip" else _attr(node, "id") if name == "imagedata" else None
            if not ref:
                continue
            kind, path = self.rels.get(ref, ("", ""))
            if not kind.endswith(IMAGE_REL):
                continue
            try:
                data = self.package.read(path, MAX_IMAGE_BYTES)
            except Unreadable:
                continue
            mime = image_type(data or b"")
            if not data or not mime or len(data) > self.images_left:
                continue
            self.images_left -= len(data)
            image: dict = {"image": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}
            width, height = _int(_attr(extent, "cx")) // EMU_PER_PX, _int(_attr(extent, "cy")) // EMU_PER_PX
            if name == "blip" and 0 < width <= 4000 and 0 < height <= 4000:
                image.update(width=width, height=height)
            runs.append(image)


def _format(rprs: list) -> dict:
    """文字格式：段落样式 → 字符样式 → 直接格式，后面的盖前面的。泰文等复杂文字的粗体 / 斜体写在 bCs / iCs。"""
    found: dict[str, bool] = {}
    for rpr in rprs:
        for el in [] if rpr is None else rpr:
            name = _local(el.tag)
            if name in TOGGLES:
                found[TOGGLES[name]] = _on(el)
            elif name == "u":
                found["underline"] = (_attr(el, "val") or "single").lower() != "none"
            elif name == "vertAlign":
                value = _attr(el, "val")
                found["sup"], found["sub"] = value == "superscript", value == "subscript"
    fmt = {"bold": found.get("b") or found.get("bCs"), "italic": found.get("i") or found.get("iCs"),
           "underline": found.get("underline"), "strike": found.get("strike"), "sup": found.get("sup"),
           "sub": found.get("sub"), "hidden": found.get("hidden")}
    return {key: True for key, value in fmt.items() if value}
