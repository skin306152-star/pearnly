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

## 现场机器与两条追问（主会话 11-08 转来）
现场：Acer Aspire Z1-601，Intel Pentium N3540（Bay Trail，约 2014 年），8 GB DDR3，Intel HD Graphics（Bay Trail 核显），机械硬盘。

1. 老核显能不能直接走软件档（不等 10 秒）：**不按型号写名单**。
   - Chrome / Edge 在这类电脑上能显示，是因为 GPU 在单独的进程里：出问题会被 Chromium 自己的显卡黑名单挡掉，
     GPU 进程崩了几次也会自动退回软件绘制。
   - CC 的网页内核里 Chromium 的 GPU 跑在本进程里（测试机 window.log 的启动参数有 `in-process-gpu`），页面最后由 Qt 自己的
     D3D11 合成，这一层不在 Chromium 黑名单管辖之内，所以 Chromium 的自动退回在 CC 里不一定起作用。
   - Bay Trail 核显在不在 Chromium 黑名单、具体是哪一层画不出来，没有真机证据，按名单猜容易漏判或误判
     （同叫「Intel(R) HD Graphics」的核显跨好几代）。截图判空白按现象认，不管是哪块显卡、哪一层出的问题都覆盖。
   - 代价：每台这样的电脑第一次多等约 10 秒，之后一直用软件档；每 30 天或升 1.x 版回试一次，各多等一次。
   - 若现场 window.log 显示某类显卡有固定的启动报错，再按那句报错直接换档，不按型号。
2. 冷启动别一整片灰：已做加载状态（CC 图标 + 原生进度条，不写字，第一次加载结束就收起）。
   - N3540 + 机械硬盘冷启动要多久：没有同类机器，**未实测**。网页内核文件约两百 MB（打包说明），按机械硬盘与这颗 CPU 估计
     要十几到几十秒。
   - 现场照片里的「整片浅灰」有可能就是冷启动还没出页面（窗口底色 #fcfcfb），也可能是显卡画不出；第 5 步取证能分开这两种情况。
   - 两种情况 1.12.19 都覆盖：加载中显示加载状态；加载完了画不出来就自动换软件档。

## 验证
- 单元测试 `companion/tests/test_render_safety.py`（档位、重新试、环境变量合并、空白判断、看不见 / 没有字 / 别的网站不判、
  窗口进程升档重开、托盘等旧进程退出再拉起、Qt 日志）；companion 全量测试本机（macOS、offscreen）通过。
- 本机真网页内核：macOS 正常显卡打开真 fatcc.com 判「已画出」不误判；offscreen 下真 TomoWindow 10 秒判空白 → 记软件档 →
  第二次启动画出登录页。
- 测试机（Windows 10/11，Intel Iris Xe，D3D11）用分支源码 + 同版本 PySide6 开真 CC 窗口打开真 fatcc.com（单独的 APPDATA，不碰已装的 CC）：
  正常显卡 3 秒判「已画出」，没写 render.json，截图是完整登录页，window.log 记到显卡信息；离线平台（无显卡输出）
  10 秒判空白 → 记 level 1 → 发 restart；第二次按软件档（Chromium GPU disabled、Qt 用 Microsoft Basic Render Driver）3 秒画出。
- 测试机装分支安装包（1.12.19，889828371 的试打包）：正常显卡打开对话页 3 秒判「已画出」、render.json 不生成，
  开窗 20 秒内窗口进程 CPU 只涨 0.7 秒、网页内核 0.4 秒；托盘以离线平台起（强制画不出来）时，真托盘 + 真窗口进程
  10 秒判空白 → 窗口进程发 restart 退出 → 托盘 0.4 秒后重开 → 软件绘制 3 秒画出。测完删测试的 render.json，装回 1.12.17
  正式版（skin3 普通权限 --background），companion.json 哈希、开机自启项不变，9333 不监听，心跳正常。
- 加载状态：macOS 真窗口 0.4 秒时显示 CC 图标 + 进度条，加载完收起、露出登录页。
- 现场那台：待取证结果；1.12.19 发布后在那台上验。

## 附：取任务 401 每 3 秒重试（11-08 erpcowork 19:20–19:23，主会话转来）
- 老接口 + 旧更新地址 404 符合搬进本仓前的 Pearnly Companion ≤1.1.77：领任务 `POST /api/erp/agent/lease`、更新查
  `<连接站点>/static/companion/latest.json`；实测 erpcowork 错密钥分别回 401、404。那时的循环出错就睡 3 秒再试、没有上限。
  日志核实（线上压测窗口只读查生产 nginx 计时日志 `pearnly-timing.log`，泰国时间）：`POST /api/erp/agent/lease` 63 次全 401、
  19:20:31–19:23:52 约每 3 秒；`/api/erp/agent/heartbeat` 4 次全 401、约每 60 秒；`GET /static/companion/latest.json` 2 次全 404
  （19:20:30、19:21:34）；19:23:52 后不再出现。老路径由服务端 `pearnly_cowork/companion/legacy_wire.py` 转到新接口，401 是密钥失效。
  同段新接口：19:24:11、19:45:13 各一次 `folder_intake.machine.sync`（200），19:34:54 一次 `queue_api.lease`（499，长轮询被断开）——
  老版停下 19 秒后出现新接口，符合「装新版时安装包按文件名结束了老版」；来源 IP 与 User-Agent 计时日志没有，待下次必要的只读查询补。
- 当前 1.12.x：取任务 401 也照样每 3 秒领；只有心跳的 401 记进 `device_link.REJECTIONS`，满 60 秒才把这把密钥标成不能再用、停止取任务，
  所以每台密钥失效的电脑停下前要打 20–40 次。
- 1.12.19 修（主会话 11-08 审定）：取任务 401 按连续次数退避（3、6、12……秒，最多 10 分钟），成功即复原；退避期间整轮跳过（心跳、认电脑也不发）；
  取任务 401 也记进 REJECTIONS。托盘沿用已有文案「连接已失效，请在设置中重新连接」，重新连接 / 配对会重起取任务线程、马上照正常节奏。
  测试 `companion/tests/test_multi_profile_worker.py`（修前 3 条红）。
- 残留：10-08 前全机安装的 `C:\Program Files\Pearnly Companion` 新安装包（按用户、无管理员权限）删不掉，只把开机自启改指新 CC；
  开始菜单「Pearnly Companion」还在，点了会再跑老版。主会话定：不加检测提示（不加新文案和入口）；老版自动升级（租户域名提供
  `/static/companion/latest.json` 与 `/downloads/companion/`）排在 1.12.19 发出之后，先在测试机装老版实测再出 nginx 方案。
