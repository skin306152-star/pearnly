#!/usr/bin/env bash
# 常驻的开通执行器：让控制面表单上那个「发号并开通」按钮真的能建站。
#
# 为什么必须是常驻进程：建站要在别的容器里跑 bench，而控制面容器里**没有 docker**
# （P1.05：部署代理独立、运行进程不持 root）。所以控制面只能"记下要做什么"，
# 真正干活的是宿主机上这个进程 —— 它每几秒问一次"有没有待办"，有就去做。
#
# 为什么用 launchd 而不是 `nohup &`：nohup 挡得住挂断信号，挡不住"父进程被杀"和"重开机"。
# 实测 `nohup … &` 起来的进程会随启动它的终端会话一起消失（我这边就是这么没的）。
# launchd 是 macOS 原生的常驻方式：崩了自动拉起、开机自动运行，
# 而且有 `launchctl` 可以查**真实**状态，不是靠一个 pid 文件猜。
#
# 用法:
#   scripts/provision-runner.sh install     # 装成常驻服务并启动（一次就够）
#   scripts/provision-runner.sh start / stop / restart
#   scripts/provision-runner.sh status      # 看 launchd 认不认、有没有在跑
#   scripts/provision-runner.sh log         # 跟日志
#   scripts/provision-runner.sh foreground  # 前台跑（调试用，Ctrl-C 退出）
#   scripts/provision-runner.sh uninstall
#
# 日志: ~/.config/pearnly/provision-runner.log
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="${PEARNLY_RUN_DIR:-$HOME/.config/pearnly}"
LOG="$RUN_DIR/provision-runner.log"
LABEL="com.pearnly.provision-runner"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WATCH="$HERE/scripts/sample-provision.sh"

mkdir -p "$RUN_DIR"

write_plist() {
	mkdir -p "$(dirname "$PLIST")"
	cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$WATCH</string>
    <string>watch</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <!-- docker / curl / python3 都在 PATH 上；launchd 默认 PATH 很短，必须显式给 -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- 真挂了别死循环重启：最快 10 秒一次 -->
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST_EOF
}

loaded() {
	launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1
}

case "${1:-status}" in
  install)
    write_plist
    if loaded; then
      launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
    fi
    launchctl bootstrap "gui/$UID" "$PLIST"
    sleep 2
    if loaded; then
      echo "[已装并启动] $LABEL"
      echo "  日志 $LOG"
    else
      echo "[失败] launchd 没接住，看日志：$LOG" >&2
      tail -20 "$LOG" 2>/dev/null >&2 || true
      exit 1
    fi
    ;;

  start)
    if [ ! -f "$PLIST" ]; then
      echo "还没装过，先跑：$0 install" >&2
      exit 1
    fi
    if ! loaded; then
      launchctl bootstrap "gui/$UID" "$PLIST"
    fi
    launchctl kickstart "gui/$UID/$LABEL"
    echo "[已启动]"
    ;;

  stop)
    if loaded; then
      # 要真的停就得 bootout：kickstart -k 是重启，不是停
      launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
      echo "[已停]"
    else
      echo "[没在跑]"
    fi
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  status)
    if loaded; then
      pid="$(launchctl print "gui/$UID/$LABEL" 2>/dev/null | awk '/pid = /{print $3; exit}')"
      echo "[在跑] $LABEL pid=${pid:-未给出}，日志 $LOG"
    else
      echo "[没在跑]（$0 install 装成常驻，或 $0 foreground 前台跑）"
      exit 1
    fi
    ;;

  log) tail -f "$LOG" ;;

  foreground) exec "$WATCH" watch ;;

  uninstall)
    if loaded; then launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true; fi
    rm -f "$PLIST"
    echo "[已卸载]"
    ;;

  *)
    echo "用法: $0 install|start|stop|restart|status|log|foreground|uninstall" >&2
    exit 2
    ;;
esac
