<#ftl output_format="HTML" auto_esc=true>
<#if requiredActions?? && requiredActions?size == 1 && requiredActions[0] == "UPDATE_PASSWORD">
<#include "password-reset.ftl">
<#else>
<#outputformat "plainText"><#assign requiredActionsText><#if requiredActions??><#list requiredActions as action>${msg("requiredAction." + action)}<#sep>, </#list></#if></#assign></#outputformat>
<#import "template.ftl" as layout>
<@layout.emailLayout>${kcSanitize(msg("executeActionsBodyHtml", link, linkExpiration, realmName, requiredActionsText, linkExpirationFormatter(linkExpiration)))?no_esc}</@layout.emailLayout>
</#if>
