# 云端 Express 实测清单（T1–T6，测试机1 当桥）

> **11-16 撤销**：本清单里用 `hidden-desk`（隐藏桌面）与 `send-run`（向云端发按键）的步骤被安全审查拦下，已作废，两个脚本已从仓库删除，不得重建；方案改为可见窗口（见 2026-11-15 施工评估与 cloud_link）。文中云端主机、账号、域都已换成占位。

> 依据：[影响评估](2026-11-11-express-cloud-assessment.md) 第 11.1 节；主会话 11-11 审定「先实测，T2 / T3 / T6 一票否决，不过就停下报」。
> 状态：**11-14 已按现状改写、探针已备齐并在本机验过；等 11-15 上午用户本人在测试机1 登录云端后执行**。执行记录写在
> [实测记录](2026-11-14-express-cloud-probe-run.md)。
> 用户 11-11：测试公司 = Z1（实测阶段连 Z1 也不写）；账号「输入啥就用啥」——T3 照测，结果定「同一账号」规则细则（评估第 4.1 节）。

## 11-14 核对：原清单过时的地方（已在下文改掉）

| 原写法 | 现在 |
| --- | --- |
| T6 探针源码与哈希在共享笔记 `local_74a4f119…/云端Express-T6探针-待审-11-11/` | 那个目录已不在。探针全部重写进仓库 `scripts/express-cloud-probe/`（本分支），哈希见实测记录第 2 节 |
| T6 只用一个自打包的 ccprobe | 另加「正式版 CC 本身」：把测试机1 已装的 CC 1.12.21 程序文件夹只读复制上云，以 `--headless`、配置目录指到探针目录跑一次（没配对 → 预期 2 秒内以 2 退出，本机已验）。CrowdStrike 拦不拦，以正式程序为准 |
| CDX 索引程序「带不存在的路径跑一次」 | 改为对探针目录里的**假表** `PROBE.DBF` 跑 `TAGS`（证明程序真跑起来了），再对不存在的表跑一次（预期 `error=`）；日志写探针目录，碰不到账套 |
| 隐藏会话里探针怎么跑起来没写 | 隐藏会话用 `probe-alt.rdp`（登录即跑探针）；若 T5 证明云端不认登录即运行，就先在可见会话里起探针，再断开、改由隐藏桌面重连回**同一个会话**（探针在断开期间照跑） |
| `probe-x.rdp` / `probe-c.rdp` 两份 | 五份：`probe-vis`（T1 可见）、`probe-hidden`、`probe-alt`（登录即跑探针）、`probe-c`、`probe-c-alt`（后两份只在 subst 盘映射不过时用） |
| Win+R 一条路 | 加备选：点云端开始按钮 → 打 `powershell` → 在窗口里打命令（11-09 证实「点开始菜单再打字可用」） |
| 读 TSplus 拉起的 .rdp | 读时一律去掉 password / credential 行的值，用户名只留域名和长度 |
| 开工时 CC 版本 | 正式版 1.12.21（`67f84cffb`）。测试机1 的 CC 版本、配置哈希（任务书：0015B5E5）开工前只读记下，全程不碰 |
| 测试机 | 测试机1 同时跑着 GitHub 检查服务和实验栈；今天被「CC 桌面版半截单」（分支 `pearnly/cc-desktop-half-doc-1ee3e3`）占用。实测只占 skin3 桌面约 25 分钟（有用户在场那段），其余在隐藏桌面挂着、不占屏幕，可与实验栈并行 |

## 总规矩

- **只读**：不打开云端任何账套写入、不在 `D:\CLOUDUSER\ExpressI-Cloud` 下建 / 改 / 删任何文件；云端只在**云端用户自己的目录**建 `%LOCALAPPDATA%\PearnlyProbe`，
  收到 stop 时探针自己删掉再注销会话。云端的启动文件夹、注册表一律不写（启动文件夹只看权限）。
- **桥 = 测试机1**（`lenovo-office-lan`，桌面用户 skin3）。经 SSH（macadmin）只做：放文件、建 / 删 `PearnlyCloudProbe-*` 一次性计划任务（skin3、Interactive、Limited、允许电池）、读结果。
  不 lab-start、不切域名、不重启 CC、不碰 CC 配置、不碰 C 盘 / S 盘账套。
- **开工前**：问主会话、在 `local_1cb93fea…/测试机占用.md` 登记；`probe.sh as idle` 看 skin3 空闲、没锁屏、没有别的 mstsc / Express 在跑。
  `prep` 会先把测试机现状存进 `C:\PearnlyCloudProbe\logs\before`（凭据列表、远程桌面客户端注册表整棵、subst、Default.rdp、已有 mstsc 进程），收尾照它原样还原。
- **密码**：只由用户本人在 Windows 原生远程桌面框里输；我们不看、不录、不截那一框。**输错一次就停**，不重试（防云端锁账号）。
- **立即停下报主会话**：T2 / T6 任一不过；T3 后台连接卡登录框且 `CredWrite` 也不行；云端出现安全软件提示、会话被对方强制结束、账号被锁或要求改密码、没预料的弹窗。停下时先做「收尾」再报。
- 截图只截云端探针窗口、记事本、云端 Express 登录框和测试机上我们自己的 mstsc 窗口。

## 驱动

Mac 上 `scripts/express-cloud-probe/probe.sh <命令>`（脚本说明在文件头）；证据落 `~/pearnly-devtools/express-cloud-probe-1115/evidence`。

## 准备（不用用户，约 25 分钟）

1. `probe.sh dist`（已于 11-14 做过，重跑无妨）→ `probe.sh push` → `probe.sh parse`（只做语法解析）。
2. `probe.sh build`：测试机1 上用钉死版本的 PyInstaller 打 ccprobe，并在桥上自检（ccprobe 读假登记表 3 次、CDX 索引程序对假表 `TAGS` / 不存在的表各一次），打印哈希。
   **自检不过就不上云**。
3. `probe.sh as idle` → `probe.sh as prep`：快照、subst 隧道盘（优先 X）、写五份 `.rdp`、造 100 MB 测速文件、列出 TSplus 客户端在哪。

## 用户在场的部分（约 25 分钟；真正要动手的只有输一次密码）

> 先请用户在自己的 TSplus 云端会话里关掉 Express、存好手上的事（T3 会故意测「同账号挤人」，他的会话可能被挤下线）。

### T1 直接用 mstsc 能否登录；主机地址能否自动读出（不一票否决）

1. `probe.sh as read-tsplus`（等 3 分钟）同时，用户照常双击 TSplus 客户端连一次云端；读到地址 / 端口 / 用户名形式后，用户断开这个会话（右上角 ×，不注销）。
   读到的地址端口与 `198.51.100.7:3390` 不同 → 用 `prep -CloudHost … -Port …` 重写 .rdp。
2. `probe.sh as hidden-desk -Action visible -Rdp C:\PearnlyCloudProbe\rdp\probe-vis.rdp`：用户本人在 Windows 框里输云端账号密码、**勾「记住我」**，接受证书（勾「不再询问」）。
3. **过**：进得了云端桌面；`cmdkey /list` 出现 `TERMSRV/198.51.100.7`。**不过**：TSplus 拒收非 TSplus 客户端 → 停，报主会话（方案要换成驱动 TSplus 客户端，重新评估）。

### T5（上半）云端探针怎么第一次跑起来（不一票否决）

4. 可见会话里 `\\tsclient\<盘>` 在不在由探针自己报（同时就是 **T2 第一问：subst 盘能否映射**）。
5. `probe.sh as send-run -Mode winr -Command "<启动行>"`（启动行见 `cloud/probe.ps1` 文件头，盘符换成 prep 选的）；30 秒内 `probe.sh status` 看到心跳 = 过。
   不过换 `-Mode start`。探针第一条 `facts-*.json` 里有：云端用户目录、临时目录、启动文件夹、D:、P: 的写权限（只看 ACL）、各盘剩余空间、CrowdStrike 服务状态、Express 程序位置。
6. `probe.sh ctl stop`（探针删自己的目录并注销）。再 `probe.sh as hidden-desk -Action visible -Rdp …\probe-alt.rdp`：登录后探针自己出现心跳 = 「登录即运行」可用；完后同样 `ctl stop`。
   subst 盘没映射过来时，改用 `probe-c.rdp` / `probe-c-alt.rdp` 重做 4–6，并在记录里写明「只能映射整个 C 盘」这条风险。

### T3 同账号再登会不会顶掉员工；后台重连会不会卡登录框；第二个 Express 占不占名额（卡登录框才一票否决）

7. 用户用 TSplus 客户端连上自己的会话并留在云端桌面。
8. `probe.sh as hidden-desk -Action start -Rdp …\probe-alt.rdp`（同一账号，靠第 2 步存的凭据，不再要人输）。观察：
   - 用户看自己那个会话有没有被断开 / 被接管，告诉我们；
   - 60 秒内隐藏那边有没有心跳；没有就 `probe.sh as hidden-desk -Action peek`：列出隐藏桌面上的窗口标题、对话框文字，截 mstsc 一张——
     停在凭据框 / 证书框 = 卡登录框（**不在里面输入**）；画面是云端桌面但没心跳 = 接进了已有会话（登录即运行只在新登录时跑），记下。
9. 两个会话能同时在：`probe.sh ctl express-open`（隐藏会话里的探针把云端 Express 开到**登录窗口**，不登录、不选公司），用户在自己会话里看 Express 有没有报用户数满 / 名额提示；
   然后 `probe.sh ctl express-close`。
10. 反过来：隐藏会话连着时，用户用 TSplus 再连一次，看隐藏那边是否被挤（`peek` 看 mstsc 报的断开原因文字）。
11. 记结果写回评估第 4.1 节「同一账号」细则：同账号能不能两个会话同时在；不能时是新会话接管旧会话还是新连接被拒；被挤那一端的断开原因文字；第二个 Express 占不占名额。
    后台连接卡登录框（云端策略每次要密码）→ 改为 `CredWrite` 存凭据再试一次（仍由用户本人输，届时另写小工具）；**仍不行 → 停**。
12. 用户可以离开。他自己的会话由他自己断开 / 留着都行。

## 用户不用在场的部分（约 2 小时 10 分，T2 / T4 / T6 同时挂）

### T2 会话藏起来后云端还跑不跑、还画不画（**一票否决**）

13. `probe.sh as hidden-desk -Action stop` 后重新 `-Action start -Rdp …\probe-alt.rdp`（T5 登录即运行不通时：先可见起探针、关掉可见 mstsc、再隐藏重连回同一会话）；
    `probe.sh bg monitor -Minutes 150`（每分钟记隐藏 mstsc 的 CPU、内存和心跳数）。
14. **过**：连续 30 分钟每 30 秒都有心跳、`tunnel_ok` 一直为真；每 5 分钟一轮截图（探针自己的窗口、记事本、整个会话屏幕）都不是整片一色（`shot-*.json`；拉两张给主会话看）。
    **不过**：换备选各 30 分钟——① 普通 mstsc 最小化 + `HKCU\Software\Microsoft\Terminal Server Client\RemoteDesktop_SuppressWhenMinimized=2`（收尾整棵注册表还原）；
    ② RDP 控件放屏幕外窗口（要多写一个宿主，到这一步先报主会话）。都不过 → 停。

### T4 云端空闲多久断开；桥资源实数（不一票否决）

15. 同一个隐藏会话挂满 2 小时，探针**不**发任何输入。记：心跳停在哪一刻、`sessions` 里自己那行最后的状态；`monitor.csv` 算 mstsc 空闲平均 CPU 与内存。
16. 空闲不断 → 不需要保活；会断 → 记下时长，施工时保活间隔取它的一半。

### T6 CrowdStrike 让不让没签名的 CC 常驻（**一票否决**）

17. 和 T2 同时：`probe.sh ctl t6`。探针起子进程依次：拷 100 MB 测速文件（量完即删）、ccprobe、正式 CC 程序文件夹、CDX 索引程序（各记秒数 = **T5 下半**，也定评估第 12 节第 4 问「云端包分不分」）；
    正式 CC `--headless` 跑一次；CDX 索引程序对假表 `TAGS`、对不存在的表各一次；ccprobe 常驻 60 分钟。
18. **过**：`t6-result.json` 里正式 CC 以 2 退出、日志写的是「需要至少一个已配对 Profile」；runner 对假表 `used=.T. … records=3`、对不存在的表 `error=`；
    60 个 `cc-0*.json` 都在、公司数一致；前后哈希一致（没被隔离）；没有 `err-*.json` 里的拒绝访问、会话没断、没弹提示。
    **不过**（进程被杀、文件被隔离、会话被断、出现提示）→ 立即 `ctl stop`、收尾、报主会话，**不重试**。

## 收尾（每次停下或做完都照做，读回确认）

1. `probe.sh ctl stop` → 等 `stopped.json`（`local_removed: true`）。探针已不在时：`hidden-desk -Action visible -Rdp …\probe-vis.rdp` 连上，
   由我们经 `send-run` 打一行 `Remove-Item -Recurse $env:LOCALAPPDATA\PearnlyProbe; logoff`（凭据还在，不用用户）。再连一次看没有「欢迎回来」= 没留断开的会话。
2. `probe.sh pull`（结果拉回 Mac）→ `probe.sh as restore`（结束我们起的 mstsc、删我们加的凭据、远程桌面客户端注册表整棵换回快照、Default.rdp 换回、撤 subst，打印读回）
   → `probe.sh cleanup`（删计划任务与 `C:\PearnlyCloudProbe`，读回 0）。用户自己的 TSplus 客户端与它的设置不动。
3. 占用表写「已释放」；结果写进实测记录（每项过 / 不过 / 没测到 + 证据），共享笔记同步一屏，SendMessage 主会话。

## 时间

| 段 | 要用户 | 机器 |
| --- | --- | --- |
| 准备 | 否 | 约 25 分钟（含 ccprobe 打包自检约 10 分钟） |
| T1 + T5 上半 + T3 | **是，约 25 分钟**（真正动手：双击 TSplus 两次、输一次密码；其余是看自己的会话有没有被挤） | 同左 |
| T2 + T4 + T6（同时挂） | 否 | 约 2 小时 10 分 |
| 收尾 + 写结论 | 否 | 约 25 分钟 |
| **合计** | **约 25 分钟** | **约 3 小时 25 分** |
