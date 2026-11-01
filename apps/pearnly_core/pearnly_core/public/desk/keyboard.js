// 手机键盘不盖住正在填的那一格（多产品共用：ERP / Cowork / DMS 的 Desk 页面与弹窗、网站页、LINE 独立页，2026-11-01；
// 控制面站点不装 pearnly_core，挂不上，管理后台在电脑上用）。
// 键盘弹出后浏览器只缩「可见区」（`window.visualViewport`）：iOS 不缩布局，安卓 Chrome 108 起默认也不缩；
// Frappe 弹窗（Bootstrap `.modal`）按整屏固定高度，最后几格滚不出来，贴顶 / 贴底的条（导航、页头、操作条）还会再盖一层。
// 这里统一处理，页面不再各写一份。只在触屏设备上加载生效；只在「文字输入格获得焦点、且可见区比同宽度下见过的最高时
// 矮 120px 以上（键盘真弹起）」期间动作，电脑、没有键盘、点勾选框 / 按钮 / 下拉时一行都不执行：
// 1. viewport 加 `interactive-widget=resizes-content`：安卓 Chrome / WebView 键盘弹出时整页变矮（iOS 不认，靠下面两条）；
//    Desk 的 viewport 写在框架 `desk.html` 里，这里运行时补上；LINE 独立页直接写在自己的 meta 里。
// 2. 键盘弹起期间 `<html>` 带 `pearnly-keyboard-open`，`--pearnly-visible-top / --pearnly-visible-height` 是可见区，
//    弹窗外框按它摆（`product.css`），弹窗在可见区里照官方方式自己滚；键盘收起即去掉，弹窗回到原样。
// 3. 聚焦那一刻、键盘弹起那一下（可见区变化）把这一格摆到「可见区减去贴顶 / 贴底条」的中间（带建议列表 / 日历弹层的
//    Link、日期格等摆到上部三分之一，下面留给弹层）：滚它所在的滚动容器，容器滚到头还差的再滚整页；在固定弹层里时不滚整页。不监听页面滚动、不锁滚动，人自己滚动不拉回来。
// 接入（`scripts/check-keyboard-coverage.py` 拦漏接）：Desk 由 `hooks.app_include_js`、网站页（`templates/web.html`）由
// `hooks.web_include_js` 加载；LINE 手机盘点页等独立页用 Jinja 的 `versioned_asset` 挂同一份；LINE 票据编辑器（Vue，
// `frontend/src/main.ts`）把这一份打进自己的包。CC 对话页（`frontend/assistant`）有自己的键盘处理，不挂。同一页挂两次只生效一次。
(() => {
	if (window.pearnlyKeyboard) return;
	window.pearnlyKeyboard = true;
	const view = window.visualViewport;
	if (!view || !window.matchMedia || !window.matchMedia("(pointer: coarse)").matches) return;
	const meta = document.querySelector('meta[name="viewport"]');
	if (meta && !/interactive-widget/.test(meta.content)) meta.content += ", interactive-widget=resizes-content";
	const root = document.documentElement;
	const MARGIN = 8;  // 摆好后离可见区上下边至少留这么多
	const KEYBOARD = 120;  // 比同宽度下见过的最高可见区矮这么多才算键盘弹起（地址栏收放只有几十像素）
	const NO_KEYBOARD = new Set(["button", "checkbox", "color", "date", "datetime-local", "file", "hidden", "image", "month",
		"radio", "range", "reset", "submit", "time", "week"]);
	let field = null;
	let timer = 0;
	let open = false;
	// 键盘只会让可见区变矮：同一宽度下见过的最高就是没键盘时的高度（安卓整页变矮时布局高度也跟着缩，不能拿布局比）；转屏换宽度重新记
	let tall = view.height;
	let wide = view.width;

	const typing = (el) => {
		if (!el || !el.tagName) return false;
		if (el.isContentEditable) return true;
		const tag = el.tagName.toLowerCase();
		if (tag === "textarea") return !el.readOnly && !el.disabled;
		return tag === "input" && !el.readOnly && !el.disabled && !NO_KEYBOARD.has((el.type || "text").toLowerCase());
	};
	// 聚焦后在下面弹出列表 / 日历的格：Link（awesomplete）、组合框、datalist、Frappe 日期 / 时间控件（日历弹层）
	const POPUP_TYPES = new Set(["Date", "Datetime", "Time"]);
	const suggests = (el) => !!(el.closest && el.closest(".awesomplete")) || el.getAttribute("role") === "combobox"
		|| !!el.getAttribute("aria-autocomplete") || !!el.getAttribute("list") || POPUP_TYPES.has(el.getAttribute("data-fieldtype"));
	const style = (el) => window.getComputedStyle(el);
	// 盖在可见区上 / 下边的贴顶 / 贴底条：命中点往上找 fixed / sticky 的祖先；包着这一格的（它所在的弹窗）不算
	const cover = (el) => {
		for (let node = el; node && node !== document.body && node !== root; node = node.parentElement) {
			if (node.contains(field)) return null;
			const position = style(node).position;
			if (position === "fixed" || position === "sticky") return node;
		}
		return null;
	};
	const visible = () => {
		let top = view.offsetTop;
		let bottom = view.offsetTop + view.height;
		const edges = {top, bottom};
		for (const share of [0.2, 0.5, 0.8]) {
			const x = view.offsetLeft + view.width * share;
			const below = cover(document.elementFromPoint(x, edges.bottom - 2));
			if (below) bottom = Math.min(bottom, below.getBoundingClientRect().top);
			const above = cover(document.elementFromPoint(x, edges.top + 2));
			if (above) top = Math.max(top, above.getBoundingClientRect().bottom);
		}
		return {top, bottom};
	};
	const scrolls = (node) => /(auto|scroll)/.test(style(node).overflowY) && node.scrollHeight > node.clientHeight;
	const inFixed = (node) => {
		for (; node && node !== document.body; node = node.parentElement) if (style(node).position === "fixed") return true;
		return false;
	};
	// 摆一次：要挪就挪、返回挪没挪
	const shift = () => {
		const {top, bottom} = visible();
		const rect = field.getBoundingClientRect();
		if (!rect.height) return false;
		let delta;
		if (suggests(field)) {
			// 带建议列表 / 日历弹层的格（Link、日期等）：弹层往下展开，格子摆到可见区上部三分之一以内，下面留给弹层
			if (rect.top >= top + MARGIN && rect.bottom <= top + (bottom - top) / 3) return false;
			delta = rect.top - top - MARGIN;
		} else {
			if (rect.top >= top + MARGIN && rect.bottom <= bottom - MARGIN) return false;
			// 高过可见区的（多行文本）顶端对齐，其余摆中间
			delta = rect.height > bottom - top - 2 * MARGIN
				? rect.top - top - MARGIN
				: rect.top + rect.height / 2 - (top + bottom) / 2;
		}
		const wanted = delta;
		for (let node = field.parentElement; node && node !== document.body && node !== root && Math.abs(delta) >= 1; node = node.parentElement) {
			if (!scrolls(node)) continue;
			const before = node.scrollTop;
			node.scrollTop = before + delta;
			delta -= node.scrollTop - before;
		}
		if (Math.abs(delta) >= 1 && !inFixed(field)) window.scrollBy(0, delta);
		return Math.abs(wanted) >= 1;
	};
	// sticky 的条（弹窗标题栏、页头）滚动之后才贴到边上：挪完再量一次，被它盖住就再挪一次
	const place = () => {
		timer = 0;
		if (!open || !field || !field.isConnected || document.activeElement !== field) return;
		if (shift()) shift();
	};
	const later = (wait) => {
		if (timer) window.clearTimeout(timer);
		timer = window.setTimeout(place, wait);
	};
	const measure = () => {
		if (Math.abs(view.width - wide) > 1) {
			wide = view.width;
			tall = view.height;
		}
		tall = Math.max(tall, view.height);
		const now = !!field && Math.abs(view.scale - 1) < 0.01 && tall - view.height > KEYBOARD;
		if (now) {
			root.style.setProperty("--pearnly-visible-top", `${view.offsetTop}px`);
			root.style.setProperty("--pearnly-visible-height", `${view.height}px`);
		}
		if (now === open) return;
		open = now;
		root.classList.toggle("pearnly-keyboard-open", open);
		if (!open) {
			root.style.removeProperty("--pearnly-visible-top");
			root.style.removeProperty("--pearnly-visible-height");
		}
	};

	document.addEventListener("focusin", (event) => {
		field = typing(event.target) ? event.target : null;
		measure();
		if (open) later(0);
	});
	document.addEventListener("focusout", () => {
		window.setTimeout(() => {
			if (document.activeElement === field) return;
			field = null;
			measure();
		}, 0);
	});
	// 键盘弹出是一段动画，可见区连着变好几次：停下来再摆
	view.addEventListener("resize", () => {
		measure();
		if (open) later(80);
	});
	view.addEventListener("scroll", () => {
		if (open) measure();
	});
})();
