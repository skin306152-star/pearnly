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

  // ---- 首页演示：三段"现实 → 系统"小故事。编排都在标记里（见 build.py 的说明），这里只是通用时间轴 ----
  function demo() {
    var root = document.querySelector('.demo');
    if (!root) return;
    var tabs = root.querySelectorAll('[data-tab]');
    var bar = root.querySelector('.progress i');
    var order = [], timers = [], current = 0, visible = false, started = false;
    var SPEED = 0.6;  // 标记里按 10 秒左右编排，整体压到 6 秒左右播放
    for (var i = 0; i < tabs.length; i++) order.push(tabs[i].getAttribute('data-tab'));
    var hashed = /^#demo-(\w+)/.exec(location.hash);
    if (hashed && order.indexOf(hashed[1]) >= 0) current = order.indexOf(hashed[1]);

    function clear() { timers.forEach(clearTimeout); timers = []; }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
    function steps(el) {
      return el.getAttribute('data-at').split(',').map(function (part) {
        var bits = part.trim().split(':');
        var cls = bits[1] || 'on', off = cls.charAt(0) === '-';
        return { at: +bits[0], cls: off ? cls.slice(1) : cls, off: off };
      });
    }
    function type(el, done) {
      var text = el.getAttribute('data-type');
      if (done || reduce) { el.textContent = text; return; }
      var n = 0;
      (function tick() { el.textContent = text.slice(0, ++n); if (n < text.length) later(tick, 28); })();
    }
    function count(el, cls, done) {
      el.getAttribute('data-count').split('|').forEach(function (rule) {
        var r = rule.split(':');
        if (r[0] !== cls) return;
        var from = +r[1], to = +r[2], t0 = performance.now();
        if (done || reduce) { el.textContent = to; return; }
        (function tick(now) {
          var k = Math.min(1, (now - t0) / 650);
          el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
          if (k < 1) requestAnimationFrame(tick);
        })(t0);
      });
    }
    function center(el, base) {
      var r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2 - base.left, y: r.top + r.height / 2 - base.top };
    }
    function fly(el, scene) {
      var src = scene.querySelector(el.getAttribute('data-from')), dst = scene.querySelector(el.getAttribute('data-to'));
      if (!src || !dst || !el.animate) return;
      var base = scene.getBoundingClientRect(), a = center(src, base), b = center(dst, base);
      var w = el.offsetWidth / 2, h = el.offsetHeight / 2, lift = Math.min(90, Math.abs(b.x - a.x) * 0.25 + 30);
      var at = function (p, s, o) {
        var x = a.x + (b.x - a.x) * p - w, y = a.y + (b.y - a.y) * p - h - Math.sin(p * Math.PI) * lift;
        return { transform: 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')', opacity: o };
      };
      el.animate([at(0, 0.6, 0), at(0.15, 1, 1), at(0.5, 1.08, 1), at(0.9, 1, 1), at(1, 0.7, 0)],
                 { duration: +(el.getAttribute('data-dur') || 750) * SPEED, easing: 'cubic-bezier(.45,0,.25,1)', fill: 'both' });
    }
    // 配对的线：从一个元素的右边缘连到另一个元素的左边缘，加 on 时由 CSS 画出来
    function thread(el, scene) {
      var src = scene.querySelector(el.getAttribute('data-from')), dst = scene.querySelector(el.getAttribute('data-to'));
      if (!src || !dst) return;
      var base = scene.getBoundingClientRect(), r1 = src.getBoundingClientRect(), r2 = dst.getBoundingClientRect();
      var x1 = r1.right - base.left - 4, y1 = r1.top + r1.height / 2 - base.top;
      if (r2.top > r1.bottom + 20) {  // 手机上两栏叠起来：从这一行的右端往下弯到卡片顶部（data-to-top 指定叠起来时连到哪个元素）
        if (el.getAttribute('data-to-top')) r2 = scene.querySelector(el.getAttribute('data-to-top')).getBoundingClientRect();
        var x2v = r2.left + r2.width / 2 - base.left, y2v = r2.top - base.top + 2;
        el.setAttribute('d', 'M' + x1 + ' ' + y1 + ' C' + (x1 + 28) + ' ' + y1 + ' ' + x2v + ' ' + (y2v - 70) + ' ' + x2v + ' ' + y2v);
        return;
      }
      var x2 = r2.left - base.left + 4, y2 = r2.top + r2.height / 2 - base.top, dx = Math.max(40, (x2 - x1) / 2);
      el.setAttribute('d', 'M' + x1 + ' ' + y1 + ' C' + (x1 + dx) + ' ' + y1 + ' ' + (x2 - dx) + ' ' + y2 + ' ' + x2 + ' ' + y2);
    }
    function apply(el, step, scene, done) {
      if (el.classList.contains('fly')) { if (!done) fly(el, scene); return; }
      if (el.classList.contains('thread') && !step.off) thread(el, scene);
      el.classList.toggle(step.cls, !step.off);
      if (step.off) return;
      if (step.cls === 'on' && el.hasAttribute('data-type')) type(el, done);
      if (el.hasAttribute('data-count')) count(el, step.cls, done);
      el.querySelectorAll(':scope > [data-count]').forEach(function (c) { count(c, step.cls, done); });
    }
    function reset(scene) {
      scene.querySelectorAll('[data-at]').forEach(function (el) {
        steps(el).forEach(function (st) { el.classList.remove(st.cls); });
        if (el.hasAttribute('data-type')) el.textContent = '';
        if (el.getAnimations) el.getAnimations().forEach(function (an) { an.cancel(); });
      });
      scene.querySelectorAll('[data-count]').forEach(function (el) { el.textContent = el.getAttribute('data-count').split('|')[0].split(':')[1]; });
    }

    function play(index, auto) {
      clear();
      current = index;
      var name = order[index], scene;
      for (var i = 0; i < tabs.length; i++) tabs[i].setAttribute('aria-selected', String(i === index));
      root.querySelectorAll('.scene').forEach(function (s) {
        var active = s.getAttribute('data-scene') === name;
        s.classList.toggle('active', active);
        if (active) scene = s;
      });
      reset(scene);
      var all = [];
      scene.querySelectorAll('[data-at]').forEach(function (el) { steps(el).forEach(function (st) { all.push([el, st]); }); });
      all.sort(function (x, y) { return x[1].at - y[1].at; });
      if (reduce) {
        all.forEach(function (pair) { apply(pair[0], pair[1], scene, true); });
        return;
      }
      all.forEach(function (pair) { later(function () { apply(pair[0], pair[1], scene, false); }, pair[1].at * SPEED); });
      var total = +scene.getAttribute('data-total') * SPEED;
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
    if (!('IntersectionObserver' in window)) { visible = true; play(current); return; }
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


  // ---- 品牌形象 น้องใบ：眼睛跟着指针，眨眼错开，点首屏那只会跳一下 ----
  function mascots() {
    var looks = Array.prototype.slice.call(document.querySelectorAll('.mascot .m-look, .hm .m-look'));
    document.querySelectorAll('.mascot .m-blink').forEach(function (g, i) { g.style.animationDelay = (-((i * 1.7) % 5.5)) + 's'; });
    document.querySelectorAll('.hero-mascot').forEach(function (m) {
      m.addEventListener('click', function () { m.classList.remove('hop'); void m.getBoundingClientRect(); m.classList.add('hop'); });
    });
    if (reduce || !looks.length) return;
    var px = 0, py = 0, queued = false;
    function update() {
      queued = false;
      looks.forEach(function (g) {
        // 整张 SVG 里只有一只小单的（品牌形象）按整张算；手绘场景里的小单按它自己的身体算
        var r = (g.closest('.hm') || g.ownerSVGElement).getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) return;
        var dx = px - (r.left + r.width / 2), dy = py - (r.top + r.height * 0.3), d = Math.sqrt(dx * dx + dy * dy) || 1, k = Math.min(1, d / 240) * 5;
        g.setAttribute('transform', 'translate(' + (dx / d * k).toFixed(2) + ' ' + (dy / d * k).toFixed(2) + ')');
      });
    }
    window.addEventListener('pointermove', function (e) { px = e.clientX; py = e.clientY; if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  }

  // ---- 首页主视觉：小单把书桌照片的一角卷起来，露出底下整理好的账（几何与 website/poster.py geom() 同一套） ----
  function peel() {
    var svg = document.querySelector('.pz');
    if (!svg) return;
    var card = svg.closest('.peel'), ask = card.querySelector('.pz-ask'), p0 = +svg.getAttribute('data-p0');
    function q(sel) { return svg.querySelector(sel); }
    function all(sel) { return svg.querySelectorAll(sel); }
    var clip = q('.pz-clip'), body = q('.pz-body'), legs = all('.pz-leg'), armPen = q('.pz-arm-pen'), handPen = q('.pz-hand-pen'),
      pen = q('.pz-pen'), armPush = q('.pz-arm-push'), handPush = q('.pz-hand-push'), ground = q('.pz-ground'),
      flaps = all('.pz-flap'), gap = q('.pz-gapshade'), g1 = q('.pz-g'), g2 = q('.pz-g2'),
      sheets = all('.pz-sheet'), front = q('.pz-front'), back = q('.pz-cheer');
    var SCALE = 1.35, HP = 560, DRAWN = [1132, 482], BACK = DRAWN;
    function f(v) { return Math.round(v * 10) / 10; }
    function clamp(v) { return Math.max(0, Math.min(1, v)); }
    function set(el, k, v) { el.setAttribute(k, v); }
    function limb(a, b, bend) {
      var dx = b[1] - a[1], dy = a[0] - b[0], d = Math.sqrt(dx * dx + dy * dy) || 1;
      return 'M' + f(a[0]) + ' ' + f(a[1]) + 'Q' + f((a[0] + b[0]) / 2 + dx / d * bend) + ' ' + f((a[1] + b[1]) / 2 + dy / d * bend) + ' ' + f(b[0]) + ' ' + f(b[1]);
    }
    // 进度 p → 翻页角的几何（与 poster.py state() / curl() / half_plane() 同一套公式）；p 超过 1 就是整张往右上揭走
    function geom(p) {
      var a = 120 + 150 * p, b = a * 1.75, ln = Math.sqrt(a * a + b * b), ux = b / ln, uy = a / ln, nx = uy, ny = -ux;
      var t0 = a * uy, fx = ux * t0, fy = HP - a + uy * t0, dist = a * b / ln;
      return { a: a, b: b, ux: ux, uy: uy, nx: nx, ny: ny, fx: fx, fy: fy, dist: dist, tx: fx + nx * dist * .66, ty: fy + ny * dist * .66 };
    }
    function sheet(p) {
      var g = geom(p), a = g.a, b = g.b, tx = g.tx, ty = g.ty, d = g.dist, far = 5000, ay = HP - a;
      var p1 = [-g.ux * far, ay - g.uy * far], p2 = [g.ux * far, ay + g.uy * far];
      set(clip, 'd', 'M' + f(p1[0]) + ' ' + f(p1[1]) + 'L' + f(p2[0]) + ' ' + f(p2[1]) + 'L' + f(p2[0] + g.nx * far) + ' ' + f(p2[1] + g.ny * far) +
        'L' + f(p1[0] + g.nx * far) + ' ' + f(p1[1] + g.ny * far) + 'Z');
      var flapD = 'M0 ' + f(ay) + 'Q' + f(tx / 2 - g.nx * d * .1) + ' ' + f((ay + ty) / 2 - g.ny * d * .1) + ' ' + f(tx) + ' ' + f(ty) +
        'Q' + f((b + tx) / 2 - g.nx * d * .14) + ' ' + f((HP + ty) / 2 - g.ny * d * .14) + ' ' + f(b) + ' ' + HP + 'Z';
      for (var i = 0; i < flaps.length; i++) set(flaps[i], 'd', flapD);
      set(gap, 'd', 'M0 ' + f(ay) + 'L' + f(b) + ' ' + HP + 'H0Z');
      set(g1, 'x1', f(g.fx)); set(g1, 'y1', f(g.fy)); set(g1, 'x2', f(tx)); set(g1, 'y2', f(ty));
      set(g2, 'x1', f(g.fx)); set(g2, 'y1', f(g.fy));
      return g;
    }
    // 小单：左手捏着纸尖往后拉、右手拿铅笔，掀得越开退得越多
    function figure(g) {
      var ph = g.a / 14, bob = -3 * Math.abs(Math.cos(ph)), t = 12 * Math.PI / 180, c = Math.cos(t), sn = Math.sin(t);
      var hip = [g.tx + 92, g.ty + 70 + bob], feet = hip[1] + 40 * SCALE - 2;
      function rot(px, py) { px *= SCALE; py *= SCALE; return [hip[0] + px * c - py * sn, hip[1] + px * sn + py * c]; }
      var swing = 14 * Math.sin(ph);
      [[-14, swing], [14, -swing]].forEach(function (l, i) {
        var h = rot(l[0], 0), x2 = h[0] + l[1] + 6;
        set(legs[i], 'd', 'M' + f(h[0]) + ' ' + f(h[1]) + 'L' + f((h[0] + x2) / 2 + 7) + ' ' + f((h[1] + feet) / 2 - 2) + 'L' + f(x2) + ' ' + f(feet) + 'h' + (i ? 14 : -14));
      });
      var hand = [g.tx + 4, g.ty + 4], penAt = rot(60, -12), penSh = rot(38, -52), penCtrl = rot(50, -34);
      set(body, 'transform', 'translate(' + f(hip[0]) + ' ' + f(hip[1]) + ') rotate(12) scale(' + SCALE + ')');
      set(armPen, 'd', 'M' + f(penSh[0]) + ' ' + f(penSh[1]) + 'Q' + f(penCtrl[0]) + ' ' + f(penCtrl[1]) + ' ' + f(penAt[0]) + ' ' + f(penAt[1]));
      set(handPen, 'cx', f(penAt[0])); set(handPen, 'cy', f(penAt[1]));
      set(pen, 'transform', 'translate(' + f(penAt[0]) + ' ' + f(penAt[1]) + ') rotate(28) translate(0 -72)');
      set(armPush, 'd', limb(rot(-34, -50), hand, 14));
      set(handPush, 'cx', f(hand[0])); set(handPush, 'cy', f(hand[1]));
      set(ground, 'cx', f(hip[0] + 6)); set(ground, 'cy', f(feet + 3));
      return hip;
    }
    function draw(p) { return figure(sheet(p)); }
    // 手机上只取左边一段
    function fit() {
      var view = svg.getAttribute(card.clientWidth < 640 ? 'data-view-sm' : 'data-view');
      svg.setAttribute('viewBox', view);
      q('.pz-frame').setAttribute('width', view.split(' ')[2]);
      BACK = card.clientWidth < 640 ? [560, 482] : [1132, 482];
    }
    fit();
    window.addEventListener('resize', fit);

    // ---- 点整张画（或“下面是什么？”）：整张画往右上揭走、淡掉，小单松手跳到账旁边朝你挥手；再点一下盖回去 ----
    var cur = 0, open = false, busy = false, OUT = 9;
    function ease(t) { return t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
    function jump(from, to, t) {
      var x = from[0] + (to[0] - from[0]) * t, y = from[1] + (to[1] - from[1]) * t - 150 * 4 * t * (1 - t);
      var squash = t > .9 ? 1 - .1 * Math.sin((t - .9) / .1 * Math.PI) : 1;
      return [x, y, squash];
    }
    function place(el, at, origin, squash, op) {
      set(el, 'transform', 'translate(' + f(at[0] - origin[0]) + ' ' + f(at[1] - origin[1]) + ')' +
        (squash !== 1 ? ' translate(' + f(at[0]) + ' ' + f(at[1] + 54) + ') scale(' + f((2 - squash) * 100) / 100 + ' ' + f(squash * 100) / 100 + ') translate(' + f(-at[0]) + ' ' + f(-at[1] - 54) + ')' : ''));
      set(el, 'opacity', op);
    }
    function toggle() {
      if (busy) return;
      open = !open;
      ask.setAttribute('aria-pressed', String(open));
      card.classList.toggle('revealed', open);
      var start = cur, hip = figure(geom(start));
      var backHip = [BACK[0], BACK[1]];
      if (reduce) {
        sheet(open ? OUT : start);
        for (var i = 0; i < sheets.length; i++) set(sheets[i], 'opacity', open ? 0 : 1);
        set(front, 'opacity', open ? 0 : 1); set(front, 'transform', '');
        set(back, 'opacity', open ? 1 : 0); place(back, BACK, DRAWN, 1, open ? 1 : 0);
        return;
      }
      busy = true;
      var t0 = performance.now(), dur = 1500;
      (function step(now) {
        var k = clamp((now - t0) / dur), e = ease(k), tt = open ? k : 1 - k;
        // 纸：揭走 / 盖回（p 从当前进度到 OUT），最后 30% 时间淡掉
        var p = start + (OUT - start) * ease(open ? k : 1 - k);
        sheet(p);
        var fade = clamp((ease(tt) - .7) / .3);
        for (var i = 0; i < sheets.length; i++) set(sheets[i], 'opacity', f(1 - fade));
        // 小单：跳起来一道弧，半空换成挥手的姿势，落到账旁边朝你挥手（盖回时倒着放）
        var jt = clamp((tt - .12) / .7), at = jump(hip, backHip, ease(jt));
        if (jt < .5) { place(front, at, hip, 1, 1); set(back, 'opacity', 0); }
        else { place(back, at, DRAWN, at[2], 1); set(front, 'opacity', 0); }
        if (k < 1) requestAnimationFrame(step);
        else {
          busy = false;
          if (!open) { set(front, 'transform', ''); set(front, 'opacity', 1); set(back, 'opacity', 0); queue(); }
        }
      })(t0);
    }
    card.addEventListener('click', toggle);   // 按钮在卡片里，键盘按回车/空格时的点击也会冒泡到这里

    if (reduce) { draw(p0); cur = p0; return; }
    // 卡片从屏幕下方往上走约 3/4 屏就卷到头；平滑跟随，只在看得见、进度有变化时重画（揭开后不再跟着滚动）
    var last = 0, queued = false, seen = true;
    function goal() {
      var r = svg.getBoundingClientRect(), vh = window.innerHeight;
      return clamp((vh * .95 - r.top) / (vh * .75));
    }
    function frame(now) {
      queued = false;
      if (open || busy) return;
      var g = goal();
      cur += (g - cur) * Math.min(1, (now - last) / 120 || .15);
      last = now;
      draw(cur);
      if (Math.abs(g - cur) > .0008) queue();
    }
    function queue() { if (!queued) { queued = true; requestAnimationFrame(frame); } }
    if ('IntersectionObserver' in window) new IntersectionObserver(function (e) { seen = e[0].isIntersecting; if (seen) queue(); }).observe(svg);
    window.addEventListener('scroll', function () { if (seen) queue(); }, { passive: true });
    last = performance.now();
    draw(0);
    queue();
  }

  // ---- 预约演示按钮：手机上直接打开预填好消息的 LINE 对话；电脑上留在加好友页（扫码） ----
  function lineMessages() {
    if (!/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return;
    document.querySelectorAll('[data-line-message]').forEach(function (a) { a.href = a.getAttribute('data-line-message'); });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); mascots(); peel(); lineMessages(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
