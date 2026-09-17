// 在头像下拉里加"密码更改"入口，三个产品共用同一个页面。
//
// 说明：产品自己的界面还没做（P3），这里先在 Frappe 自带界面里挂上，
// 用来证明入口与接口是通的。产品页面接入时只需指向同一个路由。
//
// 注意：本文件**只做过加载与语法检查，没有用真实浏览器点开下拉验证过**，
// 选择器随 Frappe 版本变化可能需要调整。
(function () {
  var ROUTE = "/pearnly-password";

  function inject() {
    var menu = document.querySelector(".navbar .dropdown-menu, #toolbar-user .dropdown-menu");
    if (!menu || menu.querySelector('[data-pearnly-password]')) return;

    var li = document.createElement("li");
    var a = document.createElement("a");
    a.className = "dropdown-item";
    a.href = ROUTE;
    a.textContent = "密码更改";
    a.setAttribute("data-pearnly-password", "1");
    li.appendChild(a);
    menu.appendChild(li);
  }

  function boot() {
    inject();
    // 下拉是延迟渲染的，挂个观察器兜底
    if (window.MutationObserver) {
      new MutationObserver(inject).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
