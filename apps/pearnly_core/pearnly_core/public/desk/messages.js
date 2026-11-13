// 整份翻译表按语言 + 指纹单独取、浏览器缓存一年（服务端见 `pearnly_core/desk/boot_messages.py`）。
// 必须是本 App 最先加载的脚本：页面解析阶段同步插入，界面启动（DOMContentLoaded 之后）前词表已就位。
(function () {
	var m = window.frappe && frappe.boot && frappe.boot.pearnly_messages;
	if (!m || !m.lang || !m.v) return;
	var src = "/api/method/pearnly_core.desk.boot_messages.messages?lang=" + encodeURIComponent(m.lang) + "&v=" + encodeURIComponent(m.v);
	document.write('<script src="' + src + '"><\/script>');
})();
