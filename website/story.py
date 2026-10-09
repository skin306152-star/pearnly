"""首页主视觉“以前 / 现在”：一间手绘办公室里，小单（会计装扮）从手动录单据录到天黑，第二天改用手机拍，单据一张张飞进 Pearnly。

一镜到底，同一张桌子，靠窗外天色、墙上的钟、桌上的单据堆讲对比：
  ① 以前：左手从单据堆拿一张、眯眼看，右手一根手指敲键盘（第二张还敲错一位、删掉重敲），看完放进旁边的“已录”筐；
     画面外还不停伸进一只手往堆上放新单据，堆反而越来越高；钟快转，窗外从白天到黄昏到夜里，台灯亮起，咖啡凉了、杯子越摆越多，冒汗、黑眼圈。
  ② 转折：小单趴到桌上叹气，桌上的手机震起来、弹出 Pearnly 的 LINE 消息，小单一下坐直；一道黄色记号笔把画面刷过去，到了第二天早上。
  ③ 现在：敲一下键盘，屏幕打开 Pearnly；拿起手机对着单据堆拍，每拍一下闪光，那张单据飞进手机，数据从手机飞进屏幕，
     表格多一行、打绿勾；拍了几张后一口气全飞进去，计数走到 16/16。钟只走了十几分钟。
  ④ 完成：单据堆空了，屏幕盖上“已入账”的章；小单放下手机，端起咖啡喝一口，朝你挥手。

画面是一个内联 SVG（不加图片、不发请求）；这里画好全部角色和道具，每一帧的位置、表情、计数都由 site.js 的 story() 按时间 t 算出来，
同一个 t 永远画出同一帧（可以重播）。系统开了“减少动态效果”时直接停在最后一帧。手机上只取中间一段。
桌上的单据、便利贴、旧软件里的字是泰国办公室的实物，三种语言都用泰文原样。
"""
import json
import math

from poster import esc, n, INK, LINE, PAPER, HAND

W, H = 1440, 540
VIEW, VIEW_SM = f"0 0 {W} {H}", f"170 20 1000 480"   # 电脑整张；手机取中间（单据堆 → 屏幕）
K = 2.0                                              # 小单的放大倍数（与 site.js 一致）
HOME = (640, 476)                                    # 小单坐着时髋部的位置（被桌子挡住）
DESK = 428                                           # 桌面上东西的底边
SLAB = 7                                             # 单据堆每张的厚度
PILE_MAX = 18
ITEMS = 16                                           # 第二天要拍的单据数（与 site.js 一致）
SCREEN = (868, 208, 264, 168)                        # 屏幕可显示区域 x, y, w, h
OUT = f'stroke="{LINE}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"'
PEN = f'stroke="{LINE}" stroke-linecap="round" stroke-linejoin="round" fill="none"'
NOTE = "ส่งก่อน 15 !!"
MONTH = "ต.ค."
# 旧软件里已经录好的两行，和这一天要敲的三行（第二行先敲错再删：⌫ 删一个字，· 停一下）
OLD_DONE = [("ร้านวัสดุภัณฑ์ทวีผล", "856.00"), ("ค่าไฟฟ้าสำนักงาน", "3,745.00")]
OLD_TYPE = ["บจ. ขนส่งด่วนไทย\t321.00", "ร้านเครื่องเขียนสยาม\t193.10····⌫⌫⌫⌫⌫39.10", "บจ. แพ็คกิ้งพลัส\t2,247.00"]


def t(x, y, text, size=12, fill="#4a443a", anchor="start", attrs=""):
    return (f'<text x="{n(x)}" y="{n(y)}" font-family="{HAND}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"{attrs}>'
            f'{esc(text)}</text>')


def ui(x, y, text, size=9, fill="#5f5e5a", anchor="start", weight=400, attrs="", family="'IBM Plex Sans Thai', sans-serif"):
    """屏幕上的印刷体小字。"""
    return (f'<text x="{n(x)}" y="{n(y)}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{attrs}>{esc(text)}</text>')


def scribble(x, y, w, color="#c4b9a5", width=2):
    seg = max(int(w / 12), 2)
    d = f"M{n(x)} {n(y)}" + "".join(f" q{w / seg / 2:.1f} {-2 if i % 2 else 2} {w / seg:.1f} 0" for i in range(seg))
    return f'<path d="{d}" stroke="{color}" stroke-width="{width}" fill="none" stroke-linecap="round"/>'


DEFS = f"""<defs>
<filter id="st-pencil" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency=".03" numOctaves="2" seed="5" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="2.4" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="st-wobble" x="-2%" y="-2%" width="104%" height="104%">
  <feTurbulence type="fractalNoise" baseFrequency=".018" numOctaves="2" seed="9" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="3" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="st-tex" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency=".9 .22" numOctaves="2" seed="4"/>
  <feColorMatrix values="0 0 0 0 .42  0 0 0 0 .32  0 0 0 0 .2  0 0 0 1.4 -.74"/>
</filter>
<filter id="st-soft" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="14"/></filter>
<pattern id="st-hatch" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-38)">
  <path d="M0 0 V9" stroke="#3d362c" stroke-width="1.3" opacity=".22"/></pattern>
<pattern id="st-cork" width="14" height="14" patternUnits="userSpaceOnUse">
  <circle cx="3" cy="4" r="1.2" fill="#b9874f"/><circle cx="10" cy="10" r="1" fill="#c2925b"/><circle cx="11" cy="2" r=".8" fill="#b07c45"/></pattern>
<linearGradient id="st-sky-day" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9fd3f0"/><stop offset="1" stop-color="#e3f4fb"/></linearGradient>
<linearGradient id="st-sky-dusk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8f7fc9"/><stop offset=".55" stop-color="#f29b72"/><stop offset="1" stop-color="#ffd38a"/></linearGradient>
<linearGradient id="st-sky-night" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#141b38"/><stop offset="1" stop-color="#2e3a68"/></linearGradient>
<radialGradient id="st-lampglow" cx=".5" cy="0" r="1"><stop offset="0" stop-color="#fff2b8" stop-opacity=".95"/><stop offset=".7" stop-color="#ffe08a" stop-opacity=".35"/><stop offset="1" stop-color="#ffe08a" stop-opacity="0"/></radialGradient>
<clipPath id="st-frame"><rect class="st-frame" width="{W}" height="{H}" rx="26"/></clipPath>
<clipPath id="st-skyclip"><rect x="114" y="54" width="232" height="192"/></clipPath>
<clipPath id="st-scrclip"><rect x="{SCREEN[0]}" y="{SCREEN[1]}" width="{SCREEN[2]}" height="{SCREEN[3]}" rx="3"/></clipPath>
<clipPath id="st-rowclip"><rect x="904" y="249" width="226" height="112"/></clipPath>
<clipPath id="st-viewclip"><rect x="-52" y="-27" width="104" height="54" rx="6"/></clipPath>
</defs>"""


# ---------------- 房间 ----------------

def room():
    strokes = "".join(f'<path d="M{x} {y} q{w / 2:.0f} -4 {w} 0" stroke="#eadcc0" stroke-width="5" fill="none" stroke-linecap="round" opacity=".7"/>'
                      for x, y, w in [(40, 300, 120), (420, 40, 90), (860, 120, 140), (1180, 300, 110), (780, 28, 70), (60, 120, 50)])
    boards = "".join(f'<path d="M0 {y} H{W}" stroke="#d2b98d" stroke-width="2"/>' for y in (470, 530, 590))
    return (f'<rect width="{W}" height="{H}" fill="#f6ecd8"/>{strokes}'
            f'<rect y="330" width="{W}" height="90" fill="#efdfc2"/><path d="M0 330 H{W}" stroke="#d9c39c" stroke-width="5"/>'
            f'<path d="M0 337 H{W}" stroke="#e6d3b2" stroke-width="2"/>'
            f'<rect y="416" width="{W}" height="{H - 416}" fill="#e4cfa8"/>{boards}'
            f'<path d="M0 416 H{W}" stroke="#c9ad7f" stroke-width="3"/>')


def window():
    stars = "".join(f'<path class="st-star" d="M{x} {y - 5} L{x + 1.4} {y - 1.4} L{x + 5} {y} L{x + 1.4} {y + 1.4} L{x} {y + 5} '
                    f'L{x - 1.4} {y + 1.4} L{x - 5} {y} L{x - 1.4} {y - 1.4} Z" fill="#fff6c8"/>'
                    for x, y in [(140, 78), (196, 112), (262, 70), (318, 120), (290, 196), (160, 176), (226, 150)])
    cloud = ('<path d="M0 0 q4 -16 22 -12 q8 -14 26 -6 q16 -6 22 8 q14 2 10 14 H-6 q-10 -2 -6 -8 q2 -4 6 -6 Z" fill="#fff" '
             'stroke="#9fb9cc" stroke-width="2" stroke-linejoin="round"/>')
    sky = (f'<g clip-path="url(#st-skyclip)">'
           '<rect class="st-sky-day" x="114" y="54" width="232" height="192" fill="url(#st-sky-day)"/>'
           '<rect class="st-sky-dusk" x="114" y="54" width="232" height="192" fill="url(#st-sky-dusk)" opacity="0"/>'
           '<rect class="st-sky-night" x="114" y="54" width="232" height="192" fill="url(#st-sky-night)" opacity="0"/>'
           f'<g class="st-stars" opacity="0">{stars}</g>'
           '<g class="st-moon" opacity="0" transform="translate(170 110)"><circle r="17" fill="#fff3c4" stroke="#e9d68e" stroke-width="2"/>'
           '<circle cx="9" cy="-6" r="15" fill="#22305a"/></g>'
           '<g class="st-sun" transform="translate(286 96)"><circle r="34" fill="#ffe27a" opacity=".35"/><circle r="22" fill="#ffd84d" stroke="#e8b923" stroke-width="2.4"/></g>'
           f'<g class="st-cloud1" transform="translate(140 120)">{cloud}</g>'
           f'<g class="st-cloud2" transform="translate(250 196) scale(.7)">{cloud}</g>'
           '<path class="st-bird" d="M0 0 q6 -8 12 0 q6 -8 12 0" stroke="#3d362c" stroke-width="2.4" fill="none" stroke-linecap="round" opacity="0"/>'
           '<path d="M130 230 L190 70 M150 236 L206 86" stroke="#fff" stroke-width="6" opacity=".35" stroke-linecap="round"/>'
           '</g>')
    curtain = ('<path d="M0 0 C-6 60 8 120 -2 180 C-8 220 4 250 -4 270 L40 270 C34 230 46 200 38 160 C30 110 44 60 36 0 Z" fill="#ffb7a8" '
               f'{OUT}/><path d="M12 6 C8 80 18 160 10 262 M26 6 C22 90 30 170 24 262" stroke="#e58f80" stroke-width="2" fill="none"/>')
    plant = (f'<path d="M300 236 L338 236 L334 258 L304 258 Z" fill="#d9774a" {OUT}/><path d="M298 236 H340" {OUT}/>'
             f'<path d="M318 236 C314 214 300 206 294 200 C308 204 318 214 318 236 C320 210 330 196 342 192 C334 204 324 218 318 236 Z" fill="#6fbf73" {OUT}/>')
    return (f'<rect x="100" y="40" width="260" height="220" rx="4" fill="#fffaf0" {OUT}/>{sky}'
            f'<rect x="114" y="54" width="232" height="192" fill="none" {OUT}/>'
            f'<path d="M230 54 V246 M114 150 H346" stroke="#fffaf0" stroke-width="9"/><path d="M230 54 V246 M114 150 H346" {PEN} stroke-width="2"/>'
            f'<rect x="86" y="256" width="288" height="16" rx="3" fill="#fffaf0" {OUT}/>'
            f'{plant}<path d="M66 32 H394" stroke="{LINE}" stroke-width="4" stroke-linecap="round"/>'
            f'<circle cx="64" cy="32" r="6" fill="#c49a6b" {OUT}/><circle cx="396" cy="32" r="6" fill="#c49a6b" {OUT}/>'
            f'<g transform="translate(76 30)">{curtain}</g><g transform="translate(384 30) scale(-1 1)">{curtain}</g>')


def clock():
    cx, cy = 520, 110
    ticks = ""
    for i in range(12):
        s, c = math.sin(i * math.pi / 6), math.cos(i * math.pi / 6)
        r = 33 if i % 3 == 0 else 37
        ticks += (f'<path d="M{n(cx + 40 * s)} {n(cy - 40 * c)} L{n(cx + r * s)} {n(cy - r * c)}" stroke="{LINE}" '
                  f'stroke-width="{3 if i % 3 == 0 else 2}" stroke-linecap="round"/>')
    return (f'<ellipse cx="{cx + 6}" cy="{cy + 8}" rx="52" ry="52" fill="#3a2a14" opacity=".1"/>'
            f'<circle cx="{cx}" cy="{cy}" r="50" fill="#fffdf6" {OUT} stroke-width="3"/><circle cx="{cx}" cy="{cy}" r="44" fill="none" stroke="#e2d6bf" stroke-width="2"/>'
            f'{ticks}'
            f'<path class="st-hour" d="M{cx} {cy + 6} V{cy - 24}" stroke="{INK}" stroke-width="5.5" stroke-linecap="round"/>'
            f'<path class="st-min" d="M{cx} {cy + 8} V{cy - 36}" stroke="{INK}" stroke-width="3.4" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="#e0565b" stroke="{LINE}" stroke-width="2"/>')


def corkboard():
    cal = (f'<g transform="translate(606 74) rotate(-4)"><rect width="62" height="74" fill="#fffdf6" {OUT}/>'
           f'<rect width="62" height="18" fill="#e0565b" {OUT}/>' + t(31, 14, MONTH, 13, "#fff", "middle")
           + "".join(f'<circle cx="{9 + (i % 5) * 11}" cy="{28 + (i // 5) * 11}" r="1.8" fill="#b8ad98"/>' for i in range(20))
           + f'<circle cx="31" cy="50" r="7.5" fill="none" stroke="#e0565b" stroke-width="2.4"/>' + t(31, 54, "15", 10, "#e0565b", "middle")
           + '<circle cx="31" cy="-2" r="4" fill="#e0565b" stroke="#3d362c" stroke-width="1.6"/></g>')
    note = (f'<g transform="translate(686 82) rotate(5)"><path d="M0 0 H70 V58 Q66 70 54 72 H0 Z" fill="#ffe066" {OUT}/>'
            + t(8, 26, NOTE, 13, "#e0565b") + '<path d="M8 34 q22 4 44 -1" stroke="#e0565b" stroke-width="2" fill="none" stroke-linecap="round"/>'
            + scribble(8, 50, 40, "#c9a937") + '<circle cx="35" cy="3" r="4" fill="#5b86d6" stroke="#3d362c" stroke-width="1.6"/></g>')
    slip = (f'<g transform="translate(676 150) rotate(-7)"><rect width="56" height="34" fill="#fff" {OUT}/>'
            + scribble(6, 12, 40) + scribble(6, 24, 30) + '<circle cx="28" cy="2" r="3.5" fill="#3fcb73" stroke="#3d362c" stroke-width="1.6"/></g>')
    return (f'<rect x="588" y="54" width="186" height="140" rx="5" fill="#c9965f" {OUT}/>'
            f'<rect x="597" y="63" width="168" height="122" fill="#dcae78"/><rect x="597" y="63" width="168" height="122" fill="url(#st-cork)"/>'
            f'{cal}{note}{slip}')


def shelf():
    books = ""
    x = 1222
    for w, h, c in [(24, 84, "#5b86d6"), (22, 78, "#e0565b"), (26, 86, "#ffd84d"), (22, 74, "#3fcb73"), (24, 82, "#ff9ba3"), (20, 70, "#a98bd8")]:
        books += (f'<rect x="{x}" y="{160 - h}" width="{w}" height="{h}" rx="2" fill="{c}" {OUT}/>'
                  f'<rect x="{x + 4}" y="{160 - h + 14}" width="{w - 8}" height="16" rx="2" fill="#fffdf6" stroke="{LINE}" stroke-width="1.4"/>'
                  f'<circle cx="{x + w / 2}" cy="{160 - 16}" r="3.4" fill="#fffdf6" stroke="{LINE}" stroke-width="1.4"/>')
        x += w + 3
    lean = f'<g transform="translate({x + 4} 160) rotate(14)"><rect x="0" y="-74" width="22" height="74" rx="2" fill="#f0a35a" {OUT}/></g>'
    return (books + lean + f'<rect x="1208" y="160" width="200" height="10" rx="2" fill="#c49a6b" {OUT}/>'
            f'<path d="M1228 170 v18 h14 M1388 170 v18 h-14" {PEN} stroke-width="2.6"/>')


# ---------------- 桌子与桌上的东西 ----------------

def desk():
    grain = "".join(f'<path d="M{x} {y} q{w / 4:.0f} -3 {w / 2:.0f} 0 t{w / 2:.0f} 0" stroke="#c18f58" stroke-width="2" fill="none" stroke-linecap="round" opacity=".6"/>'
                    for x, y, w in [(170, 470, 220), (520, 500, 300), (930, 476, 260), (250, 560, 280), (760, 590, 260), (1060, 548, 200)])
    drawers = "".join(f'<rect x="{x}" y="462" width="220" height="62" rx="5" fill="#d8ab73" {OUT}/>'
                      f'<path d="M{x + 86} 490 h48" stroke="{LINE}" stroke-width="5" stroke-linecap="round"/>'
                      for x in (170, 1030))
    return (f'<rect x="120" y="446" width="1200" height="{H - 446}" fill="#d3a56c" {OUT}/>'
            f'<rect x="120" y="446" width="1200" height="{H - 446}" fill="url(#st-hatch)"/>{grain}{drawers}'
            f'<path d="M112 416 H1328 L1328 432 H112 Z" fill="#e8c897" {OUT}/>'
            f'<path d="M108 430 H1332 V448 H108 Z" fill="#c4925b" {OUT}/>'
            f'<path d="M130 422 H1310" stroke="#f4dcb2" stroke-width="2.4" stroke-linecap="round"/>')


def monitor():
    x, y, w, h = SCREEN
    return (f'<ellipse cx="1000" cy="429" rx="70" ry="7" fill="#3a2a14" opacity=".18"/>'
            f'<rect x="984" y="384" width="32" height="40" fill="#c9ccd3" {OUT}/>'
            f'<path d="M934 428 Q1000 410 1066 428 Z" fill="#b8bcc6" {OUT}/>'
            f'<rect x="856" y="196" width="288" height="196" rx="12" fill="#3a3d45" {OUT}/>'
            f'<rect x="{x - 2}" y="{y - 2}" width="{w + 4}" height="{h + 4}" rx="4" fill="#1d1f24"/>'
            f'<circle class="st-led" cx="1126" cy="384" r="2.6" fill="#3fcb73"/>')


def old_screen():
    x, y, w, h = SCREEN
    head = (f'<rect x="{x}" y="{y}" width="{w}" height="15" fill="#d6d3ca"/>'
            + "".join(f'<circle cx="{x + 9 + i * 9}" cy="{y + 7.5}" r="2.6" fill="{c}"/>' for i, c in enumerate(("#e0565b", "#f0b400", "#3fcb73")))
            + f'<rect x="{x}" y="{y + 15}" width="{w}" height="13" fill="#efede7"/>'
            + "".join(f'<rect x="{x + 6 + i * 16}" y="{y + 18}" width="11" height="7" rx="1.5" fill="#cfcbc1"/>' for i in range(6)))
    gx, gy, rh = x, y + 28, 19
    cols = (x, x + 18, x + 172, x + w)
    grid = f'<rect x="{gx}" y="{gy}" width="{w}" height="{h - 28}" fill="#fff"/>'
    grid += f'<rect x="{gx}" y="{gy}" width="{w}" height="13" fill="#f4f3ef"/>'
    grid += "".join(ui((cols[i] + cols[i + 1]) / 2, gy + 10, ch, 8, "#8f8d87", "middle", family="ui-monospace, Menlo, monospace") for i, ch in enumerate(("", "A", "B")))
    for i in range(7):
        ry = gy + 13 + i * rh
        grid += f'<path d="M{gx} {ry + rh} H{gx + w}" stroke="#e7e5df" stroke-width="1"/>'
        grid += ui(x + 9, ry + 13, str(i + 1), 8, "#8f8d87", "middle", family="ui-monospace, Menlo, monospace")
    grid += "".join(f'<path d="M{cx} {gy} V{y + h}" stroke="#e7e5df" stroke-width="1"/>' for cx in cols[1:3])
    cells = ""
    for i in range(5):
        ry = gy + 13 + i * rh
        s, a = OLD_DONE[i] if i < 2 else ("", "")
        cells += (ui(cols[1] + 5, ry + 13, s, 10, "#1f1e1b", attrs=f' class="st-old-s"')
                  + ui(cols[3] - 6, ry + 13, a, 10, "#1f1e1b", "end", attrs=f' class="st-old-a"', family="ui-monospace, Menlo, monospace"))
    cursor = f'<rect class="st-cell" x="{cols[1]}" y="{gy + 13 + 2 * rh}" width="{cols[2] - cols[1]}" height="{rh}" fill="none" stroke="#1d7f47" stroke-width="2"/>'
    typo = (f'<path class="st-typo" d="M{cols[2] + 40} {gy + 13 + 3 * rh + 16} q4 -4 8 0 t8 0 t8 0 t8 0 t8 0 t8 0" stroke="#e0565b" '
            f'stroke-width="1.8" fill="none" opacity="0"/>')
    return f'<g class="st-scr-old">{head}{grid}{cells}{cursor}{typo}</g>'


def app_screen(L, rows):
    x, y, w, h = SCREEN
    side = (f'<rect x="{x}" y="{y + 20}" width="30" height="{h - 20}" fill="#f4f3ef"/>'
            + "".join(f'<rect x="{x + 8}" y="{y + 32 + i * 18}" width="14" height="9" rx="2.5" fill="{"#141413" if i == 1 else "#d6d3ca"}"/>' for i in range(5)))
    top = (f'<rect x="{x}" y="{y}" width="{w}" height="20" fill="#fff"/><path d="M{x} {y + 20} H{x + w}" stroke="#e7e5df"/>'
           f'<rect x="{x + 8}" y="{y + 5}" width="10" height="10" rx="3" fill="#141413"/>'
           + ui(x + 23, y + 14, "Pearnly", 10.5, "#141413", weight=600, family="'Source Serif 4', Georgia, serif")
           + f'<rect x="{x + w - 50}" y="{y + 4}" width="44" height="12" rx="6" fill="#e3f4ea"/>'
           + ui(x + w - 28, y + 13, f"0 / {ITEMS}", 8.5, "#1d7f47", "middle", 600, ' class="st-count"'))
    cols = L["cols"]
    head = (ui(910, 242, cols[0], 8, "#8f8d87") + ui(946, 242, cols[1], 8, "#8f8d87") + ui(1098, 242, cols[3], 8, "#8f8d87", "end")
            + '<path d="M904 247 H1130" stroke="#e7e5df"/>')
    body = ""
    for k in range(ITEMS):
        d, s, _v, tot = rows[k % len(rows)]
        body += (f'<g class="st-row" opacity="0" transform="translate(0 -40)">'
                 f'<rect class="st-rowhl" x="904" y="250" width="226" height="21" fill="#fff1a8" opacity="0"/>'
                 + ui(910, 264, d, 8.5, "#5f5e5a") + ui(946, 264, s, 9, "#1f1e1b") + ui(1098, 264, tot, 9, "#1f1e1b", "end")
                 + '<path class="st-rowtick" d="M1110 260 l3.5 4 l7 -8" pathLength="1" stroke="#1d7f47" stroke-width="2" fill="none" '
                   'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="1" stroke-dashoffset="1"/>'
                 f'<path d="M904 271 H1130" stroke="#f0eee8"/></g>')
    stamp = (f'<g class="st-stamp" opacity="0" transform="translate(1012 306)">'
             f'<rect x="-76" y="-30" width="152" height="60" rx="10" fill="#fff" stroke="#1d7f47" stroke-width="2.4" opacity=".96"/>'
             '<circle cx="-46" cy="0" r="15" fill="#1d7f47"/><path d="M-53 0 l5 5 l10 -11" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
             + ui(-22, 5, L["saved"], 13, "#1d7f47", weight=600) + ui(-22, 19, f"{ITEMS} / {ITEMS}", 9, "#5f5e5a") + '</g>')
    return (f'<g class="st-scr-app" opacity="0"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff"/>{side}{top}{head}'
            f'<g clip-path="url(#st-rowclip)">{body}</g>{stamp}</g>')


def screen(L, rows):
    x, y, w, h = SCREEN
    return (f'<g clip-path="url(#st-scrclip)">{old_screen()}{app_screen(L, rows)}'
            f'<rect class="st-scr-off" x="{x}" y="{y}" width="{w}" height="{h}" fill="#1d1f24" opacity="0"/>'
            f'<rect class="st-scr-boot" x="{x}" y="{y + h / 2 - 2}" width="{w}" height="4" fill="#fff" opacity="0"/>'
            f'<path d="M{x + 20} {y + 10} L{x + 80} {y + h - 10}" stroke="#fff" stroke-width="16" opacity=".06"/></g>')


def lamp():
    return (f'<ellipse cx="1252" cy="426" rx="36" ry="7" fill="#5b7fd8" {OUT}/>'
            f'<path d="M1252 422 L1276 330 L1222 276" {PEN} stroke-width="7"/><path d="M1252 422 L1276 330 L1222 276" stroke="#5b7fd8" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="1276" cy="330" r="6" fill="#fffdf6" {OUT}/>'
            f'<path d="M1238 262 L1188 302 Q1200 322 1226 316 L1250 278 Z" fill="#5b7fd8" {OUT}/>'
            f'<ellipse class="st-bulb" cx="1206" cy="311" rx="14" ry="7" fill="#fff7d1" stroke="{LINE}" stroke-width="2"/>')


def cactus():
    return (f'<path d="M1286 406 L1318 406 L1314 428 L1290 428 Z" fill="#e58f6a" {OUT}/>'
            f'<path d="M1296 406 V366 Q1302 356 1308 366 V406 Z" fill="#7cc27a" {OUT}/>'
            f'<path d="M1308 386 h8 v-12 q4 -4 6 0 v18 h-14" fill="#7cc27a" {OUT}/>'
            f'<path d="M1300 376 l-3 -2 M1305 390 l3 -2 M1300 398 l-3 -1" stroke="{LINE}" stroke-width="1.4"/>'
            f'<circle cx="1302" cy="362" r="3.4" fill="#ff9ba3" stroke="{LINE}" stroke-width="1.4"/>')


def tray():
    sheets = "".join(f'<rect class="st-tsheet" x="{402 + i % 2 * 3}" y="{419 - i * 5}" width="78" height="5" fill="{PAPER}" stroke="{LINE}" stroke-width="1.6" opacity="0"/>'
                     for i in range(4))
    return (sheets + f'<path d="M394 400 L398 428 H486 L490 400" {PEN} stroke-width="2.6"/>'
            f'<path d="M396 412 H488 M398 422 H486" stroke="{LINE}" stroke-width="1.6" opacity=".55"/>'
            + "".join(f'<path d="M{x} 402 V428" stroke="{LINE}" stroke-width="1.6" opacity=".55"/>' for x in (414, 432, 450, 468)))


def keyboard():
    keys = "".join(f'<rect x="{n(578 + c * 13 - r * 2.6)}" y="{n(416.5 + r * 3.8)}" width="10" height="3" rx="1" fill="#fffdf6" stroke="{LINE}" stroke-width="1"/>'
                   for r in range(3) for c in range(10))
    return (f'<path d="M562 429 L718 429 L710 413 L576 413 Z" fill="#d9d4c8" {OUT}/>{keys}'
            f'<rect x="604" y="427.4" width="56" height="2.4" rx="1" fill="#fffdf6" stroke="{LINE}" stroke-width="1"/>')


def pile():
    """单据堆：每张一条薄纸（site.js 按张数显示），顶上一张露出正面。"""
    slabs = ""
    for i in range(PILE_MAX):
        dx, rot = ((i * 37) % 11) - 5, (((i * 53) % 7) - 3) * .35
        fill = PAPER if i % 3 else "#f4ecdb"
        slabs += (f'<rect class="st-slab" x="{206 + dx}" y="{DESK - (i + 1) * SLAB}" width="170" height="{SLAB}" fill="{fill}" '
                  f'stroke="{LINE}" stroke-width="1.6" transform="rotate({rot} {291 + dx} {DESK - i * SLAB})"/>')
    face = (f'<g class="st-ptop"><path d="M214 0 L370 0 L380 {SLAB} H206 Z" fill="{PAPER}" {OUT} stroke-width="1.8"/>'
            f'<path d="M228 -16 L362 -16 L370 0 L214 0 Z" fill="#fffdf6" stroke="{LINE}" stroke-width="1.8" stroke-linejoin="round"/>'
            + scribble(236, -9, 60, "#c4b9a5", 1.6) + scribble(304, -9, 36, "#e0a4a4", 1.6) + '</g>')
    return f'<g class="st-pile">{slabs}{face}</g>'


def paper_sheet(cls, w=64, h=82, extra=""):
    """一张单据（中心在原点）。"""
    lines = (scribble(-w / 2 + 8, -h / 2 + 30, w * .55) + scribble(-w / 2 + 8, -h / 2 + 42, w * .7) + scribble(-w / 2 + 8, -h / 2 + 54, w * .45)
             + f'<path d="M{n(w / 2 - 26)} {n(h / 2 - 12)} h18" stroke="{LINE}" stroke-width="2.4" stroke-linecap="round"/>')
    return (f'<g class="{cls}"{extra}><rect x="{-w / 2}" y="{-h / 2}" width="{w}" height="{h}" fill="{PAPER}" {OUT}/>'
            f'<circle cx="{n(-w / 2 + 12)}" cy="{n(-h / 2 + 13)}" r="6" fill="#ffa94d" stroke="{LINE}" stroke-width="1.6"/>'
            + scribble(-w / 2 + 22, -h / 2 + 14, w * .4, "#8a8170", 1.8) + lines + '</g>')


def mug(cls, at, steam=True, full=True):
    st = ('<g class="st-steam" stroke="#b8ab95" stroke-width="2.6" fill="none" stroke-linecap="round">'
          '<path d="M-8 -48 q-6 -8 0 -16 q6 -8 0 -16"/><path d="M6 -46 q-6 -8 0 -16 q6 -8 0 -16"/></g>') if steam else ""
    inside = '<ellipse cx="0" cy="-36" rx="15" ry="4" fill="#8b5a35"/>' if full else '<ellipse cx="0" cy="-36" rx="15" ry="4" fill="#efe6d4"/><path d="M-10 -30 q10 4 20 0" stroke="#c48a5a" stroke-width="2" fill="none" opacity=".6"/>'
    return (f'<g class="{cls}" transform="translate({at[0]} {at[1]})">{st}<path d="M17 -30 q16 0 16 12 q0 12 -16 12" {PEN} stroke-width="5"/>'
            f'<path d="M17 -30 q16 0 16 12 q0 12 -16 12" stroke="#fffdf6" stroke-width="2" fill="none"/>'
            f'<path d="M-18 -36 H18 L15 -2 Q14 0 11 0 H-11 Q-14 0 -15 -2 Z" fill="#fffdf6" {OUT}/>'
            f'<ellipse cx="0" cy="-36" rx="18" ry="5" fill="#fffdf6" {OUT}/>{inside}'
            f'<path d="M-10 -24 h20" stroke="#ffd84d" stroke-width="5" stroke-linecap="round"/></g>')


def phone_flat():
    return (f'<g class="st-pflat"><ellipse class="st-pglow" cx="527" cy="420" rx="40" ry="12" fill="#a7f0c2" opacity="0" filter="url(#st-soft)"/>'
            f'<path d="M502 428 L552 428 L548 419 L506 419 Z" fill="#3a3d45" {OUT} stroke-width="2"/>'
            f'<path class="st-pscreen" d="M507 426.5 L547 426.5 L544.5 420.5 L509.5 420.5 Z" fill="#2a2c33"/>'
            '<g class="st-buzz" stroke="#3d362c" stroke-width="2.4" stroke-linecap="round" fill="none" opacity="0">'
            '<path d="M494 408 q-6 6 0 12 M486 404 q-9 10 0 20 M560 408 q6 6 0 12 M568 404 q9 10 0 20"/></g></g>')


def line_bubble(L):
    return (f'<g class="st-bubble" opacity="0" transform="translate(478 346)">'
            f'<path d="M-62 -24 H62 Q72 -24 72 -14 V12 Q72 22 62 22 H56 L50 36 L40 22 H-62 Q-72 22 -72 12 V-14 Q-72 -24 -62 -24 Z" fill="#06c755" {OUT}/>'
            f'<rect x="-60" y="-14" width="26" height="26" rx="7" fill="#fff" stroke="{LINE}" stroke-width="1.6"/>'
            f'<rect x="-54" y="-8" width="14" height="16" rx="2" fill="#fffdf6" stroke="{LINE}" stroke-width="1.4"/>'
            f'<path d="M-51 -2 h8 M-51 2 h6" stroke="{LINE}" stroke-width="1.2"/>'
            + ui(-26, -2, "Pearnly", 12, "#fff", weight=600, family="'Source Serif 4', Georgia, serif")
            + f'<rect x="-26" y="4" width="78" height="5" rx="2.5" fill="#fff" opacity=".7"/></g>')


def phone_up():
    """拿在手里的手机（横着，屏幕朝你）：取景框里是一张单据，拍下时屏幕一白，拍好右上角弹出绿勾。"""
    return (f'<g class="st-pup" opacity="0"><rect x="-60" y="-34" width="120" height="68" rx="13" fill="#3a3d45" {OUT}/>'
            f'<g clip-path="url(#st-viewclip)"><rect x="-52" y="-27" width="104" height="54" fill="#5d5a52"/>'
            f'<g class="st-vdoc"><rect x="-22" y="-22" width="44" height="48" fill="{PAPER}" stroke="{LINE}" stroke-width="1.4" transform="rotate(-4)"/>'
            + scribble(-14, -10, 26, "#b0a690", 1.6) + scribble(-14, -2, 30, "#b0a690", 1.6) + scribble(-14, 6, 20, "#b0a690", 1.6) + '</g>'
            '<path d="M-40 -18 v-6 h8 M40 -18 v-6 h-8 M-40 18 v6 h8 M40 18 v6 h-8" stroke="#ffd84d" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
            '<rect class="st-shutter" x="-52" y="-27" width="104" height="54" fill="#fff" opacity="0"/></g>'
            '<g class="st-ok" opacity="0" transform="translate(38 -16)"><circle r="9" fill="#06c755" stroke="#fff" stroke-width="2"/>'
            '<path d="M-4 0 l3 3 l6 -6" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>'
            f'<circle cx="53" cy="0" r="3" fill="#1d1f24"/></g>')


def helper():
    """画面外伸进来的一只手（同事），往单据堆上放两张新单据；手腕在原点，袖子一直伸到画面外。"""
    return (f'<g class="st-helper" opacity="0" transform="translate(-200 300)">'
            f'<rect x="-60" y="8" width="170" height="7" fill="{PAPER}" stroke="{LINE}" stroke-width="1.6" transform="rotate(-1.5 25 15)"/>'
            f'<rect x="-56" y="15" width="170" height="7" fill="#f4ecdb" stroke="{LINE}" stroke-width="1.6"/>'
            f'<rect x="-760" y="-14" width="752" height="28" rx="6" fill="#7a9be0" {OUT}/>'
            f'<path d="M-700 -4 H-60 M-640 6 H-120" stroke="#5b7fd8" stroke-width="2" stroke-linecap="round"/>'
            f'<rect x="-24" y="-16" width="16" height="32" rx="4" fill="#fffdf6" {OUT}/>'
            f'<path d="M-8 -9 Q16 -14 22 0 Q20 12 -8 10 Z" fill="#f6d2b6" {OUT}/>'
            f'<path d="M4 -6 q8 2 10 6 M2 2 q8 2 10 4" stroke="{LINE}" stroke-width="1.6" fill="none" stroke-linecap="round"/></g>')


def stream():
    """一口气全拍进去时，从手机到屏幕的一道数据流（铅笔虚线，往前流）。"""
    return '<path class="st-stream" d="M0 0" stroke="#1d7f47" stroke-width="3" stroke-dasharray="2 11" stroke-linecap="round" fill="none" opacity="0"/>'


def burst():
    return ('<g class="st-burst" opacity="0" stroke="#ffd84d" stroke-width="4" stroke-linecap="round">'
            '<path d="M0 14 V30 M-18 10 L-30 24 M18 10 L30 24 M-30 0 H-44 M30 0 H44"/></g>')


# ---------------- 小单 ----------------

def zigzag(top=-100, half=39):
    zig, up = [], True
    for _ in range(8):
        zig.append(f"l{n(-half / 4)} {-8 if up else 8}")
        up = not up
    return f"M{-half} {top + 12} Q{-half} {top} {-half + 12} {top} H{half - 12} Q{half} {top} {half} {top + 12} V0 {' '.join(zig)} Z"


def mascot_body():
    """会计装扮的小单（头顶一道黄条、圆眼镜、身上印着金额）；眼睛、嘴、汗、黑眼圈都有几种，site.js 按剧情切换。"""
    eyes = ('<g class="st-look">'
            '<g class="st-e st-e-open"><circle cx="-10" cy="-72" r="4.2" fill="currentColor" stroke="none"/><circle cx="14" cy="-72" r="4.2" fill="currentColor" stroke="none"/>'
            '<circle cx="-8.6" cy="-73.6" r="1.4" fill="#fff" stroke="none"/><circle cx="15.4" cy="-73.6" r="1.4" fill="#fff" stroke="none"/></g>'
            '<g class="st-e st-e-wide" opacity="0"><circle cx="-10" cy="-72" r="6" fill="currentColor" stroke="none"/><circle cx="14" cy="-72" r="6" fill="currentColor" stroke="none"/>'
            '<circle cx="-8" cy="-74" r="2" fill="#fff" stroke="none"/><circle cx="16" cy="-74" r="2" fill="#fff" stroke="none"/></g>'
            '<path class="st-e st-e-squint" opacity="0" d="M-15 -72 h10 M9 -72 h10" stroke-width="2.6"/>'
            '<path class="st-e st-e-happy" opacity="0" d="M-15 -70 q5 -7 10 0 M9 -70 q5 -7 10 0" stroke-width="2.6"/>'
            '<path class="st-e st-e-closed" opacity="0" d="M-15 -72 q5 4 10 0 M9 -72 q5 4 10 0" stroke-width="2.6"/>'
            '</g>')
    mouths = ('<path class="st-m st-m-smile" d="M-4 -53 q6 6 12 0" stroke-width="2.6"/>'
              '<path class="st-m st-m-flat" opacity="0" d="M-3 -51 h10" stroke-width="2.6"/>'
              '<path class="st-m st-m-wavy" opacity="0" d="M-7 -50 q2.5 -3 5 0 t5 0 t5 0" stroke-width="2.4"/>'
              '<ellipse class="st-m st-m-o" opacity="0" cx="2" cy="-51" rx="3.2" ry="4.2" fill="#e86a6a" stroke-width="2.2"/>'
              '<path class="st-m st-m-big" opacity="0" d="M-7 -55 q9 13 18 0 Z" fill="#e86a6a" stroke-width="2.4"/>')
    return (f'<path d="{zigzag()}" fill="{PAPER}"/>'
            '<path d="M-39 -88 Q-39 -100 -27 -100 H27 Q39 -100 39 -88 V-86 H-39 Z" fill="#ffd84d"/>'
            '<path class="st-bags" opacity="0" d="M-18 -62 q8 4 16 0 M6 -62 q8 4 16 0" stroke="#9c8fb8" stroke-width="2.4"/>'
            '<ellipse cx="-23" cy="-60" rx="6.5" ry="3.8" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            '<ellipse cx="27" cy="-60" rx="6.5" ry="3.8" fill="#ff9ba3" stroke="none" opacity=".85"/>'
            + eyes
            + '<circle cx="-10" cy="-72" r="9.5" fill="none" stroke-width="2.4"/><circle cx="14" cy="-72" r="9.5" fill="none" stroke-width="2.4"/>'
            '<path d="M-0.5 -72 h5" stroke-width="2.4"/>'
            + mouths
            + '<path d="M-24 -37 H18 M-24 -28 H6" stroke="#bdb7aa" stroke-width="3"/>'
            + t(29, -14, "2,889.00", 11.5, "#2d2b27", "end", ' stroke="none"')
            + '<path class="st-sweat" opacity="0" d="M44 -84 q-5 8 0 11 q5 -3 0 -11 Z" fill="#9fd3f0" stroke-width="1.8"/>'
            + '<g class="st-excl" opacity="0" stroke-width="2.6"><path d="M2 -150 V-128" stroke-width="5"/><circle cx="2" cy="-118" r="2.8" fill="currentColor"/>'
              '<path d="M-20 -142 l-8 -6 M24 -142 l8 -6 M-24 -124 h-9 M28 -124 h9"/></g>')


def mascot():
    pen = f'color="{INK}" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    x, y = HOME
    return (f'<g class="st-mascot" {pen} filter="url(#st-pencil)">'
            f'<g class="st-body" transform="translate({x} {y}) scale({K})" stroke-width="{n(3 / K)}">{mascot_body()}</g></g>')


def arms():
    pen = f'color="{INK}" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    return (f'<g class="st-arms" {pen} filter="url(#st-pencil)">'
            '<path class="st-arm-l" d="M575 391 Q560 410 588 424"/><path class="st-arm-r" d="M705 391 Q720 410 700 424"/>'
            f'<circle class="st-hand-l" cx="588" cy="424" r="10" fill="{PAPER}"/><circle class="st-hand-r" cx="700" cy="424" r="10" fill="{PAPER}"/></g>')


def effects():
    puff = ('<g class="st-puff" opacity="0"><path d="M0 0 q-4 -12 8 -14 q4 -10 16 -4 q12 -2 10 10 q8 6 -2 12 H4 q-8 0 -4 -4 Z" '
            f'fill="#fff" {OUT} stroke-width="2"/></g>')
    sparks = "".join(f'<path class="st-spark" opacity="0" transform="translate({x} {y})" d="M0 -11 L3 -3 L11 0 L3 3 L0 11 L-3 3 L-11 0 L-3 -3 Z" '
                     f'fill="#ffd84d" stroke="{LINE}" stroke-width="1.6"/>' for x, y in ((470, 236), (566, 196), (760, 228), (430, 330), (792, 318)))
    return puff + sparks


def swipe():
    """转场：一道黄色记号笔从左刷到右，盖满画面的那一刻换到第二天早上。"""
    edge_l = " ".join(f"L{(-30 if i % 2 else 0) - i * 6} {i * 40}" for i in range(18))
    edge_r = " ".join(f"L{2150 + (26 if i % 2 else 0) - (17 - i) * 6} {(17 - i) * 40}" for i in range(18))
    streaks = "".join(f'<path d="M{40 + i * 30} {30 + i * 46} H{2050 - i * 20}" stroke="#f2c21b" stroke-width="10" stroke-linecap="round" opacity=".55"/>'
                      for i in range(14))
    return (f'<g class="st-swipe" transform="translate(-2200 0)"><g transform="skewX(-12)">'
            f'<path d="M0 0 {edge_l} L2150 680 {edge_r} Z" fill="#ffd84d"/>{streaks}'
            f'<g transform="translate(1000 270)" {OUT} stroke-width="4">'
            '<path d="M0 -70 V-50 M0 50 V70 M-70 0 H-50 M50 0 H70 M-50 -50 L-36 -36 M36 36 L50 50 M50 -50 L36 -36 M-36 36 L-50 50" fill="none"/>'
            '<circle r="36" fill="#fff3b0"/><path d="M-14 -6 q4 -6 8 0 M6 -6 q4 -6 8 0 M-10 10 q10 10 20 0" fill="none"/></g></g></g>')


# ---------------- 组装 ----------------

def svg(L, rows):
    flying = "".join(paper_sheet("st-fly", 46, 58, ' opacity="0"') for _ in range(ITEMS))
    chips = "".join('<g class="st-chip" opacity="0"><rect x="-17" y="-8" width="34" height="16" rx="5" fill="#e3f4ea" stroke="#1d7f47" stroke-width="2"/>'
                    '<path d="M-11 -2 h14 M-11 3 h22" stroke="#1d7f47" stroke-width="1.8" stroke-linecap="round"/></g>' for _ in range(ITEMS))
    night_hole = f"M0 0 H{W} V{H} H0 Z M{SCREEN[0]} {SCREEN[1]} V{SCREEN[1] + SCREEN[3]} H{SCREEN[0] + SCREEN[2]} V{SCREEN[1]} Z"
    return f"""<svg class="st" viewBox="{VIEW}" data-view="{VIEW}" data-view-sm="{VIEW_SM}" role="img" aria-label="{esc(L["alt"])}">
{DEFS}
<g clip-path="url(#st-frame)">
<g filter="url(#st-wobble)">{room()}{window()}{corkboard()}{shelf()}</g>
<g filter="url(#st-wobble)">{clock()}</g>
{mascot()}
<g filter="url(#st-wobble)">{desk()}</g>
<g filter="url(#st-pencil)">{monitor()}{lamp()}{cactus()}{tray()}{keyboard()}</g>
{screen(L, rows)}
<g filter="url(#st-pencil)">{pile()}{mug("st-mug2", (1094, DESK), False, False)}{mug("st-mug3", (1130, DESK), False, False)}</g>
<g class="st-props">{phone_flat()}{mug("st-mug", (770, DESK))}{paper_sheet("st-held", extra=' opacity="0"')}{helper()}{phone_up()}</g>
{arms()}
<g class="st-fx">{effects()}{stream()}{burst()}{flying}{chips}</g>
<path class="st-night" d="{night_hole}" fill-rule="evenodd" fill="#1a2142" opacity="0"/>
<ellipse class="st-glow" cx="1000" cy="292" rx="190" ry="130" fill="#cfe3ff" opacity="0" filter="url(#st-soft)"/>
{line_bubble(L)}
<path class="st-light" d="M1190 300 L1226 314 L1330 430 L1060 430 Z" fill="url(#st-lampglow)" opacity="0"/>
<rect class="st-flash" width="{W}" height="{H}" fill="#fff" opacity="0"/>
<rect width="{W}" height="{H}" filter="url(#st-tex)" opacity=".35" pointer-events="none"/>
{swipe()}
</g>
</svg>"""


def figure(L, rows):
    caps = json.dumps(L["caps"], ensure_ascii=False)
    return (f'<figure class="st-card" data-caps="{esc(caps)}">{svg(L, rows)}'
            f'<figcaption class="st-cap" aria-live="off">{esc(L["caps"][-1][1])}</figcaption>'
            f'<button type="button" class="st-replay" hidden>'
            '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>'
            f'<span>{esc(L["replay"])}</span></button></figure>')


def chars(L):
    """手写字体要用到的字（中文手写字按字取子集）。"""
    return "".join(c for _t, c in L["caps"]) + L["replay"]
