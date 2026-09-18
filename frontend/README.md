# Pearnly 前端

四个入口（Cowork / ERP / DMS / 控制面）**共用同一份前端**。技术选型见
[ADR-005](../docs/adr/0005-frontend-architecture.md)，色值来源见
[ADR-007](../docs/adr/0007-frontend-skin.md)。

## 构建

```bash
npm install
npm run build        # 类型检查 + 打包，产物写进 apps/pearnly_core/pearnly_core/public/pearnly-app/
npm run build -- --watch   # 开发时挂着，改完刷新页面即可
npm run typecheck
```

产物**不是**交给独立的 Node 服务，而是由站点的 web 提供（`/assets/pearnly_core/pearnly-app/`）。
所以不新增任何常驻进程，也不占服务器预算。

### 本机开发怎么打开

登录租户站点之后打开：

```
http://<站点域名>:<端口>/assets/pearnly_core/pearnly-app/index.html
```

例如 ERP 租户：`http://qerp124510.localhost:8102/assets/pearnly_core/pearnly-app/index.html#/purchase`

**刻意不做 dev server 代理**：接口靠会话 cookie 认证，代理到另一个源之后登录态与
CSRF 规则都和线上不是一回事，在那种环境里"验证通过"说明不了问题。

### 改前端不用重建镜像

`infrastructure/compose/01-bench.yml` 把产物目录挂进了 web 容器，
所以 `npm run build` 完刷新页面就见效。镜像里仍然会打进同一份产物
（构建时 COPY），生产不依赖这个挂载。

**镜像构建要先生成产物。** 产物不进版本控制（文件名带 hash，进 git 只有噪音），
所以 `docker build` 之前必须先跑一次：

```bash
cd frontend && npm ci && npm run build
cd .. && docker build -f infrastructure/images/frappe.Dockerfile -t pearnly/frappe-sample:16.34.1 .
```

本机开发不需要这一步 —— compose 的挂载直接读产物目录，改完构建刷新就行。
把前端构建接进镜像构建流水线属于部署规范（P8.05）的活，等做到那一步再落。

## 目录

```
design/pearnly-ui.css   设计令牌（整份搬自旧产品，逐字节，不手改）
src/lib/api.ts          调后端：白名单方法、错误解析、csrf
src/lib/shell.ts        启动数据：产品 / 买到的功能 / csrf / 语言
src/lib/nav.ts          各产品侧栏定义 + 按授权过滤
src/App.vue             产品壳：侧栏 + 顶栏 + 主区
src/pages/              页面（按产品分目录）
```

## 两条硬规则

1. **色值只从 `design/pearnly-ui.css` 的令牌取**，组件里不写死颜色字面量。
   要改先改旧仓源头再整份重搬（见 `design/README.md`）。
2. **菜单按后端返回的授权显示**（`pearnly_core.shell.describe` 的 `features`）。
   前端隐藏菜单**不是权限边界**，真正的拦截在后端。

## 验证

```bash
cd /Users/skin/pearnly && node scripts/sample-verify-frontend-slice.mjs
```

见 [docs/frontend/02-first-slice.md](../docs/frontend/02-first-slice.md)。
