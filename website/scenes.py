"""首页三段产品演示的手绘版：和首页主视觉同一套画风（铅笔描边 + 平涂 + 手写字），小单在“现实”那一半干活。

每段演示是左右两张内联 SVG（左“现实”、右“系统里”，手机上上下叠），跨两边的东西（飞过去的金额、配对的线）
仍用 site.js demo() 的通用时间轴：data-at 到点加/去类，class="fly" 在两点之间飞，data-type 逐字写出。
SVG 里的动作全是 CSS 类驱动（见 style.css「手绘演示」一节）；系统开了“减少动态效果”时直接显示终态。
样例数据全是虚构。
"""
from poster import esc, n, INK, LINE, PAPER, HAND, OUT

PW, PH = 520, 510                   # 每半边画布
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

def mascot(x, y, k=1.0, data=""):
    """铅笔线条的小单（髋部在 x,y）：左手可以挥，右手拿着铅笔，可以抬起来打勾。

    结构：.hm（位置）> .hm-move（走动）> .hm-hop（跳）> 腿 .hm-leg、身体、左臂 .hm-wave、右臂 .hm-arm（带铅笔）。
    关节都用 translate 包一层，CSS 只在局部原点 (0,0) 上旋转。
    """
    zig, up = [], True
    for _ in range(8):
        zig.append(f"l-9.75 {-8 if up else 8}")
        up = not up
    body = (f'<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V0 {" ".join(zig)} Z" fill="{PAPER}"/>'
            '<path d="M-24 -38 H18 M-24 -26 H8" stroke="#bdb7aa" stroke-width="3"/>'
            '<rect x="2" y="-17" width="26" height="10" rx="3" fill="#ffd84d" stroke-width="2.4"/>'
            '<ellipse cx="-21" cy="-62" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<ellipse cx="23" cy="-62" rx="8" ry="4.5" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<g class="hm-eyes"><circle cx="-10" cy="-76" r="5" fill="currentColor" stroke="none"/>'
            '<circle cx="14" cy="-76" r="5" fill="currentColor" stroke="none"/></g>'
            '<path class="hm-mouth" d="M-5 -54 q6 6 12 0" stroke-width="2.6"/>')
    legs = ('<g transform="translate(-14 0)"><path class="hm-leg l" d="M0 0 L-4 40 h-14"/></g>'
            '<g transform="translate(14 0)"><path class="hm-leg r" d="M0 0 L4 40 h14"/></g>')
    wave = ('<g transform="translate(-38 -50)"><g class="hm-wave"><path d="M0 0 Q-22 12 -24 36"/>'
            f'<circle cx="-24" cy="36" r="8.5" fill="{PAPER}"/></g></g>')
    pencil = ('<g transform="translate(20 36) rotate(28) translate(0 -58)" stroke-width="2.6">'
              '<rect x="-7" y="0" width="14" height="16" rx="4" fill="#ff9ba3"/><rect x="-7" y="14" width="14" height="7" fill="#cfd2d8"/>'
              '<rect x="-7" y="21" width="14" height="74" fill="#ffd84d"/>'
              '<path d="M-7 95 L0 120 L7 95 Z" fill="#f6dcb4"/><path d="M-2.6 112 L0 120 L2.6 112 Z" fill="currentColor"/></g>')
    arm = (f'<g transform="translate(38 -50)"><g class="hm-arm"><path d="M0 0 Q12 16 20 36"/>{pencil}'
           f'<circle cx="20" cy="36" r="9" fill="{PAPER}"/></g></g>')
    return (f'<g class="hm" transform="translate({n(x)} {n(y)}) scale({k})"{data}><g class="hm-move"><g class="hm-hop" {PEN} stroke-width="{n(3 / k)}">'
            f'<ellipse class="hm-shadow" cx="0" cy="41" rx="46" ry="7" fill="#3a2a14" stroke="none" opacity=".14"/>'
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
    mm = mascot(436, 420, 1.0, f' data-at="{raise_},9100:hm-hopping,9100:hm-cheer"')
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
             f'<rect x="-120" y="-22" width="240" height="44" rx="12" fill="#43b36f" {OUT}/>'
             + t(0, 7, L["cowork_done"], 16, "#fff", "middle") + '</g></g>')
    sys_ = panel("sc-sys", label(L["system"]) + t(28, 82, L["invoices"], 18, "#2d2b27") + dots + cards + ledger + stamp)

    # ---- 跨两边：税票金额飞到对上的那一行，配对线（连到税票下沿，不横穿别的税票）；税票飞进日记账
    flies = "".join(flyer(a, f"#cw-inv{i}", f"#cw-st{row_of[i]}", T[i] + 300) for i, (_n, a, _l) in enumerate(invs))
    threads = "".join(f'<path class="thread hd-thread" pathLength="1" data-from="#cw-st{row_of[i]}" data-to="#cw-a{i}" data-to-top="#cw-inv{i}" data-at="{T[i] + 800},5800:-on"/>'
                      for i in range(3))
    flies += f'<svg class="threads" aria-hidden="true">{threads}</svg>'
    flies += "".join(flyer(a, f"#cw-inv{i}", f"#cw-er{i}", 6000 + i * 160, "doc") for i, (_n, a, _l) in enumerate(invs))
    return f'<div class="scene hd-scene" data-scene="cowork" data-total="11200">{real}{sys_}{flies}</div>'
