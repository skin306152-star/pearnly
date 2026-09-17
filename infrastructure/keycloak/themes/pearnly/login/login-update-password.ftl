<#--
  设置新密码页（从重置邮件里的链接进来）。同上：重写外壳、保留字段与提交语义。
  "在其他设备退出登录" 那个勾选沿用旧登录页的 .checkline 样式。

  文案用词说明：`passwordNew` / `passwordConfirm` / `logoutOtherSessions` 直接用
  身份服务的**标准键** —— 它们的四种语言都有准确译文，复用能保证术语跟平台其它页面
  一致，也少维护几份。页面标题和按钮不用标准键：标准键的泰语把"更新密码"译成
  "ปรับปรุงรหัสผ่าน"（语义是"改进密码"）、把"提交"译成"ส่ง"（意为"发送"），
  对这个页面都不准，所以自建（见 messages/*.properties 里的 pearnlyUpdate*）。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout
    displayMessage=!messagesPerField.existsError('password','password-confirm'); section>
    <#if section = "header">
        <h2 id="auth-title">${msg("pearnlyUpdateTitle")}</h2>
        <p id="auth-subtitle">${msg("pearnlyUpdateSub")}</p>

    <#elseif section = "form">
        <form class="auth-form active" id="kc-passwd-update-form"
              onsubmit="login.disabled = true; return true;" action="${url.loginAction}" method="post">
          <div class="form-stack">
            <div class="form-row">
              <label for="password-new">${msg("passwordNew")}</label>
              <div class="field">
                <span class="field-icon"><@layout.pnIco name="lock" /></span>
                <input type="password" id="password-new" name="password-new" dir="ltr"
                       autofocus autocomplete="new-password"
                       placeholder="${msg("pearnlyPasswordPh")}"
                       aria-invalid="<#if messagesPerField.existsError('password','password-confirm')>true</#if>" />
                <button class="toggle-password" type="button" data-toggle-password="password-new"
                        aria-label="${msg("pearnlyShowPassword")}"><@layout.pnIco name="eye" /></button>
              </div>
              <#if messagesPerField.existsError('password')>
                <span class="pn-field-error">${kcSanitize(messagesPerField.get('password'))?no_esc}</span>
              </#if>
            </div>

            <div class="form-row">
              <label for="password-confirm">${msg("passwordConfirm")}</label>
              <div class="field">
                <span class="field-icon"><@layout.pnIco name="lock" /></span>
                <input type="password" id="password-confirm" name="password-confirm" dir="ltr"
                       autocomplete="new-password"
                       placeholder="${msg("pearnlyPasswordPh")}"
                       aria-invalid="<#if messagesPerField.existsError('password-confirm')>true</#if>" />
                <button class="toggle-password" type="button" data-toggle-password="password-confirm"
                        aria-label="${msg("pearnlyShowPassword")}"><@layout.pnIco name="eye" /></button>
              </div>
              <#if messagesPerField.existsError('password-confirm')>
                <span class="pn-field-error">${kcSanitize(messagesPerField.get('password-confirm'))?no_esc}</span>
              </#if>
            </div>

            <div class="form-split">
              <label class="checkline">
                <input type="checkbox" id="logout-sessions" name="logout-sessions" value="on" checked />
                <span>${msg("logoutOtherSessions")}</span>
              </label>
              <span></span>
            </div>

            <#if isAppInitiatedAction??>
              <button class="primary-btn" name="login" type="submit">${msg("pearnlyUpdateSubmit")}</button>
              <button class="link-btn" type="submit" name="cancel-aia" value="true">${msg("doCancel")}</button>
            <#else>
              <button class="primary-btn" name="login" type="submit">${msg("pearnlyUpdateSubmit")}</button>
            </#if>
          </div>
        </form>
    </#if>
</@layout.registrationLayout>
