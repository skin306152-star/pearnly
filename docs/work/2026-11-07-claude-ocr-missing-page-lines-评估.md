# 需求影响评估：缺页多页票的明细读不出（Claude 识别）

> 分支 `pearnly/ocr-missing-page-lines-bcf1fc`，基线 `origin/main` = `8e5cb5724`。甲骨文 `pearnly-claude-ocr` 里 `claude_ocr/*.py`
> 与本基线 md5 逐个一致（11-07 核对），线上读法就是本仓这份代码。上一份记录：`docs/work/2026-11-06-intake-edit-empty-lines.md` 待办 B。

## 0. 需求原文与理解

- 现象：12 页 BJC 采购发票 PDF（`ราคาซื้อ สินค้า BJC.pdf`，11 张单），第 1 张 211067100410（第 1 页）、第 7 张 221067100014（第 7 页）
  票面印「หน้า 1/2」，第 2 页不在文件里。识别结果这两张 `lines` 为空。合计印在缺的那页，合计空是对的；第 1 页印的明细（10–11 行）应逐行读出。
- 目标：缺页票只要这一页印着明细就逐行读出；合计没印就留空，不编数。按类修：凡「多页票只拿到一部分页」都一样，不针对 BJC。

## 1. 根因（证据：测试机 testcowork 上同一份文件的 6 次真实识别检查点，只读取回；不花钱）

testcowork 库 `tabPearnly OCR Checkpoint` 里，这份文件第一段（第 1–8 页，8 张单）在 11-04、11-06 共被识别 6 次：

| 次 | Haiku 首读 | 交 Opus 的原因 | Opus 输出 token | 第 1 / 7 / 8 张的明细行数 |
| --- | --- | --- | --- | --- |
| 8d10 | 输出到 16000 上限，读不出 | `no_result` | 5933 | **0 / 0** / 5 |
| 2595 | 读完（14493 token） | 第 1、7 张 total missing 等 | 8507 | 11 / 10 / 5 |
| f1d7 | 超限读不出 | `no_result` | 8493 | 11 / 10 / 5 |
| e1dc | 超限读不出 | `no_result` | 5316 | **0 / 0 / 0** |
| 18a8 | 超限读不出 | `no_result` | 5931 | **0 / 0** / 5 |
| a462 | 超限读不出 | `no_result` | 8495 | 11 / 10 / 5 |

结论：

1. **直接原因是 Opus 读法不稳定**：同一段、同样的图，Opus 6 次里 3 次把两张「1/2」票的明细整块省掉（输出少约 2600 token，正好约 21 行），
   notes 里写着「หน้า 1/2 continued」「page 2 not included」——它把缺页票当成「明细还没完、先不录」；其中 1 次连完整的第 8 张（5 行）也省了。
   合计、未税、税额三次都读得一样，只是明细时有时无。
2. **提示词没覆盖这类票**：`spec.py` 只写了「跨页的单算一张、记在第一页」「没印明细时 lines 留空」，没说「后面的页不在文件里时，照样逐行转录手上这页的明细」。
   模型自己在「这张票明细不全」和「没印明细」之间摇摆。
3. **触发条件**：BJC 每页明细密，8 页一段 Haiku 正常要 14500 token，6 次里 5 次超过 16000 上限读不出，整段改交 Opus（带的提示只有 `no_result`，
   没告诉 Opus 要注意什么）。缺页票本身合计必空，自检 `total missing` 也一定升级。所以这类票最后几乎都由 Opus 定稿，Opus 的不稳定直接落到结果上。
4. **没有兜底**：Opus 读完不再查明细；`why_after` 只剩 `total missing`，「明细为空」不报。
5. 本端没丢：`segments.merge` 不删行，Cowork `ocr/execution/claude.py` 原样把 lines 转成 items（上一窗口已核）。

## 2. 修法（一次只改一个变量：提示词）

在 `apps/pearnly_core/pearnly_core/claude_ocr/spec.py` `VOUCHER_SYSTEM` 的 `lines` 那一条后面加一句（只这一处，其他文字一字不动）：

> A document whose other pages are not in this file (for example printed หน้า 1/2 or Page 1 of 2 with page 2 missing) is still one document: transcribe every line printed on the pages you have, for every document in the file however many there are; leave totals printed only on the missing pages empty, never computed.

- 按类：说的是「票面页码 / 续页不在本文件」这一类，任何供应商、任何页数都适用；同时写明「文件里有几张票都要逐张出明细」，盖住第 8 张那种连带省略。
- 不编数：「合计只印在缺的页上就留空，不算」，与现有「Never compute a value that is not printed」一致。
- Haiku 与 Opus 共用这份说明，两边一起生效；甲骨文与生产兜底是同一份 `spec.py`。

### 不采用的做法（为什么）

| 做法 | 不采用的原因 |
| --- | --- |
| 自检加「印了金额却没明细」→ 升级 | 回放基准里 Haiku 有 41 张、Opus 有 20 张票就是「有金额没明细」，多数是正常的（0.00 的政府收据、没有明细表的收据）。加了会多升级、多花钱；而且这次出错的就是 Opus 自己，升级解决不了 |
| Opus 明细为空时用 Haiku 的明细 | 这份文件 6 次里 5 次 Haiku 超限根本没结果，补不上 |
| 提高 Opus effort | 换模型参数，成本上浮，且不针对「缺页票」这个语义缺口 |

### 另报的后续（本次不改，单独一个变量，排在本次之后）

- **Haiku 输出上限**：明细密的 8 页一段，Haiku 6 次超限 5 次，整段改走 Opus，一段 0.24–0.31 美元（Haiku 自己读完只要约 0.01）。可改成「超限时把这一段拆成两半再用 Haiku 读」，
  不改提示词与模型。回放基准 549 份里 Haiku 从未超限（最多 14203），回放零费用、逐份不变。建议本次合入后另开一项。
- 第二段（第 8–12 页）每次都因「5 pages but only 4 documents」升级：第 9–10 页本来是一张两页的票，这条检查对含跨页票的段必然误升。同属成本项，随上一条一起评估。

## 3. 上下游数据

- Cowork / ERP / 对账发票类都经 `claude_ocr` 的同一份提示词；只是明细行变全，字段形状不变。ERP 采购按行选物料、Cowork 录入编辑页、Express 推送照旧按行处理。
- 对账三类（银行 / 总账 / 税报）、身份证用各自的提示词，不受影响。

## 4. 隔离与权限 / 入口 / 文案 / 删除

- 不涉及：不改接口、权限、界面文案、数据结构；网页、手机、LINE、CC 对话上传最后都走同一份读法。

## 5. 共用件与连带回归

- 改到共用层 `pearnly_core.claude_ocr.spec`：施工时跑 `scripts/check-impact.py` 贴结果；合入前按「共用层改动要跑候选离线检查」在生产镜像跑 `ci-candidate-checks offline`。
- 上线：合入 main 后由主会话 `bash scripts/claude-ocr/deploy.sh`（先给现有镜像打备份标签，1 分钟回退）；生产镜像里的兜底副本随下一批部署。

## 6. 验证计划与费用（6QAA 评测密钥，脚本按实际花费硬上限）

| 步 | 内容 | 判据 | 预计 | 硬上限 |
| --- | --- | --- | --- | --- |
| 1 | BJC 第 1–8 页，新提示词：Opus × 5（照线上带 `no_result` 提示）+ Haiku × 5 | Opus 5 次第 1 / 7 / 8 张都出 11 / 10 / 5 行左右明细，第 1、7 张合计仍空；Haiku 读完的那几次同样 | 约 1.5 美元 | 2.5 |
| 2 | 549 份回放基准用新提示词重录（Haiku + Opus 两遍，同 10-27 录法），按现行升级策略回放，原打分工具和金标 / Express 逐格比 | 「原来对、改后错」= 0；读错格不增；「有金额没明细」与「明细对不上合计」张数不增；升级率、每页成本不超噪声 | 约 19.4 美元 | 22 |
| 3 | 并发压测 | 提示词只多约 70 个输入 token（系统提示有缓存），输出只在原来漏行的票上变长；建议不另跑，由主会话定 | 0 | — |

- 第 2 步为什么全录：提示词变了，旧的 549 份基准就不再代表线上读法，之后任何升级策略回放（包括上面的 Haiku 超限后续）都要新基准。
  省钱的替代：Haiku 全录 + Opus 只录现行策略会升级的份（约 5 美元，上限 8），能当闸门，但不能再当以后的回放基准。
- 合计预计约 21 美元（省钱替代约 6.5 美元）。11 月台账（`~/pearnly-devtools/test-window/round12/ocr-spend.md`）只记了 11-03 的 0.509；
  长 PDF 记录另写了 2.23、testcowork 上这份 BJC 6 次识别约 2.8（走生产密钥）。开跑前请主会话给出本月累计，到 80 美元停。
- 语料没有明细行的标准答案：明细好坏用「明细与金标核实过的合计自洽」间接看（同 10-27 口径），BJC 用人工核对的行数。

## 7. 要问主会话的点

| # | 问题 | 建议 |
| --- | --- | --- |
| 1 | 修法用提示词加一句（第 2 节原文）| 采用 |
| 2 | 回放基准全量重录（约 19.4 美元）还是省钱替代（约 5 美元）| 全量：提示词变了旧基准就过期 |
| 3 | 并发压测是否免跑 | 免跑，理由见第 6 节 |
| 4 | Haiku 超限拆段作为下一项另做 | 同意排在本项之后 |
| 5 | 本月测试费用累计 | 请告知，开跑前核对 |

## 8. 工作量（机器耗时）

1. 改提示词 + 单元测试（`spec` 文字、结构化格式不变）：约 15 分钟。
2. 第 1 步 BJC 小样本：约 10 分钟。
3. 第 2 步重录 549 份 + 回放打分：约 40–60 分钟（6 路并发）。
4. 本地闸门、推分支、Development checks、候选离线检查：约 45 分钟。
5. 合入前发主会话；部署甲骨文由主会话执行。

## 9. 提示词三版都没过闸门（11-07，已改回 main）

| 写法 | BJC Opus 读全 | 6900205 多拆附件凭证（旧 0/3） | 6801623 单号留空（旧 0/3） | 6901074 判成转账凭条（旧 0/3） |
| --- | --- | --- | --- | --- |
| v1 另起一条 + 「每张票都要出明细」 | 5/5 | 3/3 | 3/3 | 2/3 |
| v2 去掉「每张票」半句 | 4/4 | 3/3 | 2/3 | 1/3 |
| v3 只在 lines 那条补一句、只讲明细 | 5/5 | 2/3 | 1/3 | 1/3 |

系统提示一动，模型对「附凭证页的文件拆不拆票」这类边界判断就跟着动；而改系统提示必须重新采样，「原来对、改后错 = 0」也无法在回放里成立。
v1 全量重录与对照数据留在甲骨文 `runs-prod/replay-p1107`（567 条，v1 写法，**不作为基准**）。主会话 11-07 定：改走不改提示词的「明细补读」。

## 10. 修法 ①：明细补读（主会话 11-07 审定方向）

- **位置**：`claude_ocr/engine.py`，每次 `engine.run`（每一段）收尾之后；甲骨文服务与生产兜底同一份代码。读法提示词、升级策略、合并、`settle` 一律不动。
- **条件**：发票类、`entry = enter`、某张票 `lines` 为空、本段是图片（表格文件导出的文字不补）。不要求印了金额（BJC 第 1 张三个金额都空）。
- **补读**：只用 Haiku；同一份系统提示（命中缓存）+ 一句只要明细的请求（单号 X、它所在的页），结果格式只有 `lines` 与 `printed_subtotal`（这几页印的明细小计 / 本页小计）。
  页 = 这张票在本段里从它的起始页到下一张票起始页之前（不超过一次调用的页数）；超限（`max_tokens`）就按页分开补读、按页序拼起来。
- **写回（宁空勿错）**：补出的行逐行过 `line_math`，且行金额之和对上票面已读出的未税小计或含税合计（`consistency` 同一套容差）；
  票面三个金额都没读出时，对上补读读出的本页小计（分页补读时每页对上各自的小计，或末页的累计小计）。对不上就不写回，阶段记录里写原因。只改 `lines`，表头、金额、`settled`、`why_after` 不动。
- **记录**：补读的调用进 `stages`（带 `purpose: lines`），费用、耗时照计；Cowork 的层级链 / 升级模型、ERP 的模型名只看识别阶段，不把补读算成升级。
- **闸门**：表头用 10-27 基准（`replay-lp`）回放、补读走真实 Haiku（6QAA），和不补读的回放逐格比，表头必须完全相同；补出的行看合计自洽，返回空 / 拒写的逐张人工抽查；BJC 用 testcowork 检查点里那三次明细为空的 Opus 读法直接补读；最小压测一轮。预计约 1 美元。

主会话 11-07 补充：先 Haiku，Haiku 读出了行却对不上才交 Opus（BJC 每行两层折扣，Haiku 拿毛额当行金额）；单行 `line_math` 只记录、不单独否决，写回以净额加总对上票面为准。

## 11. 实现与验证（11-07）

实现：`claude_ocr/lines.py`（新）、`claude_ocr/engine.py`（`fill_lines`，`engine.run` 收尾后调用）；Cowork `ocr/execution/claude.py`
（层级链 / 升级模型只看识别阶段，补读的证据行记 `claude_lines`）、ERP `recognition/claude.py`（模型名只看识别阶段）；
`scripts/claude-ocr/gate.py` 加 `--fill` / `--fill-cap`（回放时也补读，走真实 Haiku / Opus，付费）。单元测试 `tests/test_claude_ocr_rules.py::LinesFill` 12 条。

| 验证 | 结果 |
| --- | --- |
| 表头逐格（10-27 基准 `replay-lp` 552 份，补读 vs 不补读） | 除明细外 **0 份不同** |
| 语料补读（36 张空明细） | 补上 2 张（6700889 三行、6901191 一行，均对上票面）；33 张 Haiku 答「没印明细」；1 张（6801740）Haiku 有行缺金额 → Opus 答没印。补读约 0.03 美元 / 552 份 |
| 人工抽查「没印明细」 | 6801740、6700033、6801008、6801041、6700654、6801686 原件首页：保险收据、社保收据、只印合计页，确无明细表 |
| BJC（testcowork 三次明细为空的 Opus 读法直接补读） | 9 张空明细补上 8 张：e1dc 第 1 / 7 / 8 张都补上（11 / 10 / 5 行，对上本页小计 38803.20、未税 38869.00、31394.30）；8d10 第 1 张 Haiku、Opus 都对不上 38803.20，**不写回**（宁空勿错）；0 张写错 |
| 最小压测（金标单页 30 份 × 15 路，6QAA 直连） | 失败 0；单份 p50 11.5 → 10.9 秒，p90 18.5 → 21.0 秒（本轮升级 5 份、补读 3 次，旧一轮升级 3 份） |
| 本地闸门 / 候选离线检查（生产镜像 1560bb95） | 见交付说明 |

共用件连带（`check-impact.py`：命中 `ocr`，改到调用方的 `tax-documents`；新登记 `shared.claude-ocr.line-fill`）：

| 入口 | 结果 |
| --- | --- |
| `shared.claude-ocr.line-fill` | 通过：552 份回放 `--fill` 表头 0 份不同；BJC 补读 9 张补上 8 张、0 张写错；语料补上 2 张 |
| `shared.ocr.pipeline` | 读法层通过（同上；压测 30 份 × 15 路失败 0）；部署后三产品付费各传一张 **没测到**（要先部署甲骨文 claude-ocr） |
| `cowork.ocr`、`cowork.execution`、`cowork.tax-documents` | 闸门里 Cowork 正式代码出的结果（`*-p1107fill-cowork.jsonl`）表头不变；真实站点上传 **没测到**（同上，部署后验收） |
| `erp.ocr`、`erp.tax-documents`、`erp.cc.troubleshooting` | ERP 模型名改为只看识别阶段（单元测试 + 候选离线检查）；真实站点识别 **没测到**（同上）。缺页采购单补上明细后合计仍空，照旧进「没有票面合计」 |
| `dms.ocr.id-card` | 不经过：身份证读法不走发票补读（`kind != voucher`） |
| `shared.originals.download`、`erp.stock-count.upload`、`erp.stock-in.attach`、`erp.stock-out.attach` | 不经过：原件存取与附件不调识别读法 |

测试花费（6QAA，台账 `~/pearnly-devtools/test-window/round12/ocr-spend.md`）：本任务合计约 31.4 美元（提示词三版的小样本、全量重录与对照约 30.6，补读闸门约 0.8）；11 月已知累计 37.10 美元。

## 12. 上线步骤（合入 main 后，由主会话执行；不在生产上试）

顺序无硬约束：甲骨文新 + 生产旧时，生产旧代码把补读的 Haiku 阶段算进层级链 / ERP 模型名（只是标签，数据不受影响）；生产新 + 甲骨文旧时不补读。建议同一批先甲骨文、后生产。

1. 长期主目录确认 `git log -1` 是本任务合入的提交。
2. 甲骨文现有镜像打备份标签：`ssh -i ~/.ssh/oracle_free ubuntu@213.35.126.34 'sudo docker tag pearnly-claude-ocr:latest pearnly-claude-ocr:before-line-fill-20261107'`
3. 部署：`bash scripts/claude-ocr/deploy.sh`（不带 `--secrets`）。
4. 回读：`docker ps` 里 `pearnly-claude-ocr` 为 Up；`sudo docker exec pearnly-claude-ocr grep -c fill_lines /app/pearnly_core/claude_ocr/engine.py` 大于 0，`/app/pearnly_core/claude_ocr/lines.py` 存在；`curl -k https://127.0.0.1:8443/healthz`（在甲骨文上）回 `{"ok": true}`。
5. 回退：`sudo docker tag pearnly-claude-ocr:before-line-fill-20261107 pearnly-claude-ocr:latest`，再按 deploy.sh 里同一条 `docker rm -f` + `docker run` 重起容器。
6. 生产镜像（兜底副本与 Cowork / ERP 两处标签）随下一批 `scripts/production/deploy.sh` 上线。
7. 部署后验收（付费小样本）：Cowork 传 BJC 原件一次，看第 1、7 张明细是否补上、合计仍空；识别证据里补读行为 `claude_lines`。
