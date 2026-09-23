/* Account preferences remain native User controls, scoped to the current product user. */
const PEARNLY_PROFILE_FIELDS = new Set([
    "email", "first_name", "pearnly_company", "pearnly_domain",
    "language", "time_zone", "mute_sounds", "pearnly_change_password",
]);

/* 个人资料那一屏要留哪些字段 —— 连同它们**真实所在的页签/分节/分列**一起留。
 *
 * 为什么不能只写一份"可见字段名单"（2026-09-20 正式租户现场）：语言/时区落在某个
 * 分列里，把那个分列设成 hidden 之后，原生对**字段**的隐藏是立刻生效的、对分列不是；
 * 首次绘制看着正常，下一次原生 layout.refresh 才给整列挂上 hide-control ——
 * 于是同一页"刚打开还在、点刷新就没了"，用户每次打开看到的内容都不一样。
 */
function pearnlyProfileScope(frm) {
    const keep = new Set(PEARNLY_PROFILE_FIELDS);
    let tab = null;
    let section = null;
    let column = null;
    for (const field of frm.meta.fields) {
        if (field.fieldtype === "Tab Break") {
            tab = field;
            section = null;
            column = null;
        } else if (field.fieldtype === "Section Break") {
            section = field;
            column = null;
        } else if (field.fieldtype === "Column Break") {
            column = field;
        } else if (keep.has(field.fieldname)) {
            for (const ancestor of [tab, section, column]) {
                if (ancestor && ancestor.fieldname) keep.add(ancestor.fieldname);
            }
        }
    }
    for (const field of frm.meta.fields) {
        if (field.fieldname) frm.toggle_display(field.fieldname, keep.has(field.fieldname));
    }
    // 原生 toggle_display 会顺手刷新**字段**，但不会刷新页签/分节/分列。
    // 这里补一次原生布局刷新，让"刚打开"和"点刷新"看到同一份内容。
    frm.refresh_fields();
}

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
        pearnlyProfileScope(frm);
        // 用户名（`first_name`，见 account/layout.py）在 DocType 那一层就是只读的：
        // 它和邮箱都是开户时发的，用户自己改不了（用户 2026-09-23 口径）。这里只把
        // 邮箱再钉一遍（框架原生允许改自己的邮箱），以及保证"改密码"这条路在**两种
        // 站点上都走得通** —— 云站点走身份服务，本地站点点本站那个改密页。
        frm.set_df_property("email", "read_only", 1);
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
        if (!frappe.boot.pearnly_product) return;
        window.location.assign(frappe.boot.pearnly_native_account
            ? "/api/method/pearnly_core.identity.begin_password_change" : "/pearnly-password");
    },
});
