#!/bin/bash
# 回归实验室网页进程换成按请求记查询条数的 trace_server.py（8000/8001/8002，同原来的三个口）；重跑会先停掉上一轮的。
# 只在任务自有的实验栈里用：docker exec <栈>-bench-1 bash /workspace/scripts/perf/lab_tracer.sh
# 实验栈重启（docker compose restart）就回到原来的网页进程。
set -u
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ "$pid" = 1 ] && continue
  cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
  case "$cmd" in
    "../env/bin/python -m pearnly_core.web.wsgi"*|*"/workspace/scripts/perf/trace_server.py"*) kill "$pid" ;;
  esac
done
sleep 2
cd /home/frappe/frappe-bench/sites || exit 1
for port in 8000 8001 8002; do
  PEARNLY_REGRESSION_LAB=1 PYTHONDONTWRITEBYTECODE=1 nohup ../env/bin/python /workspace/scripts/perf/trace_server.py "$port" > "/tmp/trace-$port.log" 2>&1 &
done
sleep 8
grep -h "Running on" /tmp/trace-800[0-2].log
