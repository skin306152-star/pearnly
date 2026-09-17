#!/usr/bin/env bash
# 重新生成登录主题的样式包 resources/css/pearnly-auth.css
#
# 为什么要生成而不是手写：登录页的样子是旧页反复调出来的，样式整份复用，
# 一条规则都不重写。这个脚本只做"照顺序拼起来 + 去掉 BOM"。
#
# 去掉 BOM 不是洁癖：landing-auth.css 开头有个 BOM（U+FEFF），单独一个文件没问题，
# 拼到别的文件后面就落到文件中间，浏览器把 `.auth-card{...}` 当成非法选择器整条丢掉，
# 结果卡片失去 position:absolute，直接贴到页面左上角（2026-09-18 实际踩到）。
#
# 用法:
#   ./build-login-css.sh [旧仓库 static/landing 目录]
set -euo pipefail

SRC="${1:-/Users/skin/Developer/Pearnly/pearnly-app/static/landing}"
OUT="$(cd "$(dirname "$0")" && pwd)/login/resources/css/pearnly-auth.css"

# 顺序 = 旧构建脚本 scripts/build-home-css.mjs 的 LANDING_CSS 顺序（层叠依赖这个顺序）。
# 剔除: landing-tour*.css（营销导览）、auth-sso.css / auth-modal.css（第三方登录与重置弹窗）。
FILES=(
  landing.css
  landing-auth.css
  mascot.css
  mascot-effects.css
  responsive.css
  landing-static-bg.css
  landing-static-bg-mobile.css
  landing-interactions.css
)

{
  cat <<'HEADER'
/* Pearnly 统一登录页样式 —— 直接从旧登录页整份搬过来，未重写任何一条规则。
 *
 * 为什么这么做（用户 2026-09-18 纠偏）：
 * 这套登录页当年是「一整张背景图 auth-bg-desktop.png + 按坐标叠真实控件」做出来的，
 * 右边再用一张不透明磨砂卡盖住图里画的那个登录面板（旧代码注释里叫 Option B）。
 * 位置和观感都反复调过，用 CSS 重画一遍既费时又会走样，所以整份复用。
 *
 * 本文件由 build-login-css.sh 生成，不要手改；要改请改旧仓库的源样式并重跑脚本。
 */
HEADER
  for f in "${FILES[@]}"; do
    printf '\n/* ===== 源文件: %s ===== */\n' "$f"
    # 去掉 BOM，并统一成 LF。
    # 注意：这里必须用 perl —— macOS 自带的是 BSD sed，不认 \xef 这种转义，
    # 写成 sed 's/^\xef\xbb\xbf//' 会安安静静什么都不做（2026-09-18 实际踩到）。
    # 按字节匹配、全文件清除，不依赖 BOM 出现在第几行。
    LC_ALL=C perl -pe 's/\xef\xbb\xbf//g; s/\r$//' "$SRC/$f"
  done
} > "$OUT"

echo "已生成: $OUT"
wc -c "$OUT"
