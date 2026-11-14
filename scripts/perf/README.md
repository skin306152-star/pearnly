# 页面性能体检（11-14）

量「页面可用用时 + 首屏接口服务端毫秒 + 查询条数」，拦逐行查询（N+1）。全部只在任务自有的回归实验室里跑（`PEARNLY_REGRESSION_LAB=1`），
线上只读量法见 [任务记录](../../docs/work/2026-11-14-page-performance.md)。

| 文件 | 作用 |
| --- | --- |
| `seed_big_company.py` | 造一家大账套（客户 10,511、物料 1,157、251 行盘点，另一家公司 2 万物料撑出线上表规模），可重复跑 |
| `measure.py` | 以某人身份调一个读接口：用时、查询条数、重复最多的语句形状、最慢几条（每次回滚） |
| `trace_server.py` + `lab_tracer.sh` | 替代实验栈网页进程，按请求记用时与查询条数（请求头 `X-Perf-Page` 归页） |
| `pages.py` + `pages.mjs` | 无头 Chrome 按产品地图逐页打开（先预热一遍），出表；`--budget budgets.json` 超标退出 1 |
| `budgets.json` | 上限：页面 2 秒、首屏接口 300 毫秒，另按接口给查询条数上限 |
| `equal_check.py` | 改取数方式时，旧模块与新模块返回值逐字段比（行为不变的证据） |

静态闸：`scripts/check-loop-queries.py`（循环里查库，基线 `scripts/loop-queries-baseline.json` 只许减少）。

```bash
# 起自己的实验栈（端口别撞别的窗口），建站、造数
export REGRESSION_LAB_PORT=18871 REGRESSION_LAB_PORT_COWORK=18872 REGRESSION_LAB_PORT_DMS=18873 PEARNLY_REGRESSION_SRC=$PWD
docker compose -p pearnly-perf-lab -f infrastructure/compose/regression-lab.yml up -d --wait
docker compose -p pearnly-perf-lab -f infrastructure/compose/regression-lab.yml exec -T bench env/bin/python /workspace/scripts/regression/lab_init.py erp.regression.localhost cowork.regression.localhost dms.regression.localhost
docker exec -e PEARNLY_REGRESSION_LAB=1 -w /home/frappe/frappe-bench/sites pearnly-perf-lab-bench-1 ../env/bin/python /workspace/scripts/perf/seed_big_company.py erp.regression.localhost
docker exec pearnly-perf-lab-bench-1 bash /workspace/scripts/perf/lab_tracer.sh   # 网页进程换成按请求记查询条数的
python3 scripts/perf/pages.py --product erp --user admin@erp.regression --company <大账套公司> --page /desk/pearnly-stock-count/<盘点> --out /tmp/perf-erp.jsonl --budget scripts/perf/budgets.json
```
