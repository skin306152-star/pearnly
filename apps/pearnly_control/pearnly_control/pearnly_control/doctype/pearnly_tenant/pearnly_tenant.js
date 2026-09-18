// 租户表单上的「删除租户」：把这个租户**整个抹掉**。
//
// 为什么要有它：Desk 原生删除会被关联挡住（租户挂着发号单），而且就算删掉了，
// **站点、数据库、身份服务里的账号都还在** —— 那是"看不见了"，不是"清空了"。
// 用户 2026-09-19 定："删除就是清空所有，不备份，相当于格式化"。
//
// 真正干活的是宿主机上的开通执行器（控制面容器里没有 docker），所以这里的动作是
// **把拆除步骤排进队列**，然后让用户看进度（与"发号并开通"同一套路子）。
//
// ⚠️ 文案一律 `__()` + 英文 msgid（翻译在 pearnly_control/translations/*.csv），
//    不在这里写死任何语言。

function pcall(method, args) {
	return frappe
		.call({ method: method, type: "POST", args: args || {} })
		.then((r) => r.message);
}

function confirm_teardown(frm, targets) {
	const rows = [];
	rows.push(
		`<p>${__("This removes everything about this tenant. It cannot be undone.")}</p>`
	);
	const sections = [
		[
			__("Sites and databases"),
			(targets.sites || []).map((s) => frappe.utils.escape_html(s)),
		],
		[
			__("Sign-in accounts"),
			(targets.accounts || []).map((a) => frappe.utils.escape_html(a)),
		],
		[
			__("Control-plane records"),
			[
				`${__("Issues")}: ${(targets.issues || []).length}`,
				`${__("Deployments")}: ${(targets.deployments || []).length}`,
				Object.keys(targets.records || {})
					.map(
						(d) =>
							`${frappe.utils.escape_html(__(d))}: ${targets.records[d]}`
					)
					.join(", "),
			].filter(Boolean),
		],
	];
	sections.forEach(([title, items]) => {
		if (!items || !items.length) return;
		rows.push(
			`<p><b>${frappe.utils.escape_html(title)}</b><br>` +
				items.map((i) => "· " + i).join("<br>") +
				"</p>"
		);
	});

	frappe.confirm(rows.join(""), () => {
		frappe.dom.freeze(__("Queueing the deletion…"));
		pcall("pearnly_control.teardown.api_plan_teardown", { tenant: frm.doc.name })
			.then((state) => {
				frappe.dom.unfreeze();
				frappe.show_alert(
					{
						message: __("Deletion queued ({0} steps). The on-host runner will do the work.", [
							(state && state.counts && state.counts.total) || 0,
						]),
						indicator: "red",
					},
					6
				);
				frm.reload_doc();
			})
			.catch((e) => {
				frappe.dom.unfreeze();
				frappe.msgprint({
					title: __("Could not queue the deletion"),
					message: (e && e.message) || String(e),
					indicator: "red",
				});
			});
	});
}

frappe.ui.form.on("Pearnly Tenant", {
	refresh(frm) {
		if (frm.is_new()) return;
		const deleting = frm.doc.status === "deleting";
		frm.add_custom_button(deleting ? __("Retry deletion") : __("Delete tenant"), () => {
			pcall("pearnly_control.teardown.api_targets", { tenant: frm.doc.name })
				.then((targets) => {
					if (!targets || !targets.exists) {
						frappe.msgprint(__("This tenant no longer exists."));
						frm.reload_doc();
						return;
					}
					confirm_teardown(frm, targets);
				})
				.catch((e) => {
					frappe.msgprint({
						title: __("Could not read what would be deleted"),
						message: (e && e.message) || String(e),
						indicator: "red",
					});
				});
		});
	},
});
