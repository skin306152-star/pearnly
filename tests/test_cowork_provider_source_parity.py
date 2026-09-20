"""Hash-bound local comparison of deployed decoding/geometry helpers.

Accept --source ROOT or --source-stdin {relative path: source text}. Executes
only selected function AST nodes, never imports the legacy application or SDK.
Synthetic PDFs/images are generated in memory; no network or Site is used.
"""
import argparse
import ast
import hashlib
import io
import json
import logging
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace
from typing import List, Optional

from PIL import Image
import pymupdf

from pearnly_cowork.ocr.inputs_runtime.source import PageSource, downscale_image_bytes
from pearnly_cowork.ocr.providers.client import vision_page
from pearnly_cowork.ocr.schema.evidence import Block, BoundingBox, Layer1Result, Page, Paragraph, Word

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/pipeline.py": "9e8fa8fb566a8b457c0ae6f64f6961069c757994269c9b65f6284a14a7843e52",
    "services/ocr/text_path.py": "d5829b61500c55bf0dfb5c31f73c850147f9fbf552f702374ba0425f15fbf49d",
    "services/ocr/layer1_vision.py": "631ab44df70acafda4209307caf8fa28e1a8c862c3028809c4bcdb94e561b150",
}


def legacy(contents):
    namespace = {"logger": logging.getLogger("synthetic-source"), "io": io, "time": time,
        "List": List, "Optional": Optional, "Page": Page, "Layer1Result": Layer1Result,
        "Word": Word, "Block": Block, "Paragraph": Paragraph, "BoundingBox": BoundingBox,
        "DEFAULT_MIN_TEXT_PER_PAGE": 200}
    selected = {"downscale_image_bytes", "try_extract", "_response_to_page", "_extract_bbox"}
    for path, expected in HASHES.items():
        content = contents[path]
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise ValueError("Source does not match " + SOURCE_SHA + ": " + path)
        tree = ast.parse(content)
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in selected]
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, "fixed-source:" + path, "exec"), namespace)
    return namespace


def proto_bbox(value):
    return SimpleNamespace(vertices=[SimpleNamespace(x=vertex.get("x", 0), y=vertex.get("y", 0))
                                     for vertex in (value or {}).get("vertices", [])])


def proto_response(annotation):
    pages = []
    for page in annotation.get("pages", []):
        blocks = []
        for block in page.get("blocks", []):
            paragraphs = []
            for paragraph in block.get("paragraphs", []):
                words = [SimpleNamespace(symbols=[SimpleNamespace(text=s.get("text", "")) for s in word.get("symbols", [])],
                         confidence=word.get("confidence", 0), bounding_box=proto_bbox(word.get("boundingBox")))
                         for word in paragraph.get("words", [])]
                paragraphs.append(SimpleNamespace(words=words, confidence=paragraph.get("confidence", 0),
                                  bounding_box=proto_bbox(paragraph.get("boundingBox"))))
            blocks.append(SimpleNamespace(paragraphs=paragraphs, confidence=block.get("confidence", 0),
                          bounding_box=proto_bbox(block.get("boundingBox"))))
        pages.append(SimpleNamespace(width=page.get("width", 0), height=page.get("height", 0), blocks=blocks))
    return SimpleNamespace(full_text_annotation=SimpleNamespace(text=annotation.get("text", ""), pages=pages))


def verify(contents):
    old, rng = legacy(contents), random.Random(20260921)
    for n in range(500):
        annotation = {"text": rng.choice(["", "Thai text\n123", "ABC"]), "pages": []}
        for _ in range(rng.randint(0, 2)):
            page = {"width": rng.randint(100, 3000), "height": rng.randint(100, 3000), "blocks": []}
            for _ in range(rng.randint(0, 3)):
                words = [{"symbols": [{"text": rng.choice(["ก", "A", "1", " "])} for _ in range(rng.randint(0, 8))],
                          "confidence": rng.randrange(101) / 100,
                          "boundingBox": {"vertices": [{rng.choice(["x", "y"]): rng.randrange(500)}
                                            for _ in range(rng.randint(0, 6))]}}
                         for _ in range(rng.randint(0, 8))]
                page["blocks"].append({"confidence": .8, "paragraphs": [{"confidence": .7, "words": words}]})
            annotation["pages"].append(page)
        previous = old["_response_to_page"](proto_response(annotation), n + 1)
        current = vision_page(annotation, n + 1)
        assert previous.model_dump() == current.model_dump(), n

    image_cases = 0
    for format in ("PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"):
        for size in ((40, 20), (2500, 400)):
            with Image.new("RGB", size, (100, 130, 200)) as source:
                output = io.BytesIO()
                source.save(output, format=format)
                content = output.getvalue()
            for cap in (0, 2400, 50):
                assert old["downscale_image_bytes"](content, cap) == downscale_image_bytes(content, cap), (format, size, cap)
                image_cases += 1
    for texts in (("", ""), ("tiny", "short"), ("line with text\n" * 40, ""),
                  ("invoice line\n" * 20, "invoice line\n" * 20)):
        with pymupdf.open() as document:
            for text in texts:
                page = document.new_page(width=300, height=500)
                if text:
                    page.insert_text((10, 20), text, fontsize=5)
            content = document.tobytes()
        previous = old["try_extract"](content)
        with PageSource(content, "synthetic.pdf", expected_page_count=2, enable_text_path=True) as current:
            assert (previous is None) == (current.text_pages is None), texts
            if previous is not None:
                assert [p.model_dump() for p in previous.pages] == [p.model_dump() for p in current.text_pages], texts
    print(json.dumps({"status": "PASS", "source": SOURCE_SHA, "vision_annotation_cases": 500,
                      "image_downscale_cases": image_cases, "pdf_text_cases": 4}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    contents = json.load(sys.stdin) if args.source_stdin else {path: (args.source / path).read_text() for path in HASHES}
    verify(contents)
