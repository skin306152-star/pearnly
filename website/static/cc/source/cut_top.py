"""用户 Gemini 生成的「趴在横边后面探头」橘猫（2026-10-10 定稿）：抠掉棋盘格，擦掉那条横边（爪子底下那段除外，页面上登录框上沿代替它）；
找出两只眼的眼眶内沿和深色眼珠，底图里把眼眶内填成眼白，眼珠单独存成小图（页面上让它跟着鼠标转）。"""
import json, math, sys
from collections import deque
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

src, outdir = sys.argv[1], sys.argv[2]
box = (150, 130, 860, 770)
img = Image.open(src).convert("RGB").crop(box)
a = np.asarray(img).astype(int)
h, w, _ = a.shape
lum = a.mean(2)
mx, mn = a.max(2), a.min(2)
bg_like = (mn > 196) & (mx - mn < 20)
seen = np.zeros((h, w), bool)
q = deque([(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)])
while q:
    y, x = q.popleft()
    if seen[y, x] or not bg_like[y, x]:
        continue
    seen[y, x] = True
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx]:
            q.append((ny, nx))
# 横边（原图 y≈708–718）：下面是空白的那段（除了垂下来的爪子）整段擦掉
LY0, LY1 = 706 - box[1], 720 - box[1]
for x in range(w):
    if seen[min(h - 1, LY1 + 8), x]:
        seen[LY0:LY1, x] = True
    elif seen[max(0, LY0 - 8), x]:
        pass
eyes = []
base = a.copy()
for cx0, cy0 in ((370, 647), (535, 647)):           # 两只眼珠大致中心（原图坐标）
    cx0, cy0 = cx0 - box[0], cy0 - box[1]
    # 量的时候先把眼珠里的白色高光当成深色（不然从中心往外走会先碰到高光）
    L = lum.copy()
    yy, xx = np.ogrid[:h, :w]
    near = (yy - cy0) ** 2 + (xx - cx0) ** 2 < 31 ** 2
    L[near & (L > 110)] = 30   # 高光连同它发亮的边一起
    def first_bright(cx, cy, t, start=6, stop=70):
        rr = start
        while rr < stop:
            x, y = int(round(cx + rr * math.cos(t))), int(round(cy + rr * math.sin(t)))
            if L[y, x] > 150:
                return rr
            rr += 0.5
        return stop
    # 眼珠：沿 180 个方向找第一个亮点，去掉眼珠直接贴描边的方向，最小二乘拟合圆（两轮）
    cx, cy = float(cx0), float(cy0)
    for _ in range(2):
        ts = np.linspace(0, 2 * math.pi, 180, endpoint=False)
        rbs = np.array([first_bright(cx, cy, t) for t in ts])
        keep = rbs < np.median(rbs) + 2
        px, py = cx + rbs[keep] * np.cos(ts[keep]), cy + rbs[keep] * np.sin(ts[keep])
        A = np.c_[2 * px, 2 * py, np.ones(len(px))]
        sol = np.linalg.lstsq(A, px ** 2 + py ** 2, rcond=None)[0]
        cx, cy = sol[0], sol[1]
        r = math.sqrt(sol[2] + cx ** 2 + cy ** 2)
    r += 0.4
    # 只擦眼珠这一个圆：圆里每个点取同一方向上眼珠外面第一个亮像素的颜色（眼白处是眼白，贴着毛的地方是毛色）
    R = int(r + 3)
    crop = Image.fromarray(a[int(cy) - R:int(cy) + R + 1, int(cx) - R:int(cx) + R + 1].astype("uint8")).convert("RGBA")
    m = Image.new("L", crop.size, 0)
    ImageDraw.Draw(m).ellipse((R - r - 0.5, R - r - 0.5, R + r + 0.5, R + r + 0.5), fill=255)
    crop.putalpha(m.filter(ImageFilter.GaussianBlur(0.8)))
    name = f"top-eye{len(eyes)}.png"
    crop.save(f"{outdir}/{name}")
    fill = base.copy()
    ER = r + 2.2
    for y in range(int(cy - ER) - 1, int(cy + ER) + 2):
        for x in range(int(cx - ER) - 1, int(cx + ER) + 2):
            d = math.hypot(x - cx, y - cy)
            if d > ER:
                continue
            t = math.atan2(y - cy, x - cx)
            rr = r + 4
            while rr < r + 20:   # 只取真正的眼白（很亮的米白）或毛色（偏橙），跳过眼珠边上发灰的过渡像素
                sx, sy = int(round(cx + rr * math.cos(t))), int(round(cy + rr * math.sin(t)))
                cr, cg, cb = a[sy, sx]
                if lum[sy, sx] > 205 or (cr - cb > 55 and lum[sy, sx] > 150):
                    break
                rr += 0.5
            fill[y, x] = a[sy, sx]
    soft = Image.fromarray(fill.clip(0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(2.5))
    soft = np.asarray(soft).astype(float)
    disk = Image.new("L", (w, h), 0)
    ImageDraw.Draw(disk).ellipse((cx - ER, cy - ER, cx + ER, cy + ER), fill=255)
    pm = np.asarray(disk.filter(ImageFilter.GaussianBlur(0.7))).astype(float)[..., None] / 255
    base = base * (1 - pm) + soft * pm
    clip = [(cx + (r + 6) * math.cos(t), cy + (r + 6) * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 72, endpoint=False)]
    eyes.append({"img": name, "x": int(cx) - R, "y": int(cy) - R, "size": 2 * R + 1,
                 "clip": [[round(px, 1), round(py, 1)] for px, py in clip], "r": round(r, 1)})

alpha = Image.fromarray(np.where(seen, 0, 255).astype("uint8"))
alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6))
out = Image.fromarray(base.clip(0, 255).astype("uint8")).convert("RGBA")
out.putalpha(alpha)
bbox = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
out = out.crop(bbox)
for e in eyes:
    e["x"] -= bbox[0]; e["y"] -= bbox[1]
    e["clip"] = [[px - bbox[0], py - bbox[1]] for px, py in e["clip"]]
out.save(f"{outdir}/top.png")
edge_y = 712 - box[1] - bbox[1]
json.dump({"size": out.size, "edge_y": edge_y, "eyes": eyes}, open(f"{outdir}/top.json", "w"))
print("size", out.size, "edge_y", edge_y, [(e["x"], e["y"], e["size"], e["r"]) for e in eyes])
