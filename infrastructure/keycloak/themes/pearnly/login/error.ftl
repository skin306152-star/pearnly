<#--
  出错页（链接失效、流程中断等）。文案与跳转逻辑照旧，只把类名换成旧登录页那套。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        <h2 id="auth-title">${kcSanitize(msg("errorTitle"))?no_esc}</h2>

    <#elseif section = "form">
        <div id="kc-error-message">
          <div class="auth-message error" role="alert">
            <span>${kcSanitize(message.summary)?no_esc}</span>
          </div>
          <#if traceId??>
            <#-- 不用身份服务的标准键 traceIdSupportMessage：它只有英文，
                 其它语言是空的，中文/泰文/日文用户会看到一句英文。这里自建。 -->
            <p class="pn-info" id="traceId">${msg("pearnlyTraceId", traceId)}</p>
          </#if>
          <#if !skipLink??>
            <#if client?? && client.baseUrl?has_content>
              <p><a class="link-btn" id="backToApplication" href="${client.baseUrl}">${msg("backToApplication")}</a></p>
            </#if>
          </#if>
        </div>
    </#if>
</@layout.registrationLayout>
