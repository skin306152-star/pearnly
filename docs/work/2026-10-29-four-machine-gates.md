# 四道机器闸（主会话 10-28 任务书，10-29 恢复施工）

> 验收窗口施工。分支：① `pearnly/swallowed-errors-gate`（已合入 main 956482ab、已删）；②③④ `pearnly/regression-baseline`。
> 主会话 10-29 定：流程套件不上 free-arm-2（生产镜像只有 amd64，ARM 上要 qemu，又慢又不一致还要 root 权限），在本机回归实验室跑、结果回写 GitHub 提交状态 `pearnly/flows`，部署闸认这个状态，没有绕过。

## ① 吞错闸门（已合入 main 956482ab）

- 规则：`scripts/check-swallowed-errors.py`。产品前端（apps 下 public / page / doctype JS、frontend/src、frontend/assistant/src）每个 catch 必须重抛或交统一出错回传（`pearnlyCaught` / `pearnlyReportError` / `reportCaught` / `withReport`）；空 catch、只 console、只给提示一律失败。预期的吞错登记在 `tests/baseline/swallowed_errors.toml`（文件 + 那一行特征 + 原因），登记不唯一、过期、没写原因都失败。
- 存量：298 个 catch，改 218 处接入统一回传，登记 89 条预期（浏览器存储、坏 JSON、相机能力探测、推送 / SW 能力、失败响应正文非 JSON、提示音、errors.js 自身）。请求失败（服务端已报错的）`pearnlyCaught` 不重复回传。
- 拦住了什么：线上 fcb65cca 的 ERP / Cowork `stocktake-entry.js`（10-28 手机盘点事故那段）分别在 258、211 行被拦；新加空 catch、只 console 的 catch、复制一条已登记的行都被拦。
- 全闸门 144 项通过；分支 CI（free-arm-2）success；快进合入 main 并回读远端 956482ab。

## ② 两套固定数据（`scripts/regression/datasets.py`）

| 数据集 | 站点 | 内容 |
| --- | --- | --- |
| `new`（照 polymer） | `erp-new.regression.localhost` | 一家公司、按正式方式接上 Express（`books.replace`）；Express 主档同步 7 个仓（01–07）；零库存；商品分别靠编码、条码（EAN-13）、二维码内容、内部编码、Express 同步认，再加一个按台管理（车） |
| `old`（照 mrerp） | `erp-old.regression.localhost` | 两家公司（A 两仓 + 库位清单，B 一仓）；5 件商品（合法 EAN-13）；采购入库有存量 585、销售（含作废）、收银班次销售 / 退货 / 交班、盘点（进行中、已过账） |

全部走正式接口建，重跑幂等；定义改了收敛到定义上（例：老数据早先的条码不是合法 EAN-13，假摄像头画不出来，重跑时按正式改商品接口收敛）。

## ③ 入口 → 流程用例

- 登记表 `tests/flows/registry.toml`；跑法 `scripts/flows/run.py`；覆盖闸 `scripts/flows/coverage.py`（CI 跑，改到产品入口文件没有流程登记 → 失败）；说明 `tests/flows/README.md`。
- 第一条流程 `erp.stocktake.scan`（10-28 polymer 手机盘点事故）：两套数据 × 电脑 / 手机宽度；手机宽度另用假摄像头放条码画面读码。每个码断言录入区出没出来、品名对不对、提示，保存后查 `ERP Stock Count Entry` 商品与数量，没有多余记录；页面无报错；站点 Error Log 没多出前端回传。
- 10-29 结果（实验室：线上同版镜像 fb1037ad，源码 main 8fd0fa4d）：

| 数据集 / 宽度 | 结果 | 耗时 |
| --- | --- | --- |
| new / 电脑 | 通过（6 种认法 + 按台商品被拒 + 未知码） | 97 秒 |
| new / 手机 + 假摄像头（8850001000019） | 通过 | 195 秒 |
| old / 电脑 | 通过 | 93 秒 |
| old / 手机 + 假摄像头（8850002000025） | 通过（首跑因老条码不是合法 EAN-13 没画出来，修数据集后重跑） | 269 秒 |

- 覆盖面：全仓产品入口文件 339 个，有流程的 4 个。覆盖闸只看「这次改到的」，所以今后改到哪个入口就要给它补流程；存量清单 `python3 scripts/flows/coverage.py --list`。
- 观察项（产品，未改）：盘点录入框查到下一件商品后，上一件的提示（如「此仓库未登记该商品的台」）仍留在框里。
- work-followups 的流程用例（`scripts/regression/flows/`，JSON 用例 + 自己的种子数据与实验室）还在它的工作树里未提交，等它提交后按本登记表适配并入，不另跑一套。

## ④ 部署闸（`scripts/production/deploy.sh`）

- 构建前（演练也查）核对要部署的 main 提交的 `pearnly/flows`：没有、不是 success、不是全套（说明不以 `all flows` 开头）都停下，打印补跑命令，生产没动；没有绕过开关。
- 状态说明格式：`<范围>; <流程数> flows; datasets <数据集>; <通过>/<格数> runs passed; <秒>s`。`run.py --post-status` 回写前核对实验室挂的就是本工作树源码、产品代码无未提交改动。
- 部署后回读（`deploy-readback.sh`）每个租户多列 `browser_errors_since`（部署后的前端回传，算进 Error Log、非 0 即问题）和 `browser_errors_24h_before`（对照）。
- 离线测试 `scripts/production/test-deploy-run.py` 23 项通过（新增：没有结果 / 没通过 / 只跑了受影响流程三种拦下且不触发构建；演练同样拦；回读按租户列出前端回传）。

## 限制

- 依赖 / Dockerfile / 底座版本变了时实时源码盖不到，要用候选镜像起实验室再跑（README 写了）。
- 流程只有一条；339 个入口里 335 个还没有流程，靠覆盖闸逐步补。
- 实验室 bench 3 GB 内存，跑大批角色时 OOM 过一次（10-28），重启后重跑受影响部分。
