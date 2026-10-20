<#--
  退出确认页。身份服务在退出地址没带 id_token_hint 时（旧会话、提示丢失）会先要用户再点一次「注销」。
  用户 2026-10-20 定：任何产品退出都不出现这一页，点退出直接退出并跳回。
  所以这里不显示任何内容：底色沿用登录页样式，页面一加载就提交身份服务给的确认表单（session_code 原样带回）；
  浏览器禁用脚本时留一个原生按钮可点。
-->
<!DOCTYPE html>
<html lang="${lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow" />
    <title>${msg("pearnlyPageTitle")}</title>
    <link rel="icon" href="${url.resourcesPath}/img/app-icon.png" />
    <link href="${url.resourcesPath}/css/pearnly-auth.css" rel="stylesheet" />
</head>
<body>
    <form id="kc-logout-confirm" action="${url.logoutConfirmAction}" method="POST">
        <input type="hidden" name="session_code" value="${logoutConfirm.code}" />
        <noscript><button type="submit" name="confirmLogout" value="${msg("doLogout")}">${msg("doLogout")}</button></noscript>
    </form>
    <script>document.getElementById("kc-logout-confirm").submit();</script>
</body>
</html>
