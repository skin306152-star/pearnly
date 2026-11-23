# Cowork 领单时判写账人：两台同时抢同一张的并发流程（2026-11-23）

主会话 11-23 定：「谁的电脑写单」两产品同一条（用户 11-17 定稿）已在 main 并于 11-20 上线；ERP 有两台同抢的并发流程
`erp.writer-at-claim`，Cowork 只有顺序领的 `cowork.office-writer`。本分支补 Cowork 的同一面，抓到问题就按类修。

## 新流程 `cowork.writer-at-claim`

- 电脑、推单沿用 `cowork_office_writer.py` 的造法；两台各自一个线程、各自连库，认好密钥后一起放行（`threading.Barrier`），
  同一刻走正式入口同一条路领单（`queue_api._run` 的 `conflicts.retried(transport.lease)`）。
- 两格：① 提交人自己的电脑与同事的同时来领 → 本人的领到、不记替写人；② 提交人的关着、同事两台同时来领 → 只有一台领到、
  记下替写人、录入人不变。每格断言：两台确实同一刻发起、恰好一台领到、另一台空手不报错、`fence` 为 1、领取记录
  （Cowork Push Attempt）只有一条。

## 结论

没有抓到问题，产品代码不改。Cowork 与 ERP 的守护落点不同、结果相同：

- Cowork 领单头一步锁这条连接（`credentials.revalidate` 的 `for_update`），同一连接上同时来的几台在库里排成先后，后到的等
  先到的提交后再看，这张已被领走、空手回去；之后 `ledger.lease` 锁内复核、同账套写者锁、`fence` 编号仍在。
- ERP 在写账件行锁上相撞，抢输的撞 MariaDB 1020、回滚后空手（`jobs._claim`，`erp.writer-at-claim`）。
- 第一版把两台的碰头放在判定之后（照 ERP），两台都碰不上头：后到的那台卡在连接锁上，到不了判定。据此改成同一刻发起。

## 验证

- 本机实验栈（`pearnly-wac-lab`）：`cowork.writer-at-claim` 连跑 3 次通过；与 `cowork.office-writer` 同跑通过。
- 两台真电脑同时测：没测到（主会话 11-23：今天测试机2 不用，等两台都能上时排）。
