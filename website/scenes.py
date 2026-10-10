"""首页三段产品演示的手绘版：和首页主视觉同一套画风（铅笔描边 + 平涂 + 手写字），小单在“现实”那一半干活。

每段演示是左右两张内联 SVG（左“现实”、右“系统里”，手机上上下叠），跨两边的东西（飞过去的金额、配对的线）
仍用 site.js demo() 的通用时间轴：data-at 到点加/去类，class="fly" 在两点之间飞，data-type 逐字写出。
SVG 里的动作全是 CSS 类驱动（见 style.css「手绘演示」一节）；系统开了“减少动态效果”时直接显示终态。
样例数据全是虚构。
"""
from poster import esc, n, INK, LINE, PAPER, HAND, OUT

PW, PH = 520, 520                   # 每半边画布
PEN = f'color="{INK}" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"'

DEFS = ('<svg class="hd-defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>'
        '<filter id="hd-pencil" x="-5%" y="-5%" width="110%" height="110%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".03" numOctaves="2" seed="5" result="t"/>'
        '<feDisplacementMap in="SourceGraphic" in2="t" scale="2.4" xChannelSelector="R" yChannelSelector="G"/></filter>'
        '</defs></svg>')


def t(x, y, text, size=17, fill="#4a443a", anchor="start", attrs=""):
    return (f'<text x="{n(x)}" y="{n(y)}" font-family="{HAND}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"{attrs}>'
            f'{esc(text)}</text>')


def typed(x, y, text, at, size=17, fill="#2d2b27", anchor="start"):
    """到点逐字写出来的手写字。"""
    return t(x, y, "", size, fill, anchor, f' data-type="{esc(text)}" data-at="{at}"')


def tick(x, y, cls="hd-tick", at="", color="#2e8a55", w=3.4):
    """手画的对勾，到点一笔画出来。"""
    data = f' data-at="{at}"' if at else ""
    return (f'<path class="{cls}" pathLength="1"{data} d="M{n(x - 9)} {n(y - 1)} q4 3 7 9 q6 -14 15 -20" stroke="{color}" '
            f'stroke-width="{w}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')


def label(text):
    """每半边左上角的小标题（现实 / 系统里），手写加一道荧光笔。"""
    return (t(28, 44, text, 21, "#8a8170")
            + '<path d="M26 52 q40 5 84 -1" stroke="#ffd84d" stroke-width="7" fill="none" stroke-linecap="round" opacity=".7"/>')


def panel(cls, inner, extra=""):
    return (f'<div class="{cls}"><svg class="hd" viewBox="0 0 {PW} {PH}" aria-hidden="true" focusable="false"{extra}>'
            f'<g filter="url(#hd-pencil)">{inner}</g></svg></div>')


# ---------------- 小单 ----------------

def zigzag(top, half=39):
    """纸身：圆角上沿 + 右边下来 + 锯齿底边（从右往左）。"""
    zig, up = [], True
    for _ in range(8):
        zig.append(f"l{n(-half / 4)} {-8 if up else 8}")
        up = not up
    return f"M{-half} {top + 12} Q{-half} {top} {-half + 12} {top} H{half - 12} Q{half} {top} {half} {top + 12} V0 {' '.join(zig)} Z"


def face(ex, ey, r=5, gap=24):
    """两点眼 + 腮红 + 笑嘴；ex 是左眼的 x。眼睛包在 .m-look 里，跟着鼠标转（site.js mascots()）。"""
    cx = ex + gap / 2
    return ('<g class="m-look"><g class="hm-eyes">'
            f'<circle cx="{n(ex)}" cy="{n(ey)}" r="{r}" fill="currentColor" stroke="none"/>'
            f'<circle cx="{n(ex + gap)}" cy="{n(ey)}" r="{r}" fill="currentColor" stroke="none"/></g></g>'
            f'<ellipse cx="{n(ex - 11)}" cy="{n(ey + 14)}" rx="7.5" ry="4.2" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            f'<ellipse cx="{n(ex + gap + 11)}" cy="{n(ey + 14)}" rx="7.5" ry="4.2" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            f'<path class="hm-mouth" d="M{n(cx - 6)} {n(ey + 22)} q6 6 12 0" stroke-width="2.6"/>')


# 每个产品的小单穿不同的衣服（与 website/mascot.py 里的产品形象一致，这里是铅笔画版）：
#   cowork：头顶一道黄条、戴圆眼镜、身上印着金额（会计）
#   dms：身体就是一张蓝色身份证，举着一台黑色小相机
#   ai（Tomo）：一朵云，头顶一颗小星光
#   erp：戴黄色小店遮阳帽，身上一段条码带红色扫描线，提着黄色购物篮
TOMO_SPARK = "M30 -128 Q32 -119 40 -117 Q32 -115 30 -106 Q28 -115 20 -117 Q28 -119 30 -128 Z"   # Tomo 头顶的小星光
TOMO_CLOUD = "M-40 0 H40 Q62 0 60 -22 Q74 -44 52 -60 Q52 -92 20 -90 Q4 -112 -20 -98 Q-46 -102 -50 -74 Q-70 -62 -60 -40 Q-72 -14 -50 -2 Z"   # 对话页欢迎处的云朵身体（scripts/build-assistant-brand.py）


def costume(kind):
    if kind == "mrerp":   # MR.ERP：蓝色大账本，书脊深蓝、右边一排彩色书签、白框眼镜、黄领带，封面一块 MR.ERP 名牌
        tabs = "".join(f'<rect x="40" y="{-96 + i * 18}" width="7" height="13" rx="2" fill="{c}" stroke-width="1.6"/>'
                       for i, c in enumerate(("#ffd84d", "#ff9ba3", "#9fd3b0", "#c9dcee", "#ffd84d")))
        body = (tabs + '<rect x="-42" y="-104" width="84" height="104" rx="9" fill="#2f5d8a"/>'
                '<path d="M-31 -104 V0" stroke-width="2.4"/><rect x="-42" y="-104" width="11" height="104" rx="6" fill="#244a6f"/>'
                '<rect x="-20" y="-94" width="54" height="16" rx="4" fill="#fffdf6" stroke-width="2"/>'
                + t(7, -82, "MR.ERP", 10, "#2d2b27", "middle", ' stroke="none" font-weight="700"')
                # 镜框不动，只有眼珠跟着鼠标转、会眨眼（.m-look 只包眼珠）
                + '<rect x="-14" y="-70" width="16" height="13" rx="4" fill="none" stroke="#fffdf6" stroke-width="2.6"/>'
                '<rect x="8" y="-70" width="16" height="13" rx="4" fill="none" stroke="#fffdf6" stroke-width="2.6"/>'
                '<path d="M2 -63 h6" stroke="#fffdf6" stroke-width="2.6"/>'
                '<g class="m-look"><g class="hm-eyes">'
                '<circle cx="-6" cy="-63.5" r="2.8" fill="#fffdf6" stroke="none"/><circle cx="16" cy="-63.5" r="2.8" fill="#fffdf6" stroke="none"/></g></g>'
                '<path class="hm-mouth" d="M0 -48 q5 5 10 0" stroke="#fffdf6" stroke-width="2.4"/>'
                '<path d="M1 -40 h8 l-2 6 l4 22 l-6 7 l-6 -7 l4 -22 Z" fill="#ffd84d" stroke-width="2"/>')
        return body, (-40, -52), (40, -52), 16, 50
    if kind == "ai":    # Tomo：一朵软软的云，头顶冒一颗会闪的小星光（在想事情），空着手；
        # 平时是 .hm-calm（眼睛跟着鼠标、会眨眼），笑起来换成 .hm-laugh（眯眼 + 张嘴笑），由 CSS 定时 / 悬停 / .hm-laughing 切换
        body = (f'<g class="hm-spark"><path d="{TOMO_SPARK}" fill="#ffd84d" stroke-width="2"/></g>'
                f'<path d="{TOMO_CLOUD}" fill="#eef3fb"/>'
                '<g class="hm-calm">' + face(-12, -62, 4.4, 24) + '</g>'
                '<g class="hm-laugh"><path d="M-18 -60 q6 -8 12 0 M6 -60 q6 -8 12 0" stroke-width="2.8"/>'
                '<ellipse cx="-23" cy="-48" rx="7.5" ry="4.2" fill="#ff9ba3" stroke="none" opacity=".85"/>'
                '<ellipse cx="23" cy="-48" rx="7.5" ry="4.2" fill="#ff9ba3" stroke="none" opacity=".85"/>'
                '<path d="M-9 -45 H9 Q9 -33 0 -33 Q-9 -33 -9 -45 Z" fill="#7a3b3b" stroke-width="2.4"/>'
                '<path d="M-5 -36.5 Q0 -40 5 -36.5 Q3 -33.6 0 -33.6 Q-3 -33.6 -5 -36.5 Z" fill="#ff9ba3" stroke="none"/></g>')
        return body, (-58, -34), (62, -30), 16, 52
    if kind == "cowork":
        body = (f'<path d="{zigzag(-100)}" fill="{PAPER}"/>'
                '<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V-86 H-39 Z" fill="#ffd84d"/>'
                + face(-10, -72, 4.2)
                + '<circle cx="-10" cy="-72" r="9.5" fill="none" stroke-width="2.4"/><circle cx="14" cy="-72" r="9.5" fill="none" stroke-width="2.4"/>'
                '<path d="M-0.5 -72 h5" stroke-width="2.4"/>'
                '<path d="M-24 -37 H18 M-24 -28 H6" stroke="#bdb7aa" stroke-width="3"/>'
                + t(29, -16, "2,889.00", 11.5, "#2d2b27", "end", ' stroke="none"'))
        return body, (-38, -50), (38, -50), 14, 46
    if kind == "dms":
        body = ('<rect x="-56" y="-72" width="112" height="72" rx="12" fill="#eaf3fb"/>'
                '<path d="M-52 -58 H52" stroke-width="2" opacity=".25"/>'
                + face(-36, -44, 4.4, 18)
                + '<path d="M6 -48 H40 M6 -37 H34 M6 -26 H28" stroke="#9fb6cb" stroke-width="3.6"/>')
        return body, (-56, -36), (56, -40), 22, 58
    if kind == "erp":
        bars = "".join(f"M{x} -24 V-10 " for x in (-26, -22, -19, -14, -11, -6, -3, 2, 6, 10, 16, 20, 24))
        body = (f'<path d="{zigzag(-80)}" fill="{PAPER}"/>'
                '<path d="M-44 -78 Q0 -120 44 -78 Z" fill="#ffd84d"/><path d="M-50 -78 H50" stroke-width="2.8"/>'
                + face(-10, -58, 4.2)
                + f'<path d="{bars}" stroke-width="1.8"/>'
                '<g class="hm-laser"><path d="M-32 -17 H32" stroke="#e5484d" stroke-width="2.4"/>'
                '<path d="M-32 -17 H32" stroke="#e5484d" stroke-width="9" opacity=".2"/></g>')
        return body, (-38, -42), (38, -42), 14, 46
    body = (f'<path d="{zigzag(-100)}" fill="{PAPER}"/>'
            '<path d="M-24 -38 H18 M-24 -26 H8" stroke="#bdb7aa" stroke-width="3"/>'
            '<rect x="2" y="-17" width="26" height="10" rx="3" fill="#ffd84d" stroke-width="2.4"/>' + face(-10, -76))
    return body, (-38, -50), (38, -50), 14, 46


def prop_svg(kind):
    """右手拿的东西，画在手的位置 (20, 36)。"""
    if kind == "camera":                      # DMS：黑色小相机，黄色镜头
        return ('<g transform="translate(26 14) rotate(-12)" stroke-width="2.6">'
                '<rect x="-20" y="-15" width="40" height="28" rx="7" fill="#3d362c"/><rect x="-12" y="-21" width="12" height="7" rx="2" fill="#3d362c"/>'
                '<circle cx="3" cy="-1" r="9" fill="#ffd84d"/><circle cx="3" cy="-1" r="3.5" fill="#fffdf6" stroke="none"/>'
                '<g class="hm-flash" stroke="#f0b400" stroke-width="3"><path d="M24 -24 l9 -9 M30 -10 h12 M16 -30 v-12"/></g></g>')
    if kind == "none":                        # Tomo：空着手
        return ""
    if kind == "stamp":                       # MR.ERP：审批用的橡皮章（红色章面朝下）
        return ('<g transform="translate(20 30)" stroke-width="2.6">'
                '<path d="M-5 -14 Q-5 -22 0 -22 Q5 -22 5 -14 Z" fill="#c49a6b"/><rect x="-3" y="-14" width="6" height="10" fill="#c49a6b"/>'
                '<rect x="-11" y="-4" width="22" height="9" rx="2" fill="#3d362c"/><rect x="-11" y="5" width="22" height="4" rx="1" fill="#e0565b"/></g>')
    if kind == "basket":                      # ERP：黄色购物篮
        return ('<g transform="translate(22 46)" stroke-width="2.6">'
                '<path d="M-12 -12 Q0 -32 12 -12" fill="none"/>'
                '<path d="M-20 -12 H20 L15 16 H-15 Z" fill="#ffd84d"/><path d="M-8 -6 V11 M0 -6 V11 M8 -6 V11" stroke-width="1.8" opacity=".55"/></g>')
    return ('<g transform="translate(20 36) rotate(28) translate(0 -58)" stroke-width="2.6">'
            '<rect x="-7" y="0" width="14" height="16" rx="4" fill="#ff9ba3"/><rect x="-7" y="14" width="14" height="7" fill="#cfd2d8"/>'
            '<rect x="-7" y="21" width="14" height="74" fill="#ffd84d"/>'
            '<path d="M-7 95 L0 120 L7 95 Z" fill="#f6dcb4"/><path d="M-2.6 112 L0 120 L2.6 112 Z" fill="currentColor"/></g>')


def mascot(x, y, k=1.0, data="", prop="pencil", kind="base", flip=False):
    """铅笔线条的小单（髋部在 x,y），穿各产品自己的衣服：左手可以挥，右手拿道具，可以抬起来打勾、拍照。

    结构：.hm（位置）> .hm-move（走动）> .hm-hop（跳）> 腿 .hm-leg、身体、左臂 .hm-wave、右臂 .hm-arm（带道具）。
    关节都用 translate 包一层，CSS 只在局部原点 (0,0) 上旋转。
    """
    body, (lx, ly), (rx, ry), hip, shadow = costume(kind)
    # 每个小人都穿一双蓝色小鞋（和首页主视觉的小单同一双），鞋跟在腿下、鞋尖朝外
    shoe = lambda sd: (f'<g transform="translate({4 * sd} 41) scale({sd} 1)"><path d="M-6 0 Q-6 -8 4 -8 Q15 -8 16 0 Z" fill="#5b86d6" stroke-width="2.4"/>'
                       '<path d="M-6 0 H16" stroke-width="2.4"/></g>')
    legs = (f'<g transform="translate({-hip} 0)"><g class="hm-leg l"><path d="M0 0 L-4 34"/>{shoe(-1)}</g></g>'
            f'<g transform="translate({hip} 0)"><g class="hm-leg r"><path d="M0 0 L4 34"/>{shoe(1)}</g></g>')
    wave = (f'<g transform="translate({lx} {ly})"><g class="hm-wave"><path d="M0 0 Q-22 12 -24 36"/>'
            f'<circle cx="-24" cy="36" r="8.5" fill="{PAPER}"/></g></g>')
    arm = (f'<g transform="translate({rx} {ry})"><g class="hm-arm"><path d="M0 0 Q12 16 20 36"/>{prop_svg(prop)}'
           f'<circle cx="20" cy="36" r="9" fill="{PAPER}"/></g></g>')
    return (f'<g class="hm hm-{kind}" transform="translate({n(x)} {n(y)}) scale({-k if flip else k} {k})"{data}><g class="hm-move"><g class="hm-hop" {PEN} stroke-width="{n(3 / k)}">'
            f'<ellipse class="hm-shadow" cx="0" cy="41" rx="{shadow}" ry="7" fill="#3a2a14" stroke="none" opacity=".14"/>'
            f'{legs}{wave}<g>{body}</g>{arm}</g></g></g>')


def flyer(text, src, dst, at, cls=""):
    return f'<span class="fly hd-fly {cls}" data-from="{src}" data-to="{dst}" data-at="{at}">{esc(text)}</span>'


# ---------------- Cowork：银行流水对上税票 → 推进 ERP → 回读一致 ----------------

def cowork(L):
    rows = [("27/09", "โอนออก", "2,889.00", 0), ("27/09", "ค่าธรรมเนียม", "15.00", None), ("28/09", "โอนออก", "856.00", 1),
            ("28/09", "รับโอน", "12,400.00", None), ("29/09", "โอนออก", "321.00", 2)]
    invs = [("บจ. ตัวอย่างกาแฟ", "2,889.00", "#ffa94d"), ("ร้านวัสดุภัณฑ์ทวีผล", "856.00", "#4cc3c3"),
            ("บจ. ขนส่งด่วนไทย", "321.00", "#b48cf0")]
    T = [800, 2300, 3800]                 # 三张税票各自被读出来的时刻
    row_of = {0: 0, 1: 2, 2: 4}           # 第 i 张税票对上流水第几行
    match = {r: i for i, r in row_of.items()}

    # ---- 现实：一张银行流水，小单站在右下角拿着铅笔，对上一行就抬笔打一个勾
    sx, sy = 34, 78
    st = ""
    for r, (d, desc, amt, _m) in enumerate(rows):
        y = 112 + r * 50
        i = match.get(r)
        hl = (f'<path class="hd-hl" pathLength="1" data-at="{T[i] + 1000}" d="M10 {y - 6} Q150 {y - 9} 296 {y - 5}" '
              f'stroke="#ffe066" stroke-width="26" fill="none" stroke-linecap="round" opacity=".85"/>') if i is not None else ""
        tk = tick(318, y - 4, at=f"{T[i] + 1250}") if i is not None else ""
        st += (f'<g id="cw-st{r}">{hl}{t(18, y, d, 17, "#6b6356")}{t(84, y, desc, 18, "#2d2b27")}{t(290, y, amt, 18, "#2d2b27", "end")}{tk}</g>'
               f'<path d="M12 {y + 17} Q150 {y + 15} 300 {y + 18}" stroke="#e4dccb" stroke-width="1.6" fill="none"/>')
    statement = (f'<g transform="translate({sx} {sy}) rotate(-1.5)">'
                 f'<path d="M0 4 Q170 -2 336 3 L338 338 Q170 344 2 340 Z" fill="{PAPER}" {OUT}/>'
                 f'<path d="M0 4 Q170 -2 336 3 L336 58 Q170 54 0 60 Z" fill="#dce8fb" {OUT}/>'
                 + t(18, 40, L["stmt"], 22, "#2d2b27") + t(320, 40, "ก.ย. 2569", 16, "#5b7fd8", "end")
                 + st + '</g>')
    raise_ = ",".join(f"{T[i] + 1050}:hm-jot,{T[i] + 1600}:-hm-jot" for i in range(3))
    mm = mascot(414, 420, 1.25, f' data-at="{raise_},9100:hm-hopping,9100:hm-cheer"', kind="cowork")
    real = panel("sc-real", label(L["real"]) + statement + mm)

    # ---- 系统里：上面三张读出来的税票，下面一本 ERP 进项日记账
    cards = ""
    for i, (name, amt, logo) in enumerate(invs):
        x, r = 24 + i * 162, (-3, 2, -1)[i]
        cards += (f'<g transform="translate({x} 92) rotate({r})"><g class="hd-inv" id="cw-inv{i}" '
                  f'data-at="{T[i]}:on,{T[i]}:scan,{T[i] + 900}:-scan,{T[i] + 900}:read,{6000 + i * 160}:hd-sent">'
                  f'<path d="M0 2 Q74 -2 150 1 L151 118 Q74 121 1 119 Z" fill="{PAPER}" {OUT}/>'
                  f'<circle cx="20" cy="22" r="9" fill="{logo}" {OUT}/>' + t(36, 27, "ใบกำกับภาษี", 13, "#8a8170")
                  + t(12, 58, name, 14, "#2d2b27") + t(138, 96, amt, 19, "#2d2b27", "end")
                  + '<path d="M12 70 q30 -4 60 0 t60 0" stroke="#d9cfba" stroke-width="2" fill="none"/>'
                  + tick(130, 26, cls="hd-tick hd-cardtick")
                  + '<rect class="hd-scan" x="4" y="6" width="143" height="10" rx="5" fill="#5b7fd8" opacity=".35" stroke="none"/>'
                  + f'<circle id="cw-a{i}" cx="75" cy="121" r="1" fill="none" stroke="none"/></g></g>')
    dots = "".join(f'<circle class="hd-dot" cx="{430 + i * 20}" cy="76" r="6.5" fill="{PAPER}" stroke-width="2.2" stroke="{LINE}" data-at="{T[i] + 1250}"/>'
                   for i in range(3))
    ledger_rows = ""
    for i, ((name, amt, _l), d) in enumerate(zip(invs, ("27/09", "28/09", "29/09"))):
        y = 352 + i * 34
        ledger_rows += (f'<g id="cw-er{i}"><rect x="30" y="{y - 24}" width="430" height="32" fill="none" stroke="none"/>{typed(34, y, d, 6400 + i * 220, 16, "#6b6356")}{typed(96, y, name, 6450 + i * 220, 16)}'
                        f'{typed(410, y, amt, 6550 + i * 220, 17, anchor="end")}{tick(436, y - 5, at=f"{7300 + i * 140}")}</g>'
                        f'<path d="M30 {y + 12} Q240 {y + 10} 460 {y + 13}" stroke="#e4dccb" stroke-width="1.5" fill="none"/>')
    rings = "".join(f'<path d="M{60 + i * 56} 268 v-14 a8 8 0 0 1 16 0 v14" stroke-width="2.6" stroke="{LINE}" fill="none"/>' for i in range(8))
    ledger = (f'<g id="cw-ledger"><path d="M16 270 Q250 264 484 270 L486 452 Q250 458 14 452 Z" fill="#fbf3df" {OUT}/>{rings}'
              + t(34, 312, L["erp"], 20, "#2d2b27")
              + f'<g class="hd-chip" data-at="7700"><rect x="390" y="292" width="76" height="28" rx="14" fill="#e9f4ec" {OUT}/>'
              + t(428, 312, L["readback"], 15, "#2e8a55", "middle") + '</g>'
              + '<path d="M32 322 q90 6 190 -2" stroke="#ffd84d" stroke-width="8" fill="none" stroke-linecap="round" opacity=".7"/>'
              + ledger_rows
              + f'<g class="hd-lens" data-at="7700"><circle cx="0" cy="0" r="22" fill="#ffffff" fill-opacity=".35" stroke-width="3" stroke="{LINE}"/>'
              + f'<path d="M15 16 L32 33" stroke-width="7" stroke="{LINE}" stroke-linecap="round"/></g></g>')
    stamp = (f'<g transform="translate(262 470) rotate(-4)"><g class="hd-stamp" data-at="8900">'
             f'<rect x="-190" y="-22" width="380" height="44" rx="12" fill="#43b36f" {OUT}/>'
             + t(0, 7, L["cowork_done"], 15, "#fff", "middle") + '</g></g>')
    sys_ = panel("sc-sys", label(L["system"]) + t(28, 82, L["invoices"], 18, "#2d2b27") + dots + cards + ledger + stamp)

    # ---- 跨两边：税票金额飞到对上的那一行，配对线（连到税票下沿，不横穿别的税票）；税票飞进日记账
    flies = "".join(flyer(a, f"#cw-inv{i}", f"#cw-st{row_of[i]}", T[i] + 300) for i, (_n, a, _l) in enumerate(invs))
    # 第三条线要从小单和 ERP 日记账背后穿过去：单独放一层，压在两栏的画（小单、日记账）底下、底色上面
    threads = ["".join(f'<path class="thread hd-thread" pathLength="1" data-from="#cw-st{row_of[i]}" data-to="#cw-a{i}" data-to-top="#cw-inv{i}" data-at="{T[i] + 800},5800:-on"/>'
                       for i in group) for group in ((0, 1), (2,))]
    flies += f'<svg class="threads" aria-hidden="true">{threads[0]}</svg><svg class="threads hd-under" aria-hidden="true">{threads[1]}</svg>'
    flies += "".join(flyer(a, f"#cw-inv{i}", f"#cw-er{i}", 6000 + i * 160, "doc") for i, (_n, a, _l) in enumerate(invs))
    return f'<div class="scene hd-scene" data-scene="cowork" data-total="11200">{real}{sys_}{flies}</div>'


# ---------------- DMS：LINE 里收到身份证 → 读成客户资料 → 查老客户 → 订车单，回 LINE 报好 ----------------

def car(x, y):
    """订车单角上的一辆小车。"""
    return (f'<g transform="translate({x} {y})" stroke-width="2.4">'
            '<path d="M4 36 Q2 22 14 20 L26 6 Q30 2 38 2 H60 Q68 2 72 8 L82 20 Q94 22 92 36 Z" fill="#ff8f8f"/>'
            '<path d="M28 18 L36 8 H48 V18 Z M54 18 V8 H62 Q66 8 68 12 L72 18 Z" fill="#dbeafb"/>'
            '<circle cx="24" cy="38" r="8" fill="#3d362c"/><circle cx="72" cy="38" r="8" fill="#3d362c"/>'
            '<circle cx="24" cy="38" r="3" fill="#fffdf6" stroke="none"/><circle cx="72" cy="38" r="3" fill="#fffdf6" stroke="none"/></g>')


def dms(L):
    fields = [("name", L["name"], "นาย สมชาย ใจดี", 1800), ("id_no", L["id_no"], "1-1234-xxxxx-xx-1", 2250),
              ("birth", L["birth"], "12/03/2528", 2700), ("address", L["address"], "99 ถ.ตัวอย่าง อ.เมือง จ.เชียงใหม่", 3150),
              ("phone", L["phone"], "089-999-1234", 4400)]

    # ---- 现实：销售的手机里，LINE 发来身份证照片和电话；小单拿相机拍一下（读卡）
    card = (f'<g id="dm-card"><rect x="62" y="80" width="160" height="112" rx="8" fill="#eaf2ff" {OUT}/>'
            + t(70, 98, "บัตรประจำตัวประชาชน", 11, "#5b7fd8")
            + f'<rect x="180" y="106" width="34" height="42" rx="4" fill="#fffdf6" stroke-width="2" stroke="{LINE}"/>'
            + f'<circle cx="197" cy="122" r="7" fill="#f6dcb4" stroke-width="1.8" stroke="{LINE}"/>'
            + f'<path d="M186 146 q11 -14 22 0" fill="#9fb6f0" stroke-width="1.8" stroke="{LINE}"/>'
            + t(70, 124, "นาย สมชาย ใจดี", 14, "#2d2b27") + t(70, 142, "1 1234 xxxxx xx 1", 11)
            + t(70, 158, "12 มี.ค. 2528", 11) + t(70, 176, "99 ถ.ตัวอย่าง จ.เชียงใหม่", 10.5)
            + '<g class="hd-corners hd-pop" data-at="800" stroke="#e0565b" stroke-width="3.4" fill="none">'
            '<path d="M56 92 V74 H74 M210 74 H228 V92 M228 182 V198 H210 M74 198 H56 V182"/></g>'
            + '<rect class="hd-flash" data-at="1350,1750:-on" x="58" y="76" width="168" height="120" rx="10" fill="#ffffff" stroke="none"/></g>')
    phone = (f'<g transform="translate(40 66)">'
             f'<rect width="250" height="420" rx="32" fill="#3a3d45" {OUT}/>'
             f'<rect x="10" y="10" width="230" height="400" rx="24" fill="#e6edf5" stroke="none"/>'
             f'<path d="M10 34 a24 24 0 0 1 24 -24 H216 a24 24 0 0 1 24 24 V58 H10 Z" fill="#3fcb73" stroke="none"/>'
             + t(125, 42, L["chat"], 16, "#ffffff", "middle")
             + f'<g class="hd-pop hd-bub" data-at="250"><rect x="52" y="72" width="180" height="128" rx="14" fill="#3fcb73" stroke="none"/>{card}</g>'
             + f'<g class="hd-pop hd-bub" id="dm-phone" data-at="3900"><rect x="118" y="212" width="114" height="32" rx="16" fill="#3fcb73" stroke="none"/>'
             + t(175, 233, "089-999-1234", 14, "#ffffff", "middle") + '</g>'
             + f'<g class="hd-pop hd-typing" data-at="7700,8300:-on"><rect x="20" y="256" width="64" height="32" rx="16" fill="#ffffff" stroke-width="2" stroke="{LINE}"/>'
             + "".join(f'<circle class="d{i}" cx="{38 + i * 14}" cy="272" r="4" fill="#8a8170" stroke="none"/>' for i in range(3)) + '</g>'
             + f'<g class="hd-pop hd-bub" data-at="8300"><rect x="20" y="256" width="200" height="34" rx="17" fill="#ffffff" stroke-width="2" stroke="{LINE}"/>'
             + t(120, 278, L["bot"], 14, "#2e8a55", "middle") + '</g>'
             + '<rect x="96" y="18" width="58" height="12" rx="6" fill="#1f2025" stroke="none"/></g>')
    mm = mascot(384, 430, 1.25, ' data-at="1100:hm-jot,1100:hm-snap,1700:-hm-jot,1700:-hm-snap,6000:hm-hopping,6600:-hm-hopping,8300:hm-hopping,8300:hm-cheer"',
                prop="camera", kind="dms")
    real = panel("sc-real", label(L["real"]) + phone + mm)

    # ---- 系统里：新客户资料逐格写进去 → 查老客户（放大镜扫过）→ 订车单
    rows = ""
    for i, (k, lab, val, at) in enumerate(fields):
        y = 78 + i * 34
        rows += (f'<g id="dm-f-{k}"><rect x="136" y="{y - 22}" width="322" height="30" fill="none" stroke="none"/>'
                 + t(18, y, lab, 15, "#8a8170") + typed(140, y, val, at + 650, 16)
                 + f'</g><path d="M14 {y + 11} Q236 {y + 9} 458 {y + 12}" stroke="#e4dccb" stroke-width="1.5" fill="none"/>')
    form = (f'<g transform="translate(24 66)"><path d="M0 2 Q236 -3 472 2 L473 244 Q236 248 1 245 Z" fill="{PAPER}" {OUT}/>'
            + t(18, 34, L["form"], 18, "#2d2b27")
            + '<path d="M16 44 q100 6 220 -2" stroke="#ffd84d" stroke-width="8" fill="none" stroke-linecap="round" opacity=".7"/>'
            + f'<g transform="translate(0 20)">{rows}</g></g>')
    olds = ("นาง สมศรี ดีมาก", "นาย ประเสริฐ ทองดี", "นางสาว มาลี สุขใจ")
    minis = "".join(f'<g transform="translate({18 + i * 150} 46) rotate({(-2, 1, -1)[i]})"><rect width="138" height="38" rx="6" fill="{PAPER}" stroke-width="2" stroke="{LINE}"/>'
                    + t(10, 25, nm, 13, "#4a443a") + '</g>' for i, nm in enumerate(olds))
    dup = (f'<g transform="translate(24 318)"><g class="hd-pop hd-dup" data-at="5000">'
           f'<path d="M0 2 Q236 -2 472 2 L472 96 Q236 99 0 96 Z" fill="#fbf3df" {OUT}/>' + t(18, 30, L["dup"], 17, "#2d2b27") + minis
           + f'<g class="hd-pop" data-at="6000"><rect x="270" y="8" width="190" height="30" rx="15" fill="#e9f4ec" {OUT}/>'
           + t(365, 29, "✓ " + L["nodup"], 14, "#2e8a55", "middle") + '</g></g></g>'
           + f'<g class="hd-lens2" data-at="5100"><circle cx="0" cy="0" r="22" fill="#ffffff" fill-opacity=".35" stroke-width="3" stroke="{LINE}"/>'
           + f'<path d="M15 16 L32 33" stroke-width="7" stroke="{LINE}" stroke-linecap="round"/></g>')
    slip = (f'<g transform="translate(40 426) rotate(-2)"><g class="hd-pop hd-slip" data-at="6700">'
            f'<rect width="420" height="74" rx="8" fill="#fff8e1" {OUT}/>'
            + t(16, 28, f'{L["booking"]} BK-0412', 18, "#2d2b27") + t(16, 50, "นาย สมชาย ใจดี · 089-999-1234", 14)
            + t(16, 68, L["sales"], 12.5, "#8a8170") + car(318, 14) + tick(286, 24, at="7400") + '</g></g>')
    sys_ = panel("sc-sys", label(L["system"]) + form + dup + slip)

    flies = "".join(flyer(v, "#dm-card" if k != "phone" else "#dm-phone", f"#dm-f-{k}", at) for k, _lab, v, at in fields)
    return f'<div class="scene hd-scene" data-scene="dms" data-total="10500">{real}{sys_}{flies}</div>'


# ---------------- ERP：柜台扫码卖货 → 库存跟着减 → 快没货了 → 拍采购单 → 库存补回来 ----------------

def product(kind):
    """柜台上的三样货（底边中点在原点）：A4 纸、宽脊文件夹、一盒笔。"""
    if kind == "a4":
        return (f'<rect x="-28" y="-30" width="56" height="30" rx="3" fill="{PAPER}" {OUT}/>'
                f'<rect x="-28" y="-21" width="56" height="12" fill="#5b86d6" {OUT}/>' + t(0, -11, "A4", 11, "#ffffff", "middle", ' stroke="none"'))
    if kind == "binder":
        return (f'<rect x="-17" y="-52" width="34" height="52" rx="3" fill="#5b86d6" {OUT}/>'
                f'<rect x="-10" y="-42" width="20" height="16" rx="2" fill="{PAPER}" stroke-width="2" stroke="{LINE}"/>'
                f'<circle cx="0" cy="-12" r="5" fill="{PAPER}" stroke-width="2" stroke="{LINE}"/>')
    return (f'<path d="M-12 -22 V-40 M-4 -22 V-44 M4 -22 V-38 M12 -22 V-42" stroke-width="5" stroke="#3d362c" stroke-linecap="round"/>'
            f'<path d="M-12 -40 V-42 M-4 -44 V-46 M4 -38 V-40 M12 -42 V-44" stroke-width="5" stroke="#e0565b" stroke-linecap="round"/>'
            f'<rect x="-22" y="-24" width="44" height="24" rx="3" fill="#ff8fb4" {OUT}/>')


def erp(L):
    prods = [("a4", "กระดาษ A4 ×10", "1,250.00", 10), ("binder", "แฟ้มสันกว้าง ×24", "1,080.00", 24), ("pens", "ปากกา ×12", "180.00", 12)]
    T = [300, 1700, 3100]
    counter_y = 330

    # ---- 现实：门店柜台。左边收银机吐出收据；货一样样滑到小单那里，小单身上的条码一扫“嘀”，货进篮子
    receipt = (f'<g transform="translate(36 58) rotate(-2)"><path d="M0 4 Q90 0 178 3 L180 128 l-11 6 l-11 -6 l-11 6 l-11 -6 l-11 6 l-11 -6 l-11 6 l-11 -6 l-11 6 l-11 -6 '
               f'l-11 6 l-11 -6 l-11 6 l-11 -6 l-11 6 l-11 -6 Z" fill="#fbf8f0" {OUT}/>'
               + t(90, 26, L["receipt"], 15, "#2d2b27", "middle")
               + '<path d="M10 36 H170" stroke="#a89f8e" stroke-width="1.6" stroke-dasharray="4 4"/>'
               + "".join(typed(12, 56 + i * 20, nm, T[i] + 700, 12) + typed(168, 56 + i * 20, amt, T[i] + 750, 12, anchor="end")
                         for i, (_k, nm, amt, _q) in enumerate(prods))
               + '<path d="M10 112 H170" stroke="#a89f8e" stroke-width="1.6" stroke-dasharray="4 4"/>'
               + typed(12, 128 - 2, L["total"], 4100, 14) + typed(168, 126, "2,510.00", 4150, 14, anchor="end")
               + f'<g transform="translate(142 150) rotate(-14)"><g class="hd-stamp" data-at="4400"><rect x="-46" y="-16" width="92" height="32" rx="7" fill="none" stroke="#e0565b" stroke-width="3"/>'
               + t(0, 6, L["paid"], 15, "#e0565b", "middle") + '</g></g></g>')
    register = (f'<g id="er-reg" transform="translate(20 {counter_y})">'
                f'<path d="M6 0 L18 -96 H190 L202 0 Z" fill="#e6edf5" {OUT}/>'
                f'<rect x="34" y="-86" width="136" height="34" rx="6" fill="#2f3b33" {OUT}/>'
                + "".join(typed(160, -63, amt, T[i] + 650, 16, "#9ff0b0", "end") for i, (_k, _n, amt, _q) in enumerate(prods[:1]))
                + "".join(f'<rect class="hd-pop" data-at="{T[i] + 650},{T[i + 1] + 650 if i < 2 else 99999}:-on" x="36" y="-84" width="132" height="30" rx="5" fill="#2f3b33" stroke="none"/>'
                          + typed(160, -63, amt, T[i] + 650, 16, "#9ff0b0", "end") for i, (_k, _n, amt, _q) in enumerate(prods[1:], 1))
                + "".join(f'<rect x="{40 + c * 26}" y="{-44 + r * 14}" width="20" height="9" rx="2" fill="#fffdf6" stroke-width="1.8" stroke="{LINE}"/>'
                          for c in range(5) for r in range(3)) + '</g>')
    counter = (f'<path d="M6 {counter_y} H514 V{counter_y + 26} H6 Z" fill="#d9b98a" {OUT}/>'
               f'<path d="M6 {counter_y + 26} V{counter_y + 120} M514 {counter_y + 26} V{counter_y + 120}" stroke="{LINE}" stroke-width="2.4" opacity=".4"/>')
    goods = "".join(f'<g transform="translate(262 {counter_y})"><g class="hd-good" data-at="{T[i]}:on,{T[i] + 300}:go,{T[i] + 800}:in">{product(k)}</g></g>'
                    for i, (k, _n, _a, _q) in enumerate(prods))
    beeps = ",".join(f"{T[i] + 600}:on,{T[i] + 1000}:-on" for i in range(3))
    beep = (f'<g class="hd-pop" data-at="{beeps}"><path d="M300 150 Q300 128 330 128 H372 Q396 128 396 150 Q396 170 372 170 H344 L332 184 L334 170 Q300 172 300 150 Z" fill="#ffffff" {OUT}/>'
            + t(348, 157, L["beep"], 17, "#e0565b", "middle") + '</g>')
    hm_at = ",".join(f"{T[i] + 600}:hm-beep,{T[i] + 1000}:-hm-beep" for i in range(3)) + ",5800:hm-hopping,6400:-hm-hopping,7400:hm-hopping,7400:hm-cheer"
    mm = mascot(436, counter_y - 48, 1.15, f' data-at="{hm_at}"', prop="basket", kind="erp", flip=True)
    bill = (f'<g transform="translate(40 386) rotate(-3)"><g class="hd-pop" id="er-bill" data-at="5300">'
            f'<rect width="220" height="104" rx="6" fill="{PAPER}" {OUT}/>'
            + t(14, 26, L["bill"], 16, "#2d2b27") + t(14, 50, "หจก. ตัวอย่างวัสดุ", 14) + t(14, 72, "แฟ้มสันกว้าง ×24", 14)
            + t(206, 92, "+24", 20, "#2e8a55", "end")
            + '<rect class="hd-flash" data-at="5700,6100:-on" x="-4" y="-4" width="228" height="112" rx="8" fill="#ffffff" stroke="none"/></g></g>'
            + f'<g transform="translate(250 412) rotate(16)"><g class="hd-pop" data-at="5500,6500:-on">'
            f'<rect x="-30" y="-50" width="60" height="100" rx="12" fill="#3a3d45" {OUT}/><rect x="-24" y="-42" width="48" height="80" rx="7" fill="#dbe7fb" stroke="none"/>'
            f'<circle cx="0" cy="-2" r="10" fill="#ffd84d" stroke-width="2" stroke="{LINE}"/>'
            '<g class="hd-pop" data-at="5700,6100:-on" stroke="#f0b400" stroke-width="3"><path d="M-38 -30 l-14 -4 M-36 -8 h-16 M-36 14 l-14 6"/></g></g></g>')
    real = panel("sc-real", label(L["real"]) + receipt + register + counter + goods + mm + beep + bill)

    # ---- 系统里：三样货的库存条，卖一样减一截；文件夹见底贴“快没货了”；拍了采购单补回去
    stock = [(120, 110, None, .92, .85), (30, 6, 30, .6, .12), (48, 36, None, .64, .48)]
    rows = ""
    for i, ((k, nm, _a, _q), (a, b, c, w0, w1)) in enumerate(zip(prods, stock)):
        y = 118 + i * 92
        steps = f"{T[i] + 1000}" + (",6700:refill" if c else "")
        count = f"on:{a}:{b}" + (f"|refill:{b}:{c}" if c else "")
        rows += (f'<g id="er-sk{i}" class="hd-sk" data-at="{steps}" style="--w0:{w0};--w1:{w1};--w2:{w0}">'
                 f'<rect x="20" y="{y - 46}" width="460" height="80" fill="none" stroke="none"/>'
                 f'<g transform="translate(52 {y + 8}) scale(.8)">{product(k)}</g>'
                 + t(96, y - 6, nm.split(" ×")[0], 17, "#2d2b27")
                 + f'<text x="470" y="{y - 2}" font-family="{HAND}" font-size="30" fill="#2d2b27" text-anchor="end" data-count="{count}">{a}</text>'
                 + f'<rect x="96" y="{y + 8}" width="300" height="16" rx="8" fill="#f3ecdc" {OUT}/>'
                 + f'<rect class="hd-bar" x="98" y="{y + 10}" width="296" height="12" rx="6" fill="#43b36f" stroke="none"/>'
                 + (f'<g transform="translate(414 {y + 2}) rotate(6)"><g class="hd-pop hd-low" data-at="{T[i] + 1300},6700:-on">'
                    f'<rect x="-6" y="-6" width="96" height="36" rx="4" fill="#ffd1d1" {OUT}/>' + t(42, 18, L["low"], 15, "#e0565b", "middle") + '</g></g>' if c else "")
                 + '</g>')
    restock = (f'<g class="hd-pop" data-at="6700"><rect x="96" y="384" width="230" height="32" rx="16" fill="#e9f4ec" {OUT}/>'
               + t(211, 405, f'+24 · {L["restock"]}', 15, "#2e8a55", "middle") + '</g>')
    done = (f'<g transform="translate(262 466) rotate(-3)"><g class="hd-stamp" data-at="7400">'
            f'<rect x="-212" y="-22" width="424" height="44" rx="12" fill="#43b36f" {OUT}/>'
            + t(0, 7, L["erp_done"], 15, "#fff", "middle") + '</g></g>')
    sys_ = panel("sc-sys", label(L["system"]) + t(28, 82, L["stock"], 18, "#2d2b27") + rows + restock + done)

    flies = "".join(flyer(f"-{q}", "#er-reg", f"#er-sk{i}", T[i] + 800) for i, (_k, _n, _a, q) in enumerate(prods))
    flies += flyer("+24", "#er-bill", "#er-sk1", 6000, "doc")
    return f'<div class="scene hd-scene" data-scene="erp" data-total="9800">{real}{sys_}{flies}</div>'


# ---------------- MR.ERP（专业版）：一张单据保存一次，库存、税、总账一起过账，报表随时出 ----------------
# 以 MR.ERP 源码为准（/Users/skin/mrerp-source/src）：8 个系统见 index.php；保存采购 / 销售单据时在同一笔事务里写库存卡、
# 进项 / 销项税、总账（aptran/allsave.php、artran/allsave.php 的 insacglhd）；单据有审批（appsign.php）；
# 报表有 งบกำไรขาดทุน、งบทดลอง、รายงานภาษีขาย（report/296、284、232）。

def mrerp(L):
    T = [500, 2900, 5300]                       # 三张单据各自进来的时刻；+700 盖章，+1100 飞进系统，+1500 各模块更新
    docs = [("ใบรับสินค้า", "หจก. ตัวอย่างวัสดุ", "กระดาษ A4 ×50", "10,700.00", "#ffa94d"),
            ("ใบกำกับภาษีขาย", "บจ. ลูกค้าตัวอย่าง", "กระดาษ A4 ×12", "2,568.00", "#4cc3c3"),
            ("ใบเสร็จรับเงิน", "บจ. ลูกค้าตัวอย่าง", "เช็ค · ธนาคารตัวอย่าง", "2,568.00", "#b48cf0")]
    A = [x + 1500 for x in T]

    # ---- 现实：三张单据一张张进来，MR.ERP 大账本逐张盖审批章
    papers = ""
    for i, (title, party, line, amt, logo) in enumerate(docs):
        y, r = 74 + i * 126, (-2, 1.5, -1)[i]
        papers += (f'<g transform="translate(26 {y}) rotate({r})"><g class="hd-pop" id="mr-doc{i}" data-at="{T[i]}">'
                   f'<path d="M0 3 Q120 -1 246 2 L248 108 Q120 112 1 109 Z" fill="{PAPER}" {OUT}/>'
                   f'<circle cx="20" cy="22" r="9" fill="{logo}" {OUT}/>' + t(36, 28, title, 16, "#2d2b27")
                   + t(14, 58, party, 14, "#6b6356") + t(14, 84, line, 14, "#6b6356") + t(234, 96, amt, 18, "#2d2b27", "end")
                   + '<path d="M14 66 q60 -4 120 0" stroke="#e4dccb" stroke-width="1.6" fill="none"/>'
                   + f'<g transform="translate(190 34) rotate(-10)"><g class="hd-stamp" data-at="{T[i] + 700}">'
                   f'<rect x="-40" y="-15" width="80" height="30" rx="7" fill="none" stroke="#e0565b" stroke-width="3"/>'
                   + t(0, 6, L["mr_ok"], 14, "#e0565b", "middle") + '</g></g></g></g>')
    jots = ",".join(f"{T[i] + 450}:hm-jot,{T[i] + 1000}:-hm-jot" for i in range(3))
    mm = mascot(406, 430, 1.3, f' data-at="{jots},8800:hm-hopping,8800:hm-cheer"', prop="stamp", kind="mrerp")
    real = panel("sc-real", label(L["real"]) + papers + mm)

    # ---- 系统里：8 个模块，单据保存时对应的模块一起更新；最后报表升起来
    mods = L["mr_mods"]
    tiles = ""
    hits = {0: [A[0]], 1: [A[0], A[1]], 2: [A[1], A[2]], 3: [A[2]], 5: [A[0], A[1]], 6: [A[0], A[1], A[2]]}
    for k, name in enumerate(mods):
        c, rw = k % 4, k // 4
        x, y = 14 + c * 124, 74 + rw * 104
        flash = ",".join(f"{a},{a + 900}:-on" for a in hits.get(k, []))
        body = ""
        if k == 0:
            body = f'<g class="hd-chip" data-at="{A[0]}">' + t(56, 72, "10,700.00", 15, "#2d2b27", "middle") + '</g>'
        elif k == 1:
            # 计数的步骤名用 mr- 前缀，别撞上全站的 .on 以外的样式（.paid 是 Cowork 演示里的章）
            body = (f'<text x="56" y="68" font-family="{HAND}" font-size="28" fill="#2d2b27" text-anchor="middle" '
                    f'data-count="on:120:170|mr-out:170:158" data-at="{A[0]}:on,{A[1]}:mr-out">120</text>'
                    + t(56, 84, L["mr_unit_stock"], 11, "#8a8170", "middle"))
        elif k == 2:
            body = (f'<g class="hd-chip" data-at="{A[1]}">' + t(56, 72, "2,568.00", 15, "#2d2b27", "middle") + '</g>'
                    + tick(100, 66, at=f"{A[2]}"))
        elif k == 3:
            body = f'<g class="hd-chip" data-at="{A[2]}">' + t(56, 72, L["mr_cheque"], 14, "#2e8a55", "middle") + '</g>'
        elif k == 5:
            body = (f'<g class="hd-chip" data-at="{A[0]}">' + t(56, 60, "+700.00", 13, "#5b7fd8", "middle") + '</g>'
                    f'<g class="hd-chip" data-at="{A[1]}">' + t(56, 80, "+168.00", 13, "#e0565b", "middle") + '</g>')
        elif k == 6:
            body = (f'<text x="56" y="68" font-family="{HAND}" font-size="28" fill="#2d2b27" text-anchor="middle" '
                    f'data-count="on:0:1|mr-2:1:2|mr-3:2:3" data-at="{A[0]}:on,{A[1]}:mr-2,{A[2]}:mr-3">0</text>'
                    + t(56, 84, L["mr_unit_gl"], 11, "#8a8170", "middle"))
        idle = k in (4, 7)
        tiles += (f'<g transform="translate({x} {y})" id="mr-t{k}">'
                  f'<path d="M2 2 Q56 0 110 2 L112 90 Q56 92 1 90 Z" fill="{"#f6f2e8" if idle else PAPER}" {OUT}/>'
                  + (f'<rect class="hd-pop" data-at="{flash}" x="4" y="4" width="104" height="84" rx="6" fill="#fff1a8" stroke="none"/>' if flash else "")
                  + t(10, 26, name, 14, "#8a8170" if idle else "#2d2b27") + body + '</g>')
    reports = ""
    for i, (name, at) in enumerate(zip(L["mr_reports"], (7300, 7550, 7800))):
        x, r = 22 + i * 162, (-3, 1, -2)[i]
        inner = ""
        if i == 0:   # 损益表：三根小柱
            inner = "".join(f'<rect x="{24 + j * 30}" y="{98 - h}" width="18" height="{h}" fill="{c}" stroke-width="1.8" stroke="{LINE}"/>'
                            for j, (h, c) in enumerate(((30, "#c9dcee"), (44, "#9fd3b0"), (58, "#43b36f"))))
        elif i == 1:  # 试算表：借贷相等
            inner = t(72, 76, "Dr = Cr", 18, "#2d2b27", "middle") + tick(118, 70)
        else:        # 销项税报表
            inner = (''.join(f'<path d="M16 {58 + j * 12} H{110 - j * 14}" stroke="#d9cfba" stroke-width="2"/>' for j in range(3))
                     + t(126, 104, "168.00", 15, "#e0565b", "end"))
        reports += (f'<g transform="translate({x} 296) rotate({r})"><g class="hd-pop" data-at="{at}">'
                    f'<path d="M0 2 Q70 -1 144 2 L145 118 Q70 121 1 118 Z" fill="{PAPER}" {OUT}/>'
                    + t(10, 26, name, 14, "#2d2b27") + '<path d="M10 34 q50 4 100 -1" stroke="#ffd84d" stroke-width="6" fill="none" stroke-linecap="round" opacity=".7"/>'
                    + inner.replace('class="hd-tick"', 'class="hd-tick" data-at="' + str(at + 300) + '"') + '</g></g>')
    done = (f'<g transform="translate(262 466) rotate(-3)"><g class="hd-stamp" data-at="8800">'
            f'<rect x="-226" y="-22" width="452" height="44" rx="12" fill="#43b36f" {OUT}/>'
            + t(0, 7, L["mr_done"], 15, "#fff", "middle") + '</g></g>')
    sys_ = panel("sc-sys", label(L["system"]) + tiles + reports + done)

    flies = (flyer("+50", "#mr-doc0", "#mr-t1", T[0] + 1100) + flyer("10,700.00", "#mr-doc0", "#mr-t0", T[0] + 1150, "doc")
             + flyer("-12", "#mr-doc1", "#mr-t1", T[1] + 1100) + flyer("2,568.00", "#mr-doc1", "#mr-t2", T[1] + 1150, "doc")
             + flyer("2,568.00", "#mr-doc2", "#mr-t3", T[2] + 1100, "doc"))
    return f'<div class="scene hd-scene" data-scene="mrerp" data-total="9800">{real}{sys_}{flies}</div>'


# ---------------- 首页产品卡片角上的小单（手绘版，替代 mascot.py 的粗线版） ----------------

PROPS = {"cowork": "pencil", "dms": "camera", "erp": "basket", "ai": "none"}


def badge(kind, cls="poster-mascot"):
    """产品卡片上沿站着的小单：穿这个产品的衣服、拿这个产品的道具，会眨眼、眼睛跟着鼠标。viewBox 底边就是脚底，方便贴着框线站。"""
    return (f'<svg class="{cls} hd-badge" viewBox="-80 -152 180 194" aria-hidden="true" focusable="false">'
            f'<g filter="url(#hd-pencil)">{mascot(0, 0, 1.0, "", PROPS[kind], kind)}</g></svg>')
