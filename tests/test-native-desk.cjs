// Reproduce Desk's constructor-time app_ready event, before frappe.app assignment.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let ready;
const nativeLogout = () => 'native';
const frappe = {
  provide() {}, re_route: {}, pearnly: {}, app: {}, set_route(route){frappe.route = route;},
  ui: {ThemeSwitcher: class {}, get_current_theme:()=>"light", SidebarHeader: class {
    get_display_siblings(){return [{name:'toggle-theme'},{name:'toggle-full-width'},{name:'toggle-sidebar'}];}
    populate_dropdown_menu(){this.rendered_items = this.dropdown_items.slice();}
  }},
  boot: {pearnly_product:'dms', desk_settings:{search_bar:1,notifications:1,form_navigation_buttons:1}, navbar_settings:{settings_dropdown:[]},
    pearnly_pages:['pearnly-home','pearnly-connect'],
    pearnly_account_actions:[{name:'language',label:'Language',action:'language'}]},
  Application: class { constructor() { ready(); } logout() { return nativeLogout(); } },
};
const opened = [];
const context = {frappe, window:{open:(url)=>opened.push(url)}, document:{body:{classList:{add(){}}}},
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
// Native order asked for by the user, with the product's own sidebar reorder kept:
// home, website, divider, reorder-sidebar, display, reload, divider, logout.
assert.deepEqual(Array.from(header.dropdown_items,item=>item.is_divider?'divider':item.name),
  ['home','website','divider','reorder-sidebar','display','reload','divider','logout']);
assert.deepEqual(Array.from(header.dropdown_items.find(i=>i.name==='display').items,item=>item.name),
  ['toggle-theme','toggle-full-width','toggle-sidebar'],'Display keeps the native theme/full-width/sidebar actions');
const reorderSidebar = header.dropdown_items.find(i=>i.name==='reorder-sidebar');
assert.equal(typeof reorderSidebar.condition, 'function');
assert.equal(reorderSidebar.condition(), true, 'Sidebar reorder stays available while the product has several pages');
header.dropdown_items.find(item=>item.name==='home').onClick();
assert.equal(frappe.route,'/desk/pearnly-home','Home opens this product subscription home, not the control plane');
header.dropdown_items.find(item=>item.name==='website').onClick();
assert.deepEqual(opened,['https://pearnly.com']);
header.populate_dropdown_menu();
assert.deepEqual(Array.from(header.rendered_items,item=>item.name),
  ['home','website','reorder-sidebar','display','reload','logout'],
  'Dividers only render in the native context menu, never as an undefined row');
assert.equal(header.dropdown_items.filter(item=>item.is_divider).length,2,'Dividers stay in the dropdown contract');

assert.equal(frappe.boot.desk_settings.search_bar,0);
assert.equal(frappe.boot.desk_settings.notifications,0);
assert.equal(frappe.boot.desk_settings.form_navigation_buttons,0);
console.log('PASS product hides unsupported search/notifications/navigation; native order home/website/display/reload/logout');

assert.equal(frappe.re_route[""],"/desk/pearnly-home");

// User profile scoping: every visible field keeps the tab/section/column it really lives in.
let profileHandlers = {};
const toggled = {};
const properties = [];
const refreshed = [];
const profileFrappe = {
  boot: {pearnly_product:'dms', pearnly_native_account:true, pearnly_sidebar:'DMS'},
  session: {user:'operator@example.test'},
  set_route() {}, pearnly: {add_refresh() {}},
  app: {sidebar: {sidebar_title:'DMS', setup() {}}},
  ui: {form: {on(doctype, handlers) { assert.equal(doctype,'User'); profileHandlers = handlers; }}},
};
vm.runInNewContext(fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/user.js','utf8'),
  {frappe: profileFrappe, window: {location: {assign() {}}}, __: text => text});
const profileForm = {
  doc: {name:'operator@example.test', __onload:{pearnly_account:{company:'Acme Motors', domain:'https://testdms.pearnly.com'}}},
  meta: {fields: [
    {fieldtype:'Section Break', fieldname:'section_break_3'},
    {fieldtype:'Data', fieldname:'email'},
    {fieldtype:'Data', fieldname:'first_name'},
    {fieldtype:'Data', fieldname:'middle_name'},
    {fieldtype:'Data', fieldname:'last_name'},
    {fieldtype:'Column Break', fieldname:'column_break_11'},
    {fieldtype:'Data', fieldname:'username'},
    {fieldtype:'Link', fieldname:'language'},
    {fieldtype:'Autocomplete', fieldname:'time_zone'},
    {fieldtype:'Section Break', fieldname:'sb1'},
    {fieldtype:'Table', fieldname:'roles'},
    {fieldtype:'Tab Break', fieldname:'settings_tab'},
    {fieldtype:'Select', fieldname:'desk_theme'},
  ]},
  toggle_display(fieldname, show) { toggled[fieldname] = show; },
  set_df_property(fieldname, property, value) { properties.push([fieldname, property, value]); },
  refresh_field() {}, refresh_fields() { refreshed.push('refresh_fields'); },
  remove_custom_button() {},
  $wrapper: {addClass() {}, find: () => ({remove() {}})},
  page: {clear_menu() {}, sidebar: {hide() {}}, clear_icons() {}},
  dashboard: {hide() {}},
};
profileHandlers.refresh(profileForm);
assert.equal(profileForm.doc.pearnly_company,'Acme Motors');
assert.deepEqual(properties, [['email','read_only',1]]);
for (const name of ['section_break_3','column_break_11','email','first_name','language','time_zone','pearnly_change_password']) {
  assert.equal(toggled[name], true, `${name} must stay visible`);
}
for (const name of ['middle_name','last_name','username','roles','sb1','settings_tab','desk_theme']) {
  assert.equal(toggled[name], false, `${name} must stay hidden`);
}
assert.deepEqual(refreshed, ['refresh_fields'], 'Layout is refreshed so first paint matches a later form refresh');
console.log('PASS profile keeps visible fields with their real tab/section/column ancestors');
