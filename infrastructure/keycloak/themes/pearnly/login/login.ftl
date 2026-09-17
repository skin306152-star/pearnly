<#--
  登录表单。类名和结构与旧登录页完全一致（.form-stack / .field / .checkline /
  .primary-btn …），所以样式不用改一行。

  和旧登录页的差别只有用户明确要求的三处：
  - 没有"注册"（realm 关了注册，这里也没有入口）；
  - 没有第三方登录（Google / LINE）；
  - 没有条款那一行。
  账号说明：身份服务原生支持"用户名或邮箱"，开通时两者都设成同一个邮箱地址，
  所以租户直接填邮箱即可。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') displayInfo=false; section>

    <#if section = "header">
        <h2 id="auth-title">${msg("pearnlyLoginTitle")}</h2>
        <p id="auth-subtitle">${msg("pearnlyLoginSub")}</p>

    <#elseif section = "form">
        <#-- id 用身份服务约定的 kc-form-login（它自己的脚本按这个 id 找表单），
             外观类名仍旧是旧登录页那套 .auth-form.active，两边都不冲突。 -->
        <form class="auth-form active" id="kc-form-login" action="${url.loginAction}" method="post" autocomplete="on">
          <div class="form-stack">
            <#if !usernameHidden??>
              <div class="form-row">
                <label for="username">${msg("pearnlyUsername")}</label>
                <div class="field">
                  <span class="field-icon"><@layout.pnIco name="user" /></span>
                  <input id="username" name="username" type="text"
                         value="${(login.username!'')}"
                         autocomplete="username" autofocus
                         placeholder="${msg("pearnlyUsernamePh")}"
                         aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>" />
                </div>
                <#if messagesPerField.existsError('username')>
                  <span class="pn-field-error">${kcSanitize(messagesPerField.getFirstError('username'))?no_esc}</span>
                </#if>
              </div>
            </#if>

            <div class="form-row">
              <label for="password">${msg("pearnlyPassword")}</label>
              <div class="field">
                <span class="field-icon"><@layout.pnIco name="lock" /></span>
                <input id="password" name="password" type="password"
                       autocomplete="current-password"
                       placeholder="${msg("pearnlyPasswordPh")}"
                       aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>" />
                <button class="toggle-password" type="button"
                        data-toggle-password="password"
                        aria-label="${msg("pearnlyShowPassword")}"><@layout.pnIco name="eye" /></button>
              </div>
              <#if messagesPerField.existsError('password')>
                <span class="pn-field-error">${kcSanitize(messagesPerField.getFirstError('password'))?no_esc}</span>
              <#-- 账号密码同错时只报一次（父主题的行为，保持一致） -->
              <#elseif !messagesPerField.existsError('username') && messagesPerField.existsError('username','password')>
                <span class="pn-field-error">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</span>
              </#if>
            </div>

            <div class="form-split">
              <#if realm.rememberMe && !usernameHidden??>
                <label class="checkline">
                  <input id="rememberMe" name="rememberMe" type="checkbox"
                         <#if login.rememberMe??>checked</#if> />
                  <span>${msg("pearnlyRemember")}</span>
                </label>
              <#else>
                <span></span>
              </#if>

              <#if realm.resetPasswordAllowed>
                <a class="link-btn" href="${url.loginResetCredentialsUrl}">${msg("pearnlyForgot")}</a>
              </#if>
            </div>

            <input type="hidden" id="id-hidden-input" name="credentialId"
                   <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if> />
            <button class="primary-btn" id="kc-login" name="login" type="submit">${msg("pearnlyLoginBtn")}</button>
          </div>
        </form>
    </#if>
</@layout.registrationLayout>
