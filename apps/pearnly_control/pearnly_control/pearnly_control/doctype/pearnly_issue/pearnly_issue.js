// 发号单前端只做一件事：填了域名前缀就即时把完整域名显示出来。
// （真值仍以服务端 validate 计算为准，见 pearnly_control/domains.py）
//
// 功能模版那套接线（按产品过滤候选、套用常规项按钮）已经拆掉了 ——
// 现在选了产品就开通该产品的全部功能（用户 2026-09-18 定），没有可勾的东西。
//
// 另外接了两件事，都是原生能力的组合：
//   1. 「执行开通」：把这张单排进控制面的待办（幂等，重复点不会重复建站）；
//      真正建站的是宿主机上常驻的 `scripts/provision-runner.sh` ——
//      控制面容器里没有 docker，建站这一步只能在外面做。
//   2. 状态灯 + 进度弹窗：轮着问控制面"做到哪了"，做完自动刷新。

const BASE = (frappe.boot && frappe.boot.pearnly_base_domain) || "pearnly.com";
const base_for = (frm) => (frappe.boot.pearnly_target_domains || {})[frm.doc.target] || BASE;

// 只做展示用：打开表单时把已有前缀的完整域名补出来，免得"访问域名"一直空着。
// 真值仍由服务端 validate 计算（同一条规则，见 pearnly_control/domains.py）。
function preview_domains(frm) {
	let changed = false;
	(frm.doc.apps || []).forEach((row) => {
		const prefix = (row.domain_prefix || "").trim().toLowerCase();
		const want = prefix ? `${prefix}.${base_for(frm)}` : "";
		if (row.domain !== want) {
			row.domain = want;
			changed = true;
		}
	});
	if (changed) frm.refresh_field("apps");
}

frappe.ui.form.on("Pearnly Issue App", {
	domain_prefix(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const prefix = (row.domain_prefix || "").trim().toLowerCase();
		row.domain = prefix ? `${prefix}.${base_for(frm)}` : "";
		frm.refresh_field("apps");
	},
});

// ── 开通 ──────────────────────────────────────────────────────────────────

// 步骤名 → 人话。执行器认的是下面这些动作（见 pearnly_control/provisioning.py）。
const STEP_LABEL = {
	new_site: __("Create the site"),
	install_core: __("Install core apps"),
	install_product: __("Install the product app"),
	set_oidc: __("Set up unified sign-in"),
	set_billing: __("Connect cloud billing"),
	set_ocr: __("Set up OCR"),
	publish_site: __("Enable HTTPS access"),
	restart_web: __("Restart services"),
	finalize_site: __("Finalize the site"),
	set_line: __("Set up the LINE channel"),
	bind_user: __("Create the tenant account"),
	set_roles: __("Grant feature roles"),
	set_workspace: __("Build the workspace"),
	mark_active: __("Mark provisioning done"),
};

const STEP_COLOR = {
	done: "var(--green-600, #15803d)",
	skipped: "var(--gray-600, #6b7280)",
	running: "var(--orange-600, #c2410c)",
	failed: "var(--red-600, #b91c1c)",
	pending: "var(--gray-700, #4b5563)",
};

const STEP_TEXT = {
	done: __("Done"),
	skipped: __("Skipped"),
	running: __("Running"),
	failed: __("Failed"),
	pending: __("Pending"),
};

function pcall(method, args) {
	return frappe
		.call({ method: method, type: "POST", args: args || {} })
		.then((r) => r.message);
}

function state_of(frm) {
	return pcall("pearnly_control.provisioning.api_state_for_issue", { issue: frm.doc.name });
}

// 灯的颜色与话术。没有请求 = 还没开通；有请求就看它的状态。
function status_label(m) {
	if (!m) return [__("Not provisioned"), "blue"];
	const c = m.counts || {};
	if (m.status === "done") return [__("Enabled — {0} steps", [c.total || 0]), "green"];
	if (m.status === "failed") return [__("Provisioning failed"), "red"];
	if (m.status === "partial") return [__("Partly done {0}/{1}", [c.done || 0, c.total || 0]), "orange"];
	return [__("Provisioning {0}/{1}", [c.done || 0, c.total || 0]), "orange"];
}

function steps_html(m) {
	if (!m) return '<div class="text-muted">' + __("No provisioning request yet; click “Run provisioning” to start") + "</div>";
	return (m.steps || [])
		.map(
			(s) =>
				`<div style="display:flex;gap:10px;padding:3px 0;align-items:baseline">
           <span style="width:52px;flex:none;color:${STEP_COLOR[s.status] || "inherit"};font-weight:600">${
					STEP_TEXT[s.status] || s.status
				}</span>
           <span>${frappe.utils.escape_html(STEP_LABEL[s.step_key.split(":").pop()] || s.step_key)}</span>
         </div>`
		)
		.join("");
}

let poller = null;

function stop_polling() {
	if (poller) {
		clearInterval(poller);
		poller = null;
	}
}

function open_progress(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Provisioning Progress"),
		size: "small",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Close"),
		primary_action() {
			d.hide();
		},
	});
	d.show();

	let last_status = null;
	const tick = () =>
		state_of(frm)
			.then((m) => {
				d.fields_dict.body.$wrapper.html(steps_html(m));
				const [label, color] = status_label(m);
				frm.page.set_indicator(__(label), color);
				// 跑完（成或败）就停轮询，并刷新表单让灯与部署状态跟上
				const settled = m && ["done", "failed", "partial"].includes(m.status);
				if (m && last_status !== m.status && settled) {
					stop_polling();
					frm.reload_doc();
				}
				last_status = m ? m.status : null;
			})
			.catch(() => stop_polling());

	tick();
	stop_polling();
	poller = setInterval(tick, 2000);
	d.$wrapper.on("hidden.bs.modal", stop_polling);
}

function start_provision(frm) {
	// api_plan 是幂等的（幂等键就是单号）：重复点不会重复建站，只会把新步骤补进来。
	frappe.dom.freeze(__("Queueing provisioning…"));
	pcall("pearnly_control.provisioning.api_plan", { issue: frm.doc.name })
		.then(() => {
			frappe.dom.unfreeze();
			frappe.show_alert({ message: __("Queued for provisioning; building the site now"), indicator: "green" }, 4);
			open_progress(frm);
		})
		.catch((e) => {
			frappe.dom.unfreeze();
			frappe.msgprint({
				title: __("Could not queue provisioning"),
				message: (e && e.message) || String(e),
				indicator: "red",
			});
		});
}

function render_provision(frm) {
	if (frm.is_new() || frm.doc.status !== "issued") {
		// 还没发号（新单也算）：这一步是 Desk 上原来缺的那一环 ——
		// 保存只是存了条记录，没有任何东西会把账号建出来。
		// 所以给一个「发号并开通」，它自己会先保存再发号。新单不碰状态灯，
		// 那个位置得留给 "尚未保存"。
		if (!frm.is_new()) frm.page.set_indicator(__("Not issued"), "gray");
		frm.add_custom_button(__("Issue and provision"), () => issue_and_provision(frm), null);
		return;
	}
	state_of(frm)
		.then((m) => {
			const [label, color] = status_label(m);
			frm.page.set_indicator(__(label), color);
			const running = m && ["pending", "running"].includes(m.status);
			if (running) {
				frm.add_custom_button(__("Provisioning Progress"), () => open_progress(frm));
			} else {
				// 不抢主按钮：表单还是脏的时候，主按钮得留给"保存"
				frm.add_custom_button(
					m ? __("View progress") : __("Run provisioning"),
					() => (m ? open_progress(frm) : start_provision(frm)),
					null
				);
			}
		})
		.catch(() => {});
}

function issue_and_provision(frm) {
	// 先存：发号读的是库里的单子，不存就发的是旧值
	frappe.dom.freeze(__("Issuing…"));
	// ⚠️ **表单没改动时不能等 `frm.save()`**：Frappe 那条路只弹一句"未做任何修改"，
	// 压根不调回调 —— 而 `frm.save()` 是 `new Promise((resolve) => …)`，
	// 回调不调就**永远不落地**，链子会卡死在 freeze 上：界面一直"正在发号…"，
	// 后台什么也没发生（今天就是这么卡住的）。所以按 `is_dirty()` 分流。
	const save = () => (frm.is_dirty() ? frm.save() : Promise.resolve());
	save()
		.then(() => pcall("pearnly_control.issues.api_issue_from_doc", { issue: frm.doc.name }))
		.then((res) => {
			frappe.dom.unfreeze();
			frappe.show_alert(
				{
					message: __("Issued: {0} / tenant {1}", [res.issue, res.tenant]),
					indicator: "green",
				},
				5
			);
			// 发号完了紧接着排开通，一步接一步
			return frm.reload_doc().then(() => start_provision(frm));
		})
		.catch((e) => {
			frappe.dom.unfreeze();
			frappe.msgprint({
				title: __("Issue failed"),
				message: (e && e.message) || String(e),
				indicator: "red",
			});
		});
}

frappe.ui.form.on("Pearnly Issue", {
	setup(frm) {
		frm.set_query("target", () => ({
			query: "pearnly_control.onboarding.targets.search",
		}));
	},
	refresh(frm) {
		preview_domains(frm);
		render_provision(frm);
	},
});
