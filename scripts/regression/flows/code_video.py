"""业务流程用例的假摄像头画面：一个二维码（内容任意）或一个 EAN-13 条码，写成 Chrome `--use-file-for-fake-video-capture`
认的 y4m。二维码用 Frappe 自带依赖 `pyqrcode` 出矩阵（用容器里的 Python 跑），条码沿用冒烟的 `make_barcode_video`。

    ../env/bin/python /workspace/scripts/regression/flows/code_video.py <输出.y4m> qr '<内容>'
    python3 scripts/regression/flows/code_video.py <输出.y4m> ean 8850001000017

前 1.5 秒空白再出码，持续 6 秒：读码弹窗要先确认画面可见，读到码就关。
"""
import importlib.util
from pathlib import Path
import sys

WIDTH, HEIGHT, BLANK, FRAMES = 480, 360, 15, 60


def _qr_frame(text: str) -> bytes:
    import pyqrcode

    matrix = [line for line in pyqrcode.create(text, error="M").text(quiet_zone=4).splitlines() if line]
    size = len(matrix)
    unit = max(1, min(WIDTH, HEIGHT) * 9 // 10 // size)
    x0, y0 = (WIDTH - size * unit) // 2, (HEIGHT - size * unit) // 2
    luma = bytearray([235] * (WIDTH * HEIGHT))
    for r, line in enumerate(matrix):
        for c, bit in enumerate(line):
            if bit == "1":
                for y in range(y0 + r * unit, y0 + (r + 1) * unit):
                    luma[y * WIDTH + x0 + c * unit:y * WIDTH + x0 + (c + 1) * unit] = bytes([16] * unit)
    chroma = bytes([128] * (WIDTH // 2 * HEIGHT // 2))
    return b"FRAME\n" + bytes(luma) + chroma + chroma


def _ean_frame(code: str) -> bytes:
    path = Path(__file__).resolve().parents[2] / "smoke/make_barcode_video.py"
    spec = importlib.util.spec_from_file_location("make_barcode_video", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.WIDTH, module.HEIGHT) == (WIDTH, HEIGHT)
    return module.frame(code)


def write(path: str, kind: str, value: str) -> None:
    data = _qr_frame(value) if kind == "qr" else _ean_frame(value)
    empty = b"FRAME\n" + bytes([235] * (WIDTH * HEIGHT)) + bytes([128] * (WIDTH // 2 * HEIGHT // 2 * 2))
    with open(path, "wb") as handle:
        handle.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(BLANK):
            handle.write(empty)
        for _ in range(FRAMES):
            handle.write(data)


if __name__ == "__main__":
    write(sys.argv[1], sys.argv[2], sys.argv[3])
