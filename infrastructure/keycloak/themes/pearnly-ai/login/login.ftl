<#-- 生成文件：改设计请改 scripts/build-ai-entry.py 后重新生成，不要直接改这里。 -->
<#-- 第二步：入口已把账号带过来（login_hint），这里只显示账号、输密码；「更换」就地改成可编辑的账号框。 -->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password'); section>
<#if section = "form">
  <form id="kc-form-login" action="${url.loginAction}" method="post" autocomplete="on" class="step">
    <#assign who = (login.username!'')>
    <#if who?has_content && !messagesPerField.existsError('username')>
      <div class="who" id="who"><span>${who}</span><button type="button" id="who-change">${msg("pearnlyAiChange")}</button></div>
      <#-- 密码保存在本页域名下，浏览器要在同一个表单里认出账号：type=hidden 会被密码管理器忽略，所以用不显示的只读文本框。 -->
      <input type="text" id="username" name="username" value="${who}" autocomplete="username" readonly style="display:none" />
    <#else>
      <input class="input" id="username" name="username" type="text" value="${who}" autocomplete="username" placeholder="${msg("pearnlyAiLogin")}" style="margin-bottom:10px" <#if !who?has_content>autofocus</#if> />
    </#if>
    <div class="field">
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="${msg("pearnlyPasswordPh")}" <#if who?has_content>autofocus</#if>
             aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>" />
      <button class="toggle-password" type="button" data-toggle-password="password" aria-label="${msg("pearnlyShowPassword")}"><@layout.pnIco name="eye" /></button>
    </div>
    <#if messagesPerField.existsError('username','password')>
      <span class="pn-field-error">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</span>
    </#if>
    <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if> />
    <div class="form-split remember">
      <#if realm.rememberMe><label class="checkline"><input id="rememberMe" name="rememberMe" type="checkbox" <#if login.rememberMe?? || !(message?? && message.type == "error")>checked</#if> /> ${msg("pearnlyAiRemember")}</label><#else><span></span></#if>
      <#if realm.resetPasswordAllowed>
        <span class="forgot"><a class="link-btn" href="${url.loginResetCredentialsUrl?keep_before("?")}?client_id=${client.clientId?url("UTF-8")}&amp;kc_locale=${locale.currentLanguageTag?url("UTF-8")}">${msg("pearnlyForgot")}</a></span>
      </#if>
    </div>
    <button class="btn" id="kc-login" name="login" type="submit">${msg("pearnlyLoginBtn")}</button>
  </form>
</#if>
</@layout.registrationLayout>
