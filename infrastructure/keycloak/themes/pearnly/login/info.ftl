<#--
  提示页（例如"重置邮件已发出"、需要完成的动作）。逻辑照旧，类名换成旧登录页那套。
-->
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        <#if messageHeader??>
            <h2 id="auth-title">${kcSanitize(msg("${messageHeader}"))?no_esc}</h2>
        <#else>
            <h2 id="auth-title">${message.summary}</h2>
        </#if>

    <#elseif section = "form">
        <div id="kc-info-message">
          <p class="pn-info">${message.summary}<#if requiredActions??><#list requiredActions>: <b><#items as reqActionItem>${kcSanitize(msg("requiredAction.${reqActionItem}"))?no_esc}<#sep>, </#items></b></#list><#else></#if></p>
          <#if !skipLink??>
            <#if pageRedirectUri?has_content>
              <p><a class="link-btn" href="${pageRedirectUri}">${msg("backToApplication")}</a></p>
            <#elseif actionUri?has_content>
              <p><a class="link-btn" href="${actionUri}">${msg("proceedWithAction")}</a></p>
            <#elseif (client.baseUrl)?has_content>
              <p><a class="link-btn" href="${client.baseUrl}">${msg("backToApplication")}</a></p>
            </#if>
          </#if>
        </div>
    </#if>
</@layout.registrationLayout>
