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
scripts/maclab.sh mint erp testpc1      # 标准输出两行：DEVICE PCD-… / PAIRING_CODE pcc_…@erp.maclab.localhost:18991
scripts/maclab.sh mint cowork testpc2   # PAIRING_CODE exp_…@cowork.maclab.localhost:18992
printf '%s' "$code" | scripts/maclab.sh probe erp [--leave]   # 本机模拟一次心跳（冒烟用；--leave 作废这把）
```

- ERP 以测试账号走 `pearnly_core.cc_desktop.api.register`（同对话页登记电脑），设备密钥随机（共用通道只凭令牌认电脑）；每签一次一台新电脑。
- Cowork 走 `pearnly_cowork.companion.api.register` 的旧槽位（不带设备密钥；第一次签时建本站 Express 连接 `CWEC-…`）：CC 启动后
  用本机设备密钥「认电脑」时认领它。槽位上已有一把回 `in_use`，`MACLAB_REPLACE=1` 换新；已被认领过回 `update_needed`，只能 reset。
  所以 Cowork 栈一次接一台测试机。

测试机侧三步（以桌面用户跑，见下）：

1. hosts 加一行（管理员）：`192.168.0.93  erp.maclab.localhost cowork.maclab.localhost`（Mac 换了局域网地址要改）。
2. 配对码写进一个文件，`python pair_profile.py add maclab-erp <码文件> <验收数据根> <验收账套完整路径>`：读完就删码文件，
   调 CC 自己的 `pairing.pair()` 加一份**新档案**（已有档案不动、不写开机自启），再按下节让出位置。
3. Mac 栈停之前 `pause maclab-erp`（让出的都还回去），回来 `resume`；不用了 `remove`（能连上就先作废密钥）。

`pair_profile.py` 必须在桌面用户自己的会话里跑：密钥用 DPAPI（当前用户）加密存进 `companion.json`，ssh 公钥登录的会话不保证能
加 / 解；也读不到账套服务器。照验收体系的 `scripts/acceptance/win/acc-run.ps1`（一次性计划任务）跑。Python 用
`C:\PearnlyPython311\python.exe`、CC 源码 `C:\PearnlyAcceptance\ccsrc`；除已有的 `dbf`、`aenum` 外**还缺 `httpx`**
（连带 `httpcore`、`h11`、`anyio`、`sniffio`、`idna`、`certifi`、`typing_extensions`，版本照 `companion/packaging/requirements-build.txt`）。
每次改配置前停 CC、整份备份到 `C:\PearnlyAcceptance\maclab\backup`，改完用 CC 的 `ProfileStore.load` 读回校验，失败还原；原来开着就再启动。

## 已有档案的上报范围（影响）

CC 的「Express 位置」和每个位置给哪种成员读是**整台电脑一份、按通道（ERP / Cowork）分**，不按档案分；数据根还会在成员之间互相补。
同一通道上的两份档案因此隔离不开：maclab 的 ERP 档案一启用就会读到这台电脑所有勾给 ERP 的位置（把那些账套报给 Mac 栈，自动建公司、
同步主档），原来连 testerp 的 ERP 档案也会读到验收数据根。所以 `add` / `resume` 时：

- 验收数据根加进位置清单，只给这条通道；它不能在任何已有位置里面或包含已有位置（否则那个位置的成员照样读到，脚本拒绝）；
- 别的位置去掉这条通道的勾（另一条通道不动），记下来，`pause` / `remove` 时加回；
- **同通道的其余 Express 档案暂停**（例如测试机1 连 testerp 的 ERP 档案），`pause` / `remove` 时恢复。

也就是说：maclab ERP 档案启用期间，这台测试机对 testerp 的 ERP 连接离线（Cowork 档案照常）；反之 Cowork 亦然。

## 已知限制 / 没实测的

- 测试机那一半（hosts、`pair_profile.py`、CC 领单写进验收账套、回执）都没跑过；`pair_profile.py` 只有纯函数的离线单测。
- 本机 Mac 防火墙、换网络后的局域网地址、colima 重启后端口转发是否照旧：只在本机经 `192.168.0.93` 自测过。
- Cowork 只做到：计费与余额、全部功能、管理员、工作空间、ERP 推送总闸、Express 连接（签码时建）。**`Cowork Company`（公司主档）
  没建**：要 CC 先报上账套目录，再由人在网页 / CC 对话里从目录建（`companies.service.create`）；CC 对话直写还要真模型，本栈没配。
- ERP 测试员工是管理员角色；以「只有采购岗位的普通员工」验收时另用团队管理加人。
- 实时源码不带本地构建的前端 SPA（取镜像里那份），同 testenv 默认。
