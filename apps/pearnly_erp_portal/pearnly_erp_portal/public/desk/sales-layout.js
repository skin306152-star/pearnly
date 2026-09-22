/* Sales-specific arrangement of native sections, with mobile scroll anchors. */
(() => {
	const u = frappe.pearnly.sales_ui;
	u.continuous_layout = (form, page) => {
		form.wrapper.addClass("pearnly-sales-form");
		// Reuse Desk navigation styles only. These controls never switch/hide content
		// and deliberately have no tab roles, tab panes or Bootstrap tab handler.
		const nav = $('<nav class="form-tabs-list sales-section-nav">').attr("aria-label", __("nav.sales")).prependTo(form.page);
		const links = $('<ul class="nav form-tabs">').appendTo(nav);
		const body = $('<div class="sales-body">').appendTo(form.page);
		const entry = $('<div class="sales-entry">').appendTo(body);
		const documents = $('<section class="sales-evidence">').appendTo(body);
		const data = $('<div class="sales-data">').appendTo(body);
		const information = $('<section>').appendTo(data);
		const items = $('<section>').appendTo(data);
		const regions = [
			{key: "documents", label: __("Attachments"), element: documents},
			{key: "information", label: __("Details"), element: information},
			{key: "items", label: __("Items"), element: items},
		];
		let target = documents;
		for (const section of form.sections) {
			if (section.df.fieldname === "information_section") target = information;
			if (section.df.fieldname === "items_section") target = items;
			section.wrapper.appendTo(target);
		}
		const scroller = form.wrapper.closest(".main-section")[0] || document.scrollingElement;
		const head = $(page.wrapper).find(".page-head")[0];
		const mobile = window.matchMedia("(max-width: 767px)");
		let frame = 0, destroyed = false;
		const head_height = () => head?.getBoundingClientRect().height || 0;
		const top_offset = () => head_height() + (mobile.matches ? nav.outerHeight() : 0) + 8;
		const navigate = key => {
			const region = regions.find(row => row.key === key);
			if (!region) return;
			const top = region.element[0].getBoundingClientRect().top - scroller.getBoundingClientRect().top - top_offset();
			scroller.scrollBy({top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
		};
		for (const region of regions) {
			const id = `sales-${region.key}`;
			region.element.attr({id, "aria-label": region.label});
			region.link = $('<button type="button" class="nav-link">').attr("aria-controls", id).text(region.label)
				.appendTo($('<li class="nav-item">').appendTo(links));
			region.link.on("click", event => {event.preventDefault(); navigate(region.key);});
		}
		const update = () => {
			frame = 0;
			if (destroyed) return;
			form.wrapper[0].style.setProperty("--sales-head-height", `${head_height()}px`);
			if (!mobile.matches) return;
			const threshold = scroller.getBoundingClientRect().top + top_offset() + 8;
			let active = regions[0];
			for (const region of regions) {
				if (region.element[0].getBoundingClientRect().top <= threshold) active = region;
			}
			for (const region of regions) {
				region.link.toggleClass("active", region === active);
				if (region === active) region.link.attr("aria-current", "location");
				else region.link.removeAttr("aria-current");
			}
		};
		const schedule = () => {if (!frame && !destroyed) frame = requestAnimationFrame(update);};
		scroller.addEventListener("scroll", schedule, {passive: true});
		window.addEventListener("resize", schedule);
		const observer = new ResizeObserver(schedule);
		observer.observe(body[0]);
		if (head) observer.observe(head);
		schedule();
		return {navigate, entry, set_data_visible: visible => {
			data.toggle(visible);
			nav.toggleClass("hide", !visible);
			schedule();
		}, destroy: () => {
			destroyed = true;
			cancelAnimationFrame(frame); observer.disconnect();
			scroller.removeEventListener("scroll", schedule);
			window.removeEventListener("resize", schedule);
			nav.remove();
		}};
	};
})();
