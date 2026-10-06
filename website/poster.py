"""首页主视觉“掀开纸堆”：上半张是一张写实的书桌俯拍（乱放的税票、小票、流水、手机里的 LINE、咖啡），
下半张就是网页本身（不另加底色和卡片框），铅笔线条的小单（น้องใบ）把照片的一角卷起来，露出底下已经整理好的账。

整张是一个内联 SVG（电脑看整张，手机上 site.js 只取左段，变成竖版），不加图片文件、不发请求；
“照片”是矢量画出来的，纹理与光影用 SVG 滤镜。卷角几何在这里和 site.js 的 peel() 各算一次：
这里给没有脚本时的静态样子，脚本接管后随滚动卷起、随手用力微动；系统开了“减少动态效果”时静止。
"""
import html
import math
from urllib.parse import quote

W, H, HP = 1440, 860, 560          # 画布宽高、照片下沿
HAND_X = 372                        # 铅笔顶住卷边的位置（x 固定，y 跟着卷边走）
PROP = 300                          # 那支铅笔的长度；卷边越高，铅笔立得越直
HIP, TILT, SCALE = (548, 698), 5, 1.25   # 小单单膝蹲着：身体底边中点、前倾角度、放大倍数
LENS, LENS_R = (594, 456), 46       # 另一只手举着放大镜，镜片里透过相片看到底下的账
FOCUS = (660, 420)                  # 镜片对准底下那张账的位置（合计和绿勾）
P0 = .3                             # 没有脚本时的卷起程度（0–1）
ROT = -1.2                          # 相片在页面上微微斜放
VIEW, VIEW_SM = f"-40 -40 1520 {H + 40}", f"-24 -40 760 {H + 40}"   # 电脑整张；手机只取左段成竖版

INK, PAPER = "#2d2b27", "#fffdf8"
THAI = "'IBM Plex Sans Thai', sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, monospace"
HAND = "'Sriracha', 'Long Cang', cursive"
NOTE = ("ภาษีซื้อ ต.ค.", "ส่งก่อน 15 !!", "อย่าลืมสลิปโอน")   # 桌上便利贴是泰国办公室的实物，三种语言都不翻


def esc(text):
    return html.escape(str(text), quote=True)


def n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


# ---------------- 卷角几何（与 site.js peel() 同一套公式） ----------------

def geom(p):
    lift, width = 120 + 90 * p, 700 + 260 * p
    t = 14 + lift * .16
    cx, cy = width * .42, HP - lift * .62
    curve = f"M{n(width)} {HP}Q{n(cx)} {n(cy)} 0 {n(HP - lift)}"
    clip = f"M0 0H{W}V{HP}H{n(width)}Q{n(cx)} {n(cy)} 0 {n(HP - lift)}Z"
    lip = f"M0 {n(HP - lift + t)}Q{n(cx)} {n(cy + t * .85)} {n(width)} {HP}"
    band = curve + "L" + lip[1:] + "Z"
    mx, my = .25 * width + .5 * cx, .25 * HP + .5 * cy + .25 * (HP - lift)   # 卷筒明暗沿折线的法线方向
    k = t / math.hypot(lift, width)
    grad = (mx, my, mx - lift * k, my + width * k)
    lo, hi = 0.0, 1.0                   # 在卷边下沿找 x = HAND_X 的点
    for _ in range(24):
        s = (lo + hi) / 2
        x = (1 - s) ** 2 * width + 2 * s * (1 - s) * cx
        lo, hi = (s, hi) if x > HAND_X else (lo, s)
    y = (1 - s) ** 2 * HP + 2 * s * (1 - s) * (cy + t * .85) + s * s * (HP - lift + t)
    return dict(curve=curve, clip=clip, band=band, lip=lip, grad=grad, hand=(HAND_X, y))


def rot(x, y):
    a = math.radians(TILT)
    x, y = x * SCALE, y * SCALE
    return HIP[0] + x * math.cos(a) - y * math.sin(a), HIP[1] + x * math.sin(a) + y * math.cos(a)


REST_SHOULDER, LENS_SHOULDER = rot(-28, -62), rot(28, -58)
GROUND = HIP[1] + 42 * SCALE


def prop(hand):
    """一支黄铅笔斜撑在地上，顶住卷起的那一角。"""
    hx, hy = hand
    rise = GROUND - hy
    lean = -math.degrees(math.atan2(-math.sqrt(max(PROP ** 2 - rise ** 2, 0)), rise))
    return f"translate({n(hx)} {n(hy)}) rotate({n(lean)})"


# ---------------- 滤镜与渐变 ----------------

DEFS = """<defs>
<filter id="pz-wood" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency=".0028 .1" numOctaves="4" seed="11"/>
  <feColorMatrix values="0 0 0 0 .62  0 0 0 0 .46  0 0 0 0 .28  1.1 0 0 0 -.5"/>
</filter>
<filter id="pz-sh" x="-25%" y="-25%" width="160%" height="160%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="10" result="b"/><feOffset in="b" dx="16" dy="20" result="o"/>
  <feFlood flood-color="#4a3018" flood-opacity=".2"/><feComposite in2="o" operator="in" result="s1"/>
  <feGaussianBlur in="SourceAlpha" stdDeviation="1.8" result="c"/><feOffset in="c" dx="2" dy="3" result="co"/>
  <feFlood flood-color="#3a2410" flood-opacity=".3"/><feComposite in2="co" operator="in" result="s2"/>
  <feMerge><feMergeNode in="s1"/><feMergeNode in="s2"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<filter id="pz-lift" x="-25%" y="-25%" width="170%" height="170%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="16" result="b"/><feOffset in="b" dx="26" dy="34" result="o"/>
  <feFlood flood-color="#4a3018" flood-opacity=".24"/><feComposite in2="o" operator="in" result="s1"/>
  <feGaussianBlur in="SourceAlpha" stdDeviation="3" result="c"/><feOffset in="c" dx="3" dy="5" result="co"/>
  <feFlood flood-color="#3a2410" flood-opacity=".28"/><feComposite in2="co" operator="in" result="s2"/>
  <feMerge><feMergeNode in="s1"/><feMergeNode in="s2"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<filter id="pz-bokeh" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.6"/></filter>
<filter id="pz-soft" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="9"/></filter>
<filter id="pz-blur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="7"/></filter>
<filter id="pz-pencil" x="-10%" y="-10%" width="120%" height="120%">
  <feTurbulence type="fractalNoise" baseFrequency=".035" numOctaves="2" seed="5" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="2.6" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<linearGradient id="pz-desk" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#f4ebdc"/><stop offset=".55" stop-color="#ead9bd"/><stop offset="1" stop-color="#dcc5a2"/>
</linearGradient>
<linearGradient id="pz-paper" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#fffefa"/><stop offset="1" stop-color="#f0ebe0"/>
</linearGradient>
<linearGradient id="pz-note" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#ffe680"/><stop offset="1" stop-color="#ffd23f"/>
</linearGradient>
<radialGradient id="pz-sun" cx=".12" cy="-.1" r="1.05">
  <stop offset="0" stop-color="#ffe7b0" stop-opacity=".75"/><stop offset=".55" stop-color="#ffd08a" stop-opacity=".18"/>
  <stop offset="1" stop-color="#ffd08a" stop-opacity="0"/>
</radialGradient>
<radialGradient id="pz-vig" cx=".5" cy=".45" r=".75">
  <stop offset=".6" stop-color="#4a3018" stop-opacity="0"/><stop offset="1" stop-color="#4a3018" stop-opacity=".12"/>
</radialGradient>
<radialGradient id="pz-coffee" cx=".42" cy=".38" r=".7">
  <stop offset="0" stop-color="#7a4a26"/><stop offset=".7" stop-color="#4a2812"/><stop offset="1" stop-color="#2e170a"/>
</radialGradient>
<radialGradient id="pz-saucer" cx=".4" cy=".35" r=".75">
  <stop offset="0" stop-color="#ffffff"/><stop offset=".8" stop-color="#ece8e1"/><stop offset="1" stop-color="#d9d3c9"/>
</radialGradient>
<linearGradient id="pz-leaf" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#4f9a55"/><stop offset="1" stop-color="#1f5a30"/>
</linearGradient>
<linearGradient id="pz-roll" gradientUnits="userSpaceOnUse" x1="{0}" y1="{1}" x2="{2}" y2="{3}">
  <stop offset="0" stop-color="#8f8676"/><stop offset=".2" stop-color="#d6cfc1"/><stop offset=".55" stop-color="#fffdf8"/>
  <stop offset=".85" stop-color="#f1ece2"/><stop offset="1" stop-color="#c9c0ae"/>
</linearGradient>
<linearGradient id="pz-glare" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/>
</linearGradient>
</defs>"""


def obj(x, y, r, inner, lift=False):
    """一个放在桌上的物件：外层在桌面坐标里加投影（光从左上来），内层再平移旋转。"""
    return (f'<g filter="url(#pz-{"lift" if lift else "sh"})"><g transform="translate({x} {y}) rotate({r})">'
            f'{inner}</g></g>')


def t(x, y, text, size=11, fill="#3b3a36", weight=400, anchor="start", family=THAI, extra=""):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{extra}>{esc(text)}</text>')


def bars(x, y, widths, gap=13, h=4, fill="#d9d4c9"):
    return "".join(f'<rect x="{x}" y="{y + i * gap}" width="{w}" height="{h}" rx="2" fill="{fill}"/>' for i, w in enumerate(widths))


# ---------------- 桌上的东西 ----------------

def invoice(seller, logo, items, total, vat, stamp=True):
    rows = "".join(t(22, 168 + i * 24, a, 11) + t(308, 168 + i * 24, b, 11, anchor="end", family=MONO) +
                   f'<rect x="20" y="{176 + i * 24}" width="290" height="1" fill="#ebe6dc"/>' for i, (a, b) in enumerate(items))
    y = 168 + len(items) * 24 + 14
    st = (f'<g transform="translate(150 {y + 44}) rotate(-13)" opacity=".78">'
          f'<rect x="0" y="0" width="150" height="46" rx="8" fill="none" stroke="#d2343a" stroke-width="3"/>'
          + t(75, 22, "ได้รับเงินแล้ว", 17, "#d2343a", 600, "middle") + t(75, 38, "PAID · 03/10/2569", 9, "#d2343a", 500, "middle")
          + '</g>') if stamp else ""
    return (f'<rect width="330" height="440" fill="url(#pz-paper)"/>'
            f'<circle cx="36" cy="38" r="15" fill="{logo}"/><path d="M28 40 l6 -9 l6 9 l5 -6" stroke="#fff" stroke-width="2.6" fill="none"/>'
            + t(60, 36, seller, 13, "#1f1e1b", 600) + bars(60, 44, [150, 110], 9, 3)
            + f'<rect x="196" y="72" width="116" height="40" rx="4" fill="none" stroke="#1f1e1b" stroke-width="1.4"/>'
            + t(254, 90, "ใบกำกับภาษี", 13, "#1f1e1b", 600, "middle") + t(254, 105, "TAX INVOICE", 8.5, "#6a6862", 500, "middle")
            + bars(20, 80, [120, 150, 96], 11, 3)
            + '<rect x="18" y="134" width="294" height="20" fill="#efebe3"/>'
            + t(22, 148, "รายการ", 10, "#5f5e5a", 600) + t(308, 148, "จำนวนเงิน", 10, "#5f5e5a", 600, "end") + rows
            + t(200, y + 6, "VAT 7%", 10.5, "#5f5e5a") + t(308, y + 6, vat, 10.5, "#3b3a36", 400, "end", MONO)
            + t(200, y + 26, "รวมทั้งสิ้น", 12, "#1f1e1b", 600) + t(308, y + 26, total, 12, "#1f1e1b", 600, "end", MONO)
            + st
            + '<path d="M30 410 c14 -16 22 6 34 -6 s14 -14 22 2 s16 4 26 -8" stroke="#2847a0" stroke-width="1.8" fill="none"/>'
            + bars(26, 420, [90], 4, 1.2, "#bdb7aa"))


def receipt():
    edge = "".join(f"l7 {6 if i % 2 else -6}" for i in range(18))
    shape = f'<path d="M0 6 {edge} V414 {edge.replace("l7", "l-7")} Z" fill="#fbfaf5"/>'
    lines = [("กาแฟลาเต้เย็น", "65.00"), ("ครัวซองต์", "55.00"), ("น้ำดื่ม", "15.00"), ("ค่าส่ง", "40.00")]
    body = "".join(t(12, 150 + i * 19, a, 9.5, "#55534d", family=MONO) + t(114, 150 + i * 19, b, 9.5, "#55534d", anchor="end", family=MONO)
                   for i, (a, b) in enumerate(lines))
    code = "".join(f'<rect x="{18 + i * 4.4}" y="330" width="{1.4 + (i * 7 % 3)}" height="34" fill="#4a4843"/>' for i, _ in enumerate(range(21)))
    return (shape + t(63, 40, "ร้านกาแฟ บ้านสวน", 11, "#3b3a36", 600, "middle") + t(63, 56, "TAX ID 0105559876543", 8, "#6a6862", 400, "middle", MONO)
            + t(63, 70, "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ", 7.5, "#6a6862", 400, "middle")
            + '<path d="M10 86 H118" stroke="#9c998f" stroke-dasharray="3 3"/>'
            + t(12, 108, "02/10/2569 08:41", 8.5, "#6a6862", family=MONO) + t(12, 124, "POS#03  บิล 0418", 8.5, "#6a6862", family=MONO)
            + body + '<path d="M10 236 H118" stroke="#9c998f" stroke-dasharray="3 3"/>'
            + t(12, 258, "รวม", 11, "#2d2b27", 600) + t(114, 258, "175.00", 11, "#2d2b27", 600, "end", MONO)
            + t(12, 276, "VAT 7% รวมใน", 8.5, "#6a6862", family=MONO) + t(114, 276, "11.45", 8.5, "#6a6862", anchor="end", family=MONO)
            + t(63, 306, "ขอบคุณค่ะ", 10, "#55534d", 500, "middle") + code
            + '<path d="M70 120 q30 40 10 120" stroke="#8a5a2b" stroke-width="5" opacity=".07" fill="none"/>')


def statement():
    rows = [("01/10", "โอนเข้า บจ. ตัวอย่างกาแฟ", "12,500.00", True), ("02/10", "ชำระค่าไฟฟ้า", "-3,745.00", False),
            ("02/10", "PromptPay 089-xxx-4412", "-856.00", True), ("03/10", "โอนออก ขนส่งด่วนไทย", "-321.00", False),
            ("04/10", "รับชำระ INV-0921", "8,200.00", True), ("05/10", "ค่าธรรมเนียม", "-25.00", False)]
    body = ""
    for i, (d, a, b, mark) in enumerate(rows):
        y = 112 + i * 27
        body += f'<rect x="12" y="{y - 16}" width="336" height="27" fill="{"#eef3fb" if i % 2 else "#fff"}"/>'
        if mark:
            body += f'<rect x="74" y="{y - 13}" width="{len(a) * 5.6 + 8}" height="17" rx="3" fill="#fff36b" opacity=".75"/>'
        body += t(18, y, d, 10, "#3b3a36", family=MONO) + t(80, y, a, 10.5, "#2d2b27") + t(340, y, b, 10.5, "#2d2b27" if "-" in b else "#1d7f47", 500, "end", MONO)
    ticks = "".join(f'<path d="M354 {100 + i * 54} l5 6 l10 -13" stroke="#d2343a" stroke-width="2.4" fill="none"/>' for i in range(3))
    return (f'<rect width="360" height="300" fill="url(#pz-paper)"/><rect width="360" height="58" fill="#1f4fa0"/>'
            + t(18, 30, "BANK STATEMENT", 15, "#fff", 600) + t(18, 47, "บัญชีกระแสรายวัน · xxx-x-x4412-x", 9.5, "#cfe0ff")
            + t(342, 30, "ต.ค. 2569", 11, "#fff", 500, "end")
            + t(18, 80, "วันที่", 9.5, "#6a6862", 600) + t(80, 80, "รายการ", 9.5, "#6a6862", 600) + t(340, 80, "จำนวนเงิน", 9.5, "#6a6862", 600, "end")
            + body + ticks + bars(18, 278, [130, 80], 8, 3))


def phone():
    bubbles = (
        '<rect x="62" y="74" width="92" height="112" rx="14" fill="#06c755"/>'
        '<rect x="70" y="82" width="36" height="46" rx="4" fill="#fbfaf5"/><rect x="110" y="82" width="36" height="46" rx="4" fill="#fffdf8"/>'
        + bars(74, 90, [26, 18, 22, 14], 7, 2.2, "#c9c4b8") + bars(114, 90, [24, 20, 16, 22], 7, 2.2, "#c9c4b8")
        + '<rect x="70" y="132" width="36" height="46" rx="4" fill="#fffdf8"/><rect x="110" y="132" width="36" height="46" rx="4" fill="#fbfaf5"/>'
        + bars(74, 140, [22, 26, 14, 20], 7, 2.2, "#c9c4b8") + bars(114, 140, [20, 14, 26, 18], 7, 2.2, "#c9c4b8")
        + '<rect x="40" y="196" width="114" height="30" rx="14" fill="#06c755"/>'
        + t(97, 215, "ใบเสร็จเดือนนี้ค่ะ", 10.5, "#fff", 500, "middle")
        + '<rect x="18" y="238" width="96" height="30" rx="14" fill="#fff"/>'
        + t(66, 257, "รับแล้ว ขอบคุณค่ะ", 10, "#2d2b27", 500, "middle")
        + '<rect x="18" y="276" width="70" height="30" rx="14" fill="#fff"/>' + t(53, 295, "✓ บันทึกแล้ว", 9.5, "#1d7f47", 600, "middle"))
    return ('<rect width="180" height="360" rx="30" fill="#1b1b1d"/><rect x="2" y="2" width="176" height="356" rx="28" fill="none" stroke="#4a4a4f" stroke-width="1.5"/>'
            '<g transform="translate(8 8)"><rect width="164" height="344" rx="23" fill="#a9c0dc"/>'
            '<path d="M0 23 a23 23 0 0 1 23 -23 H141 a23 23 0 0 1 23 23 V48 H0 Z" fill="#f7f7f8"/>'
            + t(82, 35, "สำนักงานบัญชี", 11, "#1f1e1b", 600, "middle") + bubbles
            + '<rect width="164" height="344" rx="23" fill="url(#pz-glare)"/></g>'
            '<rect x="64" y="14" width="52" height="15" rx="7.5" fill="#0d0d0e"/>')


def cup():
    return ('<circle r="104" fill="url(#pz-saucer)"/><circle r="86" fill="none" stroke="#d6d0c5" stroke-width="2"/>'
            '<rect x="56" y="-15" width="62" height="30" rx="15" fill="#f4f1ec" stroke="#ddd6cb" stroke-width="2"/>'
            '<circle r="72" fill="#fbfaf7"/><circle r="72" fill="none" stroke="#e1dbd0" stroke-width="3"/>'
            '<circle r="60" fill="url(#pz-coffee)"/>'
            '<circle r="58" fill="none" stroke="#b9875a" stroke-width="5" opacity=".55"/>'
            '<path d="M-10 -8 c10 -16 26 -6 20 8 c-6 14 -26 10 -20 -8 z" fill="#c99a6b" opacity=".55"/>'
            '<ellipse cx="-26" cy="-30" rx="18" ry="7" transform="rotate(-35 -26 -30)" fill="#fff" opacity=".35"/>')


def note(lines):
    return ('<path d="M0 0 H150 V138 Q146 150 132 152 H0 Z" fill="url(#pz-note)"/>'
            '<path d="M150 138 Q146 150 132 152 L150 152 Z" fill="#e8b92a" opacity=".6"/>'
            + t(16, 46, lines[0], 21, "#2d2b27", family=HAND)
            + t(16, 84, lines[1], 19, "#d2343a", family=HAND)
            + '<path d="M16 96 q40 6 80 -2 q20 -3 36 2" stroke="#d2343a" stroke-width="2.4" fill="none"/>'
            + t(16, 128, lines[2], 15, "#2d2b27", family=HAND))


def pen():
    return ('<rect x="0" y="-8" width="250" height="16" rx="8" fill="#2347a8"/><rect x="0" y="-8" width="250" height="5" rx="2.5" fill="#5f7fd6" opacity=".7"/>'
            '<rect x="170" y="-9" width="80" height="18" rx="9" fill="#142e75"/><rect x="186" y="-15" width="58" height="5" rx="2.5" fill="#c9ccd3"/>'
            '<path d="M0 -8 L-26 0 L0 8 Z" fill="#d9dce2"/><path d="M-26 0 L-34 0" stroke="#1b1b1d" stroke-width="2.5"/>')


def highlighter():
    return ('<rect x="0" y="-15" width="190" height="30" rx="9" fill="#ff7aa8"/><rect x="0" y="-15" width="190" height="9" rx="4" fill="#ffadc9" opacity=".8"/>'
            '<rect x="130" y="-16" width="60" height="32" rx="9" fill="#e64f86"/><path d="M0 -10 L-22 -5 L-22 5 L0 10 Z" fill="#e64f86"/>')


def clip_wire():
    return ('<path d="M0 0 V46 a9 9 0 0 0 18 0 V8 a6 6 0 0 0 -12 0 V40" stroke="#a7acb4" stroke-width="3.2" fill="none"/>'
            '<path d="M0 0 V46 a9 9 0 0 0 18 0 V8" stroke="#eef0f3" stroke-width="1" fill="none" opacity=".8"/>')


def leaves():
    leaf = lambda x, y, r, s: (f'<g transform="translate({x} {y}) rotate({r}) scale({s})">'
                               '<path d="M0 0 C40 -60 150 -70 230 -10 C150 40 50 50 0 0 Z" fill="url(#pz-leaf)"/>'
                               '<path d="M0 0 C70 -12 150 -14 226 -10" stroke="#8fca8a" stroke-width="3" fill="none" opacity=".7"/>'
                               + "".join(f'<path d="M{40 + i * 36} {-6 - i}  l26 {-22 + i * 2} M{40 + i * 36} {-6 - i} l24 {20 - i}" stroke="#8fca8a" stroke-width="2" opacity=".45"/>' for i in range(5))
                               + '</g>')
    return leaf(-60, 40, 18, 1.1) + leaf(-40, 140, -8, .95) + leaf(-30, -20, 52, .85)


def desk():
    plank = "".join(f'<rect x="0" y="{y}" width="{W}" height="2" fill="#a3825a" opacity=".22"/>' for y in (186, 392))
    leaf_shadow = ('<g opacity=".12" filter="url(#pz-blur)"><path d="M120 120 C200 40 330 60 420 140 C320 170 200 180 120 120 Z" fill="#1d0e03"/>'
                   '<path d="M90 230 C170 180 300 200 360 270 C260 290 160 280 90 230 Z" fill="#1d0e03"/></g>')
    return (f'<rect width="{W}" height="{HP}" fill="url(#pz-desk)"/>'
            f'<rect width="{W}" height="{HP}" filter="url(#pz-wood)"/>' + plank + leaf_shadow)


def lighting():
    blinds = "".join(f'<rect x="-600" y="{-500 + i * 92}" width="2800" height="40"/>' for i in range(16))
    return (f'<g fill="#5a3a1a" opacity=".09" filter="url(#pz-soft)" style="mix-blend-mode:multiply" transform="rotate(-27 720 280)">{blinds}</g>'
            f'<rect width="{W}" height="{HP}" fill="url(#pz-sun)" style="mix-blend-mode:screen"/>'
            f'<rect width="{W}" height="{HP}" fill="url(#pz-vig)"/>')


def photo():
    return (desk()
            + obj(880, 120, -5, statement())
            + obj(190, 40, -9, invoice("บจ. สยามซัพพลาย", "#f08a24", [("กระดาษ A4 80 แกรม ×20", "2,400.00"), ("หมึกพิมพ์ HP 682", "1,180.00"),
                                                                     ("แฟ้มสันกว้าง ×12", "540.00"), ("ค่าจัดส่ง", "120.00")], "4,540.80", "297.80"))
            + obj(360, 92, 6, invoice("บจ. นมสดฟาร์มดี", "#18a0a0", [("นมสด 2L ×12", "840.00"), ("โยเกิร์ต ×24", "552.00"),
                                                                     ("ค่าขนส่งความเย็น", "180.00")], "1,682.04", "110.04"))
            + '<g fill="none" stroke="#8a5a2b" opacity=".32"><path d="M520 250 a44 44 0 1 1 30 40" stroke-width="3.2"/><path d="M523 258 a40 40 0 0 0 22 30" stroke-width="1.6"/></g>'
            + obj(520, 48, 4, clip_wire())
            + obj(700, 28, 11, receipt())
            + obj(1090, 200, 13, phone(), lift=True)
            + obj(1300, 92, 0, cup(), lift=True)
            + obj(612, 330, -7, note(NOTE))
            + obj(980, 490, 17, pen())
            + obj(1220, 470, -14, highlighter())
            + f'<g filter="url(#pz-bokeh)" opacity=".96"><g filter="url(#pz-lift)">{leaves()}</g></g>'
            + lighting())


def under(L):
    """照片底下那张：整理好的账。"""
    lines = "".join(f'<rect x="0" y="{y}" width="{W}" height="1" fill="#e9e3d6"/>' for y in range(320, HP, 38))
    head = (t(48, 350, L["cols"][0], 15, "#8f8d87", 600) + t(150, 350, L["cols"][1], 15, "#8f8d87", 600)
            + t(520, 350, L["cols"][2], 15, "#8f8d87", 600, "end") + t(660, 350, L["cols"][3], 15, "#8f8d87", 600, "end"))
    rows = "".join(
        t(48, 388 + i * 38, d, 17, "#2d2b27", 500, family=MONO) + t(150, 388 + i * 38, s, 17, "#1f1e1b", 500)
        + t(520, 388 + i * 38, v, 17, "#2d2b27", 500, "end", MONO) + t(660, 388 + i * 38, tot, 17, "#1f1e1b", 600, "end", MONO)
        + f'<circle cx="690" cy="{382 + i * 38}" r="10" fill="#1d7f47"/><path d="M685 {382 + i * 38} l4 4 l7 -8" stroke="#fff" stroke-width="2.4" fill="none"/>'
        for i, (d, s, v, tot) in enumerate(L["rows"]))
    return (f'<rect width="{W}" height="{HP}" fill="#fffdf8"/>{lines}'
            f'<rect x="30" y="0" width="1.5" height="{HP}" fill="#f2b8b8"/>{head}{rows}')


def lens(L):
    """放大镜：镜片里是放大 1.1 倍的“底下那张账”，像透过乱糟糟的相片直接读到数据。"""
    cx, cy = LENS
    d = (-.62, .78)
    a = (cx + d[0] * (LENS_R + 4), cy + d[1] * (LENS_R + 4))
    b = (cx + d[0] * (LENS_R + 46), cy + d[1] * (LENS_R + 46))
    return (f'<clipPath id="pz-glass"><circle cx="{cx}" cy="{cy}" r="{LENS_R}"/></clipPath>'
            f'<g clip-path="url(#pz-glass)"><g transform="translate({cx} {cy}) scale(1.1) translate({-FOCUS[0]} {-FOCUS[1]})">{under(L)}</g>'
            f'<path d="M{cx + 4} {cy - 32} a30 30 0 0 1 24 20" stroke="#fff" stroke-width="5" fill="none" opacity=".8"/></g>'
            f'<g color="{INK}" stroke="currentColor" stroke-linecap="round" fill="none" filter="url(#pz-pencil)">'
            f'<circle cx="{cx}" cy="{cy}" r="{LENS_R}" stroke-width="4.5"/>'
            f'<path d="M{n(a[0])} {n(a[1])} L{n(b[0])} {n(b[1])}" stroke-width="13"/>'
            f'<path d="M{n(a[0])} {n(a[1])} L{n(b[0])} {n(b[1])}" stroke="#ffd84d" stroke-width="7"/></g>'), b


def mascot(grip):
    """铅笔线条的小单：用一支铅笔撑起相片一角，自己单膝蹲着举放大镜往里看。"""
    def zig():
        out, up = [], True
        for _ in range(8):
            out.append(f"l-9.75 {-8 if up else 8}")
            up = not up
        return " ".join(out)
    body = (f'<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V0 {zig()} Z" fill="{PAPER}"/>'
            '<path d="M-24 -40 H18 M-24 -28 H8" stroke="#bdb7aa" stroke-width="3"/>'
            '<rect x="2" y="-18" width="26" height="10" rx="3" fill="#ffd84d" stroke="currentColor" stroke-width="2.4"/>'
            '<ellipse cx="6" cy="-52" rx="4" ry="5" stroke-width="2.4"/>'
            '<svg class="mascot pz-face" x="-39" y="-100" width="78" height="60" viewBox="-39 -100 78 60" overflow="visible">'
            '<ellipse cx="-25" cy="-62" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".8"/>'
            '<ellipse cx="19" cy="-64" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".8"/>'
            '<g class="m-blink"><circle cx="-9" cy="-78" r="5" fill="currentColor" stroke="none"/>'
            '<circle cx="17" cy="-77" r="5" fill="currentColor" stroke="none"/></g></svg>')
    (ax, ay), (bx, by) = rot(-12, 0), rot(14, 0)
    k = SCALE
    g = n(HIP[1] + 42 * k)
    legs = (f'<path d="M{n(bx)} {n(by)} L{n(bx + 30 * k)} {n(by + 16 * k)} L{n(bx + 34 * k)} {g} h{n(16 * k)}"/>'
            f'<path d="M{n(ax)} {n(ay)} L{n(ax - 12 * k)} {g} L{n(ax - 46 * k)} {g}"/>')
    (sx, sy), (gx, gy) = LENS_SHOULDER, grip
    rx, ry = REST_SHOULDER
    return (f'<g class="pz-pencil" color="{INK}" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none" filter="url(#pz-pencil)">'
            f'<path d="M{HIP[0] - 96} {g} m0 4 h190 M{HIP[0] - 64} {g} m0 11 h124 M{HIP[0] - 34} {g} m0 18 h60" stroke-width="1.6" opacity=".35"/>'
            + legs
            + f'<g transform="translate({HIP[0]} {HIP[1]}) rotate({TILT}) scale({SCALE})" stroke-width="{n(3 / SCALE)}">{body}</g>'
            + f'<path d="M{n(rx)} {n(ry)}Q{n(rx - 30)} {n(ry + 24)} {n(rx - 4)} {n(ry + 50)}"/><circle cx="{n(rx - 4)}" cy="{n(ry + 50)}" r="7" fill="{PAPER}"/>'
            + f'<path d="M{n(sx)} {n(sy)}Q{n(max(sx, gx) + 14)} {n((sy + gy) / 2 + 16)} {n(gx)} {n(gy)}"/>'
            + f'<circle cx="{n(gx)}" cy="{n(gy)}" r="7" fill="{PAPER}"/>'
            + '</g>')


def pencil(hand):
    L = PROP
    return (f'<g class="pz-prop" transform="{prop(hand)}" color="{INK}" stroke="currentColor" stroke-width="2.6" stroke-linejoin="round" filter="url(#pz-pencil)">'
            f'<rect x="-9" y="-2" width="18" height="20" rx="5" fill="#ff9ba3"/><rect x="-9" y="16" width="18" height="10" fill="#cfd2d8"/>'
            f'<rect x="-9" y="26" width="18" height="{L - 60}" fill="#ffd84d"/><path d="M-3 30 V{L - 38}" stroke="#e0b52e" stroke-width="2"/>'
            f'<path d="M-9 {L - 34} L0 {L} L9 {L - 34} Z" fill="#f3d9b1"/><path d="M-3 {L - 11} L0 {L} L3 {L - 11} Z" fill="currentColor"/></g>')


def svg(L):
    g = geom(P0)
    defs = DEFS.replace("{0}", n(g['grad'][0])).replace("{1}", n(g['grad'][1])).replace("{2}", n(g['grad'][2])).replace("{3}", n(g['grad'][3]))
    data = f'data-view="{VIEW}" data-view-sm="{VIEW_SM}" data-hp="{HP}" data-hand-x="{HAND_X}" data-p0="{P0}" data-prop="{PROP}" data-ground="{n(GROUND)}"'
    glass, grip = lens(L)
    return f"""<svg class="pz" viewBox="{VIEW}" {data} role="img" aria-label="{esc(L["alt"])}">
{defs}
<g transform="rotate({ROT} {W / 2} {HP / 2})">
<rect x="6" y="20" width="{W - 12}" height="{HP - 4}" fill="#4a3018" opacity=".2" filter="url(#pz-blur)"/>
<clipPath id="pz-clip"><path class="pz-clip" d="{g['clip']}"/></clipPath>
<clipPath id="pz-under"><rect width="{W}" height="{HP}"/></clipPath>
<g clip-path="url(#pz-under)">{under(L)}
<path class="pz-shade" d="{g['band']}" fill="#4a3018" opacity=".26" filter="url(#pz-blur)" transform="translate(8 16)"/></g>
<g clip-path="url(#pz-clip)">{photo()}<path class="pz-bend" d="{g['curve']}" stroke="#4a3018" stroke-width="46" fill="none" opacity=".2" filter="url(#pz-blur)"/></g>
<path class="pz-band" d="{g['band']}" fill="url(#pz-roll)"/>
<g color="{INK}" filter="url(#pz-pencil)" fill="none" stroke="currentColor" stroke-linecap="round">
<path class="pz-curve" d="{g['curve']}" stroke-width="1.4" opacity=".5"/><path class="pz-lip" d="{g['lip']}" stroke-width="2.4"/></g>
{pencil(g['hand'])}
{glass}
{mascot(grip)}
</g>
<text class="pz-caption" x="{HIP[0]}" y="{HIP[1] + 122}" font-family="{HAND}" font-size="30" fill="#3b3934" text-anchor="middle">{esc(L["caption"])}</text>
</svg>"""


def fonts(L):
    """手写字只用到几个字：按字取子集（中文手写字体很大，整套不下）。"""
    chars = "".join(sorted(set(L["caption"] + "".join(NOTE))))
    families = "family=Sriracha" + ("&family=Long+Cang" if any("\u4e00" <= c <= "\u9fff" for c in chars) else "")
    return f"https://fonts.googleapis.com/css2?{families}&text={quote(chars)}&display=swap"


def doodle():
    """标题区到相片之间：一架铅笔画的纸飞机沿虚线飞向相片——单据飞进系统。"""
    return ('<svg class="pz-doodle" viewBox="0 0 180 140" fill="none" stroke="#2d2b27" stroke-width="2.2" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            '<path d="M6 30 C40 6 90 4 112 60" stroke-dasharray="1 8" stroke-width="2.6"/>'
            '<g transform="translate(128 98) rotate(70) scale(.95) translate(-36 -36)">'
            '<path d="M66 6 L6 27 L33 39 L45 66 Z" fill="#fffdf8"/><path d="M66 6 L33 39 L45 66 Z" fill="#ebe6da"/>'
            '<path d="M66 6 L33 39"/></g></svg>')
