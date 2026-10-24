#!/usr/bin/env python3
"""生成冒烟用的假摄像头画面：一张白底 EAN-13 条码，写成 Chrome `--use-file-for-fake-video-capture` 认的 y4m。

    python3 scripts/smoke/make_barcode_video.py <输出.y4m> [码值，默认 TEST_CODE]

纯标准库；码值固定（`TEST_CODE`），扫码入口读出它才算通过。文件约 1.4 MB，运行时生成，不进仓库。
"""
import sys

TEST_CODE = "2000000000008"  # 200 开头是店内码段，不会撞真实商品
WIDTH, HEIGHT, FRAMES = 640, 480, 3
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


def write(path: str, code: str = TEST_CODE) -> None:
    data = frame(code)
    with open(path, "wb") as handle:
        handle.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(FRAMES):
            handle.write(data)


if __name__ == "__main__":
    write(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else TEST_CODE)
