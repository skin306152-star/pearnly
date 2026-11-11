# 网页容器内存调整（C 上传线，11-11）

分支 `pearnly/web-container-memory`，基线 origin/main 59799c3ca。主会话 11-10 已批（改法见共享笔记「C线-网页容器内存建议-11-10」）。
随下一批部署；**本分支不动生产**，生产那一步由发布窗口按第 4 节照做。

## 1. 为什么改（11-10 晚一次只读 SSH：cgroup memory.stat / memory.events、docker stats、free）

| 容器 | 上限 | 匿名内存 | 撞上限次数（memory.events max） | 单进程常驻 |
|---|---|---|---|---|
| Cowork 网页（6 进程 × 4 线程） | 1536m | 1.51GB（99%） | 2684 | 150–404MB |
| ERP 网页（4 进程 × 8 线程） | 1536m | 1.39GB（91%），峰值 = 上限 | 2 | 233–442MB |
| ERP 识别（4 进程 × 16 线程） | 6656m | 198MB | — | — |

- 没被内核杀过（oom_kill 0），但撞上限时两个网页容器被换进 swap（整机还有 23.8GB 可用，swap 已用 5.2GB），换出去的页面再用时变慢。
- 原依据「单进程峰值 < 200 MiB」是 10-02 的，过时了。Cowork 网页没设 `--max-requests`，进程只涨不回收。
- 上传上限提到 40MB 后，一份大文件瞬时约再多 100–130MB（`docs/work/2026-11-10-upload-size-limits.md`）。

## 2. 改了什么

| 项 | 原来 | 现在 | 文件 |
|---|---|---|---|
| Cowork 网页每进程 | 256 MiB（6 进程 1536m） | 512 MiB（3072m） | `infrastructure/production/sizing.py` `WEB_PROCESS_MB` |
| Cowork 网页回收 | 无 | `--max-requests=2000 --max-requests-jitter=200`（同 ERP） | `infrastructure/production/compose.yml` cowork-web |
| ERP 网页每进程 | 384 MiB（4 进程 1536m） | 640 MiB（2560m） | `sizing.py` `WEB_PROCESS_MB` |
| ERP 识别每线程 | 80 MiB（6656m） | 40 MiB（4352m = ceil256((450 + 40 × 15) × 4)） | `sizing.py` `OCR_THREADS` |
| 守护 | 8 核 / 32095 上公式 = 旧现值 | = 新现值；另核生产机带 swap 的整机预算、Cowork 网页带回收参数 | `scripts/check-production-capacity.py` |
| 文档 | | 对照表、依据、整机预算余量 | `docs/development/production-capacity.md` |

整机预算（8 核 / 32095 MiB、swap 16383）：上限 min(32095 × 1.3, 32095 + 16383) = 41723；
上限之和 37440 → **37696**（+1536 +1024 −2304 = +256），加宿主保留 3209 = 40905，**余 818 MiB**。MariaDB（8g）和其他容器不动，不加钱。

ERP 识别降到 40 MiB 的前提：生产全部走 Claude、原件放甲骨文，ERP 只交原件引用、由甲骨文渲染（`recognition/claude.py` `read_all`
的 `originals`），本机线程只等回复。原件层不收、退回本机识别的少数文件仍有每进程 450 MiB 底线（`pool.local_slot` 每进程一份）。
进程回收线随之变成 300 + 40 × 15 = 900 MB（`execution/recycle.py`），仍低于每进程约 1088 MiB 的份额。
**以后切回本机识别引擎或原件留本机，要重新核这个数。**

## 3. 激活顺序与容量检查

`capacity.py` 对生产服务一律按渲染出的目标值算，不按正在跑的旧容器算；先激活 ERP 或先激活 Cowork 都是 40905 / 41723，放行。
`deploy-run.sh` 照旧逐个激活 ERP / Cowork / DMS（`--force-recreate`），三个容器在激活时换新，不用另外重启。

## 4. 发布窗口照做（部署这一批时）

生产 `/opt/pearnly/secrets/runtime.env` 把这几项写死了（compose 先读 sizing.env、后读 runtime.env，写死的以它为准），**只合代码不会生效**。
改成新值（不删行：沿用「生产现值写死在 runtime.env」的做法）。

### 4.1 部署前（在跑 `deploy.sh` 之前、同一次维护里；一次 SSH 做完）

```bash
F=/opt/pearnly/secrets/runtime.env
B=$F.bak-$(date -u +%Y%m%d-%H%M)-webmem
sudo cp -p "$F" "$B" && echo "备份 $B"
sudo grep -nE '^PEARNLY_(COWORK_WEB_MEM|ERP_WEB_MEM|ERP_OCR_MEM|ERP_OCR_THREAD_MB)=' "$F"   # 记下原值（预期 1536m / 1536m / 6656m / 80 或没有）
for kv in PEARNLY_COWORK_WEB_MEM=3072m PEARNLY_ERP_WEB_MEM=2560m PEARNLY_ERP_OCR_MEM=4352m PEARNLY_ERP_OCR_THREAD_MB=40; do
  k=${kv%%=*}
  if sudo grep -q "^$k=" "$F"; then sudo sed -i "s|^$k=.*|$kv|" "$F"; else echo "$kv" | sudo tee -a "$F" >/dev/null; fi
done
sudo grep -nE '^PEARNLY_(COWORK_WEB_MEM|ERP_WEB_MEM|ERP_OCR_MEM|ERP_OCR_THREAD_MB)=' "$F"   # 回读：四行各一条，都是新值
sudo stat -c '%a %U:%G' "$F" "$B"                                                       # 权限与原来相同（600）
```

- 只改这四行，不碰别的键（密钥、地址等）。
- 改完到激活之前，如果有别的东西重建了这几个容器，只是提前用上新上限，没有坏处。

### 4.2 部署

照常 `scripts/production/deploy.sh`（先 `--dry-run`）。激活前的容量检查按新值核整机预算；不过就停在那一步、生产不受影响，按 4.4 还原 runtime.env。

### 4.3 部署后回读（部署回读通过后，一次 SSH）

```bash
P=pearnly-production
sid() { docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=$1 | head -1; }
for s in cowork-web erp-web erp-ocr-worker; do docker inspect -f "$s {{.HostConfig.Memory}}" "$(sid $s)"; done
# 预期：cowork-web 3221225472、erp-web 2684354560、erp-ocr-worker 4563402752
docker inspect -f '{{json .Config.Cmd}}' "$(sid cowork-web)" | grep -o 'max-requests[^"]*'     # 预期 max-requests=2000、max-requests-jitter=200
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(sid erp-ocr-worker)" | grep '^PEARNLY_OCR_THREAD_MB='   # 预期 40
for s in cowork-web erp-web erp-ocr-worker; do
  d=$(find /sys/fs/cgroup -maxdepth 3 -type d -name "docker-$(docker inspect -f '{{.Id}}' "$(sid $s)").scope" | head -1)
  echo "$s max=$(awk '$1=="max"{print $2}' $d/memory.events) oom_kill=$(awk '$1=="oom_kill"{print $2}' $d/memory.events) anon_mib=$(( $(awk '$1=="anon"{print $2}' $d/memory.stat) / 1048576 ))"
done
free -m          # 记下 swap 已用，作对照
```

容器重建后 cgroup 计数从 0 开始，上面那组就是基数。**第二天（或压测写入轮之后）再读一次，同样一次 SSH，不循环**。通过的标准：

- cowork-web、erp-web 的 `max` 不再涨（或只有个位数），`anon` 明显低于新上限（Cowork < 2.5GB、ERP < 2GB）；
- erp-ocr-worker `oom_kill` 为 0，ERP 识别没有因内存失败的记录；
- `free -m` 的 swap 已用不比部署时多。

结果写进这一批的发布记录（`docs/work/<日期>-release-*.md`）。

### 4.4 回退

- **四项一起回退**：只把 ERP 识别改回 6656m 而网页保留新值，上限之和 40000 + 3209 = 43209，超过 41723，下次激活容量检查会拒绝。
- 步骤（维护时做；重建网页容器有几秒不可用，ERP 识别容器重建会中断正在跑的识别，由队列重试）：

```bash
F=/opt/pearnly/secrets/runtime.env
sudo cp -p "$F.bak-<4.1 那份的时间>-webmem" "$F"
cd /opt/pearnly/deploy/infrastructure/production
C=(docker compose --env-file /opt/pearnly/state/sizing.env --env-file "$F" -f compose.yml)
sudo "${C[@]}" --profile cowork up -d --no-deps cowork-web
sudo "${C[@]}" --profile erp up -d --no-deps erp-web erp-ocr-worker
```

- 回退的触发条件：erp-ocr-worker 出现 `oom_kill`、ERP 识别因内存失败，或整机可用内存长时间低于 8GB。
- Cowork 网页的 `--max-requests` 写在 compose 里，还原 runtime.env 不会去掉它；它只让进程按请求数换新、不改上限，不需要回退。真要去掉就部署上一个发布标记。

## 5. 验证（本分支）

- 先红后绿：`scripts/check-production-capacity.py` 先改成新现值，在旧公式上红（公式 ≠ 新现值）；改公式和 compose 后 33 项全过。
- 三种激活顺序（先 Cowork、先 ERP、两者都新）在生产机规格带 swap 下都放行：40905 / 41723。
- 本地闸门：见交给主会话的消息与 C 线交接。
- 没有改 app 代码，不跑候选检查与流程；部署回读、真实效果见第 4.3 节。

## 6. 限制

- 实现与离线检查在本分支；生产没动。上线后的效果只看第 4.3 节第二天那次回读，HTTP 200 不算。
- 40 MiB / 线程是按「本机只等回复」推的，ERP 识别还没在生产压测里量过 memory.peak；第二天回读的 erp-ocr-worker anon 就是这次的实测。

## 7. 上传上限 40MB 后的并发最坏情况（主会话 11-11 要求：3 租户 × 10 人同时发大文件）

一份 40MB 的文件在各处瞬时多占（生产镜像实测或按实测拆算）：

| 入口 | 在哪个容器 | 同时能处理几份 | 一份瞬时多占 | 30 份同时 | 新上限下 |
|---|---|---|---|---|---|
| LINE → ERP | erp-ocr-worker（4 进程 × 16 线程，不 fork） | 64 | 下载 +41MB（改 BytesIO 后；原来 +81）+ 流式存原件 +9MB ≈ 50MB | 约 1.5GB + 常驻约 0.2GB ≈ 1.7GB | 4352m 内；64 线程全占也约 3.4GB，仍在内。不改 BytesIO 时 64 × 约 90MB ≈ 5.8GB 会超 |
| LINE → Cowork | cowork-worker（short / default，1 个进程、每个任务 fork） | 1（排队） | 下载 +41 + 读页数约 +20 + 存原件 +9 ≈ 70MB | 内存不叠加，约 70MB | 768m 内。**代价是排队**：30 份依次处理，最后一份要等前面全部做完，同队列的其他短任务也跟着等 |
| CC 对话 / 网页上传（Cowork） | cowork-web（6 进程 × 4 线程） | 24 | 约 +104MB（11-10 实测代录上传，含传引擎 +75） | 24 份在处理时约 +2.5GB，加常驻约 1.5GB ≈ 4GB | **超过 3072m**：约 15 份同时就到上限，多出的进 swap（不是被杀），这段时间页面变慢 |
| CC 对话 / 网页上传（ERP） | erp-web（4 进程 × 8 线程） | 32 | 同上约 +104MB | 约 3.1GB + 常驻约 1.4GB | **超过 2560m**：约 11 份同时就到上限，同上进 swap |
| CC 桌面版文件夹取文件 | cowork-web / erp-web（对话工具在网页进程里） | 同上 | 整份读一次 40 + 传引擎约 75 ≈ 115MB | 与对话上传同量级 | 同上 |

结论：
- 后台两条（LINE）在新上限下最坏情况都放得下；ERP 能放下靠的是这次下载少一份拷贝。
- 网页进程是短板：30 个人在同一时刻各传一份 40MB 时会进 swap。日常单据 1–5MB，50 页扫描件 20–28MB，这种同时刻 30 份 40MB 很少见；真遇上是变慢，不丢件、不报错。
- 要彻底避免，可以在网页进程里限制「同时处理的大文件」份数（例如每进程同时最多 2 份超过 10MB 的，多的原地等几秒），这是新改动，没做，交主会话定要不要排。
