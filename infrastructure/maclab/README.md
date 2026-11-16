# 本机 Mac 实验栈（maclab）

用途：写账链路的真环境验收不再排队等测试机1 的实验栈。ERP、Cowork 两套服务端栈在本机 Mac（colima `pearnly`）上**同时**跑，
局域网里的两台 Windows 测试机上的 CC 桌面版各连一套。不切线上域名、不要线上登录服务、不要证书；CC 与服务端之间只有 CC 主动出站
（心跳 / 领单 / 回执）。

- 栈定义只用 [`production/compose.yml`](../production/compose.yml) 一份，Mac 的差异全在 [`override.yml`](override.yml)（文件头逐条写了差异）。
- 命令入口 [`scripts/maclab.sh`](../../scripts/maclab.sh)；容器里跑的：[`init.py`](init.py)（站点、计费、测试账号）、
  [`finish.py`](finish.py)（收尾，复用 testenv 的 `finish-start.py`）、[`mint.py`](mint.py)（签配对码）；本机跑的：[`probe.py`](probe.py)
  （按 CC 的写法发一次心跳）；测试机跑的：[`win/pair_profile.py`](win/pair_profile.py)。离线单测 `tests/test_maclab.py`（CI 跑）。

## 端口与站点

| 栈（项目名） | 网页入口（0.0.0.0） | 站点 | 测试账号 |
| --- | --- | --- | --- |
| `pearnly-maclab-erp` | 18991（`MACLAB_ERP_PORT`） | `erp.maclab.localhost` | `acc.erp@maclab.test`：ERP 管理员 + 采购 / 销售 / 库存等岗位，能把电脑连上 ERP |
| `pearnly-maclab-cowork` | 18992（`MACLAB_COWORK_PORT`） | `cowork.maclab.localhost` | `acc.cowork@maclab.test`：Cowork 管理员 |

- 账本（`billing-web`，站点 `billing.internal`）、数据库、Redis 都不发布端口；每套各自一份卷：`pearnly-maclab-<产品>_{database,queue,sites-billing,sites-<产品>}`。
- 站点按 Host 头认（前端 nginx 取 `$host`，不带端口），`Host: erp.maclab.localhost:18991` 就是 ERP 站点；别的名字 404。
- 测试账号的本地密码、数据库口令在 `~/.config/pearnly-maclab/<产品>.env`（600，第一次 `up` 生成，不进仓库）。站点用本地密码登录
  （没有统一身份）；浏览器从测试机打开 `http://erp.maclab.localhost:18991` 也能登。
- 18791 / 18792 已被别的实验栈（onboard-lab，只绑回环）占着，所以默认用 18991 / 18992；`up` 会先查端口。
- `PEARNLY_DEPLOYMENT=acceptance`：产品代码认得的非生产标记之一（新加一个值要改产品代码）；没配智能体密钥，CC 对话页不能用。

## 起、收

```bash
scripts/maclab.sh up erp          # 第一次约 70 秒：起栈 → 建站点与账号 → 收尾 → 经局域网地址 ping
scripts/maclab.sh up cowork       # 两套可同时跑
scripts/maclab.sh status
scripts/maclab.sh live erp        # 挂上 MACLAB_SRC（默认本工作树）的产品代码，同 testenv live；live-off 摘掉
scripts/maclab.sh down erp        # 容器删、卷保留（下次 up 只做收尾）
scripts/maclab.sh reset erp --yes # 删本栈全部卷（要重新签配对码、测试机档案作废）
```

`up` 先看本机 1 分钟负载（> 20 不起）和 colima 可用内存（< 8G 不起），本机没有镜像不自动拉。镜像默认线上 digest
`054aa603…`（env 文件里的 `PEARNLY_IMAGE`，或临时 `MACLAB_IMAGE=`）；数据库就是生产钉的那一份（`production/compose.yml` 里的摘要，多架构，本机取到 arm64 的同一版 11.8.9）；本机没有先 `docker pull`，拉不下来不要换别的镜像。

验收站点侧脚本：容器名固定 `pearnly-maclab-<产品>-bench-1`，`scripts/smoke/run.py` 的 `Remote("local")` 设
`PEARNLY_FLOWS_LAB=pearnly-maclab-erp` 就进它跑。注意：站点名带两段（`erp.maclab.localhost`），**不在**产品的测试站点名单 `pearnly_core.execution.replay.TEST_SITES` 里（那个名单只认一段的 `*.localhost`）：
识别回放这类只对测试站点开的功能在这里不生效；验收的站点侧脚本自己认 `.maclab.localhost`。

## 签配对码、在测试机上接

```bash
scripts/maclab.sh mint erp maclab-erp     # 标准输出两行：DEVICE PCD-… / PAIRING_CODE pcc_…@erp.maclab.localhost:18991
scripts/maclab.sh mint cowork testpc2   # PAIRING_CODE exp_…@cowork.maclab.localhost:18992
printf '%s' "$code" | scripts/maclab.sh probe erp [--leave]   # 本机模拟一次心跳（冒烟用；--leave 作废这把）
```

- ERP 以测试账号走 `pearnly_core.cc_desktop.api.register`（同对话页登记电脑），设备密钥随机（共用通道只凭令牌认电脑）；每签一次一台新电脑。
- Cowork 走 `pearnly_cowork.companion.api.register` 的旧槽位（不带设备密钥；第一次签时建本站 Express 连接 `CWEC-…`）：CC 启动后
  用本机设备密钥「认电脑」时认领它。槽位上已有一把回 `in_use`，`MACLAB_REPLACE=1` 换新；已被认领过回 `update_needed`，只能 reset。
  所以 Cowork 栈一次接一台测试机。

## 测试机侧（只用测试机2，`scripts/acceptance/maclab_pc.py`）

主会话 11-14 定：只用测试机2（ssh `testpc2`，桌面用户 aksar），它的 ERP、Cowork 两种连接各接一套 Mac 栈；用完原样还回，
`companion.json` 的哈希必须等于启用前（11-14 是 `C216460D`）。四条命令：

```bash
python3 scripts/acceptance/maclab_pc.py prepare --host testpc2 [--address 192.168.0.93]
python3 scripts/acceptance/maclab_pc.py attach  --host testpc2 --app erp     # 再 --app cowork；名字默认 maclab-<产品>
python3 scripts/acceptance/maclab_pc.py release --host testpc2 --expect C216460D
python3 scripts/acceptance/maclab_pc.py status  --host testpc2
```

1. **prepare**：① 管理员 ssh 跑 `win/hosts_add.ps1`，hosts 加一行 `<Mac 局域网地址> erp.maclab.localhost cowork.maclab.localhost`
   （改前整份备份到 `C:\PearnlyAcceptance\maclab\hosts-backup-<时间>`；已有同一行不动；同名字指向别处或只带一个名字就停下不改）；
   ② 本机 `pip install --target … --platform win_amd64 --python-version 3.11 --only-binary=:all: --no-deps` 装 httpx 一族
   （版本照 `companion/packaging/requirements-build.txt`），拷到 `C:\PearnlyAcceptance\maclab\site`（`pair_profile.py` 把它加到 `sys.path`
   最前面，不动测试机现有 Python）；本工作树 `companion/src/companion` 刷新到 `C:\PearnlyAcceptance\ccsrc\companion`；打印包与版本清单；
   ③ 以桌面用户（`win/maclab_reach.py`）看两个站点解析到的地址、各 ping 一次，栈没起的标 `down`，不算失败。
2. **attach**：第一次启用前在测试机 `C:\PearnlyAcceptance\maclab\before\` 记下 `companion.json` 的逐字节备份与哈希（已有不覆盖，
   `win/cfg_guard.py snapshot`）→ 本机 `maclab.sh mint` 取配对码（只在内存和 600 的临时文件里）→ 拷到测试机 `maclab\codes\<随机>.code`
   → 以桌面用户 `pair_profile.py add <名字> <码文件> C:\PearnlyAcceptance\root \\accserver\ACCOUNT\69EXP\TEST2`（11-16 起用命名共享 ACCOUNT，与 Express 程序登记表的映射盘同一个共享，代录选公司才认得出；旧的 d$ 数据根留在 root-dshare-20261116）（读完即删，
   调 CC 自己的 `pairing.pair()` 加一份**新档案**，再按下节让出位置）→ 回读确认码文件已不在（还在就删掉并报错）。
3. **release**：状态里每份 maclab 档案 `remove`（能连上 Mac 栈就让服务端作废密钥）→ `cfg_guard.py check`：字节等于启用前（与 `--expect`）
   就过；不等但 JSON 语义相同 → 停 CC、逐字节拷回、启动、隔 10 秒再核两次；语义也不同 → 停下列出差异键路径（不带值），不硬拷 →
   重启 CC，`companion.log` 里重启之后要有 `[heartbeat]` INFO、其后没有心跳告警或错误（最多 150 秒），之后哈希仍等于启用前 →
   `before\` 改名留档（下次 attach 重新记）。
4. **status**：`pair_profile.py show`（档案、位置、maclab 状态）+ 当前哈希 + 启用前哈希。

桌面用户的脚本都经 `scripts/acceptance/targets.py` 的 `Windows(host).python`（一次性计划任务：参数一行一个写进 `jobs\<名字>.args`、
输出写 `jobs\<名字>.out`）跑：DPAPI 加密密钥、读账套服务器都要桌面用户本人的会话。`add` 的参数里只有配对码**文件路径**，
配对码不进任何参数文件、输出与 CC 日志。每次改配置前停 CC、整份备份到 `C:\PearnlyAcceptance\maclab\backup`，改完用 CC 的
`ProfileStore.load` 读回校验，失败还原；原来开着就再启动。手工也可直接跑 `win/pair_profile.py add | pause | resume | remove | show`。

## 已有档案的上报范围（影响）

CC 的「Express 位置」和每个位置给哪种成员读是**整台电脑一份、按通道（ERP / Cowork）分**，不按档案分；数据根还会在成员之间互相补。
同一通道上的两份档案因此隔离不开：maclab 的 ERP 档案一启用就会读到这台电脑所有勾给 ERP 的位置（把那些账套报给 Mac 栈，自动建公司、
同步主档），原来连 testerp 的 ERP 档案也会读到验收数据根。所以 `add` / `resume` 时：

- 验收数据根加进位置清单、给上这条通道（ERP、Cowork 两份 maclab 档案共用同一个验收数据根时两种都给，最后一份拿走时整个去掉）；它不能在任何已有位置里面或包含已有位置（否则那个位置的成员照样读到，脚本拒绝）；
- 别的位置去掉这条通道的勾（另一条通道不动），记下来，`pause` / `remove` 时加回；
- **同通道的其余 Express 档案暂停**（例如测试机2 连 testerp 的 ERP 档案），`pause` / `remove` 时恢复。

也就是说：maclab ERP 档案启用期间，这台测试机对 testerp 的 ERP 连接离线（Cowork 档案照常）；反之 Cowork 亦然。

## 已知限制 / 没实测的

- 测试机那一半（`maclab_pc.py` 四条命令、CC 领单写进验收账套、回执）都没在真机跑过；只有纯函数与编排的离线单测（`tests/test_maclab.py`、`tests/test_maclab_pc.py`）。
- 本机 Mac 防火墙、换网络后的局域网地址、colima 重启后端口转发是否照旧：只在本机经 `192.168.0.93` 自测过。
- Cowork 只做到：计费与余额、全部功能、管理员、工作空间、ERP 推送总闸、Express 连接（签码时建）。**`Cowork Company`（公司主档）
  没建**：要 CC 先报上账套目录，再由人在网页 / CC 对话里从目录建（`companies.service.create`）；CC 对话直写还要真模型，本栈没配。
- ERP 测试员工是管理员角色；以「只有采购岗位的普通员工」验收时另用团队管理加人。
- 实时源码不带本地构建的前端 SPA（取镜像里那份），同 testenv 默认。
