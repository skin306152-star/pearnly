#!/usr/bin/env python3
"""生成冒烟用的假摄像头画面：一张白底 EAN-13 条码，写成 Chrome `--use-file-for-fake-video-capture` 认的 y4m。

    python3 scripts/smoke/make_barcode_video.py <输出.y4m> [码值，默认 TEST_CODE；或 scripts/smoke/codes/ 下一张码图]

纯标准库；码值固定（`TEST_CODE`），扫码入口读出它才算通过。文件约 12 MB，运行时生成，不进仓库。
别的码制（Data Matrix 等，10-30）用 `scripts/smoke/codes/` 里画好的灰度 PNG（只画码本身、带白边，居中放进画面）；
那几张图的内容见 `codes/README.md`。
"""
import struct
import sys
import zlib
from pathlib import Path

TEST_CODE = "2000000000008"  # 200 开头是店内码段，不会撞真实商品
WIDTH, HEIGHT, FRAMES = 480, 360, 30  # 条码持续 3 秒：解码器每隔几百毫秒才扫一帧，太短会一直错过
# 前 1.5 秒空白（10 帧 / 秒）再出条码：扫码弹窗读到码就关，探针要先在空白这段确认画面真的可见（10-23 黑屏事故：画面被换掉时读码可能照常）
BLANK = 15
L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def check_digit(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def modules(code: str) -> str:
    if len(code) != 13 or not code.isdigit() or check_digit(code[:12]) != code[12]:
        raise ValueError(f"not a valid EAN-13: {code}")
    left = "".join((L if p == "L" else G)[int(d)] for p, d in zip(PARITY[int(code[0])], code[1:7]))
    right = "".join(R[int(d)] for d in code[7:])
    return "101" + left + "01010" + right + "101"


def frame(code: str) -> bytes:
    bars = modules(code)
    unit = 4
    width = len(bars) * unit
    x0, top, bottom = (WIDTH - width) // 2, HEIGHT // 4, HEIGHT * 3 // 4
    row = bytearray([235] * WIDTH)
    for i, bit in enumerate(bars):
        if bit == "1":
            row[x0 + i * unit:x0 + (i + 1) * unit] = bytes([16] * unit)
    white = bytes([235] * WIDTH)
    luma = b"".join(bytes(row) if top <= y < bottom else white for y in range(HEIGHT))
    chroma = bytes([128] * (WIDTH // 2 * HEIGHT // 2))
    return b"FRAME\n" + luma + chroma + chroma


def read_gray_png(path) -> tuple[int, int, bytes]:
    """8 位灰度、不隔行的 PNG（`codes/` 里那几张）→ (宽, 高, 每像素一字节)。"""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    at, chunks, width = 8, [], 0
    while at < len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + size]
        if kind == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if (depth, color, interlace) != (8, 0, 0):
                raise ValueError(f"{path}: needs an 8 bit grey, non interlaced PNG")
        elif kind == b"IDAT":
            chunks.append(body)
        at += 12 + size
    raw, rows, previous = zlib.decompress(b"".join(chunks)), [], bytearray(width)
    for y in range(height):
        kind, line = raw[y * (width + 1)], bytearray(raw[y * (width + 1) + 1:(y + 1) * (width + 1)])
        for x in range(width):
            left, up, corner = (line[x - 1] if x else 0), previous[x], (previous[x - 1] if x else 0)
            if kind == 1:
                line[x] = (line[x] + left) & 255
            elif kind == 2:
                line[x] = (line[x] + up) & 255
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 255
            elif kind == 4:
                guess = left + up - corner
                pa, pb, pc = abs(guess - left), abs(guess - up), abs(guess - corner)
                line[x] = (line[x] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(bytes(line))
        previous = line
    return width, height, b"".join(rows)


def picture_frame(path) -> bytes:
    """码图居中放进画面（黑 16、白 235，同条码那一帧的亮度）。"""
    width, height, pixels = read_gray_png(path)
    if width > WIDTH or height > HEIGHT:
        raise ValueError(f"{path} is larger than the {WIDTH}x{HEIGHT} frame")
    x0, y0 = (WIDTH - width) // 2, (HEIGHT - height) // 2
    luma = bytearray([235] * (WIDTH * HEIGHT))
    for y in range(height):
        for x in range(width):
            if pixels[y * width + x] < 128:
                luma[(y0 + y) * WIDTH + x0 + x] = 16
    chroma = bytes([128] * (WIDTH // 2 * HEIGHT // 2))
    return b"FRAME\n" + bytes(luma) + chroma + chroma


def blank() -> bytes:
    return b"FRAME\n" + bytes([235] * (WIDTH * HEIGHT)) + bytes([128] * (WIDTH // 2 * HEIGHT // 2 * 2))


def write(path: str, code: str = TEST_CODE) -> None:
    data = picture_frame(code) if str(code).endswith(".png") else frame(code)
    empty = blank()
    with open(path, "wb") as handle:
        handle.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(BLANK):
            handle.write(empty)
        for _ in range(FRAMES):
            handle.write(data)


if __name__ == "__main__":
    write(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else TEST_CODE)
