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

def mascot(x, y, k=1.0, data="", prop="pencil"):
    """铅笔线条的小单（髋部在 x,y）：左手可以挥，右手拿着道具（铅笔 / 相机），可以抬起来打勾、拍照。

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
    camera = ('<g transform="translate(24 30) rotate(-12)" stroke-width="2.6">'
              '<rect x="-20" y="-15" width="40" height="28" rx="6" fill="#5b7fd8"/><rect x="-10" y="-21" width="14" height="7" rx="2" fill="#3a5bb0"/>'
              '<circle cx="2" cy="-1" r="9" fill="#ffffff"/><circle cx="2" cy="-1" r="4" fill="#2d2b27" stroke="none"/>'
              '<g class="hm-flash" stroke="#f0b400" stroke-width="3"><path d="M24 -24 l9 -9 M30 -10 h12 M16 -30 v-12"/></g></g>')
    held = camera if prop == "camera" else pencil
    arm = (f'<g transform="translate(38 -50)"><g class="hm-arm"><path d="M0 0 Q12 16 20 36"/>{held}'
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
    mm = mascot(420, 430, 1.0, ' data-at="1100:hm-jot,1100:hm-snap,1700:-hm-jot,1700:-hm-snap,6000:hm-hopping,6600:-hm-hopping,8300:hm-hopping,8300:hm-cheer"',
                prop="camera")
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
