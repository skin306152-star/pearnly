"""识别输入：PDF 页数上限与文字层快路（照旧线上 `pearnly-app@ec2e5c95`）。

- 页数：旧线上直读最多 20 页（`direct_read.py:67-72`，`OCR_IMAGE_DIRECT_MAX_PAGES`），这里超过就拒。
- 文字层：旧线上 `OCR_FAST_PATH_ENABLED=true`（运行配置取证见
  docs/work/cowork-backend/ocr-runtime-policy.md），PDF 平均每页至少 200 个字符就先用文字读，
  不传原件（更便宜也更准）；文字读出来过不了检查，再由升级模型直接读 PDF 原件（`policy.read`）。
"""
import io

import frappe
from frappe import _

MAX_PAGES = 20
MIN_CHARS_PER_PAGE = 200


def pdf_text(content):
    """PDF 的文字层：(页数, 文字)。不是 PDF、读不开返回 (0, "")。"""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = len(reader.pages)
        if pages > MAX_PAGES:
            frappe.throw(_("This PDF has more than {0} pages").format(MAX_PAGES))
        texts = [(page.extract_text() or "").strip() for page in reader.pages]
    except frappe.ValidationError:
        raise
    except Exception:
        return 0, ""
    return pages, "\n\n".join(f"--- page {index} ---\n{text}" for index, text in enumerate(texts, 1))


def text_path(images):
    """只有一个 PDF 且文字层够（平均每页 ≥ 200 字）时，返回它的文字；否则 None（照常传原件）。
    页数超限的 PDF 在这里就拒。"""
    pdfs = [content for content, mime in images if mime == "application/pdf"]
    for content in pdfs:
        pages, text = pdf_text(content)
        if len(images) == 1 and pages and len(text) >= MIN_CHARS_PER_PAGE * pages:
            return text
    return None


def pdf_pages(content):
    """多页 PDF 拆成单页（每页一个 PDF，输出确定：同一文件每次拆出的字节相同，识别断点才对得上）。
    不是 PDF 或只有一页返回 []。页数超限在这里就拒。"""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(content))
        if len(reader.pages) > MAX_PAGES:
            frappe.throw(_("This PDF has more than {0} pages").format(MAX_PAGES))
        if len(reader.pages) < 2:
            return []
        pages = []
        for page in reader.pages:
            writer = pypdf.PdfWriter()
            writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            pages.append(out.getvalue())
        return pages
    except frappe.ValidationError:
        raise
    except Exception:
        return []


def pdf_page_count(content) -> int:
    try:
        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return 1
