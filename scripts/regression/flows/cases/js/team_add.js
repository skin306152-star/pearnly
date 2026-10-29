// 团队「添加用户」（f1f7b02f）：邮箱留空 → 补成 <登录名>.<站点前缀>@pearnly.com、发了勾的岗位、指到这家公司，
// 乱填的邮箱被拒；同一登录名不能再加。重新打开团队页列表里有这一行由下一步（team_listed.js）看。
// 用登录名登录不在这里测：站点关了账号密码登录（disable_user_pass_login），正式登录走 Keycloak，实验栈没有。
const T = "pearnly_erp_portal.team.api.";
const login = "flows" + Math.random().toString(36).slice(2, 8);
const prefix = location.hostname.split(".")[0];
const expected = login + "." + prefix + "@pearnly.com";
const password = "Lab-" + login + "-9";
const catalog = (await call(T + "api_list_members", {}, "GET")).roles || [];
const roles = catalog.filter(r => /stock|products/.test(r.name)).map(r => r.name).slice(0, 2);
const errorOf = async fn => { try { await fn(); return null; } catch (e) { return String((e && (e.message || e.exc)) || "error"); } };
const quiet = (method, args) => new Promise((resolve, reject) => frappe.call({method, args, type: "POST",
  error: reject, callback: r => resolve(r.message)}).fail(reject));
await call(T + "api_add_member", {username: login, email: "", password, roles, companies: ["${company}"]});
try { localStorage.setItem("flows_team_login", login); } catch (e) { /* 下一步取不到就判不通过 */ }
const members = (await call(T + "api_list_members", {}, "GET")).members || [];
const member = members.find(m => m.username === login);
$$(".modal.show .btn-modal-close, .msgprint-dialog .btn-modal-close").forEach(b => b.click());
const bad_email = await errorOf(() => quiet(T + "api_add_member", {username: login + "x", email: "not-an-email", password, roles}));
$$(".modal.show .btn-modal-close").forEach(b => b.click());
const dup = await errorOf(() => quiet(T + "api_add_member", {username: login, email: "", password, roles}));
$$(".modal.show .btn-modal-close").forEach(b => b.click());
const checks = {email: member && member.email, roles: member && member.roles, companies: member && member.companies,
  bad_email_rejected: !!bad_email, duplicate_rejected: !!dup};
const ok = !!member && member.email === expected && roles.length > 0 && roles.every(r => member.roles.includes(r))
  && (member.companies || []).includes("${company}") && checks.bad_email_rejected && checks.duplicate_rejected;
return {ok, expected, checks};
