"""首页主视觉“掀开纸堆”：上半张是一幅彩色铅笔画的书桌（乱放的税票、小票、流水、手机里的 LINE、咖啡），
斜放在网页上；下半张就是网页本身（不另加底色和卡片框）。铅笔线条的小单（น้องใบ）一手扶着撑住卷角的大铅笔，
一手扒着卷边往里看，底下是已经整理好的账。

整张是一个内联 SVG（电脑看整张，手机上 site.js 只取左段，变成竖版），不加图片文件、不发请求；
画风是铅笔描边 + 平涂上色 + 排线阴影，线条抖动和铅笔颗粒用 SVG 滤镜。卷角几何在这里和 site.js 的 peel() 各算一次：
这里给没有脚本时的静态样子，脚本接管后随滚动卷起、每隔几秒拽一下；系统开了“减少动态效果”时静止。
"""
import html
import math
from urllib.parse import quote

W, H, HP = 1440, 900, 560          # 画布宽高、画面下沿
PROP_X, PROP = 300, 340             # 大铅笔顶住卷边的位置（x 固定，y 跟着卷边走）与铅笔长度
PEEK_X = 446                        # 小单另一只手扒住卷边的位置
GRIP = .52                          # 小单握在铅笔上的位置（从笔尖算起的比例）
HIP, TILT, SCALE = (362, 732), 6, 1.7   # 小单身体底边中点、后仰角度、放大倍数
GROUND = HIP[1] + 40 * SCALE
P0 = .3                             # 没有脚本时的卷起程度（0–1）
ROLL = ((.16, 2.4, .55), (.3, 1.8, .4), (.44, 1.4, .28))   # 卷筒明暗线：位置比例、粗细、深浅
ROT = -1.2                          # 画面在页面上微微斜放
VIEW, VIEW_SM = f"-40 -40 1520 {H + 40}", f"-24 -40 760 {H + 40}"   # 电脑整张；手机只取左段成竖版

INK, LINE, PAPER = "#2d2b27", "#3d362c", "#fffdf6"
HAND = "'Sriracha', 'Long Cang', cursive"
NOTE = ("ภาษีซื้อ ต.ค.", "ส่งก่อน 15 !!", "อย่าลืมสลิปโอน")   # 桌上便利贴是泰国办公室的实物，三种语言都不翻
OUT = f'stroke="{LINE}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"'


def esc(text):
    return html.escape(str(text), quote=True)


def n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


# ---------------- 卷角几何（与 site.js peel() 同一套公式） ----------------

def geom(p):
    lift, width = 120 + 90 * p, 700 + 260 * p
    t = 16 + lift * .2
    cx, cy = width * .42, HP - lift * .62
    curve = f"M{n(width)} {HP}Q{n(cx)} {n(cy)} 0 {n(HP - lift)}"
    clip = f"M0 0H{W}V{HP}H{n(width)}Q{n(cx)} {n(cy)} 0 {n(HP - lift)}Z"
    lip = f"M0 {n(HP - lift + t)}Q{n(cx)} {n(cy + t * .85)} {n(width)} {HP}"

    def lip_at(x0):                     # 卷边下沿上 x = x0 的点
        lo, hi = 0.0, 1.0
        for _ in range(24):
            s = (lo + hi) / 2
            x = (1 - s) ** 2 * width + 2 * s * (1 - s) * cx
            lo, hi = (s, hi) if x > x0 else (lo, s)
        return x0, (1 - s) ** 2 * HP + 2 * s * (1 - s) * (cy + t * .85) + s * s * (HP - lift + t)
    def roll(f):                        # 卷筒上顺着卷边走的明暗线：在折线与卷边之间按比例插值
        return f"M0 {n(HP - lift + f * t)}Q{n(cx)} {n(cy + .85 * f * t)} {n(width)} {HP}"
    return dict(curve=curve, clip=clip, band=curve + "L" + lip[1:] + "Z", lip=lip, roll=roll, top=lip_at(PROP_X), peek=lip_at(PEEK_X))


def rot(x, y):
    a = math.radians(TILT)
    x, y = x * SCALE, y * SCALE
    return HIP[0] + x * math.cos(a) - y * math.sin(a), HIP[1] + x * math.sin(a) + y * math.cos(a)


GRIP_SHOULDER, PEEK_SHOULDER = rot(-38, -42), rot(38, -48)


def pencil_pose(top):
    """铅笔顶在卷边上、笔尖着地并往左斜：卷得越高立得越直。返回旋转角和小单握住的点。"""
    hx, hy = top
    rise = GROUND - hy
    run = math.sqrt(max(PROP ** 2 - rise ** 2, 0))
    lean = math.degrees(math.atan2(run, rise))
    return lean, (hx - run * GRIP, GROUND - rise * GRIP)


def limb(a, b, bend):
    """一条铅笔画的胳膊：从肩到手，向 bend 一侧拱一点。"""
    (ax, ay), (bx, by) = a, b
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = by - ay, ax - bx
    d = math.hypot(dx, dy) or 1
    return f"M{n(ax)} {n(ay)}Q{n(mx + dx / d * bend)} {n(my + dy / d * bend)} {n(bx)} {n(by)}"


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
<pattern id="pz-hatch2" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(30)">
  <path d="M0 4.5 H9" stroke="#b39c78" stroke-width="1.6"/>
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
    """画面底下那张：整理好的账。"""
    lines = "".join(f'<path d="M0 {y} H{W}" stroke="#e3dccd" stroke-width="1.6"/>' for y in range(322, HP, 38))
    head = (t(48, 352, L["cols"][0], 17, "#9a917f") + t(150, 352, L["cols"][1], 17, "#9a917f")
            + t(520, 352, L["cols"][2], 17, "#9a917f", "end") + t(660, 352, L["cols"][3], 17, "#9a917f", "end"))
    rows = "".join(
        t(48, 390 + i * 38, d, 19) + t(150, 390 + i * 38, s, 19, "#2d2b27") + t(520, 390 + i * 38, v, 19, anchor="end")
        + t(660, 390 + i * 38, tot, 19, "#2d2b27", "end")
        + f'<circle cx="692" cy="{383 + i * 38}" r="11" fill="#43b36f" {OUT}/><path d="M686 {383 + i * 38} l4 4 l8 -9" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
        for i, (d, s, v, tot) in enumerate(L["rows"]))
    return (f'<rect width="{W}" height="{HP}" fill="{PAPER}"/>{lines}'
            f'<path d="M30 0 V{HP}" stroke="#f0a9a9" stroke-width="2"/>{head}{rows}')


# ---------------- 小单与大铅笔 ----------------

def pencil(top):
    """一支黄色大铅笔，橡皮头顶住卷边，笔尖着地。"""
    lean, _ = pencil_pose(top)
    L = PROP
    return (f'<g class="pz-prop" transform="translate({n(top[0])} {n(top[1])}) rotate({n(lean)})" color="{INK}" stroke="currentColor" '
            f'stroke-width="2.6" stroke-linejoin="round" filter="url(#pz-pencil)">'
            '<rect x="-11" y="-4" width="22" height="24" rx="6" fill="#ff9ba3"/><rect x="-11" y="18" width="22" height="12" fill="#cfd2d8"/>'
            '<path d="M-11 22 H11 M-11 26 H11" stroke-width="1.4"/>'
            f'<rect x="-11" y="30" width="22" height="{L - 70}" fill="#ffd84d"/><path d="M-3.5 34 V{L - 44}" stroke="#e6b62e" stroke-width="2.4"/>'
            f'<path d="M-11 {L - 40} L0 {L} L11 {L - 40} Z" fill="#f6dcb4"/><path d="M-3.6 {L - 13} L0 {L} L3.6 {L - 13} Z" fill="currentColor"/></g>')


def mascot(g):
    """铅笔线条的小单：左手扶着大铅笔，右手扒住卷边，仰头往里看。"""
    def zig():
        out, up = [], True
        for _ in range(8):
            out.append(f"l-9.75 {-8 if up else 8}")
            up = not up
        return " ".join(out)
    body = (f'<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V0 {zig()} Z" fill="{PAPER}"/>'
            '<path d="M-24 -38 H18 M-24 -26 H8" stroke="#bdb7aa" stroke-width="3"/>'
            '<rect x="2" y="-17" width="26" height="10" rx="3" fill="#ffd84d" stroke="currentColor" stroke-width="2.4"/>'
            '<ellipse cx="10" cy="-52" rx="4.5" ry="5.5" stroke-width="2.4"/>'
            '<svg class="mascot pz-face" x="-39" y="-100" width="78" height="60" viewBox="-39 -100 78 60" overflow="visible">'
            '<ellipse cx="-19" cy="-64" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<ellipse cx="25" cy="-65" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<g class="m-blink"><circle cx="-4" cy="-80" r="5" fill="currentColor" stroke="none"/>'
            '<circle cx="21" cy="-81" r="5" fill="currentColor" stroke="none"/></g></svg>')
    (ax, ay), (bx, by) = rot(-14, 0), rot(14, 0)
    k = SCALE
    gy = n(GROUND)
    legs = (f'<path d="M{n(ax)} {n(ay)} L{n(ax - 10 * k)} {n(ay + 22 * k)} L{n(ax - 6 * k)} {gy} h{n(-18 * k)}"/>'
            f'<path d="M{n(bx)} {n(by)} L{n(bx + 16 * k)} {n(by + 20 * k)} L{n(bx + 22 * k)} {gy} h{n(18 * k)}"/>')
    _, grip = pencil_pose(g["top"])
    peek = g["peek"]
    return (f'<g class="pz-pencil" color="{INK}" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none" filter="url(#pz-pencil)">'
            f'<path d="M{HIP[0] - 120} {gy} m0 4 h250 M{HIP[0] - 84} {gy} m0 11 h170 M{HIP[0] - 40} {gy} m0 18 h80" stroke-width="1.6" opacity=".35"/>'
            + legs
            + f'<g transform="translate({HIP[0]} {HIP[1]}) rotate({TILT}) scale({SCALE})" stroke-width="{n(3 / SCALE)}">{body}</g>'
            + f'<path class="pz-grip" d="{limb(GRIP_SHOULDER, grip, -18)}"/>'
            + f'<circle class="pz-grip-hand" cx="{n(grip[0])}" cy="{n(grip[1])}" r="9" fill="{PAPER}"/>'
            + f'<path class="pz-peek" d="{limb(PEEK_SHOULDER, peek, 16)}"/>'
            + f'<g class="pz-peek-hand" transform="translate({n(peek[0])} {n(peek[1])})">'
              f'<circle cy="2" r="9.5" fill="{PAPER}"/><path d="M-6 -6 q6 -6 12 0" stroke-width="2.2"/></g>'
            + '</g>')


def svg(L):
    g = geom(P0)
    data = (f'data-view="{VIEW}" data-view-sm="{VIEW_SM}" data-hp="{HP}" data-p0="{P0}" data-prop-x="{PROP_X}" data-prop="{PROP}" '
            f'data-peek-x="{PEEK_X}" data-ground="{n(GROUND)}" data-grip="{GRIP}" '
            f'data-shoulders="{",".join(n(v) for xy in (GRIP_SHOULDER, PEEK_SHOULDER) for v in xy)}"')
    return f"""<svg class="pz" viewBox="{VIEW}" {data} role="img" aria-label="{esc(L["alt"])}">
{DEFS}
<g transform="rotate({ROT} {W / 2} {HP / 2})">
<path d="M10 16 H{W + 12} V{HP + 12} H10 Z" fill="#e7dcc8"/>
<clipPath id="pz-clip"><path class="pz-clip" d="{g['clip']}"/></clipPath>
<clipPath id="pz-under"><rect width="{W}" height="{HP}"/></clipPath>
<g clip-path="url(#pz-under)">{under(L)}
<path class="pz-shade" d="{g['band']}" fill="url(#pz-hatch)" opacity=".55" transform="translate(6 18)"/></g>
<g clip-path="url(#pz-clip)"><g filter="url(#pz-wobble)">{photo()}</g>
<rect width="{W}" height="{HP}" filter="url(#pz-tex)" opacity=".5"/>
<path class="pz-bend" d="{g['curve']}" stroke="url(#pz-hatch2)" stroke-width="40" fill="none" opacity=".8"/>
<rect width="{W}" height="{HP}" fill="none" stroke="{LINE}" stroke-width="5" filter="url(#pz-pencil)"/></g>
<path class="pz-band" d="{g['band']}" fill="#fbf6ea"/>
{"".join(f'<path class="pz-roll" data-f="{f}" d="{g["roll"](f)}" fill="none" stroke="#9c8f77" stroke-width="{w}" opacity="{o}" stroke-linecap="round"/>' for f, w, o in ROLL)}
<g color="{LINE}" filter="url(#pz-pencil)" fill="none" stroke="currentColor" stroke-linecap="round">
<path class="pz-curve" d="{g['curve']}" stroke-width="2"/><path class="pz-lip" d="{g['lip']}" stroke-width="2.8"/></g>
{pencil(g['top'])}
{mascot(g)}
</g>
<text class="pz-caption" x="{HIP[0] + 10}" y="{n(GROUND + 66)}" font-family="{HAND}" font-size="30" fill="#3b3934" text-anchor="middle">{esc(L["caption"])}</text>
</svg>"""


def fonts(L):
    """手写字体：Sriracha（泰文 + 拉丁，Google 按字符集分片，只下用到的那片）；中文手写字很大，只按用到的几个字取子集。"""
    urls = ["https://fonts.googleapis.com/css2?family=Sriracha&display=swap"]
    cjk = "".join(sorted({c for c in L["caption"] if "一" <= c <= "鿿" or c in "，。？"}))
    if cjk:
        urls.append(f"https://fonts.googleapis.com/css2?family=Long+Cang&text={quote(cjk)}&display=swap")
    return urls


def doodle():
    """标题区到画面之间：随手一笔，绕一个小圈，末端是一架纸飞机——单据飞进系统。"""
    return ('<svg class="pz-doodle" viewBox="0 0 200 230" fill="none" stroke="#2d2b27" stroke-width="2.6" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false"><g filter="url(#pz-pencil)">'
            '<path d="M8 56 C40 22 100 12 130 30 C150 42 158 64 144 78 C130 92 104 84 104 64 C104 44 128 38 144 48 '
            'C164 60 170 88 164 114 C160 134 150 150 138 162"/>'
            '<g transform="translate(122 186) rotate(180) scale(.95) translate(-36 -36)">'
            '<path d="M66 6 L6 27 L33 39 L45 66 Z" fill="#fffdf6"/><path d="M66 6 L33 39 L45 66 Z" fill="#e9e1cf"/>'
            '<path d="M66 6 L33 39"/></g></g></svg>')
