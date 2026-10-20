<#--
  提示页（例如"重置邮件已发出"、需要完成的动作）。逻辑照旧，类名换成旧登录页那套。

  例外：退出成功后的「您已被登出」（退出地址带了 client_id 但没带跳回地址时出现）。用户 10-20 定任何产品退出都直接跳回，
  不留这张卡片：能跳回（跳回地址或客户端首页）就输出不显示内容的过渡页立即跳走；身份服务自带的客户端（account 等）
  首页是它自己的账号页，不跳，照常显示。
-->
<#import "template.ftl" as layout>
<#assign pnBuiltIn = ["account", "account-console", "admin-cli", "broker", "realm-management", "security-admin-console"]>
<#assign pnBack = "">
<#if (message.type)! == "success" && (message.summary)! == msg("successLogout")>
  <#if pageRedirectUri?has_content>
    <#assign pnBack = pageRedirectUri>
  <#elseif (client.baseUrl)?has_content && !pnBuiltIn?seq_contains((client.clientId)!"")>
    <#assign pnBack = client.baseUrl>
  </#if>
</#if>
<#if pnBack?has_content>
<!DOCTYPE html>
<html lang="${lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow" />
    <meta http-equiv="refresh" content="0;url=${pnBack}" />
    <title>${properties.pearnlyBlankTitle!msg("pearnlyPageTitle")}</title>
    <link href="${url.resourcesPath}/${properties.pearnlyBlankCss}" rel="stylesheet" />
</head>
<body></body>
</html>
<#else>
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
</#if>
