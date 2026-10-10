"""生成智能体入口 fatcc.com 的两步登录页（ADR-019），同一份设计出两处，保证两步看起来是同一个页面：

1. 入口站点的第一步（用户名或邮箱 → 继续；多家公司时选一次）：apps/pearnly_control/pearnly_control/www/ai.html
2. Keycloak 的 ai 专用登录主题（密码页、找回密码、设新密码、出错页）：infrastructure/keycloak/themes/pearnly-ai/

小人与 MR.ERP 记账本直接用宣传页 website/scenes.py 生成（不重画），背景点阵与黄色记号笔照搬宣传页。
入口站点在计费服务那套程序上，没有静态文件服务，所以页面的样式、脚本、插图都内联在页面里。

用法：python3 scripts/build-ai-entry.py          生成
      python3 scripts/build-ai-entry.py --check  只检查生成物是否与本脚本一致（CI 用）
"""
import base64
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "website"))
from poster import INK, PAPER  # noqa: E402
from scenes import DEFS, PEN, mascot  # noqa: E402

PAGE = ROOT / "apps/pearnly_control/pearnly_control/www/ai.html"
THEME = ROOT / "infrastructure/keycloak/themes/pearnly-ai/login"
LINE_URL = "https://line.me/R/ti/p/@pearnly"
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&family=Sriracha&display=swap" rel="stylesheet">')
# 登录框旁只有橘猫（左上角是黑体「CC」，不放标题和副标题，用户 2026-10-10）
# 猫用用户自己用 Gemini 生成的水彩橘猫（2026-10-10 定稿：趴在登录框上沿后面探头，两只前爪搭在框边，尾巴翘着）。
# 抠图脚本 website/static/cc/source/cut_top.py：去掉棋盘格背景和原图那条横边（登录框上沿代替它），
# 两颗眼珠抠成单独的小图叠回眼睛里，跟着鼠标转、输密码时往上看（不看你输入）。直接内嵌进页面（入口页与 Keycloak 主题都不用另放静态文件）。
def _data(name, kind="image/webp"):
    return f"data:{kind};base64," + base64.b64encode((ROOT / "website/static/cc" / name).read_bytes()).decode()


def _cat_svg():
    meta = json.loads((ROOT / "website/static/cc/top.json").read_text())
    w, h = meta["size"]
    clips = "".join(f'<clipPath id="cc-eye{i}"><polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in e["clip"])}"/></clipPath>'
                    for i, e in enumerate(meta["eyes"]))
    eyes = "".join(f'<g clip-path="url(#cc-eye{i})"><g class="cc-look"><image href="{_data(e["img"])}" x="{e["x"]}" y="{e["y"]}" '
                   f'width="{e["size"]}" height="{e["size"]}"/></g></g>' for i, e in enumerate(meta["eyes"]))
    return (f'<svg class="cc-perch" viewBox="0 0 {w} {h}" aria-hidden="true" focusable="false"><defs>{clips}</defs>'
            f'<image href="{_data("top.webp")}" width="{w}" height="{h}"/>{eyes}</svg>')


CC_CAT = _cat_svg()
# 左上角只放 CC 图标（白字带胡须的原版，frontend/assistant/src/brand/cc-icon.svg；用户 2026-10-10：不要文字、图标大一点）
BRAND_MARK = ((ROOT / "frontend/assistant/src/brand/cc-icon.svg").read_text()
              .replace('width="1024" height="1024"', 'width="46" height="46" role="img" aria-label="CC"').strip())
# 标签页小图标：白字 CC（32 像素去胡须版，frontend/assistant/public/icon-32.png），直接内嵌
FAVICON = ('<link rel="icon" type="image/png" href="data:image/png;base64,'
           + base64.b64encode((ROOT / "frontend/assistant/public/icon-32.png").read_bytes()).decode() + '">')
MAIL_ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5f5e5a" stroke-width="1.9" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m4 7 8 6 8-6"/></svg>')
YOUTUBE_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="4.5" fill="#ff0033"/>'
                '<path d="M10 9v6l5.2-3z" fill="#fff"/></svg>')
CONTACTS = ('<div class="contacts">'
            f'<a href="{"https://line.me/R/ti/p/@pearnly"}" target="_blank" rel="noopener">' + "{line}" + '@pearnly</a>'
            '<a href="mailto:hello@pearnly.com">' + "{mail}" + 'hello@pearnly.com</a>'
            '<a href="https://www.youtube.com/@pearnlyth" target="_blank" rel="noopener">' + "{yt}" + 'YouTube</a></div>')
LINE_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="#06c755" aria-hidden="true"><path d="M12 3C6.5 3 2 6.6 2 11c0 3.9 3.5 7.2 8.3 7.9.3.1.8.2.9.5.1.3.1.7 0 .9l-.1.9c0 .3-.2 1 .9.6 1.1-.5 5.9-3.5 8-6C21.4 14.2 22 12.7 22 11c0-4.4-4.5-8-10-8Z"/></svg>')




CONTACTS = CONTACTS.format(line=LINE_ICON.replace('width="17" height="17"', 'width="16" height="16"'), mail=MAIL_ICON, yt=YOUTUBE_ICON)

# ---------------- 插图：宣传页的小人 + 对话泡点缀（每个泡对得上它主人的身份） ----------------

def bubble(x, y, w, h, inner, cls=""):
    return (f'<g transform="translate({x} {y})"><g class="deco bubble {cls}">'
            f'<rect x="0" y="0" width="{w}" height="{h}" rx="16" fill="{PAPER}"/>'
            f'<path d="M14 {h - 1} l4 14 l10 -14" fill="{PAPER}"/>{inner}</g></g>')


def sparkle(x, y, s=1.0, cls=""):
    return (f'<g transform="translate({x} {y}) scale({s})"><g class="deco sparkle {cls}">'
            '<path d="M0 -12 Q2 -2 12 0 Q2 2 0 12 Q-2 2 -12 0 Q-2 -2 0 -12 Z" fill="#ffd84d" stroke-width="2.2"/></g></g>')


def receipt(x, y, rot, cls=""):
    zig = " l-4 6 l-4 -6" * 4
    return (f'<g transform="translate({x} {y}) rotate({rot})"><g class="deco drift {cls}">'
            f'<path d="M0 0 H32 V40{zig} Z" fill="{PAPER}" stroke-width="2.4"/>'
            '<path d="M7 10 H25 M7 18 H21 M7 26 H17" stroke="#bdb7aa" stroke-width="2.4"/></g></g>')


def plane(x, y, cls=""):
    return (f'<g transform="translate({x} {y})"><g class="deco plane {cls}">'
            f'<path d="M0 12 L44 0 L20 28 Z" fill="{PAPER}" stroke-width="2.4"/><path d="M44 0 L14 17 L20 28" stroke-width="2.2"/>'
            '<path d="M-6 22 q-20 6 -40 -2 q-16 -6 -30 2" stroke="#bdb7aa" stroke-width="2.2" stroke-dasharray="2 7"/></g></g>')


DOTS = "".join(f'<circle class="dot d{i}" cx="{22 + i * 16}" cy="20" r="4" fill="{INK}" stroke="none"/>' for i in range(3))
TICK = '<path d="M18 21 l8 8 l16 -18" stroke="#2e8a55" stroke-width="3.6"/>'
CHART = ('<path d="M16 32 V18 M28 32 V10 M40 32 V22 M52 32 V14" stroke="#5b86d6" stroke-width="5"/>'
         '<path d="M12 34 H58" stroke-width="2"/>')
MONEY = f'<text x="46" y="27" font-size="15" font-family="Sriracha, cursive" text-anchor="middle" fill="{INK}" stroke="none">฿540.00</text>'
PHOTO = ('<rect x="16" y="9" width="34" height="25" rx="4" fill="#eaf3fb" stroke-width="2.4"/>'
         '<path d="M18 31 l10 -10 l7 7 l5 -5 l8 8" stroke="#5b86d6" stroke-width="2.4"/>'
         '<circle cx="41" cy="16" r="3" fill="#ffd84d" stroke-width="1.8"/>')


def scene(cls, view, ground, parts):
    return (f'<svg class="scene-svg {cls}" viewBox="{view}" preserveAspectRatio="xMidYMax meet" aria-hidden="true" focusable="false">'
            f'<g filter="url(#hd-pencil)" {PEN}><path d="{ground}" stroke="#c7cfdb" stroke-width="3" stroke-dasharray="10 9"/>'
            + "".join(parts) + '</g></svg>')


def stand(x, ground, k, prop, kind, lift=8):
    """小人站在虚线上方一点点（脚底离线 lift），不踩线；脚底在髋部下方 41 个单位。"""
    return mascot(x, ground - lift - 41 * k, k, "", prop, kind)


# 底部一排：宣传页的小人往左挪，右边留给睡觉的橘猫视频（用户 2026-10-10，Gemini 生成的视频剪前 5 秒循环）
SCENE_WIDE = scene("wide", "0 0 1200 300", "M40 286 H1160", [
    bubble(166, 92, 76, 40, DOTS, "typing"), bubble(373, 118, 66, 44, PHOTO, "b2"),
    bubble(575, 66, 92, 42, MONEY, "b3"), bubble(869, 28, 70, 44, CHART, "b4"), bubble(943, 96, 62, 42, TICK, "b2"),
    sparkle(76, 120, 0.9, "s1"), sparkle(440, 74, 0.7, "s2"), sparkle(612, 128, 0.9, "s3"),
    receipt(520, 204, -14, "r1"), plane(760, 30, "p1"),
    stand(140, 286, 0.95, "pencil", "cowork"), stand(350, 286, 0.95, "camera", "dms"),
    stand(555, 286, 0.95, "basket", "erp"), stand(820, 286, 1.55, "stamp", "mrerp")])
SCENE_PHONE = scene("phone", "0 0 620 330", "M10 316 H610", [
    bubble(86, 150, 70, 38, DOTS, "typing"), bubble(226, 170, 66, 44, PHOTO, "b2"), bubble(346, 120, 92, 42, MONEY, "b3"),
    bubble(536, 40, 70, 44, CHART, "b4"), sparkle(26, 150, 0.8, "s1"), sparkle(452, 110, 0.7, "s2"), sparkle(608, 130, 0.7, "s4"),
    plane(430, 62, "p1"),
    stand(70, 316, 0.74, "pencil", "cowork", 7), stand(200, 316, 0.74, "camera", "dms", 7),
    stand(330, 316, 0.74, "basket", "erp", 7), stand(500, 316, 1.18, "stamp", "mrerp", 7)])

# 睡觉的橘猫：逐帧抠掉棋盘格背景（website/static/cc/source/cut_nap_video.py）的透明视频，
# Safari 用 HEVC 带透明的 mp4，Chrome / Edge / Firefox 用 VP9 带透明的 webm；放在宽屏那排的最右边，躺在地面线上，5 秒循环。
NAP_DIR = ROOT / "apps/pearnly_control/pearnly_control/public/cc"
NAP_FILES = {ext: f"cat-nap.{hashlib.sha256((NAP_DIR / f'cat-nap.{ext}').read_bytes()).hexdigest()[:10]}.{ext}" for ext in ("webm", "mp4")}


def scene_with_nap(base, hashed):
    names = NAP_FILES if hashed else {ext: f"cat-nap.{ext}?v={NAP_FILES[ext].split('.')[1]}" for ext in NAP_FILES}
    video = ('<video class="cc-nap" autoplay muted loop playsinline preload="auto" aria-hidden="true" tabindex="-1">'
             f'<source src="{base}{names["mp4"]}" type=\'video/mp4; codecs="hvc1"\'>'
             f'<source src="{base}{names["webm"]}" type="video/webm"></video>')
    return f'<div class="nap-wrap">{SCENE_WIDE}{video}</div>{SCENE_PHONE}'

# ---------------- 样式：宣传页的底色、点阵、记号笔与按钮；也给 Keycloak 流程页的表单类名上样式 ----------------

CSS = """
:root{--bg:#f8f8f6;--ink:#141413;--text:#1f1e1b;--muted:#5f5e5a;--faint:#8f8d87;--line:#e7e5df;--line-strong:#d6d3ca;--soft:#f2f1ed;--ease:cubic-bezier(.2,.7,.2,1);
--sans:"IBM Plex Sans Thai",-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
:lang(zh) body,:lang(zh-CN) body{font-family:"PingFang SC","Noto Sans SC","Microsoft YaHei",var(--sans)}
canvas.dots{position:fixed;inset:0;z-index:0;pointer-events:none}
.hd-defs{position:absolute}
.page{position:relative;z-index:1;min-height:100dvh;display:flex;flex-direction:column;align-items:center;overflow-x:clip}
.top{align-self:stretch;display:flex;align-items:center;justify-content:space-between;padding:18px 24px}
.brand{display:flex;align-items:center;gap:9px;font-family:"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;font-weight:800;font-size:24px;letter-spacing:.02em;line-height:1;color:#000}.brand svg{display:block;flex:none}
.langs{display:flex;gap:2px;padding:3px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.7)}
.langs a{font-size:13px;color:var(--muted);padding:3px 11px;border-radius:999px;text-decoration:none}
.langs a.on{background:var(--ink);color:#fff}
.hero{width:100%;max-width:560px;padding:clamp(28px,8vh,84px) 16px 0;text-align:center}
.card{position:relative;width:100%;max-width:392px;margin:212px auto 0;background:#fff;border:1px solid var(--line);border-radius:20px;padding:24px;text-align:left;
box-shadow:0 1px 2px rgba(20,20,19,.04),0 16px 40px -12px rgba(20,20,19,.14),0 48px 96px -32px rgba(20,20,19,.12)}
.card h2{margin:0 0 14px;font-size:17px;font-weight:600;color:var(--ink);text-align:center}
.input,.field input{display:block;width:100%;height:44px;border:1px solid var(--line-strong);border-radius:12px;padding:0 14px;font:inherit;font-size:15px;color:var(--ink);background:#fff;outline:none;transition:border-color .15s,box-shadow .15s}
.input::placeholder,.field input::placeholder{color:var(--faint)}
.input:focus,.field input:focus{border-color:var(--ink);box-shadow:0 0 0 3px rgba(20,20,19,.07)}
.btn,.primary-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;height:42px;margin-top:12px;border:0;border-radius:999px;background:var(--ink);color:#fff;font:inherit;font-size:15px;font-weight:500;cursor:pointer;text-decoration:none;
box-shadow:0 1px 2px rgba(20,20,19,.2),inset 0 1px 0 rgba(255,255,255,.08);transition:transform .2s var(--ease),background .2s}
.btn:hover,.primary-btn:hover{background:#2a2925;transform:translateY(-1px)}
.btn[disabled]{opacity:.6;cursor:default;transform:none}
.btn.light{background:#fff;color:var(--ink);border:1px solid var(--line);box-shadow:0 1px 2px rgba(20,20,19,.06);margin-top:0}
.btn.light:hover{background:#fff;border-color:var(--line-strong)}
.contacts{display:flex;flex-wrap:wrap;justify-content:center;gap:6px 16px;margin-top:20px}
.contacts a{display:inline-flex;align-items:center;gap:6px;color:var(--ink);font-size:13px;text-decoration:none;padding:2px 0;border-bottom:1px solid transparent}
.contacts a:hover{border-bottom-color:var(--line-strong)}
.hm-spark{transform-box:fill-box;transform-origin:center}
.hm-spark{animation:twinkle 2.2s ease-in-out infinite}
.hm-laugh{opacity:0}.hm-ai .hm-calm,.hm-ai .hm-laugh{animation:tomo-calm 8s infinite}.hm-ai .hm-laugh{animation-name:tomo-laugh}
@keyframes tomo-calm{0%,72%,92%,100%{opacity:1}75%,89%{opacity:0}}@keyframes tomo-laugh{0%,72%,92%,100%{opacity:0}75%,89%{opacity:1}}
.hm-ai:hover .hm-calm,.hm.hm-laughing .hm-calm{animation:none;opacity:0}.hm-ai:hover .hm-laugh,.hm.hm-laughing .hm-laugh{animation:none;opacity:1}
.or{display:flex;align-items:center;gap:12px;margin:18px 0 14px;color:var(--faint);font-size:12.5px}
.or::before,.or::after{content:"";flex:1;height:1px;background:var(--line)}
.who{display:flex;align-items:center;justify-content:space-between;gap:8px;height:44px;padding:0 6px 0 14px;border-radius:12px;background:var(--soft);margin-bottom:10px;font-size:15px;color:var(--ink)}
.who span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.who button,.link-btn{border:0;background:none;color:var(--muted);font:inherit;font-size:13px;padding:4px 8px;border-radius:999px;cursor:pointer;text-decoration:none}
.who button:hover,.link-btn:hover{background:#e8e6df;color:var(--ink)}
.forgot{display:block;margin-top:12px;text-align:center}
.legal{margin:16px 0 0;text-align:center;color:var(--faint);font-size:12px;line-height:1.6}
.legal a{color:var(--muted);text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--line-strong);white-space:nowrap}
.err,.pn-field-error{display:block;margin:6px 2px 0;color:#c4373a;font-size:12.5px}
.err[hidden]{display:none}
.notice,.auth-message:not(:empty){margin:0 0 12px;padding:9px 12px;border-radius:10px;background:var(--soft);color:var(--text);font-size:13.5px}
.auth-message.error{background:#fdecec;color:#a52d2f}.auth-message.success{background:#e8f5ec;color:#1d6b3c}
.picks{display:grid;gap:8px}
.pick{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;color:var(--ink);text-decoration:none;transition:border-color .15s,background .15s}
.pick:hover{border-color:var(--line-strong);background:#fbfbf9}
.pick b{font-weight:500}.pick small{color:var(--faint);font-size:12.5px}
.form-stack{display:grid;gap:12px}.form-row label{display:block;margin:0 0 6px;font-size:13px;color:var(--muted)}
.field{position:relative}.field-icon{display:none}
.toggle-password{position:absolute;right:8px;top:50%;transform:translateY(-50%);border:0;background:none;color:var(--faint);padding:4px;cursor:pointer;width:30px;height:30px}
.toggle-password svg{width:18px;height:18px}
.form-split{display:flex;align-items:center;justify-content:space-between;font-size:13px}
.checkline{display:flex;align-items:center;gap:6px;color:var(--muted)}
.pn-info{margin-top:14px;text-align:center;font-size:13px;color:var(--muted)}
.scene{width:100%;max-width:1200px;margin-top:auto;padding:24px 8px 0}
.nap-wrap{position:relative}.cc-nap{position:absolute;left:80%;bottom:4.4%;width:18%;height:auto;pointer-events:none}
.scene-svg{display:block;width:100%;height:auto;overflow:visible}.scene-svg.phone{display:none}
.hm-hop,.hm-eyes,.deco,.dot{transform-box:fill-box;transform-origin:center}
.hm-arm,.hm-wave,.hm-leg{transition:transform .35s var(--ease)}
.hm .hm-eyes{animation:blink 5s infinite}
@keyframes blink{0%,95%,100%{transform:none}97%{transform:scaleY(.1)}}
.hm.hm-hopping .hm-hop{animation:hop .6s var(--ease)}
@keyframes hop{35%{transform:translateY(-22px)}60%{transform:translateY(0) scaleY(.94)}}
.hm.hm-cheer .hm-wave{animation:cheer 1s var(--ease) 2}
@keyframes cheer{30%,70%{transform:rotate(130deg)}50%{transform:rotate(105deg)}}
.hm-mouth{transform-box:fill-box;transform-origin:center top;transition:transform .3s var(--ease)}
.hm.hm-cheer .hm-mouth{transform:scale(1.3,1.5)}
.hm-flash{opacity:0}.hm.hm-snap .hm-flash{animation:flash .5s .2s ease-out both}@keyframes flash{0%{opacity:.95}100%{opacity:0}}
.hm-laser{opacity:.35;transition:opacity .15s}.hm.hm-beep .hm-laser{opacity:1}
.shy .hm .hm-eyes{animation:none;transform:scaleY(.1)}.shy .hm .hm-mouth{transform:scale(.7,.6)}
.bubble{animation:float 5s ease-in-out infinite}.b2{animation-delay:-1.2s}.b3{animation-delay:-2.4s}.b4{animation-delay:-3.6s}
@keyframes float{50%{transform:translateY(-7px)}}
.dot{animation:typing 1.2s infinite}.d1{animation-delay:.15s}.d2{animation-delay:.3s}
@keyframes typing{0%,60%,100%{transform:translateY(0);opacity:.35}30%{transform:translateY(-5px);opacity:1}}
.sparkle{animation:twinkle 2.6s ease-in-out infinite}.s2{animation-delay:-.7s}.s3{animation-delay:-1.4s}.s4{animation-delay:-2s}
@keyframes twinkle{50%{transform:scale(.55) rotate(25deg);opacity:.5}}
.drift{animation:drift 7s ease-in-out infinite}.r2{animation-delay:-3s}
@keyframes drift{50%{transform:translate(6px,-10px) rotate(8deg)}}
.plane{animation:glide 9s ease-in-out infinite}
@keyframes glide{0%,100%{transform:translate(0,0)}50%{transform:translate(18px,-12px)}}
.step{animation:in .35s var(--ease)}@keyframes in{from{opacity:0;transform:translateY(6px)}}
@media (max-width:640px){.card{padding:20px;border-radius:18px}.top{padding:14px 16px}.scene{padding-top:24px}.scene-svg.wide{display:none}.scene-svg.phone{display:block}.cc-nap{display:none}}
.cc-look{transition:transform .25s var(--ease)}
.cc-perch{position:absolute;left:50%;margin-left:-112px;top:-219px;width:250px;height:234px;overflow:visible;user-select:none;pointer-events:none}
@media (max-width:640px){.cc-perch{margin-left:-98px;width:220px;height:206px;top:-193px}.card{margin-top:188px}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
""".strip()

# 背景点阵（宣传页 site.js dots() 原样）+ 小人：眼睛跟鼠标、随机做自己的动作、点一下跳、输密码时闭眼。
JS = """
(function(){var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
var canvas=document.querySelector('canvas.dots');if(canvas&&canvas.getContext){var ctx=canvas.getContext('2d'),GAP=22,R=1,REACH=150,w=0,h=0,dpr=1,mouse={x:-9999,y:-9999,tx:-9999,ty:-9999},t0=performance.now();
function size(){dpr=Math.min(window.devicePixelRatio||1,2);w=innerWidth;h=innerHeight;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);canvas.style.width=w+'px';canvas.style.height=h+'px';ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#141413'}
function draw(now){var t=(now-t0)/1000;mouse.x+=(mouse.tx-mouse.x)*.12;mouse.y+=(mouse.ty-mouse.y)*.12;ctx.clearRect(0,0,w,h);
for(var y=0;y<h+GAP;y+=GAP){for(var x=GAP/2;x<w;x+=GAP){var wave=reduce?0:.5+.5*Math.sin(t*.7-(x+y)*.008),a=.10+wave*.06,r=R,px=x,py=y,dx=x-mouse.x,dy=y-mouse.y,d=Math.sqrt(dx*dx+dy*dy);
if(d<REACH){var k=1-d/REACH;k=k*k;a+=k*.45;r+=k*1.3;if(d>.1){px+=dx/d*k*7;py+=dy/d*k*7}}ctx.globalAlpha=a;ctx.beginPath();ctx.arc(px,py,r,0,6.2832);ctx.fill()}}
if(!reduce&&!document.hidden)requestAnimationFrame(draw)}
size();draw(performance.now());addEventListener('resize',function(){size();draw(performance.now())});
if(!reduce){addEventListener('pointermove',function(e){mouse.tx=e.clientX;mouse.ty=e.clientY},{passive:true});document.addEventListener('pointerleave',function(){mouse.tx=mouse.ty=-9999});
document.addEventListener('visibilitychange',function(){if(!document.hidden)requestAnimationFrame(draw)})}}
var scene=document.querySelector('.scene'),all=[].slice.call(document.querySelectorAll('.hm'));
function play(m,cls,ms){m.classList.remove(cls);void m.getBoundingClientRect();m.classList.add(cls);setTimeout(function(){m.classList.remove(cls)},ms||1200)}
function act(m){var k=m.getAttribute('class');if(k.indexOf('hm-ai')>=0)return 'hm-laughing';if(k.indexOf('hm-dms')>=0)return 'hm-snap';if(k.indexOf('hm-erp')>=0)return 'hm-beep';return Math.random()<.5?'hm-hopping':'hm-cheer'}
window.pearnlyCheer=function(){all.forEach(function(m,i){setTimeout(function(){play(m,'hm-cheer',2000);play(m,'hm-laughing',2000)},i*120)})};
if(!reduce){setInterval(function(){var m=all[Math.floor(Math.random()*all.length)];if(m&&m.getBoundingClientRect().width)play(m,act(m),1400)},2600)}
all.forEach(function(m){m.style.cursor='pointer';m.addEventListener('click',function(){play(m,'hm-hopping',700);play(m,'hm-laughing',1400)})});
document.addEventListener('focusin',function(e){if(e.target.type==='password')document.body.classList.add('shy')});
document.addEventListener('focusout',function(e){if(e.target.type==='password')document.body.classList.remove('shy')});
var looks=[].slice.call(document.querySelectorAll('.hm .m-look')),px=0,py=0,q=false;
function upd(){q=false;if(document.body.classList.contains('shy'))return;looks.forEach(function(g){var r=g.closest('.hm').getBoundingClientRect();if(!r.width)return;
var dx=px-(r.left+r.width/2),dy=py-(r.top+r.height*.3),d=Math.sqrt(dx*dx+dy*dy)||1,k=Math.min(1,d/240)*(g.closest('.hm-mrerp')?3:5);g.setAttribute('transform','translate('+(dx/d*k).toFixed(2)+' '+(dy/d*k).toFixed(2)+')')})}
if(!reduce)addEventListener('pointermove',function(e){px=e.clientX;py=e.clientY;if(!q){q=true;requestAnimationFrame(upd)}},{passive:true});
var ccEyes=[].slice.call(document.querySelectorAll('.cc-look')),cq=false;
function ccLook(){cq=false;var shy=document.body.classList.contains('shy');ccEyes.forEach(function(g){var r=g.parentNode.getBoundingClientRect();if(!r.width)return;
var dx=shy?.4:px-(r.left+r.width/2),dy=shy?-1:py-(r.top+r.height/2),d=Math.sqrt(dx*dx+dy*dy)||1,k=shy?1:Math.min(1,d/260),ux=dx/d*k*8,uy=dy/d*k*(dy>0?3:5);
g.setAttribute('transform','translate('+ux.toFixed(2)+' '+uy.toFixed(2)+')')})}
if(!reduce&&ccEyes.length){addEventListener('pointermove',function(){if(!cq){cq=true;requestAnimationFrame(ccLook)}},{passive:true});
document.addEventListener('focusin',ccLook);document.addEventListener('focusout',function(){setTimeout(ccLook,0)})}
})();
""".strip()

GENERATED = "生成文件：改设计请改 scripts/build-ai-entry.py 后重新生成，不要直接改这里。"

# ---------------- 1. 入口站点第一步（Frappe www 页面，Jinja） ----------------

PAGE_HTML = """<!doctype html>
<!-- """ + GENERATED + """ -->
<html lang="{{ lang }}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC</title>""" + FAVICON + """<meta name="robots" content="noindex">""" + FONTS + """
<style>""" + CSS + """</style></head>
<body>
<canvas class="dots" aria-hidden="true"></canvas>""" + DEFS + """
<div class="page">
  <header class="top">
    <span class="brand">""" + BRAND_MARK + """</span>
    <nav class="langs" aria-label="Language">{% for code, label in langs %}<a href="#" data-lang="{{ code }}"{% if code == lang %} class="on"{% endif %}>{{ label }}</a>{% endfor %}</nav>
  </header>
  <section class="hero">
    {% if mode == "pick" %}
    <div class="card step">""" + CC_CAT + """
      <h2>{{ _("Choose a workspace") }}</h2>
      <div class="picks">{% for s in sites %}<a class="pick" href="/api/method/pearnly_control.assistant_entry.api.choose?pick={{ pick | urlencode }}&amp;site={{ s.site | urlencode }}"><b>{{ s.company | e }}</b><small>{{ products.get(s.product, s.product) }}</small></a>{% endfor %}</div>
    </div>
    {% elif mode == "none" %}
    <div class="card step">""" + CC_CAT + """
      <p class="notice">{{ _("This account has no workspace available. Contact your administrator.") }}</p>
      <a class="btn" href="/?fresh=1">{{ _("Back") }}</a>
    </div>
    {% else %}
    <form class="card" id="entry" novalidate>""" + CC_CAT + """
      <input class="input" id="login" name="login" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="{{ _("Username or email") }}" autofocus>
      <span class="err" id="err" role="alert" hidden></span>
      <button class="btn" type="submit" id="go">{{ _("Continue") }}</button>
      """ + CONTACTS + """
      <p class="legal">{{ legal_html | safe }}</p>
    </form>
    {% endif %}
  </section>
  <div class="scene">""" + scene_with_nap("/assets/pearnly_control/cc/", False) + """</div>
</div>
<script>""" + JS + """
(function(){
  document.querySelectorAll('[data-lang]').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();
    document.cookie='pearnly_locale='+a.getAttribute('data-lang')+'; path=/; max-age=31536000; samesite=lax; secure';location.reload()})});
  var form=document.getElementById('entry');if(!form)return;
  var input=document.getElementById('login'),err=document.getElementById('err'),go=document.getElementById('go');
  function fail(text){err.textContent=text;err.hidden=false;go.disabled=false}
  input.addEventListener('input',function(){err.hidden=true});
  form.addEventListener('submit',function(e){e.preventDefault();var v=input.value.trim();
    if(!v)return fail({{ _("Enter your username or email.") | tojson }});
    go.disabled=true;if(window.pearnlyCheer)window.pearnlyCheer();
    fetch('/api/method/pearnly_control.assistant_entry.api.start',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-Frappe-CSRF-Token':{{ csrf_token | tojson }}},body:JSON.stringify({login:v})})
    .then(function(r){return r.json().then(function(b){return {ok:r.ok,b:b}})})
    .then(function(x){if(x.ok&&x.b.message&&x.b.message.url){location.href=x.b.message.url;return}
      fail({{ _("The sign-in service is not available. Try again later.") | tojson }})})
    .catch(function(){fail({{ _("The sign-in service is not available. Try again later.") | tojson }})})});
})();
</script>
</body></html>
"""

# ---------------- 2. Keycloak ai 专用主题（第二步：密码；以及找回 / 设新密码 / 出错页） ----------------

THEME_JS = JS + """
(function(){
  var change=document.getElementById('who-change');
  if(change)change.addEventListener('click',function(){var who=document.getElementById('who'),u=document.getElementById('username');
    var input=document.createElement('input');input.className='input';input.id='username';input.name='username';input.type='text';input.autocomplete='username';
    input.style.marginBottom='10px';input.value='';u.remove();who.replaceWith(input);input.focus()});
  document.querySelectorAll('[data-toggle-password]').forEach(function(b){b.addEventListener('click',function(){
    var i=document.getElementById(b.getAttribute('data-toggle-password'));if(i)i.type=i.type==='password'?'text':'password'})});
})();
"""

# 样式 / 脚本文件名带内容指纹：Keycloak 给主题资源的缓存期很长，文件名不变时浏览器（含 Tomo 桌面端）会一直用旧样式。
CSS_NAME = "ai." + hashlib.sha256(CSS.encode()).hexdigest()[:10] + ".css"
JS_NAME = "ai." + hashlib.sha256(THEME_JS.encode()).hexdigest()[:10] + ".js"


THEME_PROPERTIES = """# Pearnly 智能体入口（fatcc.com）的登录主题，只给 pearnly-ai 客户端用（客户端属性 login_theme）。""" + """
# """ + GENERATED + """
# 继承 pearnly：找回密码、设新密码、出错页沿用其模板与文案，只换外壳（template.ftl）与样式（resources/css/ai.<指纹>.css）；
# 登录页（login.ftl）重写为「上面显示账号 + 下面只有密码框」，与入口第一步是同一个页面的样子。
parent=pearnly
import=common/keycloak
styles=
locales=th,zh-CN,en
"""

TEMPLATE_FTL = """<#-- """ + GENERATED + """ -->
<#macro pnIco name><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><#if name == "eye"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="3"/><#elseif name == "user"><path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/><#elseif name == "lock"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><#elseif name == "mail"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></#if></svg></#macro>
<#function pnLangShort tag><#if tag?starts_with("zh")><#return "中文"><#elseif tag == "th"><#return "ไทย"><#elseif tag == "en"><#return "English"><#else><#return tag></#if></#function>
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false resetResult=false>
<!doctype html>
<html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC</title>""" + FAVICON + """<meta name="robots" content="noindex">""" + FONTS + """
<link href="${url.resourcesPath}/css/""" + CSS_NAME + """" rel="stylesheet" />
<#if scripts??><#list scripts as script><script src="${script}" type="text/javascript"></script></#list></#if>
<script src="${url.resourcesPath}/js/""" + JS_NAME + """" defer></script>
</head>
<body class="${bodyClass}">
<canvas class="dots" aria-hidden="true"></canvas>""" + DEFS + """
<div class="page">
  <header class="top">
    <span class="brand">""" + BRAND_MARK + """</span>
    <#if realm.internationalizationEnabled && locale.supported?size gt 1>
    <nav class="langs" aria-label="Language"><#list ["th", "zh-CN", "en"] as want><#list locale.supported as l><#if l.languageTag == want><a href="${l.url}" class="<#if l.languageTag == locale.currentLanguageTag>on</#if>">${pnLangShort(l.languageTag)}</a></#if></#list></#list></nav>
    </#if>
  </header>
  <section class="hero">
    <div class="card">""" + CC_CAT + """
      <#nested "header">
      <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
        <div class="auth-message ${message.type}" role="alert">${kcSanitize(message.summary)?no_esc}</div>
      </#if>
      <#nested "form">
      <#if displayInfo><div class="pn-info"><#nested "info"></div></#if>
      """ + CONTACTS + """
      <#assign site = "https://pearnly.com" + (locale.currentLanguageTag?starts_with("zh"))?then("/zh", (locale.currentLanguageTag == "en")?then("/en", ""))>
      <p class="legal">${kcSanitize(msg("pearnlyAiLegal", '<a href="' + site + '/terms" target="_blank" rel="noopener">' + msg("pearnlyAiTerms") + '</a>', '<a href="' + site + '/privacy" target="_blank" rel="noopener">' + msg("pearnlyAiPrivacy") + '</a>'))?no_esc}</p>
    </div>
  </section>
  <div class="scene">""" + scene_with_nap("${url.resourcesPath}/video/", True) + """</div>
</div>
</body></html>
</#macro>
"""

LOGIN_FTL = """<#-- """ + GENERATED + """ -->
<#-- 第二步：入口已把账号带过来（login_hint），这里只显示账号、输密码；「更换」就地改成可编辑的账号框。 -->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password'); section>
<#if section = "form">
  <form id="kc-form-login" action="${url.loginAction}" method="post" autocomplete="on" class="step">
    <#assign who = (login.username!'')>
    <#if who?has_content && !messagesPerField.existsError('username')>
      <div class="who" id="who"><span>${who}</span><button type="button" id="who-change">${msg("pearnlyAiChange")}</button></div>
      <input type="hidden" id="username" name="username" value="${who}" />
    <#else>
      <input class="input" id="username" name="username" type="text" value="${who}" autocomplete="username" placeholder="${msg("pearnlyAiLogin")}" style="margin-bottom:10px" <#if !who?has_content>autofocus</#if> />
    </#if>
    <div class="field">
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="${msg("pearnlyPasswordPh")}" <#if who?has_content>autofocus</#if>
             aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>" />
      <button class="toggle-password" type="button" data-toggle-password="password" aria-label="${msg("pearnlyShowPassword")}"><@layout.pnIco name="eye" /></button>
    </div>
    <#if messagesPerField.existsError('username','password')>
      <span class="pn-field-error">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</span>
    </#if>
    <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if> />
    <button class="btn" id="kc-login" name="login" type="submit">${msg("pearnlyLoginBtn")}</button>
    <#if realm.resetPasswordAllowed>
      <span class="forgot"><a class="link-btn" href="${url.loginResetCredentialsUrl?keep_before("?")}?client_id=${client.clientId?url("UTF-8")}&amp;kc_locale=${locale.currentLanguageTag?url("UTF-8")}">${msg("pearnlyForgot")}</a></span>
    </#if>
  </form>
</#if>
</@layout.registrationLayout>
"""

# Keycloak 文案：只放 ai 主题新增的键，其余（密码、登录、忘记密码…）继承 pearnly 主题。
MESSAGES = {
    "zh_CN": {"pearnlyAiChange": "更换", "pearnlyAiLogin": "用户名或邮箱",
              "pearnlyAiLegal": "继续即表示您已阅读并同意 Pearnly 的{0}和{1}。", "pearnlyAiTerms": "服务条款", "pearnlyAiPrivacy": "隐私政策"},
    "th": {"pearnlyAiChange": "เปลี่ยน", "pearnlyAiLogin": "ชื่อผู้ใช้หรืออีเมล",
           "pearnlyAiLegal": "การดำเนินการต่อถือว่าคุณยอมรับ{0}และ{1}ของ Pearnly", "pearnlyAiTerms": "ข้อกำหนดการให้บริการ",
           "pearnlyAiPrivacy": "นโยบายความเป็นส่วนตัว"},
    "en": {"pearnlyAiChange": "Change", "pearnlyAiLogin": "Username or email",
           "pearnlyAiLegal": "By continuing, you agree to Pearnly''s {0} and {1}.", "pearnlyAiTerms": "Terms of Service",
           "pearnlyAiPrivacy": "Privacy Policy"},
}


def properties(values: dict) -> str:
    def esc(text):
        return "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in text)
    return f"# {GENERATED}\n" + "".join(f"{k}={esc(v)}\n" for k, v in values.items())


def outputs() -> dict:
    files = {PAGE: PAGE_HTML, THEME / "theme.properties": THEME_PROPERTIES, THEME / "template.ftl": TEMPLATE_FTL,
             THEME / "login.ftl": LOGIN_FTL, THEME / "resources/css" / CSS_NAME: CSS + "\n", THEME / "resources/js" / JS_NAME: THEME_JS}
    for code, values in MESSAGES.items():
        files[THEME / f"messages/messages_{code}.properties"] = properties(values)
    return files


def main():
    check = "--check" in sys.argv
    stale = []
    wanted = outputs()
    binaries = {THEME / "resources/video" / name: (NAP_DIR / f"cat-nap.{ext}").read_bytes() for ext, name in NAP_FILES.items()}
    for path, data in binaries.items():
        if check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    for old in ((THEME / "resources/video").glob("*") if (THEME / "resources/video").exists() else ()):
        if old not in binaries:
            if check:
                stale.append(str(old.relative_to(ROOT)) + " (remove)")
            else:
                old.unlink()
    for folder in (THEME / "resources/css", THEME / "resources/js"):   # 换了指纹的旧文件不留
        for old in (folder.glob("*") if folder.exists() else ()):
            if old not in wanted:
                if check:
                    stale.append(str(old.relative_to(ROOT)) + " (remove)")
                else:
                    old.unlink()
    for path, body in wanted.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                stale.append(str(path.relative_to(ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if stale:
        print("stale (run python3 scripts/build-ai-entry.py):\n  " + "\n  ".join(stale))
        sys.exit(1)
    print("ai entry: " + ("up to date" if check else f"wrote {len(outputs())} files"))


if __name__ == "__main__":
    main()
