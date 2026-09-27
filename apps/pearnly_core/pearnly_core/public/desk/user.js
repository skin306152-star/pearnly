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
function pearnlyProfileKeep(frm) {
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
    return keep;
}

/* 表单第一次画出来之前（`setup`：布局已建、还没按文档刷新）就把不留的字段 / 页签 / 分节标成隐藏。
 *
 * 原先只在 `refresh` 里隐藏：框架先把整张原生用户表单（十几个字段、6 个页签、右侧栏）画出来，
 * 再触发 `refresh`，点左下角时原生界面会先闪一下（2026-09-27 逐帧实测：约 1 秒，首次最明显）。
 * 这时文档还没挂到布局上，不能调 `toggle_display`（它会立刻刷新控件），只改字段定义本身：
 * 本表单用的这份、以及之后按单据复制出的那份都带上 hidden，第一次 `refresh_fields` 就是我们要的样子。
 */
function pearnlyProfileHideEarly(frm) {
    const keep = pearnlyProfileKeep(frm);
    const copies = frappe.meta.docfield_copy?.[frm.doctype]?.[frm.docname] || {};
    for (const field of frm.meta.fields) {
        if (!field.fieldname) continue;
        const hidden = keep.has(field.fieldname) ? 0 : 1;
        for (const df of [field, copies[field.fieldname], frm.fields_dict?.[field.fieldname]?.df]) {
            if (df) df.hidden = hidden;
        }
    }
}

function pearnlyProfileScope(frm) {
    const keep = pearnlyProfileKeep(frm);
    for (const field of frm.meta.fields) {
        if (field.fieldname) frm.toggle_display(field.fieldname, keep.has(field.fieldname));
    }
    // 原生 toggle_display 会顺手刷新**字段**，但不会刷新页签/分节/分列。
    // 这里补一次原生布局刷新，让"刚打开"和"点刷新"看到同一份内容。
    frm.refresh_fields();
}

frappe.ui.form.on("User", {
    setup(frm) {
        if (!frappe.boot.pearnly_product) return;
        // 右侧栏、仪表盘、菜单由 product.css 按这个类从第一帧起就不显示（原先在 refresh 里才隐藏）。
        frm.$wrapper.addClass("pearnly-account-profile");
        pearnlyProfileHideEarly(frm);
        // 原生「密码 → 重置密码」由我们的「修改密码」取代（refresh 里也会摘掉）。原生脚本在它自己的
        // refresh 里加这颗按钮、中间还有一次异步，所以会先露出来再被摘掉；这里直接不让它加上。
        const add = frm.add_custom_button.bind(frm);
        frm.add_custom_button = (label, click, group) =>
            label === __("Reset Password") && group === __("Password") ? null : add(label, click, group);
    },
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
        // 静音开关是真的（框架的 `frappe.utils.play_sound` 见到 `frappe.boot.user.mute_sounds`
        // 就直接不播；我们自己的提示音也都走它）。但那个值**只在整页加载时取一次** ——
        // 勾上并保存之后当前这一页照响，要刷新才静音，看着就像开关是假的（2026-09-23 实测）。
        // 这里在每次表单刷新（含保存之后那一次）回写一次，勾上立刻生效。
        frappe.boot.user.mute_sounds = frm.doc.mute_sounds ? 1 : 0;
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
