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
		// 嵌套那一步在 `prepare()` 里（`make_sidebar()` 自己不做）：少了它，分组标题会把
		// 子项画一遍、后面顶层再画一遍 —— 保存/放弃之后每组的子项都会出现两次（实测）。
		sidebar.prepare();
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
					// 提交的是**界面上排出来的顺序，铺平成路由**：编辑器的顶层项里有分组
					// 标题（Section Break，没有 `link_to`），子项挂在 `nested_items` 里。
					// 直接拿顶层项去提交会带上 `null`、也会把子项漏掉 —— 服务端只会回
					// "值无效"（这是改之前就有的毛病，2026-09-23 顺手修掉）。
					const routes = [];
					const collect = items => (items || []).forEach(item => {
						if (item.link_to) routes.push(item.link_to);
						collect(item.nested_items);
					});
					collect(editor.new_sidebar_items);
					const result = await frappe.pearnly.call(
						"pearnly_core.desk.navigation.save_sidebar_order",
						{ routes }, "POST",
					);
					// `routes` 是侧栏的新顺序；能打开的页面另给一份（含走左下角菜单的那些），
					// 页面路由的准入判据用它，别因为排序少了页面。
					frappe.boot.pearnly_pages = result.pages || result.routes;
					// 分组标题是产品声明的、不是这条顺序画的，所以这里用原生那条路把侧栏
					// 画回去（原先自己把 `data.items` 换成一份平铺清单，分组会当场消失）。
					finish();
				} finally {
					save.prop("disabled", false);
				}
			});
	};
	sidebar.wrapper.addClass("pearnly-sidebar-order");
	editor.start();
};
