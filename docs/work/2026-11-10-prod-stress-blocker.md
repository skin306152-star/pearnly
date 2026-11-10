# 2026-11-10 线上压测开压前的阻塞（属 docs/work/2026-11-08-prod-stress-100.md）

发布窗口 15:5x 通知可以开压（release-20261110-0618，81ec70ae3）。开压前核前提时发现，**压测会写进事务所真账套**，所以一格都没跑。

## 事实
- testcowork 的 e2e 账号（e2ecowork）在选择公司弹窗的 Express 标签下能看到 34 家，其中 ซินเซียร์ไอซ์（0105546015062）等多数挂的是 **69EXP**；只有少数几家挂 70EXP（税号 01055992100xx 的测试公司、มานะชัยบริการ 两边都有）。
- 测试机 skin3 的 CC 桌面版配置（`C:\Users\skin3\AppData\Roaming\Pearnly\companion.json`，11-10 11:58 改过，只读查看）：
  `express_places` = `\\accserver\d$\ACCOUNT\70EXP`、`\\accserver\d$\ACCOUNT\69EXP`；`place_channels` 两处**都给了 cowork**；`authorized_roots` 含 69EXP。
  两个档案：一个没暂停（应是 testcowork），一个暂停（应是 testerp），所选账套都是 70EXP\TEST。
- 11-08 20:39 岳父在 erpcowork 的直写记录显示：直写模式里说「录进 Express」，CC 做完开始前检查后**自己调推送**，不需要人点按钮。

## 推论
在 testcowork 里用 Sincere 票据跑「录进 Express」（S1、S7、S6），或任何能让 CC 选到 69EXP 公司的直写，都可能经测试机写进真实的 69SINCER 等账套。连「只看内容」（S2）也会让 CC 经测试机读 69EXP 的主档（读真账套）。
原方案写的「今晚不点推送 / 开始就不写账」**在直写模式下不成立**。

## 要主会话定（任选其一，或组合）
1. 测试机 CC 去掉 69EXP 位置（只留 70EXP 给 cowork），或压测期间暂停 testcowork 档案——改测试机配置要你批；
2. 压测改用 70EXP 的测试公司做买卖方，Sincere 票据只用于「超限 / 只看」且不选公司——事故形状（同税号多个 Big C）要先在 TEST 里建；
3. 直写格的说法改成「先识别给我看，不要录」——但就测不到用户真实的那句话。
这份配置不是本窗口改的（11-10 11:58 最后改动），69EXP 给 cowork 本身也可能让别的测试窗口误写真账套，建议一并查是谁、为什么加的。
