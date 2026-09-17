<#--
  忘记密码页。原生模板用的是 PatternFly 的类名，我们没引那套样式，直接搬过来会变成
  裸控件（2026-09-18 实测过），所以按旧登录页的类名重写一层外壳，字段和按钮不变。
  文案取自旧登录页的忘记密码弹窗（forgotTitle / forgotText）：
  旧页是弹窗，这里是整页，说的是一回事。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayInfo=false displayMessage=!messagesPerField.existsError('username'); section>
    <#if section = "header">
        <h2 id="auth-title">${msg("pearnlyResetTitle")}</h2>
        <p id="auth-subtitle">${msg("pearnlyResetSub")}</p>

    <#elseif section = "form">
        <form class="auth-form active" id="kc-reset-password-form" action="${url.loginAction}" method="post">
          <div class="form-stack">
            <div class="form-row">
              <label for="username">
                <#if !realm.loginWithEmailAllowed>${msg("username")}
                <#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}
                <#else>${msg("email")}</#if>
              </label>
              <div class="field">
                <span class="field-icon"><@layout.pnIco name="user" /></span>
                <input type="text" id="username" name="username" autofocus dir="ltr"
                       value="${(auth.attemptedUsername!'')}"
                       placeholder="${msg("pearnlyUsernamePh")}"
                       aria-invalid="<#if messagesPerField.existsError('username')>true</#if>" />
              </div>
              <#if messagesPerField.existsError('username')>
                <span class="pn-field-error">${kcSanitize(messagesPerField.get('username'))?no_esc}</span>
              </#if>
            </div>

            <div class="form-split">
              <span></span>
              <a class="link-btn" href="${url.loginUrl}">${msg("backToLogin")}</a>
            </div>

            <button class="primary-btn" type="submit">${msg("pearnlyResetSubmit")}</button>
          </div>
        </form>
    </#if>
</@layout.registrationLayout>
