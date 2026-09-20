/* Account preferences remain native User controls, scoped to the current product user. */
frappe.ui.form.on("User", {
    refresh(frm) {
        if (!frappe.boot.pearnly_product) return;
        if (frm.doc.name !== frappe.session.user) {
            frappe.set_route("pearnly-roster");
            return;
        }
        const account = frm.doc.__onload?.pearnly_account || {};
        frm.doc.pearnly_company = account.company || "";
        frm.doc.pearnly_domain = account.domain || "";
        frm.refresh_field("pearnly_company");
        frm.refresh_field("pearnly_domain");
        const visible = new Set([
            "user_details_tab", "section_break_3", "email", "first_name",
            "pearnly_company", "pearnly_domain", "column_break0", "language",
            "time_zone", "mute_sounds", "pearnly_change_password",
        ]);
        for (const field of frm.meta.fields) {
            frm.toggle_display(field.fieldname, visible.has(field.fieldname));
        }
        frm.set_df_property("email", "read_only", 1);
        frm.toggle_display("pearnly_change_password", Boolean(frappe.boot.pearnly_native_account));
        frm.$wrapper.addClass("pearnly-account-profile");
        frm.page.clear_menu();
        frm.page.sidebar.hide();
        frm.dashboard.hide();
        frm.page.clear_icons();
        frappe.pearnly.add_refresh(frm.page, () => frm.reload_doc());
        frm.remove_custom_button(__("Reset Password"), __("Password"));
        frm.$wrapper.find(".pearnly-password-action").remove();
        if (frappe.app.sidebar.sidebar_title !== frappe.boot.pearnly_sidebar) {
            frappe.app.sidebar.setup(frappe.boot.pearnly_sidebar);
        }
    },
    pearnly_change_password() {
        if (frappe.boot.pearnly_product && frappe.boot.pearnly_native_account) {
            window.location.assign("/api/method/pearnly_core.identity.begin_password_change");
        }
    },
});
