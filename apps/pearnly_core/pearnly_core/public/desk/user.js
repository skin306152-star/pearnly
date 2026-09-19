/* Native User form: one language entry, identity-owned passwords. */
frappe.ui.form.on("User", {
	refresh(frm) {
		if (!frappe.boot.pearnly_product) return;
		if (frm.doc.name !== frappe.session.user) {
			frappe.set_route("pearnly-roster");
			return;
		}
		// Framework settings without a product workflow must not be presented as usable.
		frm.toggle_display(
			[
				"roles_permissions_tab",
				"connections_tab",
				"short_bio",
				"sessions_tab",
				"banner_image",
				"unsubscribed",
				"sb_allow_modules",
				"sb2",
				"sb3",
				"document_follow_notifications_section",
				"email_settings",
				"third_party_authentication",
				"api_access",
				"workspace_section",
				"app_section",
				"navigation_settings_section",
				"list_settings_section",
				"form_settings_section",
				"change_password",
				"send_welcome_email",
				"home_settings",
				"code_editor_type",
				"username",
			],
			false,
		);
		if (frm.doc.name === frappe.session.user) {
			// Native layout recomputes tab visibility from its children after refresh.
			// Hide the inapplicable fields as well, so it cannot resurrect these tabs.
			let excludedTab = false;
			for (const field of frm.meta.fields) {
				if (field.fieldtype === "Tab Break") {
					excludedTab = ["short_bio", "sessions_tab"].includes(field.fieldname);
				}
				if (excludedTab) frm.toggle_display(field.fieldname, false);
			}
			frm.$wrapper.addClass("pearnly-account-profile");
			frm.page.clear_menu();
			// Email/rename/duplicate/new-user are document-management actions, not account preferences.
			frm.page.sidebar.hide();
			frm.dashboard.hide();
			frm.page.clear_icons();
			frappe.pearnly.add_refresh(frm.page, () => frm.reload_doc());
		}
		frm.remove_custom_button(__("Reset Password"), __("Password"));
		frm.remove_custom_button(__("account.changePassword"));
		frm.$wrapper.find(".pearnly-password-action").remove();
		if (frm.doc.name === frappe.session.user && frappe.boot.pearnly_native_account) {
			const settings = frm.layout.tabs.find(tab => tab.df.fieldname === "settings_tab");
			if (settings) {
				const item = $('<li class="nav-item pearnly-password-action"></li>');
				$("<button type=\"button\" class=\"nav-link\"></button>")
					.text(__("account.changePassword"))
					.on("click", () => {
						window.location.assign("/api/method/pearnly_core.identity.begin_password_change");
					})
					.appendTo(item);
				item.insertAfter(settings.tab_link);
			}
		}
		// User's native field and after_save reload handle language; no duplicate dialog.
		// Keep the product navigation while reading one's own profile.
		if (
			frm.doc.name === frappe.session.user &&
			frappe.app.sidebar.sidebar_title !== frappe.boot.pearnly_sidebar
		) {
			frappe.app.sidebar.setup(frappe.boot.pearnly_sidebar);
		}
	},
});
