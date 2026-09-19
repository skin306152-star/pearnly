<#ftl output_format="plainText">
<#if requiredActions?? && requiredActions?size == 1 && requiredActions[0] == "UPDATE_PASSWORD">
<#include "password-reset.ftl">
<#else>
<#assign requiredActionsText><#if requiredActions??><#list requiredActions as action>${msg("requiredAction." + action)}<#sep>, </#list></#if></#assign>
${msg("executeActionsBody", link, linkExpiration, realmName, requiredActionsText, linkExpirationFormatter(linkExpiration))}
</#if>
