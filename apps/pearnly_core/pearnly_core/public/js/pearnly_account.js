// pearnly_core 账号组件：头像菜单 + 改密弹窗。
//
// 位置：挂在**产品界面里左下角或右下角的头像**上（用户 2026-09-17 明确要求），
// 不是 Frappe 自带界面右上角，也不是登录页。
//
// 三个产品共用这一份：产品界面做好后，拿到那个头像元素调用一次
//   pearnlyAccount.mountAvatarMenu(avatarEl, { csrfToken: <可选> })
// 即可，不需要各自实现表单和请求。
//
// 依赖：无框架，纯 DOM + fetch，Vue / React / 原生 JS 的产品壳都能用。
//
// 说明：本文件已做语法与加载检查，并跑通它调用的接口；**弹窗与菜单的真实
// 点击效果尚未在浏览器里逐项点验**（产品界面尚未落地）。

(function (root) {
  var API = "/api/method/pearnly_core.password.change_password";
  // 密码强度不卡（用户 2026-09-18 要求：纯字母、纯数字都行，首次使用也一样）。
  // 这里只挡"空"，与后端 pearnly_core/password.py 的 MIN_LENGTH 保持一致。
  var MIN_LENGTH = 1;

  function csrf(opt) {
    if (opt && opt.csrfToken) return opt.csrfToken;
    if (root.frappe && root.frappe.csrf_token) return root.frappe.csrf_token;
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function el(tag, attrs, style) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (style) node.setAttribute("style", style);
    return node;
  }

  function injectStyleOnce() {
    if (document.getElementById("pearnly-account-style")) return;
    var s = el("style", { id: "pearnly-account-style" });
    s.textContent = [
      ".pn-account-menu{position:absolute;z-index:9999;background:#fff;border:1px solid #e5e7eb;",
      "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);padding:6px;min-width:150px}",
      ".pn-account-menu button{display:block;width:100%;text-align:left;border:0;background:none;",
      "padding:9px 12px;border-radius:7px;cursor:pointer;font-size:14px;color:#374151}",
      ".pn-account-menu button:hover{background:#f3f4f6}",
      ".pn-mask{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:10000;display:flex;",
      "align-items:center;justify-content:center}",
      ".pn-dialog{background:#fff;border-radius:14px;padding:22px;width:380px;max-width:92vw;",
      "font-size:14px;color:#111827}",
      ".pn-dialog h4{margin:0 0 4px;font-size:16px}",
      ".pn-dialog p.tip{color:#6b7280;margin:0 0 16px;font-size:12.5px}",
      ".pn-field{margin-bottom:12px}.pn-field label{display:block;margin-bottom:5px;color:#374151}",
      ".pn-field input{width:100%;box-sizing:border-box;padding:9px 11px;border:1px solid #d1d5db;",
      "border-radius:8px;font-size:14px}",
      ".pn-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}",
      ".pn-actions button{padding:8px 16px;border-radius:8px;border:1px solid #d1d5db;background:#fff;cursor:pointer}",
      ".pn-actions button.primary{background:#6d5bd0;border-color:#6d5bd0;color:#fff}",
      ".pn-msg{margin-top:12px;min-height:18px;font-size:13px}",
      ".pn-msg.err{color:#dc2626}.pn-msg.ok{color:#059669}"
    ].join("");
    document.head.appendChild(s);
  }

  function openDialog(opt) {
    injectStyleOnce();
    var mask = el("div", { class: "pn-mask" });
    var dlg = el("div", { class: "pn-dialog", role: "dialog", "aria-modal": "true" });
    dlg.innerHTML =
      "<h4>密码更改</h4>" +
      '<p class="tip">改的是统一账号的密码，三个产品通用。</p>' +
      '<div class="pn-field"><label>当前密码</label><input type="password" id="pn-current" autocomplete="current-password"></div>' +
      '<div class="pn-field"><label>新密码</label><input type="password" id="pn-new" autocomplete="new-password"></div>' +
      '<div class="pn-field"><label>确定新密码</label><input type="password" id="pn-confirm" autocomplete="new-password"></div>' +
      '<div class="pn-msg" id="pn-msg"></div>' +
      '<div class="pn-actions"><button id="pn-cancel">取消</button>' +
      '<button class="primary" id="pn-submit">确定</button></div>';
    mask.appendChild(dlg);
    document.body.appendChild(mask);

    var msg = dlg.querySelector("#pn-msg");
    function close() { mask.remove(); }
    dlg.querySelector("#pn-cancel").onclick = close;
    mask.addEventListener("click", function (e) { if (e.target === mask) close(); });

    dlg.querySelector("#pn-submit").onclick = function () {
      var current = dlg.querySelector("#pn-current").value.trim();
      var next = dlg.querySelector("#pn-new").value.trim();
      var confirm = dlg.querySelector("#pn-confirm").value.trim();
      msg.className = "pn-msg";

      if (!current || !next || !confirm) { msg.className = "pn-msg err"; msg.textContent = "三项都要填"; return; }
      if (next !== confirm) { msg.className = "pn-msg err"; msg.textContent = "两次输入的新密码不一致"; return; }
      if (next.length < MIN_LENGTH) { msg.className = "pn-msg err"; msg.textContent = "新密码不能为空"; return; }

      msg.textContent = "提交中…";
      fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf(opt) },
        body: JSON.stringify({ current_password: current, new_password: next, confirm_password: confirm })
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j && j.message && j.message.ok) {
            msg.className = "pn-msg ok";
            msg.textContent = "已修改。下次登录请用新密码。";
          } else {
            msg.className = "pn-msg err";
            msg.textContent = "失败：" + prettyError(j);
          }
        })
        .catch(function (e) { msg.className = "pn-msg err"; msg.textContent = "失败：" + e; });
    };
  }

  function prettyError(j) {
    if (!j) return "未知错误";
    if (j._server_messages) {
      try {
        var arr = JSON.parse(j._server_messages);
        var first = JSON.parse(arr[0]);
        return first.message || "服务器拒绝";
      } catch (e) { return "服务器拒绝"; }
    }
    return j.exc_type || j.exception || "未知错误";
  }

  // 把菜单挂到产品界面左下角/右下角的头像元素上
  function mountAvatarMenu(avatarEl, opt) {
    if (!avatarEl) return;
    injectStyleOnce();
    avatarEl.style.cursor = "pointer";
    avatarEl.addEventListener("click", function (e) {
      e.stopPropagation();
      var existing = document.querySelector(".pn-account-menu");
      if (existing) { existing.remove(); return; }

      var menu = el("div", { class: "pn-account-menu" });
      var btn = el("button", { type: "button" });
      btn.textContent = "密码更改";
      btn.onclick = function () { menu.remove(); openDialog(opt); };
      menu.appendChild(btn);

      // 贴在头像上方（左下角头像的常见位置），并夹在视口内
      var rect = avatarEl.getBoundingClientRect();
      menu.style.left = Math.max(8, rect.left) + "px";
      menu.style.top = Math.max(8, rect.top - 52) + "px";
      document.body.appendChild(menu);

      setTimeout(function () {
        document.addEventListener("click", function once() {
          menu.remove();
          document.removeEventListener("click", once);
        });
      }, 0);
    });
  }

  root.pearnlyAccount = { mountAvatarMenu: mountAvatarMenu, openDialog: openDialog };
})(window);
