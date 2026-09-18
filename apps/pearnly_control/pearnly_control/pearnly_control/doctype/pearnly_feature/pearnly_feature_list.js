// 功能目录列表：`feature_label` 存的是**英文 msgid**（见 pearnly_control/features.py
// 开头那段说明），各语言的显示名在翻译包里。
//
// 为什么列表要单独做一次翻译：`Data` 字段的值框架**不会**自动过 `__()`
// （`frappe/public/js/frappe/form/formatters.js` 里只有 Link / Select /
// Autocomplete 这几类会翻）。所以同一串英文在"功能目录"列表里会原样显示英文，
// 而在开通单的功能表里（Link 字段）又是中文 —— 两处不一致。
// 用 listview 自带的 `formatters` 补上这一次翻译，是框架给的标准口子
// （`frappe/public/js/frappe/list/list_view.js:1036` 会调它）。
frappe.listview_settings["Pearnly Feature"] = {
	formatters: {
		feature_label: (value) => __(value),
		// 角色名同理：库里存英文（Frappe 惯例，框架自己的 System Manager 也是英文），
		// 显示时翻。不加这一行，同一页里"功能名称"是中文、"对应角色"是英文。
		role_name: (value) => __(value),
	},
};
