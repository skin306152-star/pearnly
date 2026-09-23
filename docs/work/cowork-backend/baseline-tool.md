# Cowork 后端迁移：旧线上来源身份检查

归属：本仓开发工具，不属于产品 App，也不增加产品 API、页面或权限。依据 [迁移基准](../../development/migration-baseline.md)。入口为 `scripts/cowork-migration-baseline.py`，仅使用 Python 标准库；离线测试为 `tests/test_cowork_migration_baseline.py`。

## 本次解决什么

脚本把 Cloud Run Web/Worker 的实际流量指向、revision、镜像 digest、固定 Git 部署账本和 GitHub 成功运行记录关联起来。revision 名中包含短 SHA 不算源码证据。首次、交付前必须分别实际采集；离线比较拒绝版本和配置投影漂移，不能把第一次文件改个时间作为最后核验。

来源映射采用以下可复核证据：

1. Cloud Run service 的 `status.traffic` 明确指向单个 100% revision，服务已 Ready 且 generation 收敛。
2. 对应 revision 已 Ready、属于该 service；`status.imageDigest` 与容器配置的不可变镜像一致。
3. 旧仓固定 commit 下 `docs/deployment/MIGRATION_STATUS.md` 的“正在服务的发布身份”把完整源码 SHA、digest、两个 revision 和部署运行链接对应起来。
4. GitHub 运行 ID、仓库、`head_sha`、完成且成功的状态与该账本一致；本地可解析完整源 commit 的 tree。

这是“固定 Git 部署账本 + 成功发布运行”的交叉核验，不是镜像签名或镜像内容的独立反编译证明。旧账本格式改变、来源不明、分流、其它 revision 带标签路由、组件不一致、缺字段、可变 secret 版本都会失败；不能为了通过而改证据或降低检查。

## 使用

已有文件默认离线校验，不访问云端：

```bash
python3 scripts/cowork-migration-baseline.py /tmp/cowork-baseline-first.json \
  --source-repo /Users/skin/Developer/Pearnly/pearnly-app
```

`--source-repo` 只执行本地 `git rev-parse`、`git show`，核对固定源码 tree 与固定账本片段；不 checkout、fetch、pull 或修改旧仓。省略该参数时，仅核验已保存证据的完整性与内部关系，不重复证明本地 Git 来源。

仅 `collect-readonly` 明确执行外部读取。示例绑定的是本轮已调查的发布记录；之后运行须先确认实际部署运行 ID，不能永久沿用：

```bash
python3 scripts/cowork-migration-baseline.py collect-readonly \
  --project pearnly --region asia-southeast1 \
  --services pearnly-web pearnly-worker \
  --source-repo /Users/skin/Developer/Pearnly/pearnly-app \
  --ledger-ref origin/master \
  --github-repo skin306152-star/pearnly-app \
  --run-id 35321281376 \
  --output /tmp/cowork-baseline-first.json
```

采集后按同样方式生成另一个交付前文件，然后比较：

```bash
python3 scripts/cowork-migration-baseline.py compare \
  /tmp/cowork-baseline-first.json /tmp/cowork-baseline-final.json
```

采集命令把 digest 校验和打印到终端，应将该值独立记入本任务记录。`verify --expected-sha256` 和 `compare --before-sha256 / --after-sha256` 可绑定这些已记录的值。文件采用排他创建和 `0444` 权限，不覆盖旧文件；内嵌 SHA-256 检查意外改动。拥有文件写权限的人能重新生成文件及校验和，因此这不是数字签名，也不是不可绕过的访问控制；独立保存的校验和与真实采集操作仍是必要证据。

所有失败返回非零；采集失败不产生可接受的基准。采集结束前再次读取服务身份，若采集过程中服务变化则拒绝。通过仅证明记录时间、所选组件范围内的来源身份；没有长期“始终与线上一致”的承诺。

## 数据与副作用

- 外部命令只允许此实现中固定的 `gcloud run services describe`、`gcloud run revisions describe` 和 `gh api` GET；不调用业务 API、OCR、消息、外部建单、部署或配置写入。
- Cloud Run 的嵌套数组字段投影会遗漏 `containers`。revision 使用完整 `describe --format=json` 在进程内接收配置，随后立即丢弃全部 `env.value`、命令参数和无关 metadata，只保存白名单。不能声称此方式完全没有接收明文环境配置；原始响应不落盘、不输出。
- 不调用 Secret Manager payload/access，不读取运行密钥内容；证据只留 secret 名称、固定数字版本和挂载/环境引用。`latest` 等可变版本拒绝。若来源把凭据写在明文环境变量，收集方式需改用服务端字段投影，不能把完整响应另存为原始证据。
- 部署账本只保留四条身份信息，并截断运行日志描述；不把后续历史业务记录、用户数据写入证据。
- 不接触 Site、数据库、容器、共享样板或来源工作树未提交内容；唯一写入是新证据文件。

## 通过不代表什么

这份工具只覆盖本轮 Cloud Run Web/Worker 的来源身份，不等于 Cowork 行为迁移完成，也未接入 CI 或分支保护。配置值不复制，固定 revision 和数字 secret 引用用来追踪变动；业务依赖的配置含义仍需逐项审查。

Cloudflare 路由、Cloud Tasks 消费者绑定、Scheduler 开关、WeKan、Windows 小助手、挂载对象内容、运行时补丁、数据库开关及权限数据需要对应工作包证据。记录了挂载路径不代表已校验挂载文件内容。尤其出现覆盖源码的挂载或热补丁时，按规范暂停该部分，不能只凭本工具通过开始移植。

首尾有变化时脚本退出失败，不自动接受新基线。负责人必须比较新旧线上源码及共享依赖，补迁受影响增量并重新验证；确认为无关改动也须记录具体依据。新平台业务样本、成功/拒绝/恢复、权限、幂等、队列和外部结果的对照验证仍独立进行。

## 本地验证

```bash
python3 tests/test_cowork_migration_baseline.py
python3 scripts/check-architecture.py
git diff --check
```

本工具作者只运行离线测试；未执行云端采集、部署或业务操作。父任务实际采集的时间、摘要和结果另记主工作包，不由本文件代签。
