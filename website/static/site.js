/* pearnly.com 站点交互：点阵背景（随鼠标起伏）、滚动渐入、顶栏、首页演示、总览视频播放。
 * 不发任何请求；系统开了“减少动态效果”时全部静止。 */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- 点阵背景 ----
  function dots() {
    var canvas = document.querySelector('canvas.dots');
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext('2d');
    var GAP = 22, R = 1, REACH = 150;
    var w = 0, h = 0, dpr = 1;
    var mouse = { x: -9999, y: -9999, tx: -9999, ty: -9999 };
    var running = false, t0 = performance.now();

    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw(now) {
      var t = (now - t0) / 1000;
      mouse.x += (mouse.tx - mouse.x) * 0.12;
      mouse.y += (mouse.ty - mouse.y) * 0.12;
      ctx.clearRect(0, 0, w, h);
      var offset = (window.scrollY || 0) % GAP;
      for (var y = -offset; y < h + GAP; y += GAP) {
        for (var x = GAP / 2; x < w; x += GAP) {
          // 缓慢的斜向波纹，让点阵“呼吸”
          var wave = reduce ? 0 : 0.5 + 0.5 * Math.sin(t * 0.7 - (x + y) * 0.008);
          var a = 0.10 + wave * 0.06, r = R, px = x, py = y;
          var dx = x - mouse.x, dy = y - mouse.y, d = Math.sqrt(dx * dx + dy * dy);
          if (d < REACH) {
            var k = 1 - d / REACH; k = k * k;
            a += k * 0.45; r += k * 1.3;
            if (d > 0.1) { px += dx / d * k * 7; py += dy / d * k * 7; }
          }
          ctx.globalAlpha = a;
          ctx.beginPath(); ctx.arc(px, py, r, 0, 6.2832); ctx.fill();
        }
      }
      if (running) requestAnimationFrame(draw);
    }

    function start() { if (!running && !reduce && !document.hidden) { running = true; requestAnimationFrame(draw); } }
    function stop() { running = false; }

    ctx.fillStyle = '#141413';
    size();
    draw(performance.now());
    window.addEventListener('resize', function () { size(); draw(performance.now()); });
    window.addEventListener('scroll', function () { if (reduce) draw(performance.now()); }, { passive: true });
    if (reduce) return;
    window.addEventListener('pointermove', function (e) { mouse.tx = e.clientX; mouse.ty = e.clientY; }, { passive: true });
    document.addEventListener('pointerleave', function () { mouse.tx = mouse.ty = -9999; });
    document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else start(); });
    start();
  }

  // ---- 滚动渐入 ----
  function reveal() {
    var items = document.querySelectorAll('.reveal');
    if (reduce || !('IntersectionObserver' in window)) {
      for (var i = 0; i < items.length; i++) items[i].classList.add('in');
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    for (var j = 0; j < items.length; j++) io.observe(items[j]);
  }

  // ---- 顶栏：滚过 40px 后从透明全宽条收成胶囊 ----
  function topbar() {
    var bar = document.querySelector('.topbar');
    if (!bar) return;
    var on = function () { bar.classList.toggle('scrolled', window.scrollY > 40); };
    window.addEventListener('scroll', on, { passive: true });
    on();
  }

  // ---- 首页演示：扫描单据 → 字段逐个出现 → 完成 → 换下一个产品 ----
  function demo() {
    var root = document.querySelector('.demo');
    if (!root) return;
    var tabs = root.querySelectorAll('[data-tab]');
    var bar = root.querySelector('.progress i');
    var order = [], timers = [], current = 0, visible = false, started = false;
    for (var i = 0; i < tabs.length; i++) order.push(tabs[i].getAttribute('data-tab'));

    function clear() { timers.forEach(clearTimeout); timers = []; }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }

    function play(index, auto) {
      clear();
      current = index;
      var name = order[index];
      for (var i = 0; i < tabs.length; i++) tabs[i].setAttribute('aria-selected', String(i === index));
      var scenes = root.querySelectorAll('.scene');
      var scene;
      for (var s = 0; s < scenes.length; s++) {
        var active = scenes[s].getAttribute('data-scene') === name;
        scenes[s].classList.toggle('active', active);
        if (active) scene = scenes[s];
      }
      var fields = scene.querySelectorAll('.f');
      var msgs = scene.querySelectorAll('.msg');
      var done = scene.querySelector('.done');
      var doc = scene.querySelector('.doc');
      scene.querySelectorAll('.on, .hit').forEach(function (el) { el.classList.remove('on', 'hit'); });
      done.classList.remove('on');
      if (reduce) {
        fields.forEach(function (f) { f.classList.add('on'); });
        msgs.forEach(function (m) { m.classList.add('on'); });
        done.classList.add('on');
        return;
      }
      var at = 200;
      msgs.forEach(function (m) { later(function () { m.classList.add('on'); }, at); at += 650; });
      later(function () { doc.classList.add('scanning'); }, at);
      at += 500;
      fields.forEach(function (f) {
        later(function () {
          f.classList.add('on');
          var hit = doc.querySelector('[data-k="' + f.getAttribute('data-k') + '"]');
          if (hit) { hit.classList.add('hit'); later(function () { hit.classList.remove('hit'); }, 700); }
        }, at);
        at += 420;
      });
      later(function () { doc.classList.remove('scanning'); done.classList.add('on'); }, at + 100);
      var total = at + 3200;
      if (bar) {
        bar.style.transition = 'none'; bar.style.width = '0%';
        void bar.offsetWidth;
        bar.style.transition = 'width ' + total + 'ms linear'; bar.style.width = '100%';
      }
      if (auto !== false) later(function () { if (visible) play((current + 1) % order.length); else started = false; }, total);
    }

    for (var t = 0; t < tabs.length; t++) {
      (function (index) { tabs[index].addEventListener('click', function () { play(index); }); })(t);
    }
    // 悬停字段时在单据上标出对应位置
    root.addEventListener('pointerover', function (e) {
      var f = e.target.closest && e.target.closest('.f');
      root.querySelectorAll('.doc .hl').forEach(function (el) { el.classList.remove('hl'); });
      if (!f) return;
      var scene = f.closest('.scene');
      var hit = scene.querySelector('.doc [data-k="' + f.getAttribute('data-k') + '"]');
      if (hit) hit.classList.add('hl');
    });
    root.addEventListener('pointerleave', function () {
      root.querySelectorAll('.doc .hl').forEach(function (el) { el.classList.remove('hl'); });
    });

    if (!('IntersectionObserver' in window)) { visible = true; play(0); return; }
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible && !started) { started = true; play(current); }
    }, { threshold: 0.35 }).observe(root);
  }

  // ---- 总览视频：自定义播放按钮 ----
  function players() {
    document.querySelectorAll('.player').forEach(function (box) {
      var video = box.querySelector('video'), btn = box.querySelector('.playbtn');
      if (!video || !btn) return;
      box.classList.add('ready');
      video.controls = false;
      btn.addEventListener('click', function () { video.controls = true; video.play(); });
      video.addEventListener('play', function () { video.controls = true; box.classList.add('playing'); });
      video.addEventListener('pause', function () { if (video.ended || video.currentTime === 0) box.classList.remove('playing'); });
      video.addEventListener('ended', function () { box.classList.remove('playing'); });
    });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
