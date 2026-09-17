<#--
  Pearnly 统一登录页 —— 外壳模板（身份服务 Keycloak 的服务端模板，不是独立网页）。

  形态完全沿用旧登录页（pearnly-app/static/landing/landing.js 里的 auth 区块）：
  左侧是**一整张背景图** auth-bg-desktop.png（猫、气泡、清单卡、引言的底都在图里），
  真实文字按当年调好的坐标叠在图上；右侧是一张不透明的磨砂卡，卡里全是真实控件，
  正好盖住图里画的那个登录面板（旧代码注释里写的 "Option B"）。

  为什么不重画：位置和观感当年是反复调过的，用 CSS 再画一遍既费时又会走样
  （用户 2026-09-18 明确纠偏）。所以样式整份复用 resources/css/pearnly-auth.css。

  这个文件只做三件事，其余一律照旧：
  1. 去掉"注册 / 第三方登录 / 条款"三块（用户要求去掉）；
  2. 语言切换改成服务端链接（走 Keycloak 的 locale 机制，点一下换整页）；
  3. 其余流程页（忘记密码、设新密码、出错页）都套这一层外壳，观感统一。
-->

<#-- 图标：从旧登录页 landing.js 的 icon() 里逐条抄过来的同一套路径，没有换图标。 -->
<#macro pnIco name>
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><#if name == "user"><path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/><#elseif name == "lock"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><#elseif name == "mail"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/><#elseif name == "eye"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="3"/><#elseif name == "shield"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-5"/><#elseif name == "cloud"><path d="M17.5 19H8a6 6 0 1 1 1.2-11.9A7 7 0 0 1 21 12a4 4 0 0 1-3.5 7Z"/><#elseif name == "chat"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><#elseif name == "phone"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.6a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.5-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.5 2.6.6a2 2 0 0 1 1.8 2Z"/></#if></svg>
</#macro>

<#--
  语言切换的短标签。

  不用 ${l.label}：Keycloak 给的是"语言自己的显示名"（中文（简体）、ไทย、English、日本語），
  四个摆一排会把卡片顶部撑满。旧设计稿上就是「中 / TH / EN / 日」四个短标签
  （旧代码 landing-i18n.js: labels = { zh:'中', th:'TH', en:'EN', ja:'日' }），
  完整名字放在 title 上，鼠标悬停和读屏仍拿得到。认不出的语言退回语言标签本身。
-->
<#function pnLangShort tag>
  <#if tag?starts_with("zh")><#return "中">
  <#elseif tag == "th"><#return "TH">
  <#elseif tag == "en"><#return "EN">
  <#elseif tag == "ja"><#return "日">
  <#else><#return tag?upper_case>
  </#if>
</#function>

<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html lang="${lang}"<#if realm.internationalizationEnabled> dir="${(locale.rtl)?then('rtl','ltr')}"</#if>>
<head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${msg("pearnlyPageTitle")}</title>
    <link rel="icon" href="${url.resourcesPath}/img/app-icon.png" />
    <link href="${url.resourcesPath}/css/pearnly-auth.css" rel="stylesheet" />
    <link href="${url.resourcesPath}/css/pearnly-theme.css" rel="stylesheet" />
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <#-- 缩放与交互：pearnly-stage.js 是旧页 landing-scale.js 的原样拷贝；
         pearnly-login.js 只补三件小事（见该文件头注释）。都用 defer，等 DOM 就绪。 -->
    <script src="${url.resourcesPath}/js/pearnly-stage.js" defer></script>
    <script src="${url.resourcesPath}/js/pearnly-login.js" defer></script>
</head>

<body class="${bodyClass}">
<div id="pearnly-auth-root">
  <div class="auth-stage">
    <main class="auth-shell" aria-label="Pearnly">

      <#-- ── 左：品牌与场景（底图在 CSS 里，这里只叠图里留白的真实文字） ── -->
      <section class="brand-panel" aria-label="Pearnly welcome">
        <img class="brand-logo" src="${url.resourcesPath}/img/logo.png" alt="Pearnly" />

        <div class="welcome-copy">
          <h1>${msg("pearnlyHello")}</h1>
          <p>${msg("pearnlyHelloSub")}</p>
        </div>

        <div class="work-bubble">
          <div class="mini-bars" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
          <#-- 文案里有 <br />，跟旧页一样当 HTML 用 -->
          <p>${msg("pearnlyBubble")?no_esc}</p>
        </div>

        <#-- 底图里已经把猫画好了，桌面端这层是隐藏的；手机端底图换成渐变，才用得上真图。 -->
        <div class="cat-wrap">
          <img class="cat-art" src="${url.resourcesPath}/img/cat-illustration.png" alt="" />
        </div>

        <div class="check-card" aria-label="${msg("pearnlyTaskListLabel")}">
          <div class="check-row"><span class="tick">✓</span><span>${msg("pearnlyTask1")}</span></div>
          <div class="check-row"><span class="tick">✓</span><span>${msg("pearnlyTask2")}</span></div>
          <div class="check-row"><span class="tick">✓</span><span>${msg("pearnlyTask3")}</span></div>
          <div class="check-row"><span class="tick">✓</span><span>${msg("pearnlyTask4")}</span></div>
        </div>

        <article class="quote-card">
          <header><span class="quote-icon"><@pnIco name="chat" /></span><span>${msg("pearnlyQuoteLabel")}</span></header>
          <blockquote>${msg("pearnlyQuote")?no_esc}</blockquote>
          <footer>${msg("pearnlyQuoteBy")}</footer>
        </article>
      </section>

      <#-- ── 右：登录卡（不透明磨砂卡，盖住底图里画的那个登录面板） ── -->
      <section class="auth-card" aria-label="${msg("pearnlyPageTitle")}">
        <button class="m-sheet-close" type="button" id="m-sheet-close" aria-label="${msg("pearnlyClose")}">&times;</button>

        <#--
          语言切换：旧页是 JS 按钮，这里必须做成服务端链接 —— 文案在服务端渲染，
          切语言要让 Keycloak 重新出整页（并记住偏好）。
          顺序按设计走「中 / TH / EN / 日」，不跟 Keycloak 返回的顺序。
        -->
        <#if realm.internationalizationEnabled && locale.supported?size gt 1>
          <nav class="language-switcher" aria-label="${msg("pearnlyLanguages")}"
               style="grid-template-columns: repeat(${locale.supported?size}, 38px)">
            <#assign pnOrder = ["zh-CN", "zh", "th", "en", "ja", "ja-JP"]>
            <#list pnOrder as want>
              <#list locale.supported as l>
                <#if l.languageTag == want>
                  <a href="${l.url}" title="${l.label}"
                     class="<#if l.languageTag == locale.current>active</#if>"
                     <#if l.languageTag == locale.current>aria-current="true"</#if>>${pnLangShort(l.languageTag)}</a>
                </#if>
              </#list>
            </#list>
            <#-- 设计四种之外的语种也别藏起来，排在后面 -->
            <#list locale.supported as l>
              <#if !pnOrder?seq_contains(l.languageTag)>
                <a href="${l.url}" title="${l.label}"
                   class="<#if l.languageTag == locale.current>active</#if>"
                   <#if l.languageTag == locale.current>aria-current="true"</#if>>${pnLangShort(l.languageTag)}</a>
              </#if>
            </#list>
          </nav>
        </#if>

        <div class="auth-heading">
          <#if auth?has_content && auth.showUsername() && !auth.showResetCredentials()>
            <h2>${auth.attemptedUsername}</h2>
            <p><a href="${url.loginRestartFlowUrl}" class="link-btn">${msg("restartLoginTooltip")}</a></p>
          <#else>
            <#nested "header">
            <#if displayRequiredFields>
              <p class="pn-required"><span class="pn-req">*</span> ${msg("requiredFields")}</p>
            </#if>
          </#if>
        </div>

        <#-- 全局提示（会话过期、退登等）。旧页这里是个空的 .auth-message 容器，形态一致。 -->
        <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
          <div class="auth-message ${message.type}" role="alert">
            <span>${kcSanitize(message.summary)?no_esc}</span>
          </div>
        <#else>
          <div class="auth-message" role="status" aria-live="polite"></div>
        </#if>

        <#nested "form">

        <#if auth?has_content && auth.showTryAnotherWayLink()>
          <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
            <input type="hidden" name="tryAnotherWay" value="on"/>
            <button type="submit" class="link-btn">${msg("doTryAnotherWay")}</button>
          </form>
        </#if>

        <#-- 第三方登录：本平台不要（用户要求去掉）。挂载点留着但不渲染内容，
             将来若真要接，由身份源配置决定，不用改模板。 -->
        <#nested "socialProviders">

        <#if displayInfo>
          <div class="pn-info"><#nested "info"></div>
        </#if>

        <#-- 帮助卡：旧设计里本来就在卡片底部，正好填住登录按钮之下的空档。
             联系方式和旧登录页完全一致，没有新造内容。 -->
        <aside class="support-card" aria-label="${msg("pearnlySupportTitle")}">
          <div class="support-head">
            <img src="${url.resourcesPath}/img/support-avatar.png" alt="" />
            <div>
              <strong>${msg("pearnlySupportTitle")}</strong>
              <span>${msg("pearnlySupportSub")}</span>
            </div>
          </div>
          <div class="contact-grid">
            <a class="contact-item" href="mailto:hello@pearnly.com"><span class="contact-dot email"><@pnIco name="mail" /></span><span>Email</span></a>
            <a class="contact-item" href="https://line.me/R/ti/p/@pearnly" target="_blank" rel="noopener"><span class="contact-dot line"><@pnIco name="chat" /></span><span>@pearnly</span></a>
            <a class="contact-item" href="tel:0868892228"><span class="contact-dot phone"><@pnIco name="phone" /></span><span>086-889-2228</span></a>
          </div>
        </aside>
      </section>
    </main>

    <#-- 手机端底部那条：CTA 拉起登录卡（旧页的行为，照旧） -->
    <div class="m-backdrop" id="m-backdrop"></div>
    <div class="m-dock">
      <button class="m-login-cta" type="button" id="m-login-cta">${msg("pearnlyLoginBtn")}</button>
      <nav class="m-contact" aria-label="${msg("pearnlySupportTitle")}">
        <a href="mailto:hello@pearnly.com"><@pnIco name="mail" /><span>Email</span></a>
        <a href="https://line.me/R/ti/p/@pearnly" target="_blank" rel="noopener"><@pnIco name="chat" /><span>@pearnly</span></a>
        <a href="tel:0868892228"><@pnIco name="phone" /><span>086-889-2228</span></a>
      </nav>
      <ul class="m-secure" aria-label="${msg("pearnlyTrustLabel")}">
        <li class="m-sec-item"><@pnIco name="shield" /><b>${msg("pearnlySecure1")}</b></li>
        <li class="m-sec-item"><@pnIco name="cloud" /><b>${msg("pearnlySecure2")}</b></li>
        <li class="m-sec-item"><@pnIco name="lock" /><b>${msg("pearnlySecure3")}</b></li>
      </ul>
    </div>

    <footer class="security-footer" aria-label="${msg("pearnlyTrustLabel")}">
      <div class="security-item"><span class="security-icon"><@pnIco name="shield" /></span><div><strong>${msg("pearnlySecure1")}</strong><span>${msg("pearnlySecure1Sub")}</span></div></div>
      <div class="security-item"><span class="security-icon"><@pnIco name="cloud" /></span><div><strong>${msg("pearnlySecure2")}</strong><span>${msg("pearnlySecure2Sub")}</span></div></div>
      <div class="security-item"><span class="security-icon"><@pnIco name="lock" /></span><div><strong>${msg("pearnlySecure3")}</strong><span>${msg("pearnlySecure3Sub")}</span></div></div>
    </footer>
    <div class="copyright">© 2026 PEARNLY Co., Ltd. All rights reserved.</div>
  </div>
</div>
</body>
</html>
</#macro>
