# 对账遇到没有余额的行不再崩溃；程序错误不再说成「系统繁忙」（2026-10-02）

多人压测（100 人）testcowork2 B08-冰厂SINCERE-BBL0555-2026-01（对账号 4b910e19…）：`bank_recon_service._merge_on` 往余额链缺口里插别的份时，
「这两行已经接好就不插」只查了前一行有余额，后一行余额为空时 `step` 里 `second.balance - first.balance` 抛 TypeError，整套失败；
10-01 19:50 也出过一次。用户看到的是「系统繁忙，这次对账没有完成，请稍后重试」，重试照样失败。

## 改法

- `step`：任一行没有余额就是接不上（与 `fits` 对 after 的判断一致），不再抛异常。
- `recon_center/jobs._failure_text`：数据库锁 / 超时、连接断了说「系统繁忙、稍后重试」；校验与数据问题说具体原因（不变）；
  其余程序错误说新提示 `reconciliation.program_error`（已记录、重试结果相同，四种语言）。照旧每次记 Error Log。
- 重放脚本加 `--job` / `--case`：按对账号重放没留下结果的失败运行（压测用户已删时用同一工作区跑过同一套的人）。

## 核对

- 生产机一次性容器重放这次失败的运行：生产代码（0fd1621f）复现同一个 TypeError；修好后 44 行、匹配 41 笔
  （另外 7 次成功的 B08-01 都是 43 行、匹配 41 笔；多出的 1 行就是那行没余额的，接不进链、按日期留下交人看；
  各张照片每次读出的行数本来就有出入，如 6.jpg 10–25 行）。
- 测试：`test_cowork_recon_scanned_docs` 加没有余额的行不崩（旧代码复现 TypeError）；新 `test_cowork_recon_failure_text` 3 项，加进候选离线道。
