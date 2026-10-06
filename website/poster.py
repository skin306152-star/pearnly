"""首页主视觉“卷起来”：一幅彩色铅笔画的书桌（乱放的税票、小票、流水、手机里的 LINE、咖啡）放在液态玻璃卡片里。
往下滑时卡片在屏幕上停住，铅笔线条的小单（น้องใบ）一手推、一手拿铅笔，迈着步子把这幅画从左往右卷成纸卷，
底下露出整理好的账；最后乱的都卷到了右边，小单直起身挥手，账上贴出“30/30 已入账”。

整张是一个内联 SVG（电脑看整张，手机上 site.js 只取左段），不加图片文件、不发请求；
画风是铅笔描边 + 平涂上色 + 排线阴影，线条抖动和铅笔颗粒用 SVG 滤镜。
卷到哪、纸卷多粗、小单每一步的腿和手，都由 state() 按进度 p 算出；site.js 的 peel() 用同一套公式逐帧重画。
这里给没有脚本时的静态样子；系统开了“减少动态效果”时也停在这一帧。
"""
import html
import math
from urllib.parse import quote

W, HP = 1440, 560                   # 画布宽、画面高
H = HP + 78                         # 下面留一行手写字
TOP = 30                            # 纸卷顶端的卷口会冒出画面上沿，viewBox 往上多留一点
P0 = .45                            # 没有脚本时（或减少动态效果时）卷到的程度（0–1）
RX = 26                             # 画面圆角
SCALE, GROUND = 1.75, 550            # 小单的放大倍数、脚踩的高度
STRIDE = 34                         # 纸卷每往前滚多少，小单迈半步
VIEW, VIEW_SM = f"0 {-TOP} {W} {H + TOP}", f"0 {-TOP} 780 {H + TOP}"   # 电脑整张；手机只取左段

INK, LINE, PAPER = "#2d2b27", "#3d362c", "#fffdf6"
HAND = "'Sriracha', 'Long Cang', cursive"
NOTE = ("ภาษีซื้อ ต.ค.", "ส่งก่อน 15 !!", "อย่าลืมสลิปโอน")   # 桌上便利贴是泰国办公室的实物，三种语言都不翻
OUT = f'stroke="{LINE}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"'


def esc(text):
    return html.escape(str(text), quote=True)


def n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


# ---------------- 动画状态（与 site.js peel() 同一套公式） ----------------

def state(p, width=W):
    """进度 p → 纸卷位置与粗细、小单的身体、两条腿、两只手。width 是看得见的画布宽（手机只看左段）。"""
    x = 150 + (width - 310) * p                # 纸卷中心
    d = 36 + 70 * p                            # 纸卷直径
    fin = clamp((p - .86) / .12)               # 最后一幕：停下、直起身、挥手
    ph = x / STRIDE
    walk = 1 - fin
    bob = -5 * abs(math.cos(ph)) * walk
    tilt = 18 - 22 * fin
    hip = (x - d / 2 - 88 - 34 * fin, 482 + bob)

    def rot(px, py):
        a = math.radians(tilt)
        px, py = px * SCALE, py * SCALE
        return hip[0] + px * math.cos(a) - py * math.sin(a), hip[1] + px * math.sin(a) + py * math.cos(a)
    swing = 22 * math.sin(ph) * walk
    legs = []
    for side, sw in ((-14, swing), (14, -swing)):
        hx, hy = rot(side, 0)
        fx = hx + sw + 6 * fin * (1 if side > 0 else -1)
        kx, ky = (hx + fx) / 2 + 9 * walk, (hy + GROUND) / 2 - 2
        legs.append((hx, hy, kx, ky, fx))
    push_sh, pen_sh = rot(36, -50), rot(-36, -46)
    push = (x - d / 2 - 2, 392 + bob)
    wave = rot(70, -128)
    hand = (push[0] + (wave[0] - push[0]) * fin, push[1] + (wave[1] - push[1]) * fin)
    pen = rot(-58, -24)
    return dict(x=x, d=d, fin=fin, hip=hip, tilt=tilt, legs=legs, push_sh=push_sh, hand=hand, pen_sh=pen_sh, pen=pen)


def limb(a, b, bend):
    """一条铅笔画的胳膊：从肩到手，向 bend 一侧拱一点。"""
    (ax, ay), (bx, by) = a, b
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = by - ay, ax - bx
    d = math.hypot(dx, dy) or 1
    return f"M{n(ax)} {n(ay)}Q{n(mx + dx / d * bend)} {n(my + dy / d * bend)} {n(bx)} {n(by)}"


def leg(l, side):
    hx, hy, kx, ky, fx = l
    return f"M{n(hx)} {n(hy)}L{n(kx)} {n(ky)}L{n(fx)} {GROUND}h{n(16 * side)}"


# ---------------- 滤镜与花纹 ----------------

DEFS = """<defs>
<filter id="pz-pencil" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency=".03" numOctaves="2" seed="5" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="2.6" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="pz-wobble" x="-2%" y="-2%" width="104%" height="104%">
  <feTurbulence type="fractalNoise" baseFrequency=".018" numOctaves="2" seed="9" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="3.2" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="pz-tex" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency=".9 .22" numOctaves="2" seed="4"/>
  <feColorMatrix values="0 0 0 0 .42  0 0 0 0 .32  0 0 0 0 .2  0 0 0 1.4 -.72"/>
</filter>
<filter id="pz-flat" x="-10%" y="-10%" width="125%" height="125%">
  <feOffset in="SourceAlpha" dx="8" dy="10" result="o"/>
  <feFlood flood-color="#dcc39a"/><feComposite in2="o" operator="in" result="s"/>
  <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<pattern id="pz-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
  <path d="M0 4 H8" stroke="#8d7f66" stroke-width="1.5"/>
</pattern>
</defs>"""


def obj(x, y, r, inner):
    """一个放在桌上的物件：外层加一块平涂的投影，内层再平移旋转。"""
    return f'<g filter="url(#pz-flat)"><g transform="translate({x} {y}) rotate({r})">{inner}</g></g>'


def t(x, y, text, size=12, fill="#4a443a", anchor="start"):
    return f'<text x="{x}" y="{y}" font-family="{HAND}" font-size="{size}" fill="{fill}" text-anchor="{anchor}">{esc(text)}</text>'


def scribble(x, y, w, color="#c4b9a5", width=2.2):
    """一行看不清的小字：一条随手的波浪线。"""
    seg = max(int(w / 14), 2)
    d = f"M{x} {y}" + "".join(f" q{w / seg / 2:.1f} {-2.4 if i % 2 else 2.4} {w / seg:.1f} 0" for i in range(seg))
    return f'<path d="{d}" stroke="{color}" stroke-width="{width}" fill="none" stroke-linecap="round"/>'


# ---------------- 桌上的东西 ----------------

def invoice(seller, logo, items, total, stamp=True):
    rows = "".join(t(22, 174 + i * 26, a, 12) + t(308, 174 + i * 26, b, 12, anchor="end")
                   + f'<path d="M20 {182 + i * 26} H310" stroke="#e6dfd1" stroke-width="1.6"/>' for i, (a, b) in enumerate(items))
    y = 174 + len(items) * 26 + 16
    st = (f'<g transform="translate(140 {y + 40}) rotate(-12)">'
          f'<rect width="156" height="46" rx="9" fill="none" stroke="#e0565b" stroke-width="3"/>'
          + t(78, 30, "ได้รับเงินแล้ว", 19, "#e0565b", "middle") + '</g>') if stamp else ""
    return (f'<rect width="330" height="440" fill="{PAPER}" {OUT}/>'
            f'<circle cx="36" cy="40" r="15" fill="{logo}" {OUT}/>'
            + t(60, 40, seller, 16, "#2d2b27") + scribble(60, 56, 140)
            + f'<rect x="196" y="74" width="116" height="40" rx="5" fill="none" {OUT}/>'
            + t(254, 100, "ใบกำกับภาษี", 15, "#2d2b27", "middle")
            + scribble(20, 86, 120) + scribble(20, 102, 150) + scribble(20, 118, 90)
            + '<rect x="18" y="138" width="294" height="22" fill="#efe8da" stroke="#d8cdb8" stroke-width="1.6"/>'
            + t(22, 154, "รายการ", 12, "#6b6356") + t(308, 154, "จำนวนเงิน", 12, "#6b6356", "end") + rows
            + t(190, y + 8, "รวมทั้งสิ้น", 14, "#2d2b27") + t(308, y + 8, total, 15, "#2d2b27", "end")
            + st
            + '<path d="M30 404 c14 -16 22 6 34 -6 s14 -14 22 2 s16 4 26 -8" stroke="#4a6fd1" stroke-width="2.2" fill="none" stroke-linecap="round"/>')


def receipt():
    edge = "".join(f"l7 {6 if i % 2 else -6}" for i in range(18))
    lines = [("ลาเต้เย็น", "65"), ("ครัวซองต์", "55"), ("น้ำดื่ม", "15"), ("ค่าส่ง", "40")]
    body = "".join(t(12, 132 + i * 22, a, 11) + t(114, 132 + i * 22, b, 11, anchor="end") for i, (a, b) in enumerate(lines))
    code = "".join(f'<path d="M{20 + i * 4.4} 332 v30" stroke="#4a443a" stroke-width="{1.2 + (i * 7 % 3) * .7}"/>' for i in range(20))
    return (f'<path d="M0 6 {edge} V414 {edge.replace("l7", "l-7")} Z" fill="#fbf8f0" {OUT}/>'
            + t(63, 42, "ร้านกาแฟ บ้านสวน", 13, "#2d2b27", "middle") + scribble(24, 58, 80) + scribble(30, 74, 66)
            + '<path d="M10 92 H118" stroke="#a89f8e" stroke-width="1.6" stroke-dasharray="4 4"/>' + body
            + '<path d="M10 236 H118" stroke="#a89f8e" stroke-width="1.6" stroke-dasharray="4 4"/>'
            + t(12, 262, "รวม", 14, "#2d2b27") + t(114, 262, "175.00", 14, "#2d2b27", "end")
            + t(63, 302, "ขอบคุณค่ะ", 12, "#6b6356", "middle") + code)


def statement():
    rows = [("01/10", "โอนเข้า", "12,500", True), ("02/10", "ค่าไฟฟ้า", "-3,745", False), ("02/10", "PromptPay", "-856", True),
            ("03/10", "ขนส่งด่วนไทย", "-321", False), ("04/10", "รับชำระ INV", "8,200", True), ("05/10", "ค่าธรรมเนียม", "-25", False)]
    body = ""
    for i, (d, a, b, mark) in enumerate(rows):
        y = 116 + i * 28
        if mark:
            body += f'<path d="M78 {y - 5} H{84 + len(a) * 8}" stroke="#ffe45c" stroke-width="15" stroke-linecap="round" opacity=".85"/>'
        body += (t(18, y, d, 12, "#6b6356") + t(80, y, a, 13, "#2d2b27") + t(342, y, b, 13, "#2d2b27" if "-" in b else "#2e8a55", "end")
                 + f'<path d="M14 {y + 9} H346" stroke="#e6dfd1" stroke-width="1.5"/>')
    ticks = "".join(f'<path d="M352 {104 + i * 56} l5 6 l10 -13" stroke="#e0565b" stroke-width="2.6" fill="none" stroke-linecap="round"/>' for i in range(3))
    return (f'<rect width="360" height="300" fill="{PAPER}" {OUT}/><rect width="360" height="60" fill="#5b86d6" {OUT}/>'
            + t(18, 34, "BANK STATEMENT", 18, "#fff") + scribble(18, 48, 150, "#c9dbff")
            + t(18, 86, "วันที่", 12, "#8a8170") + t(80, 86, "รายการ", 12, "#8a8170") + t(342, 86, "บาท", 12, "#8a8170", "end")
            + body + ticks)


def phone():
    bubbles = (
        f'<rect x="60" y="72" width="94" height="110" rx="14" fill="#3fcb73" {OUT}/>'
        + "".join(f'<rect x="{68 + (i % 2) * 42}" y="{80 + (i // 2) * 50}" width="36" height="44" rx="4" fill="{PAPER}" stroke="{LINE}" stroke-width="1.6"/>'
                  + scribble(73 + (i % 2) * 42, 94 + (i // 2) * 50, 24, "#c4b9a5", 1.8) + scribble(73 + (i % 2) * 42, 106 + (i // 2) * 50, 20, "#c4b9a5", 1.8)
                  for i in range(4))
        + f'<rect x="36" y="194" width="118" height="32" rx="15" fill="#3fcb73" {OUT}/>' + t(95, 215, "ใบเสร็จเดือนนี้ค่ะ", 12, "#fff", "middle")
        + f'<rect x="16" y="236" width="104" height="32" rx="15" fill="#fff" {OUT}/>' + t(68, 257, "รับแล้ว ขอบคุณค่ะ", 11.5, "#2d2b27", "middle")
        + f'<rect x="16" y="276" width="86" height="32" rx="15" fill="#fff" {OUT}/>' + t(59, 297, "✓ บันทึกแล้ว", 11.5, "#2e8a55", "middle"))
    return (f'<rect width="180" height="360" rx="30" fill="#3a3d45" {OUT}/>'
            '<g transform="translate(8 8)"><rect width="164" height="344" rx="23" fill="#d6e4f3"/>'
            '<path d="M0 23 a23 23 0 0 1 23 -23 H141 a23 23 0 0 1 23 23 V50 H0 Z" fill="#fbfaf6"/>'
            + t(82, 38, "สำนักงานบัญชี", 13, "#2d2b27", "middle") + bubbles
            + '<path d="M118 20 l30 30 M128 14 l28 28" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".7"/></g>'
            + '<rect x="64" y="16" width="52" height="14" rx="7" fill="#1f2025"/>')


def cup():
    return (f'<circle r="104" fill="#fbf9f4" {OUT}/><circle r="86" fill="none" stroke="#ddd3c2" stroke-width="2"/>'
            f'<rect x="58" y="-15" width="60" height="30" rx="15" fill="#fbf9f4" {OUT}/>'
            f'<circle r="72" fill="#fff" {OUT}/><circle r="58" fill="#8b5a35" {OUT}/>'
            '<circle r="50" fill="none" stroke="#c48a5a" stroke-width="6" opacity=".7"/>'
            '<path d="M-24 -38 q-14 10 -16 26" stroke="#fff" stroke-width="5" fill="none" stroke-linecap="round" opacity=".75"/>'
            '<path d="M-6 -12 q10 -10 18 0 q-4 12 -18 0 z" fill="#d9a678"/>')


def note(lines):
    return (f'<path d="M0 0 H152 V134 Q148 150 130 154 H0 Z" fill="#ffe066" {OUT}/>'
            f'<path d="M152 134 Q148 150 130 154 Q134 142 152 134 Z" fill="#f0c53a" {OUT}/>'
            + t(16, 46, lines[0], 22, "#2d2b27") + t(16, 86, lines[1], 20, "#e0565b")
            + '<path d="M16 98 q40 6 80 -2 q20 -3 36 2" stroke="#e0565b" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
            + t(16, 130, lines[2], 16, "#2d2b27"))


def pen():
    return (f'<path d="M0 -8 L-26 0 L0 8 Z" fill="#efe4cf" {OUT}/><path d="M-26 0 L-34 0" stroke="{LINE}" stroke-width="3" stroke-linecap="round"/>'
            f'<rect x="0" y="-9" width="250" height="18" rx="9" fill="#5b7fd8" {OUT}/>'
            f'<rect x="168" y="-10" width="82" height="20" rx="10" fill="#3a5bb0" {OUT}/><path d="M186 -16 H244" stroke="{LINE}" stroke-width="5" stroke-linecap="round"/>'
            '<path d="M14 -3 H160" stroke="#9fb6f0" stroke-width="3" stroke-linecap="round"/>')


def highlighter():
    return (f'<path d="M0 -10 L-22 -5 L-22 5 L0 10 Z" fill="#e8669a" {OUT}/>'
            f'<rect x="0" y="-15" width="190" height="30" rx="9" fill="#ff8fb4" {OUT}/>'
            f'<rect x="128" y="-16" width="62" height="32" rx="9" fill="#e8669a" {OUT}/>'
            '<path d="M12 -7 H118" stroke="#ffc2d6" stroke-width="4" stroke-linecap="round"/>')


def clip_wire():
    return f'<path d="M0 0 V46 a9 9 0 0 0 18 0 V8 a6 6 0 0 0 -12 0 V40" stroke="{LINE}" stroke-width="3" fill="none" stroke-linecap="round"/>'


def leaves():
    def leaf(x, y, r, s, fill):
        veins = "".join(f'<path d="M{40 + i * 36} {-6 - i} l24 {-20 + i * 2} M{40 + i * 36} {-6 - i} l22 {18 - i}" stroke="#2f6b3c" stroke-width="2" stroke-linecap="round"/>'
                        for i in range(5))
        return (f'<g transform="translate({x} {y}) rotate({r}) scale({s})">'
                f'<path d="M0 0 C40 -60 150 -70 230 -10 C150 40 50 50 0 0 Z" fill="{fill}" {OUT}/>'
                f'<path d="M0 0 C70 -12 150 -14 226 -10" stroke="#2f6b3c" stroke-width="2.4" fill="none" stroke-linecap="round"/>{veins}</g>')
    return leaf(-60, 40, 18, 1.1, "#7cc27a") + leaf(-40, 140, -8, .95, "#5fae66") + leaf(-30, -20, 52, .85, "#93cf86")


def desk():
    grain = "".join(
        f'<path d="M{x} {y} q{w / 4:.0f} -5 {w / 2:.0f} 0 t{w / 2:.0f} 0" stroke="#dcc49c" stroke-width="2.2" fill="none" stroke-linecap="round"/>'
        for x, y, w in [(40, 70, 300), (420, 46, 380), (900, 84, 260), (1180, 40, 220), (120, 150, 260), (560, 160, 300),
                        (980, 140, 340), (60, 250, 340), (480, 278, 240), (820, 240, 300), (1200, 300, 200), (140, 360, 280),
                        (640, 350, 320), (1040, 380, 300), (60, 470, 300), (520, 470, 260), (900, 520, 360), (1260, 500, 160)])
    knots = ('<ellipse cx="760" cy="300" rx="22" ry="8" fill="none" stroke="#d2b688" stroke-width="2.2"/>'
             '<ellipse cx="760" cy="300" rx="9" ry="3" fill="#dcc49c"/>'
             '<ellipse cx="1340" cy="420" rx="18" ry="7" fill="none" stroke="#d2b688" stroke-width="2.2"/>')
    seams = "".join(f'<path d="M0 {y} H{W}" stroke="#cfb185" stroke-width="2.6"/>' for y in (196, 402))
    return f'<rect width="{W}" height="{HP}" fill="#f4e6cb"/>' + grain + knots + seams


def photo():
    return (desk()
            + obj(880, 120, -5, statement())
            + obj(190, 40, -9, invoice("บจ. สยามซัพพลาย", "#ffa94d", [("กระดาษ A4 ×20", "2,400.00"), ("หมึกพิมพ์", "1,180.00"),
                                                                     ("แฟ้ม ×12", "540.00"), ("ค่าจัดส่ง", "120.00")], "4,540.80"))
            + obj(360, 92, 6, invoice("บจ. นมสดฟาร์มดี", "#4cc3c3", [("นมสด 2L ×12", "840.00"), ("โยเกิร์ต ×24", "552.00"),
                                                                     ("ค่าขนส่งเย็น", "180.00")], "1,682.04"))
            + '<path d="M520 250 a44 44 0 1 1 30 40" stroke="#c49a6b" stroke-width="3" fill="none" stroke-linecap="round" opacity=".7"/>'
            + obj(520, 48, 4, clip_wire())
            + obj(700, 28, 11, receipt())
            + obj(1090, 200, 13, phone())
            + obj(1300, 92, 0, cup())
            + obj(612, 330, -7, note(NOTE))
            + obj(980, 490, 17, pen())
            + obj(1220, 470, -14, highlighter())
            + obj(0, 0, 0, leaves()))


def under(L):
    """画面底下那张：整理好的账，卷开后整张露出来。"""
    rows = L["rows"]
    lines = "".join(f'<path d="M30 {y} H{W - 30}" stroke="#e8e1d2" stroke-width="1.6"/>' for y in range(158, HP - 20, 46))
    head = (t(60, 146, L["cols"][0], 19, "#9a917f") + t(170, 146, L["cols"][1], 19, "#9a917f")
            + t(600, 146, L["cols"][2], 19, "#9a917f", "end") + t(780, 146, L["cols"][3], 19, "#9a917f", "end"))
    body = "".join(
        t(60, 192 + i * 46, d, 21) + t(170, 192 + i * 46, s, 21, "#2d2b27") + t(600, 192 + i * 46, v, 21, anchor="end")
        + t(780, 192 + i * 46, tot, 21, "#2d2b27", "end")
        + f'<circle cx="816" cy="{185 + i * 46}" r="12" fill="#43b36f" {OUT}/><path d="M810 {185 + i * 46} l4 4 l8 -9" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
        for i, (d, s, v, tot) in enumerate(rows))
    return (f'<rect width="{W}" height="{HP}" fill="{PAPER}"/>{lines}<path d="M40 0 V{HP}" stroke="#f0a9a9" stroke-width="2"/>'
            + t(60, 84, L["ledger"], 34, "#2d2b27")
            + '<path d="M60 100 q120 10 260 -2" stroke="#ffd84d" stroke-width="10" fill="none" stroke-linecap="round" opacity=".8"/>'
            + head + body)


def roll(s):
    """纸卷：画布背面朝外卷成的一根卷，顶端露出螺旋卷口；按直径横向缩放（描边不跟着缩）。"""
    k = s["d"] / 100
    ns = 'vector-effect="non-scaling-stroke"'
    return (f'<rect class="pz-rshade" x="{n(s["x"] + s["d"] / 2 - 4)}" y="0" width="26" height="{HP}" fill="url(#pz-hatch)" opacity=".7"/>'
            f'<g class="pz-roll" transform="translate({n(s["x"])} 0) scale({n(k)} 1)" filter="url(#pz-pencil)">'
            f'<path d="M-50 0 V{HP} A50 16 0 0 0 50 {HP} V0 Z" fill="#f6efdf" stroke="{LINE}" stroke-width="2.6" {ns}/>'
            f'<path d="M-30 6 V{HP + 8} M14 10 V{HP + 12} M34 8 V{HP + 8}" stroke="#c9b993" stroke-width="2" {ns}/>'
            f'<path d="M-44 4 V{HP + 2}" stroke="#ddcfb1" stroke-width="7" {ns}/>'
            f'<ellipse rx="50" ry="16" fill="#fbf6ea" stroke="{LINE}" stroke-width="2.6" {ns}/>'
            f'<path d="M2 0 a6 3 0 1 1 9 1 a15 6 0 1 1 -24 -1 a26 10 0 1 1 42 2 a37 13 0 1 1 -62 -2" fill="none" stroke="#9c8f77" stroke-width="1.8" {ns}/></g>')


def pencil(at):
    hx, hy = at
    L, grip = 150, 64
    return (f'<g class="pz-pen" transform="translate({n(hx)} {n(hy)}) rotate(-24) translate(0 {-grip})" color="{INK}" stroke="currentColor" stroke-width="2.6" '
            f'stroke-linejoin="round" filter="url(#pz-pencil)">'
            f'<rect x="-8" y="0" width="16" height="18" rx="5" fill="#ff9ba3"/><rect x="-8" y="16" width="16" height="8" fill="#cfd2d8"/>'
            f'<rect x="-8" y="24" width="16" height="{L - 54}" fill="#ffd84d"/>'
            f'<path d="M-8 {L - 30} L0 {L} L8 {L - 30} Z" fill="#f6dcb4"/><path d="M-3 {L - 10} L0 {L} L3 {L - 10} Z" fill="currentColor"/></g>')


def mascot(s):
    """铅笔线条的小单：一手推纸卷、一手拿铅笔，身子前倾迈步；最后一幕直起身挥手。腿、手、身体都由 site.js 逐帧改。"""
    def zig():
        out, up = [], True
        for _ in range(8):
            out.append(f"l-9.75 {-8 if up else 8}")
            up = not up
        return " ".join(out)
    body = (f'<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V0 {zig()} Z" fill="{PAPER}"/>'
            '<path d="M-24 -38 H18 M-24 -26 H8" stroke="#bdb7aa" stroke-width="3"/>'
            '<rect x="2" y="-17" width="26" height="10" rx="3" fill="#ffd84d" stroke="currentColor" stroke-width="2.4"/>'
            '<path d="M-6 -54 q6 6 12 0" stroke-width="2.6"/>'
            '<svg class="mascot pz-face" x="-39" y="-100" width="78" height="60" viewBox="-39 -100 78 60" overflow="visible">'
            '<ellipse cx="-21" cy="-62" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<ellipse cx="23" cy="-62" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<g class="m-look"><g class="m-blink"><circle cx="-10" cy="-76" r="5" fill="currentColor" stroke="none"/>'
            '<circle cx="14" cy="-76" r="5" fill="currentColor" stroke="none"/></g></g></svg>')
    hip = s["hip"]
    pen = f'color="{INK}" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none" filter="url(#pz-pencil)"'
    return (f'<g class="pz-mascot">'
            f'<g {pen}><path class="pz-leg" d="{leg(s["legs"][0], -1)}"/><path class="pz-leg" d="{leg(s["legs"][1], 1)}"/>'
            f'<g class="pz-body" transform="translate({n(hip[0])} {n(hip[1])}) rotate({n(s["tilt"])}) scale({SCALE})" stroke-width="{n(3 / SCALE)}">{body}</g>'
            f'<path class="pz-arm-pen" d="{limb(s["pen_sh"], s["pen"], -12)}"/></g>'
            + pencil(s["pen"])
            + f'<g {pen}><circle class="pz-hand-pen" cx="{n(s["pen"][0])}" cy="{n(s["pen"][1])}" r="8.5" fill="{PAPER}"/>'
            f'<path class="pz-arm-push" d="{limb(s["push_sh"], s["hand"], 14)}"/>'
            f'<circle class="pz-hand-push" cx="{n(s["hand"][0])}" cy="{n(s["hand"][1])}" r="8.5" fill="{PAPER}"/></g></g>')


def svg(L):
    s = state(P0)
    data = f'data-view="{VIEW}" data-view-sm="{VIEW_SM}" data-p0="{P0}"'
    done = (f'<g class="pz-done" opacity="{n(s["fin"])}" transform="translate(860 64) rotate(-8)" filter="url(#pz-pencil)">'
            f'<rect x="-6" y="-6" width="232" height="66" rx="16" fill="#43b36f" {OUT}/>'
            + t(110, 40, "✓ 30/30 " + L["saved"], 26, "#fff", "middle") + '</g>')
    return f"""<svg class="pz" viewBox="{VIEW}" {data} role="img" aria-label="{esc(L["alt"])}">
{DEFS}
<clipPath id="pz-frame"><rect class="pz-frame" width="{W}" height="{HP}" rx="{RX}"/></clipPath>
<clipPath id="pz-clip"><rect class="pz-clip" x="{n(s["x"])}" width="{W}" height="{HP}"/></clipPath>
<g clip-path="url(#pz-frame)">
{under(L)}{done}
<g clip-path="url(#pz-clip)"><g filter="url(#pz-wobble)">{photo()}</g>
<rect width="{W}" height="{HP}" filter="url(#pz-tex)" opacity=".5"/></g>
</g>
{roll(s)}
{mascot(s)}
<text class="pz-caption" x="44" y="{HP + 56}" font-family="{HAND}" font-size="30" fill="#3b3934">{esc(L["caption"])}</text>
</svg>"""


def fonts(L):
    """手写字体：Sriracha（泰文 + 拉丁，Google 按字符集分片，只下用到的那片）；中文手写字很大，只按用到的几个字取子集。"""
    urls = ["https://fonts.googleapis.com/css2?family=Sriracha&display=swap"]
    cjk = "".join(sorted({c for c in L["caption"] if "\u4e00" <= c <= "\u9fff" or c in "，。？"}))
    if cjk:
        urls.append(f"https://fonts.googleapis.com/css2?family=Long+Cang&text={quote(cjk)}&display=swap")
    return urls


def doodle():
    """标题区到卡片之间：一笔黄色记号笔，绕一个小圈落进卡片（和标题下的荧光笔同色）。"""
    return ('<svg class="pz-doodle" viewBox="370 100 300 310" fill="none" stroke="#ffd84d" stroke-width="9" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            '<path d="M385 143 C430 115 520 112 568 140 C600 158 625 190 615 225 C605 252 560 258 535 240 C510 222 512 180 545 160 '
            'C575 142 625 170 640 200 C652 228 650 280 638 310 C628 335 608 362 582 390"/>'
            '<path d="M585 356 L582 390 L614 382"/></svg>')
