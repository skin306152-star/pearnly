// 充值审核的两个动作。钱的动作只给两个按钮，不做别的花样。
//
// 「同意」要能改**实收金额**：转账少转、多转都真实存在，一切以银行到账为准，
// 不以租户自己填的申请金额为准（老产品就是这么做的）。

frappe.ui.form.on("Pearnly Topup Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "pending") return;

		frm.add_custom_button(__("Approve and credit"), () => approve(frm), null);
		frm.add_custom_button(__("Reject"), () => reject(frm), null);
	},
});

function approve(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Approve top-up"),
		fields: [
			{
				fieldtype: "Currency",
				fieldname: "actual_amount_thb",
				label: __("Received amount (as actually credited by the bank)"),
				default: frm.doc.requested_thb,
				reqd: 1,
			},
			{ fieldtype: "Small Text", fieldname: "note", label: __("Review Note") },
		],
		primary_action_label: __("Confirm credit"),
		primary_action(values) {
			frappe.dom.freeze(__("Crediting…"));
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
							message: __("Credited ฿{0}; {1} now has ฿{2}", [
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
						title: __("Could not credit"),
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
		title: __("Reject top-up"),
		fields: [
			{
				fieldtype: "Small Text",
				fieldname: "note",
				label: __("Reason (the tenant will see it; required)"),
				reqd: 1,
			},
		],
		primary_action_label: __("Confirm rejection"),
		primary_action(values) {
			frappe
				.call({
					method: "pearnly_control.billing_api.admin_reject",
					type: "POST",
					args: { request: frm.doc.name, note: values.note },
				})
				.then(() => {
					d.hide();
					frappe.show_alert({ message: __("Rejected"), indicator: "orange" }, 4);
					frm.reload_doc();
				})
				.catch((e) => {
					frappe.msgprint({
						title: __("Could not reject"),
						message: (e && e.message) || String(e),
						indicator: "red",
					});
				});
		},
	});
	d.show();
}
