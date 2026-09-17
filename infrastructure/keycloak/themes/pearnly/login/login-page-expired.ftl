<#--
  会话过期页。逻辑照旧，类名换成旧登录页那套。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout; section>
    <#if section = "header">
        <h2 id="auth-title">${msg("pageExpiredTitle")}</h2>

    <#elseif section = "form">
        <p id="instruction1" class="pn-info">
          ${msg("pageExpiredMsg1")} <a class="link-btn" id="loginRestartLink" href="${url.loginRestartFlowUrl}">${msg("doClickHere")}</a> .<br/>
          ${msg("pageExpiredMsg2")} <a class="link-btn" id="loginContinueLink" href="${url.loginAction}">${msg("doClickHere")}</a> .
        </p>
    </#if>
</@layout.registrationLayout>
