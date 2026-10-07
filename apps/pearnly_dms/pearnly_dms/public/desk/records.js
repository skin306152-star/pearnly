/* Scoped push history, using the installed native Frappe list renderer. */
(() => {
	const p = frappe.pearnly;
	p.records = async (page, t) => {
		let list, deleting = false, confirming = false;
		function updateAction(rows) {
			if (rows.length) {
				page.set_primary_action(__("Delete"), removeSelected);
				page.btn_primary.prop("disabled", deleting || confirming);
			} else page.clear_primary_action();
		}
		function removeSelected() {
			if (deleting || confirming) return;
			const names = [...new Set(list.get_checked_items().map(row => row.name))];
			if (!names.length) return;
			confirming = true;
			updateAction(list.get_checked_items());
			const reset = () => {
				confirming = deleting = false;
				updateAction(list.get_checked_items());
			};
			const dialog = frappe.confirm(p.escape(t("connect.records.confirmDelete", names.length)), async () => {
				if (deleting) return;
				deleting = true;
				try {
					const result = await p.call("pearnly_dms.records.manage.delete_records", {names}, "POST");
					if (result.deleted) p.success(t("connect.records.deletedCount", result.deleted), "delete");
					await list.reload();
				} catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally { reset(); }
			}, reset);
			// Dismissal through X/Escape must also restore the action. While the
			// confirmed request runs, keep it disabled to prevent duplicate submits.
			dialog.onhide = () => { if (!deleting) reset(); };
		}
		// 类型用 LINE 菜单上同一组词（客户资料 / 订车），操作员认得，也不另造词条。
		const menu = await p.texts("dms.card.TXT_MENU_ITEM");
		const kinds = {customer: menu("dms.card.TXT_MENU_ITEM1"), booking: menu("dms.card.TXT_MENU_ITEM2")};
		const statuses = ["pending", "retrying", "success", "failed", "skipped_dup", "manual"];
		const statusLabel = value => t(`connect.records.${value}`);
		const filters = {};
		for (const df of [
			{fieldtype: "Select", fieldname: "status", label: __("Status"),
				options: [{label: __("All"), value: ""}, ...statuses.map(value => ({label: statusLabel(value), value}))]},
			{fieldtype: "Date", fieldname: "from_date", label: __("From Date")},
			{fieldtype: "Date", fieldname: "to_date", label: __("To Date")},
		]) {
			filters[df.fieldname] = page.add_field({...df, change: () => list?.reload()});
		}
		list = p.list({page, doctype: "Pearnly Push Log", selectable: true,
			on_selection: updateAction,
			load: args => p.call("pearnly_dms.query.records.list_records", {
				...args, ...Object.fromEntries(Object.entries(filters).map(([key, field]) => [key, field.get_value()])),
			}),
			columns: [
				// Microseconds from the ledger are noise here; the old page showed the minute.
				{fieldname: "creation", label: t("connect.records.time"),
					format: value => frappe.datetime.str_to_user(String(value ?? "").split(".")[0]).slice(0, 16)},
				{fieldname: "actor_name", label: __("Operator")},
				{fieldname: "kind", label: __("Type"), format: value => kinds[value] || "—"},
				// 客户写入显示 DMS 客户编号（不是内部行号），订车显示订车单号；服务端已换好。
				{fieldname: "number", label: t("connect.records.businessNumber"), format: value => value || "—"},
				{fieldname: "error_code", label: t("connect.records.result"), format: (value, row) => value || (row.has_error_detail ? t("connect.records.contactSupport") : "—")},
				{fieldname: "status", label: __("Status"), format: statusLabel},
			],
		});
		await list.ready;
	};
})();
