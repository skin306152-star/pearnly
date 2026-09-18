// 充值审核的两个动作。钱的动作只给两个按钮，不做别的花样。
//
// 「同意」要能改**实收金额**：转账少转、多转都真实存在，一切以银行到账为准，
// 不以租户自己填的申请金额为准（老产品就是这么做的）。

frappe.ui.form.on("Pearnly Topup Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "pending") return;

		frm.add_custom_button(__("同意并到账"), () => approve(frm), null);
		frm.add_custom_button(__("拒绝"), () => reject(frm), null);
	},
});

function approve(frm) {
	const d = new frappe.ui.Dialog({
		title: __("同意充值"),
		fields: [
			{
				fieldtype: "Currency",
				fieldname: "actual_amount_thb",
				label: __("实收金额（以银行到账为准）"),
				default: frm.doc.requested_thb,
				reqd: 1,
			},
			{ fieldtype: "Small Text", fieldname: "note", label: __("审核备注") },
		],
		primary_action_label: __("确认到账"),
		primary_action(values) {
			frappe.dom.freeze(__("入账中…"));
			frappe
				.call({
					method: "pearnly_control.billing_api.admin_approve",
					type: "POST",
					args: {
						request: frm.doc.name,
						actual_amount_thb: values.actual_amount_thb,
						note: values.note || "",
					},
				})
				.then((r) => {
					frappe.dom.unfreeze();
					d.hide();
					const m = r.message || {};
					frappe.show_alert(
						{
							message: __("已到账 ฿{0}，{1} 现在余额 ฿{2}", [
								m.approved_thb,
								m.tenant,
								m.new_balance,
							]),
							indicator: "green",
						},
						6
					);
					frm.reload_doc();
				})
				.catch((e) => {
					frappe.dom.unfreeze();
					frappe.msgprint({
						title: __("入账失败"),
						message: (e && e.message) || String(e),
						indicator: "red",
					});
				});
		},
	});
	d.show();
}

function reject(frm) {
	const d = new frappe.ui.Dialog({
		title: __("拒绝充值"),
		fields: [
			{
				fieldtype: "Small Text",
				fieldname: "note",
				label: __("原因（租户那边看得到，必填）"),
				reqd: 1,
			},
		],
		primary_action_label: __("确认拒绝"),
		primary_action(values) {
			frappe
				.call({
					method: "pearnly_control.billing_api.admin_reject",
					type: "POST",
					args: { request: frm.doc.name, note: values.note },
				})
				.then(() => {
					d.hide();
					frappe.show_alert({ message: __("已拒绝"), indicator: "orange" }, 4);
					frm.reload_doc();
				})
				.catch((e) => {
					frappe.msgprint({
						title: __("拒绝失败"),
						message: (e && e.message) || String(e),
						indicator: "red",
					});
				});
		},
	});
	d.show();
}
