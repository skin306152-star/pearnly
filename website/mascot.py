"""品牌形象 น้องใบ（小单）：一张会自己走进系统的单据。

同一套骨架（圆角纸身 + 锯齿底 + 两点眼 + 腮红 + 粗黑描边），换衣服是不同产品，换表情是不同状态。
全部是内联 SVG，不加图片文件；眼睛（.m-look）跟随指针、眨眼（.m-blink）、挥手（.m-wave）由 site.js / style.css 驱动。
"""

INK, PAPER, YELLOW, PINK, GREEN, BLUE, NAVY = "#141413", "#fffdf8", "#ffd84d", "#ff9ba3", "#1d7f47", "#eaf3fb", "#2f5d8a"


def zigzag(x0, x1, y, step=10, depth=12):
    """从右往左的锯齿底边（x1 → x0）。"""
    parts, up = [], True
    for _ in range(int((x1 - x0) / step)):
        parts.append(f"l-{step} {-depth if up else depth}")
        up = not up
    return " ".join(parts)


def paper(x0=60, y0=34, x1=200, y1=280, fill=PAPER, stroke=8):
    return (f'<path class="m-body" d="M{x0} {y0 + 16} Q{x0} {y0} {x0 + 16} {y0} H{x1 - 16} Q{x1} {y0} {x1} {y0 + 16} V{y1} '
            f'{zigzag(x0, x1, y1)} Z" fill="{fill}" stroke="{INK}" stroke-width="{stroke}"/>')


def eyes(lx, rx, y, r=10):
    """两点眼放进 .m-look（跟随指针）> .m-blink（眨眼），腮红不动。"""
    hl = r * .35
    return (f'<g class="m-look"><g class="m-blink">'
            f'<circle cx="{lx}" cy="{y}" r="{r}" fill="{INK}"/><circle cx="{rx}" cy="{y}" r="{r}" fill="{INK}"/>'
            f'<circle cx="{lx + hl}" cy="{y - hl}" r="{hl}" fill="#fff"/><circle cx="{rx + hl}" cy="{y - hl}" r="{hl}" fill="#fff"/></g></g>')


def cheeks(lx, rx, y, rx_=12, ry=7):
    return (f'<ellipse cx="{lx}" cy="{y}" rx="{rx_}" ry="{ry}" fill="{PINK}" opacity=".75"/>'
            f'<ellipse cx="{rx}" cy="{y}" rx="{rx_}" ry="{ry}" fill="{PINK}" opacity=".75"/>')


def smile(x, y, w=14, stroke=7):
    return f'<path d="M{x - w} {y} Q{x} {y + 16} {x + w} {y}" stroke="{INK}" stroke-width="{stroke}"/>'


SHOE = "#5b86d6"   # 所有小人同一双蓝鞋（和首页手绘里的一样）


def shoe(x, y, side, sw=5):
    """一只鞋：鞋跟在 x、鞋底在 y，鞋尖朝 side（-1 左、1 右）。"""
    t, h = x + side * 26, x - side * 6
    return (f'<path d="M{h} {y} Q{h} {y - 14} {x + side * 8} {y - 14} Q{t} {y - 14} {t} {y} Z" fill="{SHOE}" stroke="{INK}" stroke-width="{sw}" stroke-linejoin="round"/>'
            f'<path d="M{h} {y} H{t}" stroke="{INK}" stroke-width="{sw}"/>')


def legs(lx, rx, top, bottom=316):
    return (f'<path d="M{lx} {top} V{bottom - 8} M{rx} {top} V{bottom - 8}" stroke="{INK}" stroke-width="8"/>'
            + shoe(lx, bottom + 4, -1) + shoe(rx, bottom + 4, 1))


def hand(x, y, r=11):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{PAPER}" stroke="{INK}" stroke-width="6"/>'


def shadow(cx, rx, cy=326):
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="9" fill="rgba(20,20,19,.12)"/>'


def base():
    return "0 0 260 340", "".join([
        shadow(130, 80), legs(108, 152, 280), paper(), eyes(108, 152, 100), cheeks(90, 170, 124), smile(130, 124),
        f'<path d="M88 172 H172 M88 196 H150 M88 220 H162" stroke="#d6d2c7" stroke-width="8"/>',
        f'<rect x="120" y="240" width="52" height="18" rx="5" fill="{YELLOW}" stroke="{INK}" stroke-width="4"/>',
        f'<g class="m-wave" style="transform-origin:60px 150px"><path d="M60 150 Q32 132 28 94" stroke="{INK}" stroke-width="8"/>{hand(27, 84, 12)}'
        f'<path d="M8 60 q-6 -10 -2 -20 M30 52 q2 -10 10 -16" stroke="{INK}" stroke-width="4" opacity=".5"/></g>',
        f'<path d="M200 160 Q226 180 222 212" stroke="{INK}" stroke-width="8"/>{hand(222, 222)}',
    ])


def cowork():
    buddy = (f'<g class="m-buddy"><path d="M234 316 V332 M260 316 V332" stroke="{INK}" stroke-width="7"/>'
             f'<rect x="222" y="150" width="52" height="168" rx="10" fill="{BLUE}" stroke="{INK}" stroke-width="7"/>'
             f'<circle cx="239" cy="180" r="6" fill="{INK}"/><circle cx="257" cy="180" r="6" fill="{INK}"/>'
             f'<path d="M240 196 Q248 204 256 196" stroke="{INK}" stroke-width="5"/>'
             f'<path d="M232 230 H264 M232 250 H264 M232 270 H264 M232 290 H264" stroke="#9fb6cb" stroke-width="5"/>'
             f'<rect x="228" y="244" width="40" height="12" rx="3" fill="{YELLOW}" opacity=".9"/></g>')
    return "-10 0 300 340", "".join([
        shadow(150, 120), legs(108, 152, 280), paper(),
        f'<rect x="60" y="34" width="140" height="30" fill="{YELLOW}" stroke="{INK}" stroke-width="8"/>',
        eyes(108, 152, 100), cheeks(90, 170, 124),
        f'<circle cx="108" cy="100" r="19" stroke="{INK}" stroke-width="5"/><circle cx="152" cy="100" r="19" stroke="{INK}" stroke-width="5"/>'
        f'<path d="M127 100 h6" stroke="{INK}" stroke-width="5"/>',
        f'<path d="M118 128 Q130 138 142 128" stroke="{INK}" stroke-width="7"/>',
        f'<path d="M88 176 H172 M88 200 H150" stroke="#d6d2c7" stroke-width="8"/>',
        f'<text x="172" y="250" text-anchor="end" font-family="ui-monospace,Menlo,monospace" font-weight="700" font-size="22" fill="{INK}">2,889.00</text>',
        buddy,
        f'<path d="M200 170 Q214 176 222 186" stroke="{INK}" stroke-width="8"/>',
        f'<g class="m-pop"><circle cx="212" cy="128" r="20" fill="{GREEN}"/><path d="M202 128 l7 7 13 -14" stroke="#fff" stroke-width="5"/></g>',
        f'<path d="M60 160 Q34 186 40 220" stroke="{INK}" stroke-width="8"/>{hand(40, 230)}',
    ])


def dms():
    return "0 0 290 340", "".join([
        shadow(140, 100),
        f'<path d="M100 236 V308 M170 236 V308" stroke="{INK}" stroke-width="8"/>{shoe(100, 320, -1)}{shoe(170, 320, 1)}',
        f'<rect class="m-body" x="30" y="100" width="220" height="140" rx="20" fill="{BLUE}" stroke="{INK}" stroke-width="8"/>',
        f'<path d="M34 130 H246" stroke="{INK}" stroke-width="4" opacity=".2"/>',
        eyes(80, 122, 166), cheeks(64, 138, 190, 11, 6), smile(101, 192, 13),
        f'<path d="M160 160 H226 M160 182 H214 M160 204 H200" stroke="#9fb6cb" stroke-width="7"/>',
        f'<path d="M30 190 Q8 214 14 244" stroke="{INK}" stroke-width="8"/>{hand(14, 254)}',
        f'<g class="m-wave" style="transform-origin:250px 150px"><path d="M250 150 Q276 130 274 96" stroke="{INK}" stroke-width="8"/>{hand(274, 86)}'
        f'<circle cx="274" cy="62" r="9" stroke="{INK}" stroke-width="5"/>'
        f'<rect x="258" y="14" width="32" height="40" rx="12" fill="{INK}"/><circle cx="274" cy="28" r="6" fill="{YELLOW}"/></g>',
    ])


def erp():
    bars = " ".join(f"M{x} 200 V230" for x in (94, 102, 108, 118, 124, 134, 140, 150, 158, 166))
    return "0 0 280 340", "".join([
        shadow(130, 90), legs(108, 152, 262),
        paper(70, 84, 190, 262),
        f'<path d="M62 88 Q130 44 198 88 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="7"/><path d="M58 88 H214" stroke="{INK}" stroke-width="7"/>',
        eyes(112, 148, 128, 9), cheeks(96, 164, 150, 10, 6), smile(130, 150, 12, 6),
        f'<path d="{bars}" stroke="{INK}" stroke-width="4"/>',
        f'<g class="m-laser"><path d="M80 214 H180" stroke="#e5484d" stroke-width="4"/><path d="M80 214 H180" stroke="#e5484d" stroke-width="14" opacity=".2"/></g>',
        f'<path d="M190 180 Q214 190 220 214" stroke="{INK}" stroke-width="8"/>',
        f'<path d="M204 216 H262 L254 262 H212 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="6"/>'
        f'<path d="M214 216 Q233 186 252 216" stroke="{INK}" stroke-width="5"/>'
        f'<path d="M222 230 V250 M233 230 V250 M244 230 V250" stroke="{INK}" stroke-width="3" opacity=".5"/>',
        f'<g class="m-wave" style="transform-origin:70px 170px"><path d="M70 170 Q46 160 40 130" stroke="{INK}" stroke-width="8"/>{hand(38, 120)}</g>',
    ])


def pro(dms=False):
    """MR.ERP 专业版：蓝色大账本哥哥，8 张书签 = 8 个模块，左手托着一只小单；DMS 版胸前挂 DMS 牌、手里拎车钥匙。"""
    tabs = "".join(
        f'<rect x="262" y="{-20 + i * 40}" width="26" height="{30 if i < 7 else 22}" rx="5" fill="{c}"/>'
        for i, c in enumerate((YELLOW, PINK, "#9fd3b0", "#c9dcee") * 2))
    mini = (f'<g transform="translate(-2 92) scale(.32)">{paper(stroke=12)}'
            f'<circle cx="108" cy="100" r="10" fill="{INK}"/><circle cx="152" cy="100" r="10" fill="{INK}"/>'
            f'{cheeks(90, 170, 124)}{smile(130, 124, 14, 10)}'
            f'<g class="m-wave" style="transform-origin:60px 150px"><path d="M60 150 Q32 132 28 94" stroke="{INK}" stroke-width="12"/></g></g>')
    return "0 -60 320 400", "".join([
        shadow(160, 120),
        f'<path d="M130 290 V310 M200 290 V310" stroke="{INK}" stroke-width="9"/>{shoe(130, 322, -1, 6)}{shoe(200, 322, 1, 6)}',
        f'<g stroke="{INK}" stroke-width="4">{tabs}</g>',
        f'<rect x="86" y="-40" width="184" height="334" rx="16" fill="{PAPER}" stroke="{INK}" stroke-width="8"/>',
        f'<path d="M262 -24 V284 M254 -28 V288" stroke="#d6d2c7" stroke-width="3"/>',
        f'<rect class="m-body" x="70" y="-44" width="184" height="334" rx="16" fill="{NAVY}" stroke="{INK}" stroke-width="9"/>',
        f'<rect x="70" y="-44" width="30" height="334" rx="12" fill="#244a6f" stroke="{INK}" stroke-width="9"/>',
        f'<rect x="120" y="-14" width="110" height="34" rx="8" fill="{PAPER}" stroke="{INK}" stroke-width="5"/>',
        f'<text x="175" y="10" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-weight="700" font-size="18" fill="{INK}">MR.ERP</text>',
        f'<rect x="126" y="54" width="36" height="30" rx="8" stroke="{PAPER}" stroke-width="5"/><rect x="178" y="54" width="36" height="30" rx="8" stroke="{PAPER}" stroke-width="5"/>'
        f'<path d="M162 66 h16" stroke="{PAPER}" stroke-width="5"/>',
        f'<g class="m-look"><g class="m-blink"><circle cx="144" cy="69" r="8" fill="{PAPER}"/><circle cx="196" cy="69" r="8" fill="{PAPER}"/></g></g>',
        f'<path d="M156 104 Q170 116 184 104" stroke="{PAPER}" stroke-width="6"/>',
        f'<path d="M160 132 L180 132 L176 144 L186 196 L170 212 L154 196 L164 144 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="5"/>',
        f'<path d="M254 150 Q286 170 282 210" stroke="{INK}" stroke-width="9"/>{hand(282, 222, 12)}',
        *([f'<rect x="196" y="226" width="50" height="26" rx="6" fill="{PAPER}" stroke="{INK}" stroke-width="4"/>'
            f'<text x="221" y="245" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-weight="700" font-size="15" fill="{INK}">DMS</text>',
            f'<g class="m-wave" style="transform-origin:70px 170px"><path d="M70 170 Q40 160 30 124" stroke="{INK}" stroke-width="9"/>{hand(28, 112, 12)}'
            f'<circle cx="28" cy="86" r="10" stroke="{INK}" stroke-width="5"/>'
            f'<rect x="10" y="34" width="36" height="44" rx="13" fill="{INK}"/><circle cx="28" cy="50" r="7" fill="{YELLOW}"/></g>']
          if dms else
          [f'<path d="M70 170 Q40 182 34 214" stroke="{INK}" stroke-width="9"/>',
           f'<path d="M8 222 Q34 236 60 222" stroke="{INK}" stroke-width="7" fill="{PAPER}"/>', mini]),
    ])


def done():
    """完成：眯眼大笑，身上一枚绿章（.m-stamp 盖下来），四周彩纸。"""
    return "0 0 260 340", "".join([
        legs(108, 152, 280), paper(stroke=9),
        f'<path d="M96 106 Q108 92 120 106 M140 106 Q152 92 164 106" stroke="{INK}" stroke-width="8"/>',
        cheeks(90, 170, 126), f'<path d="M112 126 Q130 150 148 126 Z" fill="{INK}"/>',
        f'<g class="m-stamp" style="transform-origin:130px 206px"><g transform="rotate(-12 130 206)"><circle cx="130" cy="206" r="40" stroke="{GREEN}" stroke-width="7"/>'
        f'<path d="M112 206 l12 12 22 -24" stroke="{GREEN}" stroke-width="8"/></g></g>',
        f'<g class="m-confetti"><path d="M20 40 l10 6 M236 30 l-8 10 M30 150 l12 -2 M232 130 l-12 4" stroke="{INK}" stroke-width="5"/>'
        f'<rect x="216" y="70" width="12" height="12" fill="{YELLOW}" transform="rotate(20 222 76)"/>'
        f'<rect x="26" y="86" width="12" height="12" fill="{PINK}" transform="rotate(-20 32 92)"/></g>',
    ])


def find():
    """404：拿放大镜找页面。"""
    return "-20 0 280 340", "".join([
        shadow(130, 80), legs(108, 152, 280), paper(), eyes(108, 152, 100), cheeks(90, 170, 124),
        f'<circle cx="130" cy="132" r="8" fill="{INK}"/>',
        f'<g class="m-wave" style="transform-origin:60px 150px"><path d="M60 150 Q30 146 18 118" stroke="{INK}" stroke-width="8"/>'
        f'<circle cx="2" cy="92" r="24" fill="rgba(255,255,255,.6)" stroke="{INK}" stroke-width="7"/><path d="M18 110 l14 14" stroke="{INK}" stroke-width="9"/></g>',
        f'<path d="M200 160 Q226 180 222 212" stroke="{INK}" stroke-width="8"/>{hand(222, 222)}',
        f'<text x="130" y="236" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-weight="700" font-size="44" fill="{INK}">404</text>',
    ])


KINDS = {"base": base, "cowork": cowork, "dms": dms, "erp": erp, "pro": pro, "prodms": lambda: pro(dms=True), "done": done, "find": find}


def svg(kind, cls=""):
    view, inner = KINDS[kind]()
    classes = " ".join(c for c in ("mascot", "m-" + kind, cls) if c)
    return (f'<svg class="{classes}" viewBox="{view}" fill="none" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true" focusable="false">{inner}</svg>')
