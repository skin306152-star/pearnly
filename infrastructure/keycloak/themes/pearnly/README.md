# Pearnly 统一登录主题（身份服务 Keycloak）

三个产品（Cowork / ERP / DMS）共用这一个登录口。外观**整份复用**旧登录页那套设计，
没有重画 —— 原因和来龙去脉见 `docs/p4/10-login-theme.md`。

## 目录

```
login/
├── theme.properties            主题声明（parent=base，语言四种，样式在模板里 <link>）
├── template.ftl                外壳：左侧场景（叠字）+ 右侧登录卡 + 页脚
├── login.ftl                   登录表单（账号密码 + 记住登录 + 忘记密码）
├── login-reset-password.ftl    忘记密码
├── login-update-password.ftl   设置新密码
├── error.ftl / info.ftl        出错页 / 提示页
├── login-page-expired.ftl      会话过期
├── messages/messages_{zh_CN,th,en,ja}.properties   四语文案
└── resources/
    ├── css/pearnly-auth.css    ← 生成物，不要手改（旧登录页样式整份）
    ├── css/pearnly-theme.css   本主题的少量增补
    ├── js/pearnly-stage.js     ← 旧页 landing-scale.js 原样拷贝
    ├── js/pearnly-login.js     密码眼睛 / 手机端抽屉 / 带错自动拉开
    └── img/                    auth-bg-desktop.webp / logo.png / cat-illustration.png / …
```

## 改完怎么生效

```bash
./build-login-css.sh      # 只有动了 login/resources/css/pearnly-auth.css 的来源才需要
./reload-login-theme.sh   # 必须跑：清 Keycloak 的 gzip 缓存 + 重启容器
```

**`reload-login-theme.sh` 不能省。** Keycloak 会把主题资源压成 gzip 缓存在容器里
（`/opt/keycloak/data/tmp/kc-gzip-cache/`），重启容器也不清；不跑它的话，
浏览器（发 `Accept-Encoding: gzip`）拿到的还是旧文件，而 `curl` 拿到的是新文件。

## 样式包的来源

`resources/css/pearnly-auth.css` 由 `build-login-css.sh` 从旧仓库
`pearnly-app/static/landing/` 拼出来，顺序照旧构建脚本 `scripts/build-home-css.mjs`
的 `LANDING_CSS`（层叠依赖这个顺序）。要改外观，**改旧仓库的源样式再重跑脚本**，
不要直接编辑生成物。

生成时会去掉 BOM：旧 `landing-auth.css` 开头有个 U+FEFF，拼到文件中间会让浏览器
把 `.auth-card{position:absolute}` 整条丢掉，登录卡会跑到页面左上角。

## 验证

```bash
cd /Users/skin/pearnly && node scripts/sample-verify-login-theme.mjs
```

31 项，覆盖入口、几何（与线上量出来的卡片位置比对）、底图、文案、语言切换、
密码错误提示、真实登录、忘记密码页、手机端。截图落在 `docs/p4/evidence/`。
