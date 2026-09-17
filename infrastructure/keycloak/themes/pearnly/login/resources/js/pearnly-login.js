// 登录页的几处交互。旧登录页那套是 landing.js 里的，这里只补身份服务这边必须的三件：
//   1. 密码"眼睛"（旧页用事件委托挂在 document 上，这里也是同一个写法）；
//   2. 手机端上拉登录卡（旧页的 .m-login-cta / .m-backdrop / .m-sheet-close 三件套）；
//   3. 一进来就带着错误时，手机端自动把卡拉开，否则用户看不到提示。
// 缩放逻辑不在这里 —— 那是 landing-scale.js 的原样拷贝（pearnly-stage.js）。
(function () {
    var stage = document.querySelector('.auth-stage');
    if (!stage) return;

    // ── 1. 密码显示 / 隐藏 ──────────────────────────────────────────────
    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('[data-toggle-password]');
        if (!toggle) return;
        var input = document.getElementById(toggle.dataset.togglePassword);
        if (!input) return;
        var showing = input.type === 'password';
        input.type = showing ? 'text' : 'password';
        toggle.setAttribute('aria-pressed', showing ? 'true' : 'false');
    });

    // ── 2. 手机端抽屉 ───────────────────────────────────────────────────
    function setSheet(open) {
        stage.classList.toggle('m-auth-open', open);
        document.body.style.overflow = open ? 'hidden' : '';
    }

    var cta = document.getElementById('m-login-cta');
    if (cta) cta.addEventListener('click', function () { setSheet(true); });

    var backdrop = document.getElementById('m-backdrop');
    if (backdrop) backdrop.addEventListener('click', function () { setSheet(false); });

    var close = document.getElementById('m-sheet-close');
    if (close) close.addEventListener('click', function () { setSheet(false); });

    // Esc 也能关（外接键盘 / 桌面窄窗口）
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && stage.classList.contains('m-auth-open')) setSheet(false);
    });

    // ── 3. 带错误进来时自动拉开 ─────────────────────────────────────────
    // 先让样式类生效，再交给上面的抽屉逻辑。
    if (document.querySelector('.pn-field-error')) stage.classList.add('pn-has-error');

    // 密码框拿到焦点时（手机端点了"登录"之后自动聚焦），也把抽屉拉开
    var pwd = document.getElementById('password');
    if (pwd) {
        pwd.addEventListener('focus', function () {
            if (window.innerWidth <= 760) stage.classList.add('m-auth-open');
        });
    }
})();
