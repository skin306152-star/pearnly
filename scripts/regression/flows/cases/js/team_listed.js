// 重新打开团队页：上一步加的成员在列表里，邮箱列是补出来的 <登录名>.<站点前缀>@pearnly.com
let login = null;
try { login = localStorage.getItem("flows_team_login"); } catch (e) { login = null; }
const prefix = location.hostname.split(".")[0];
const row = login && await wait(() => $$(".page-container[data-page-route='pearnly-team'] .list-row, .page-container[data-page-route='pearnly-team'] .dt-row")
  .find(el => el.innerText.includes(login)), 30000);
const text = row ? row.innerText.replace(/\s+/g, " ") : "";
return {ok: !!row && text.includes(login + "." + prefix + "@pearnly.com"), login, text: text.slice(0, 200)};
