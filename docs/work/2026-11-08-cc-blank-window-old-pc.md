# CC 桌面版老电脑窗口一整片空白（2026-11-08）

## 现象
11-08 19:19 办公室一台老 Acer 一体机（Intel Pentium、Windows 10）装好 CC 后，标题栏「CC」的窗口整片浅灰，登录页不出来。没有远程访问。

## 原因（按代码 + 本机复现）
- CC 窗口是 PySide6 6.11.1 的 QtWebEngine（自带 Chromium，不用 WebView2），窗口进程 `companion.exe --window`。
- 网页内核默认用显卡画页面；原代码没有画不出来时的兜底，Qt / Chromium 的警告写到打包后丢掉的标准错误，`window.log` 里看不到。
- 本机在没有 OpenGL 的平台上复现了同类现象：显卡档截图一整片空白，加 `--disable-gpu --disable-gpu-compositing` 后正常画出。
- 现场那台的根因还要看取证结果（第 5 步临时关显卡能否出登录页）；修复按类覆盖所有画不出来的电脑。

## 修复（1.12.19，`companion/src/companion/render_safety.py`）
- 档位：0 显卡（默认）/ 1 软件绘制（Chromium 关 GPU + `QSG_RHI_PREFER_SOFTWARE_RENDERER=1`，Qt 合成用 D3D11 WARP）。
  记在 `%APPDATA%\Pearnly\tomo\render.json`（档位、原因、时间、CC 版本）。
- 判空白：CC 自己的页面加载成功、窗口看得见、页面 `innerText` 非空，加载完成后 3 / 6 / 10 秒截图都同一种颜色 → 升档，
  窗口进程经管道发 `restart` 后退出，托盘等旧进程退出（最多 15 秒）再拉起。已是最后一档只记日志。页面还没有字、窗口看不见、
  别的网站、加载失败都不判断。
- 不永久锁死（主会话 11-08 要求）：软件档记下时的 CC 版本前两段变了（如 1.12 → 1.13）或满 30 天，下次打开回到显卡重新试；
  还画不出来再自动换回（那一次多等约 10 秒）。
- Qt 消息（含 `qt.webenginecontext` 启动信息：显卡、绘制方式）写进 `window.log`；页面控制台不写，同一句只写一次，每进程最多 300 行。
- 没有新增或改动界面文案。

## 验证
- 单元测试 `companion/tests/test_render_safety.py`（档位、重新试、环境变量合并、空白判断、看不见 / 没有字 / 别的网站不判、
  窗口进程升档重开、托盘等旧进程退出再拉起、Qt 日志）；companion 全量测试本机（macOS、offscreen）通过。
- 本机真网页内核：macOS 正常显卡打开真 fatcc.com 判「已画出」不误判；offscreen 下真 TomoWindow 10 秒判空白 → 记软件档 →
  第二次启动画出登录页。
- 测试机（Windows、Intel Iris Xe）：见下方结果。
- 现场那台：待取证结果；1.12.19 发布后在那台上验。
