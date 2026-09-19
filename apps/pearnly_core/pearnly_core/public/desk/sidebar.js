/* Reuse native sidebar drag handles/Sortable; persist only personal order. */
frappe.pearnly.reorder_sidebar = () => {
	const sidebar = frappe.app.sidebar;
	const editor = sidebar.editor;
	if (editor.edit_mode) return;
	if (sidebar.sidebar_title !== frappe.boot.pearnly_sidebar) {
		sidebar.setup(frappe.boot.pearnly_sidebar);
	}
	const finish = () => {
		editor.sortable?.destroy();
		editor.stop();
		sidebar.wrapper.removeClass("pearnly-sidebar-order");
		sidebar.workspace_sidebar_items = frappe.boot.workspace_sidebar_item[frappe.boot.pearnly_sidebar.toLowerCase()].items;
		sidebar.make_sidebar();
	};
	// There is deliberately no path to the native add/edit/delete or shared save APIs.
	editor.show_new_dialog = () => {};
	editor.perform_action = () => {};
	editor.setup_editing_controls = () => {
		const save = sidebar.wrapper.find(".save-sidebar").text(__("Save"));
		const discard = sidebar.wrapper.find(".discard-button").text(__("Discard"));
		discard.off("click").on("click", finish);
		save.off("click").on("click", async () => {
			if (save.prop("disabled")) return;
			save.prop("disabled", true);
			try {
				const result = await frappe.pearnly.call(
					"pearnly_core.desk.navigation.save_sidebar_order",
					{ routes: editor.new_sidebar_items.map(item => item.link_to) }, "POST",
				);
				const data = frappe.boot.workspace_sidebar_item[frappe.boot.pearnly_sidebar.toLowerCase()];
				const items = new Map(data.items.map(item => [item.link_to, item]));
				data.items = result.routes.map(route => items.get(route));
				frappe.boot.pearnly_pages = result.routes;
				finish();
			} finally {
				save.prop("disabled", false);
			}
		});
	};
	sidebar.wrapper.addClass("pearnly-sidebar-order");
	editor.start();
};
