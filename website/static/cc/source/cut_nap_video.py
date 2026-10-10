"""逐帧抠掉视频里烤进去的棋盘格背景（同 website/static/cc/source/cut_top.py 的做法：从四边往里灌低饱和的亮像素）。"""
import sys, glob, os
from collections import deque
import numpy as np
from PIL import Image, ImageFilter

src, out = sys.argv[1], sys.argv[2]
frames = sorted(glob.glob(f"{src}/f*.png"))
boxes = []
for path in frames:
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    h, w, _ = a.shape
    mx, mn = a.max(2), a.min(2)
    bg = (mn > 188) & (mx - mn < 22)
    seen = np.zeros((h, w), bool)
    q = deque([(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)])
    while q:
        y, x = q.popleft()
        if seen[y, x] or not bg[y, x]:
            continue
        seen[y, x] = True
        if y + 1 < h: q.append((y + 1, x))
        if y > 0: q.append((y - 1, x))
        if x + 1 < w: q.append((y, x + 1))
        if x > 0: q.append((y, x - 1))
    alpha = Image.fromarray(np.where(seen, 0, 255).astype("uint8"))
    alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    im = Image.open(path).convert("RGBA"); im.putalpha(alpha)
    im.save(f"{out}/{os.path.basename(path)}")
    boxes.append(alpha.point(lambda v: 255 if v > 10 else 0).getbbox())
xs0 = min(b[0] for b in boxes if b); ys0 = min(b[1] for b in boxes if b)
xs1 = max(b[2] for b in boxes if b); ys1 = max(b[3] for b in boxes if b)
print("union bbox", xs0, ys0, xs1, ys1, "frames", len(frames))
