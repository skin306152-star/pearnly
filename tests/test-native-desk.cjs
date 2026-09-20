// Reproduce Desk's constructor-time app_ready event, before frappe.app assignment.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let ready;
const nativeLogout = () => 'native';
const frappe = {
  provide() {}, re_route: {}, pearnly: {}, app: {}, ui: {ThemeSwitcher: class {}, SidebarHeader: class {get_display_siblings(){return [];}}, get_current_theme:()=>"light"},
  boot: {pearnly_product:'dms', desk_settings:{search_bar:1,notifications:1,form_navigation_buttons:1}, navbar_settings:{settings_dropdown:[]},
    pearnly_account_actions:[{name:'language',label:'Language',action:'language'}]},
  Application: class { constructor() { ready(); } logout() { return nativeLogout(); } },
};
const context = {frappe, document:{body:{classList:{add(){}}}},
  $: () => ({on(event, handler) { assert.equal(event,'app_ready'); ready=handler; }}),
  __: text=>text};
vm.runInNewContext(fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/product.js','utf8'),context);
frappe.app = new frappe.Application();
assert.match(frappe.app.logout.toString(), /pearnly_core.identity.sign_out/);
assert.equal(frappe.boot.navbar_settings.settings_dropdown.length,0,'Account language is only in native User profile');
console.log('PASS native constructor-time logout extension and no duplicate account menu');
const header = new frappe.ui.SidebarHeader();
header.dropdown_items = [{name:'desktop'},{name:'website'},{name:'help'},{name:'display'},{name:'logout'}];
header.add_navbar_items();
assert.deepEqual(Array.from(header.dropdown_items,item=>item.name),['reorder-sidebar','reload','logout']);

assert.equal(frappe.boot.desk_settings.search_bar,0);
assert.equal(frappe.boot.desk_settings.notifications,0);
assert.equal(frappe.boot.desk_settings.form_navigation_buttons,0);
console.log('PASS product hides unsupported display/search/notifications/navigation; reorder/reload/logout retained');

assert.equal(frappe.re_route[""],"/desk/pearnly-home");
