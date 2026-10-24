return { ok: true, route: frappe.get_route(), ready: document.readyState, rows: document.querySelectorAll(".dt-row, .list-row, tr").length };
