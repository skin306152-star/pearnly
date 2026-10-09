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

  // ---- 首页主视觉“以前 / 现在”（website/story.py）：每一帧都由时间 t 算出来，同一个 t 永远画出同一帧，所以能重播、能停在最后一帧 ----
  function story() {
    var svg = document.querySelector('.st');
    if (!svg) return;
    var card = svg.closest('.st-card'), capEl = card.querySelector('.st-cap'), replay = card.querySelector('.st-replay');
    var CAPS = JSON.parse(card.getAttribute('data-caps'));
    var K = 2, HOME = [640, 476], DESK = 428, SLAB = 7, ITEMS = 16, END = 14400;
    var T2 = 4800, T3 = 6250, T4 = 11500;            // ② 转折、③ 现在（记号笔盖满画面的那一刻）、④ 完成
    var C1 = [300, 1800, 3300];                       // 以前：每张 1.5 秒
    var FALL = [1150, 2550, 3200, 4100];              // 以前：画面外伸进一只手，每次往堆上再放两张
    var SNAP = [7750, 8350, 8880, 9330, 9700];        // 现在：前五张一张张拍，剩下的一口气
    for (var s0 = 0; SNAP.length < ITEMS; s0++) SNAP.push(10000 + s0 * 75);
    var ARRIVE = SNAP.map(function (s) { return s + 680; });
    var HL0 = [586, 424], HR0 = [700, 424], READ = [522, 322], TRAY = [442, 408];
    var KEYS = [[668, 419], [690, 414], [706, 419], [680, 416]];
    var PHONE_DESK = [527, 421], PHONE_HOLD = [398, 256], MUG_DESK = [770, 428], TABLE = [1010, 258];
    var OLD = ['บจ. ขนส่งด่วนไทย\t321.00', 'ร้านเครื่องเขียนสยาม\t193.10····⌫⌫⌫⌫⌫39.10', 'บจ. แพ็คกิ้งพลัส\t2,247.00'];

    function q(s) { return svg.querySelector(s); }
    function qa(s) { return Array.prototype.slice.call(svg.querySelectorAll(s)); }
    var E = {
      body: q('.st-body'), look: q('.st-look'), bags: q('.st-bags'), sweat: q('.st-sweat'), excl: q('.st-excl'),
      armL: q('.st-arm-l'), armR: q('.st-arm-r'), handL: q('.st-hand-l'), handR: q('.st-hand-r'),
      held: q('.st-held'), helper: q('.st-helper'), stream: q('.st-stream'), slabs: qa('.st-slab'), ptop: q('.st-ptop'), tsheets: qa('.st-tsheet'),
      pflat: q('.st-pflat'), pglow: q('.st-pglow'), pscreen: q('.st-pscreen'), buzz: q('.st-buzz'), bubble: q('.st-bubble'),
      pup: q('.st-pup'), shutter: q('.st-shutter'), ok: q('.st-ok'), vdoc: q('.st-vdoc'), burst: q('.st-burst'),
      mug: q('.st-mug'), steam: q('.st-mug .st-steam'), mug2: q('.st-mug2'), mug3: q('.st-mug3'),
      fly: qa('.st-fly'), chips: qa('.st-chip'), rows: qa('.st-row'), count: q('.st-count'), stamp: q('.st-stamp'),
      old: q('.st-scr-old'), oldS: qa('.st-old-s'), oldA: qa('.st-old-a'), cell: q('.st-cell'), typo: q('.st-typo'),
      app: q('.st-scr-app'), off: q('.st-scr-off'), boot: q('.st-scr-boot'),
      dusk: q('.st-sky-dusk'), nightSky: q('.st-sky-night'), stars: qa('.st-star'), starG: q('.st-stars'),
      sun: q('.st-sun'), moon: q('.st-moon'), cloud1: q('.st-cloud1'), cloud2: q('.st-cloud2'), bird: q('.st-bird'),
      hour: q('.st-hour'), min: q('.st-min'), night: q('.st-night'), glow: q('.st-glow'), light: q('.st-light'), bulb: q('.st-bulb'),
      flash: q('.st-flash'), swipe: q('.st-swipe'), puff: q('.st-puff'), sparks: qa('.st-spark'), frame: q('.st-frame')
    };
    var EYES = {}, MOUTHS = {};
    qa('.st-e').forEach(function (el) { EYES[el.getAttribute('class').split('st-e-')[1]] = el; });
    qa('.st-m').forEach(function (el) { MOUTHS[el.getAttribute('class').split('st-m-')[1]] = el; });
    E.rows = E.rows.map(function (g) { return { g: g, hl: g.querySelector('.st-rowhl'), tick: g.querySelector('.st-rowtick') }; });

    // ---- 小工具 ----
    function c01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
    function io(k) { return k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; }
    function out(k) { return 1 - Math.pow(1 - k, 3); }
    function back(k) { var a = 1.70158; return 1 + (a + 1) * Math.pow(k - 1, 3) + a * Math.pow(k - 1, 2); }
    function lin(k) { return k; }
    function seg(t, a, b, e) { return (e || io)(c01((t - a) / (b - a))); }
    function mix(a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]; }
    function f(v) { return Math.round(v * 10) / 10; }
    function f2(v) { return Math.round(v * 1000) / 1000; }
    function count(list, t, d) { var c = 0; for (var i = 0; i < list.length; i++) if (t >= list[i] + (d || 0)) c++; return c; }
    function set(el, k, v) { var key = '_' + k; if (el[key] !== v) { el[key] = v; el.setAttribute(k, v); } }
    function op(el, v) { set(el, 'opacity', String(Math.round(c01(v) * 100) / 100)); }
    function move(el, x, y, extra) { set(el, 'transform', 'translate(' + f(x) + ' ' + f(y) + ')' + (extra || '')); }
    function text(el, v) { if (el._text !== v) { el._text = v; el.textContent = v; } }
    function arc(a, b, k, lift) { var p = mix(a, b, k); p[1] -= Math.sin(k * Math.PI) * lift; return p; }
    function bump(t, list, dur) { var v = 0; list.forEach(function (s) { var d = t - s; if (d >= 0 && d < dur) v = Math.max(v, Math.sin(d / dur * Math.PI)); }); return v; }

    // ---- 单据堆：以前一边拿一边掉新的；现在一张张被拍走 ----
    function pileN(t) { return t < T3 ? 13 + 2 * count(FALL, t, 340) - count(C1, t, 350) : ITEMS - count(SNAP, t, 80); }

    // ---- 旧软件里逐字敲出来的那一行（⌫ 删一个字，· 停一下）；返回 [供应商, 金额, 是否正在改错, 光标在第几格] ----
    function typed(c, t) {
      var u = t - C1[c] - 650, toks = Array.from(OLD[c]);
      if (u < 0) return ['', '', false, -1];
      var total = 0; toks.forEach(function (x) { total += x === '⌫' ? .7 : 1; });
      var budget = c01(u / 600) * total, buf = [], err = false, firstPause = OLD[c].indexOf('·');
      for (var i = 0, acc = 0; i < toks.length; i++) {
        acc += toks[i] === '⌫' ? .7 : 1;
        if (acc > budget + 1e-6) break;
        if (toks[i] === '⌫') buf.pop(); else if (toks[i] !== '·') buf.push(toks[i]);
        err = firstPause >= 0 && i >= firstPause - 1 && toks.slice(i + 1).indexOf('⌫') >= 0;
      }
      var str = buf.join(''), tab = str.indexOf('\t');
      return tab < 0 ? [str, '', false, u < 600 ? 0 : -1] : [str.slice(0, tab), str.slice(tab + 1), err, u < 600 ? 1 : -1];
    }

    // ---- 小单每一帧的姿势 ----
    function pose(t) {
      var m = { x: HOME[0], y: HOME[1], r: 0, sy: 1, hl: HL0.slice(), hr: HR0.slice(), eyes: 'open', mouth: 'smile', look: [0, 0],
                bags: 0, sweat: 0, excl: 0, held: null, phone: 'flat', mug: null };
      if (t < T2) act1(t, m); else if (t < T3) act2(t, m); else if (t < T4) act3(t, m); else act4(t, m);
      return m;
    }
    // ① 以前：左手拿一张、眯眼看，右手一根手指敲；看完放进“已录”筐。越到晚上越累：往下塌、冒汗、黑眼圈
    function act1(t, m) {
      var tired = seg(t, 2400, 4400);
      m.y += 7 * tired; m.bags = tired; m.mouth = t < 300 ? 'smile' : t < 2600 ? 'flat' : 'wavy'; m.sweat = t > 2000 ? 1 : 0;
      for (var c = 0; c < 3; c++) {
        var u = t - C1[c], k;
        if (u < 0 || u > 1500) continue;
        var grab = [296, DESK - pileN(C1[c] + 349) * SLAB - 3];
        if (u < 350) {
          k = seg(u, 0, 350); m.hl = mix(HL0, grab, k); m.r = -5 * k; m.x -= 12 * k; m.look = [-3 * k, k];
        } else if (u < 650) {
          k = seg(u, 350, 650); m.hl = mix(grab, READ, k); m.r = -5 + 2 * k; m.x -= 12; m.look = [-3, -1];
          m.held = { at: m.hl, rot: -8, sy: 1 }; if (u > 450) m.eyes = 'squint';
        } else if (u < 1250) {
          k = seg(u, 650, 800); m.r = -3 + 7 * k; m.x -= 12 * (1 - k);
          m.hl = [READ[0] + Math.sin(u / 90) * 1.5, READ[1] + Math.sin(u / 130) * 2]; m.held = { at: m.hl, rot: -8, sy: 1 };
          var p = Math.floor((u - 650) / 110), fr = ((u - 650) % 110) / 110, key = KEYS[(p + c) % KEYS.length];
          m.hr = [key[0], key[1] - Math.sin(fr * Math.PI) * 12];
          m.look = [3, 1]; m.eyes = u < 820 ? 'squint' : 'open';
          if (typed(c, t)[2]) { m.eyes = 'wide'; m.mouth = 'o'; m.look = [4, 0]; }      // 敲错了一位
        } else {
          k = seg(u, 1250, 1500); m.hl = mix(READ, TRAY, k); m.hr = mix(KEYS[(5 + c) % KEYS.length], HR0, k);
          m.held = { at: m.hl, rot: -8 * (1 - k), sy: 1 - .75 * k, o: u > 1480 ? 0 : 1 }; m.r = 4 * (1 - k); m.look = [-2 * k, 0];
        }
      }
    }
    // ② 转折：趴到桌上叹气；手机震、弹出 Pearnly 的消息，一下坐直
    function act2(t, m) {
      m.bags = 1; m.mouth = 'wavy';
      var down = seg(t, 4800, 5250) * (1 - seg(t, 5650, 5900, back));
      m.y = HOME[1] + 7 + 32 * down; m.sy = 1 - .07 * down; m.eyes = down > .5 ? 'closed' : 'open';
      m.hl = mix(HL0, [548, 426], seg(t, 4800, 5200)); m.hr = mix(HR0, [726, 426], seg(t, 4800, 5200));
      if (t >= 5650) { m.eyes = 'wide'; m.mouth = 'o'; m.look = [-4, 3]; m.excl = seg(t, 5650, 5820, back); }
    }
    // ③ 现在：敲一下键盘开屏，拿起手机对着单据堆一张张拍，最后一口气全进去
    function act3(t, m) {
      var tap = seg(t, 6450, 6700, lin);
      if (tap > 0 && tap < 1) m.hr = [650, 424 - Math.sin(tap * Math.PI) * 14];
      var reach = seg(t, 7050, 7350), lift = seg(t, 7350, 7700);
      m.hl = mix(mix(HL0, PHONE_DESK, reach), PHONE_HOLD, lift);
      m.x -= 52 * lift; m.y -= 14 * lift; m.r = -7 * lift; m.phone = t < 7350 ? 'flat' : 'up';
      var kick = bump(t, SNAP.slice(0, 5), 180) + .5 * bump(t, SNAP.slice(5), 70);
      m.hl[1] -= 5 * kick; m.y -= 3 * kick;
      if (t > 7700 && t < 9950) m.look = [-3, -2];
      if (t >= 9950) { m.look = [4, -1]; m.mouth = t < 10700 ? 'o' : 'big'; m.eyes = t < 10700 ? 'wide' : 'happy'; }
      var pump = seg(t, 10900, 11150) * (1 - seg(t, 11300, 11500));
      if (pump > 0) m.hr = mix(m.hr, [744, 324 - 12 * Math.sin(seg(t, 11050, 11300, lin) * Math.PI * 2)], pump);
    }
    // ④ 完成：坐回去放下手机，端起咖啡喝一口，朝你挥手
    function act4(t, m) {
      m.eyes = 'happy'; m.mouth = 'big';
      var sit = seg(t, 11500, 11950);
      m.x -= 52 * (1 - sit); m.y -= 14 * (1 - sit); m.r = -7 * (1 - sit);
      m.hl = t < 11950 ? mix(PHONE_HOLD, PHONE_DESK, sit) : mix(PHONE_DESK, HL0, seg(t, 11950, 12150));
      m.phone = t < 11950 ? 'up' : 'flat';
      var handle = [MUG_DESK[0] + 24, MUG_DESK[1] - 20], sip = [702, 374];
      var a = seg(t, 11950, 12350), b = seg(t, 12350, 12700), c = seg(t, 13100, 13450), d = seg(t, 13450, 13650);
      m.hr = t < 13450 ? mix(mix(mix(HR0, handle, a), sip, b), handle, c) : mix(handle, HR0, d);
      if (t >= 12350 && t < 13450) m.mug = { hand: m.hr, rot: -26 * b * (1 - c) };
      m.r += -5 * b * (1 - c);
      if (t >= 12600 && t < 13150) m.mouth = 'smile';
      if (t >= 13450) {
        var w = seg(t, 13450, 13750), wave = Math.sin(seg(t, 13750, END, lin) * Math.PI * 4) * 16;
        m.hl = mix(m.hl, [494 + wave, 262], w); m.eyes = t < 13650 ? 'happy' : 'open';
      }
    }

    function limb(a, b, bend) {
      var dx = b[1] - a[1], dy = a[0] - b[0], d = Math.sqrt(dx * dx + dy * dy) || 1;
      return 'M' + f(a[0]) + ' ' + f(a[1]) + 'Q' + f((a[0] + b[0]) / 2 + dx / d * bend) + ' ' + f((a[1] + b[1]) / 2 + dy / d * bend) + ' ' + f(b[0]) + ' ' + f(b[1]);
    }
    function bp(m, px, py) {
      var a = m.r * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
      px *= K; py *= K * m.sy;
      return [m.x + px * c - py * s, m.y + px * s + py * c];
    }
    function drawMascot(m, t) {
      set(E.body, 'transform', 'translate(' + f(m.x) + ' ' + f(m.y) + ') rotate(' + f(m.r) + ') scale(' + K + ' ' + f2(K * m.sy) + ')');
      for (var e in EYES) op(EYES[e], e === m.eyes ? 1 : 0);
      for (var mo in MOUTHS) op(MOUTHS[mo], mo === m.mouth ? 1 : 0);
      var blink = m.eyes === 'open' && (t % 2900) < 120 ? .12 : 1;
      set(EYES.open, 'transform', blink === 1 ? '' : 'translate(0 -72) scale(1 .12) translate(0 72)');
      set(E.look, 'transform', 'translate(' + f(m.look[0]) + ' ' + f(m.look[1]) + ')');
      op(E.bags, m.bags * .9);
      var sp = (t % 900) / 900;
      op(E.sweat, m.sweat * (sp < .15 ? sp / .15 : 1 - (sp - .15) / .85)); move(E.sweat, 0, 14 * sp);
      op(E.excl, m.excl); set(E.excl, 'transform', 'translate(2 -128) scale(' + f2(Math.max(.01, m.excl)) + ') translate(-2 128)');
      var sl = bp(m, -38, -50), sr = bp(m, 38, -50);
      set(E.armL, 'd', limb(sl, m.hl, 12)); set(E.armR, 'd', limb(sr, m.hr, -12));
      set(E.handL, 'cx', f(m.hl[0])); set(E.handL, 'cy', f(m.hl[1])); set(E.handR, 'cx', f(m.hr[0])); set(E.handR, 'cy', f(m.hr[1]));
      if (m.held) { op(E.held, m.held.o === undefined ? 1 : m.held.o); move(E.held, m.held.at[0] - 22, m.held.at[1] + 6, ' rotate(' + f(m.held.rot) + ') scale(1 ' + f2(m.held.sy) + ')'); }
      else op(E.held, 0);
      return { sl: sl, sr: sr };
    }

    // ---- 每一帧 ----
    function render(t) {
      var m = pose(t), night = t < T3 ? seg(t, 2700, 3900) : 0, dusk = t < T3 ? seg(t, 1500, 2700) : 0;
      drawMascot(m, t);

      // 窗外：白天 → 黄昏 → 夜里（第二天早上又是白天）；太阳落下、月亮升起、星星眨眼，第二天飞过一只鸟
      op(E.dusk, dusk); op(E.nightSky, night); op(E.starG, night);
      E.stars.forEach(function (s, i) { op(s, .55 + .45 * Math.sin(t / 260 + i * 1.7)); });
      var sun = t < T3 ? mix([286, 96], [196, 272], seg(t, 300, 3300)) : mix([262, 106], [288, 90], seg(t, T3, END, lin));
      move(E.sun, sun[0], sun[1]);
      op(E.moon, t < T3 ? seg(t, 3300, 4100) : 0); var moon = mix([150, 196], [176, 104], seg(t, 3300, 5800)); move(E.moon, moon[0], moon[1]);
      move(E.cloud1, 100 + (t * .012) % 300, 120); move(E.cloud2, 80 + (t * .008 + 160) % 300, 200, ' scale(.7)');
      var bird = seg(t, 8200, 9600, lin);
      op(E.bird, bird > 0 && bird < 1 ? 1 : 0);
      move(E.bird, 96 + 270 * bird, 132 - 30 * bird + Math.sin(bird * 12) * 4, ' scale(1 ' + (Math.floor(t / 110) % 2 ? '-.6' : '1') + ')');

      // 钟：以前一下午转过去十几个小时，现在只走了十几分钟
      var h = t < T2 ? 9 + 11.5 * seg(t, 0, T2) : t < T3 ? 20.5 + .5 * seg(t, T2, T3, lin) : t < T4 ? 9 + .2 * seg(t, T3, T4, lin) : 9.2 + .05 * seg(t, T4, END, lin);
      set(E.hour, 'transform', 'rotate(' + f((h % 12) * 30) + ' 520 110)'); set(E.min, 'transform', 'rotate(' + f((h % 1) * 360) + ' 520 110)');

      // 夜里屋里暗下来，台灯“啪”地亮起，屏幕的光照在脸上
      op(E.night, .36 * night);
      var lampOn = t >= 3700 && t < T3 && !(t > 3740 && t < 3800);
      op(E.light, lampOn ? .85 : 0); set(E.bulb, 'fill', lampOn ? '#fff1a0' : '#fff7d1'); op(E.glow, .5 * night);

      // 单据堆与“已录”筐
      var N = pileN(t);
      E.slabs.forEach(function (s, i) { op(s, i < N ? 1 : 0); });
      op(E.ptop, N > 0 ? 1 : 0); move(E.ptop, 0, DESK - N * SLAB - SLAB);
      var D = count(C1, t, 1500);
      E.tsheets.forEach(function (s, i) { op(s, i < D ? 1 : 0); });
      // 同事的手：从画面左边伸进来，把两张新单据压到堆顶，松手缩回去
      var hk = -1;
      FALL.forEach(function (F, i) { if (t >= F && t < F + 650 && t < T3) hk = i; });
      op(E.helper, hk >= 0 ? 1 : 0);
      if (hk >= 0) {
        var hu = t - FALL[hk], top = DESK - pileN(FALL[hk] + 339) * SLAB;
        var hx = hu < 300 ? -160 + 430 * out(hu / 300) : hu < 380 ? 270 : 270 - 450 * io((hu - 380) / 270);
        var hy = top - 22 + (hu >= 300 && hu < 380 ? 4 * Math.sin((hu - 300) / 80 * Math.PI) : 0);
        move(E.helper, hx, hy);
        Array.prototype.forEach.call(E.helper.querySelectorAll('rect'), function (r, i) { if (i < 2) op(r, hu < 340 ? 1 : 0); });
      }

      // 咖啡：以前凉了、空杯越摆越多；现在是一杯热的，最后端起来喝
      op(E.mug2, t < T3 ? seg(t, 2300, 2500, back) : 0); op(E.mug3, t < T3 ? seg(t, 3900, 4100, back) : 0);
      op(E.steam, t < T3 ? 1 - seg(t, 900, 1700) : 1); move(E.steam, Math.sin(t / 260) * 2.5, -((t / 30) % 6));
      if (m.mug) move(E.mug, m.mug.hand[0] - 24, m.mug.hand[1] + 20, ' rotate(' + f(m.mug.rot) + ' 24 -20)'); else move(E.mug, MUG_DESK[0], MUG_DESK[1]);

      // 手机：桌上震动、弹出 Pearnly 的 LINE 消息；拿起来对着单据堆拍
      var buzzing = t >= 5350 && t < 5950 && ((t - 5350) % 400) < 270;
      op(E.pflat, m.phone === 'flat' ? 1 : 0); move(E.pflat, buzzing ? Math.sin(t / 12) * 2.6 : 0, 0);
      op(E.buzz, buzzing ? 1 : 0);
      var lit = t >= 5350 && t < T3;
      op(E.pglow, lit ? .9 : 0); set(E.pscreen, 'fill', lit ? '#dff7e8' : '#2a2c33');
      var bub = t >= 5400 && t < T3 ? seg(t, 5400, 5600, back) : 0;
      op(E.bubble, bub); set(E.bubble, 'transform', 'translate(478 346) scale(' + f2(Math.max(.01, bub)) + ')');
      var upK = m.phone === 'up' ? (t < T4 ? seg(t, 7350, 7480) : 1 - seg(t, 11800, 11950)) : 0;
      var pc = [m.hl[0] - 50, m.hl[1] - 6];
      op(E.pup, upK > .02 ? 1 : 0); move(E.pup, pc[0], pc[1], ' rotate(-6) scale(1 ' + f2(Math.max(.05, upK)) + ')');
      var snapD = -1, snapI = -1;
      SNAP.forEach(function (s, i) { if (t >= s && (snapD < 0 || t - s < snapD)) { snapD = t - s; snapI = i; } });
      op(E.shutter, snapD >= 0 && snapD < 140 ? 1 - snapD / 140 : 0);
      var okK = snapD >= 120 && snapD < 520 ? seg(snapD, 120, 260, back) * (1 - seg(snapD, 420, 520)) : 0;
      op(E.ok, okK); set(E.ok, 'transform', 'translate(38 -16) scale(' + f2(Math.max(.01, okK)) + ')');
      op(E.vdoc, N > 0 ? 1 : 0); move(E.vdoc, snapD >= 140 && snapD < 320 ? 34 * (1 - seg(snapD, 140, 320)) : 0, 0);
      var big = snapI >= 0 && snapI < 5, bd = snapD >= 0 && snapD < 220 ? 1 - snapD / 220 : 0;
      op(E.burst, big ? bd : bd * .5); move(E.burst, pc[0], pc[1] + 34, ' scale(' + f2(.6 + .6 * (1 - bd)) + ')');
      op(E.flash, snapD >= 0 && snapD < 160 ? (big ? .3 : .1) * (1 - snapD / 160) : 0);
      var flow = t >= T3 ? seg(t, 9950, 10150) * (1 - seg(t, 11050, 11300)) : 0, s1 = [pc[0] + 40, pc[1] - 14];
      op(E.stream, .75 * flow);
      if (flow > 0) {
        set(E.stream, 'd', 'M' + f(s1[0]) + ' ' + f(s1[1]) + 'Q' + f((s1[0] + TABLE[0]) / 2) + ' ' + f(Math.min(s1[1], TABLE[1]) - 210) + ' ' + TABLE[0] + ' ' + TABLE[1]);
        set(E.stream, 'stroke-dashoffset', String(f(-(t / 9) % 130)));
      }

      // 拍下的单据飞进手机，数据从手机飞进屏幕
      E.fly.forEach(function (el, k) {
        var d = t - SNAP[k];
        if (d < 0 || d >= 320) { op(el, 0); return; }
        var p = io(d / 320), from = [296, DESK - (ITEMS - k) * SLAB - 6], at = arc(from, pc, p, 60);
        op(el, d > 260 ? 1 - (d - 260) / 60 : 1); move(el, at[0], at[1], ' rotate(' + f(-24 * p) + ') scale(' + f2(1 - .72 * p) + ')');
      });
      E.chips.forEach(function (el, k) {
        var d = t - SNAP[k] - 260;
        if (d < 0 || d >= 420) { op(el, 0); return; }
        var p = io(d / 420), a0 = [pc[0] + 40, pc[1] - 14], c0 = [(a0[0] + TABLE[0]) / 2, Math.min(a0[1], TABLE[1]) - 210];
        var at = [(1 - p) * (1 - p) * a0[0] + 2 * p * (1 - p) * c0[0] + p * p * TABLE[0], (1 - p) * (1 - p) * a0[1] + 2 * p * (1 - p) * c0[1] + p * p * TABLE[1]];
        op(el, d < 60 ? d / 60 : d > 360 ? (420 - d) / 60 : 1); move(el, at[0], at[1], ' scale(' + f2(.8 + .4 * Math.sin(p * Math.PI)) + ')');
      });

      // 屏幕：以前是旧表格（逐字敲、敲错删掉重敲）；第二天先黑屏，敲一下开机进 Pearnly，一行行长出来、打勾，最后盖“已入账”
      op(E.old, t < T3 ? 1 : 0); op(E.off, t >= T3 && t < 6900 ? 1 : 0);
      var boot = seg(t, 6650, 6900);
      op(E.boot, t >= 6650 && t < 7000 ? 1 - seg(t, 6900, 7000) : 0);
      set(E.boot, 'transform', 'translate(0 292) scale(1 ' + f2(1 + boot * 40) + ') translate(0 -292)');
      op(E.app, t >= T3 ? seg(t, 6850, 7050) : 0);
      var cursor = null, err = false;
      for (var c = 0; c < 3; c++) {
        var row = typed(c, t), blink = Math.floor(t / 260) % 2 === 0;
        if (row[3] >= 0) { cursor = [c, row[3]]; err = row[2]; }
        text(E.oldS[2 + c], row[0] + (row[3] === 0 && blink ? '|' : ''));
        text(E.oldA[2 + c], row[1] + (row[3] === 1 && blink ? '|' : ''));
      }
      op(E.cell, cursor ? 1 : 0);
      if (cursor) { set(E.cell, 'y', String(249 + cursor[0] * 19 + 2 * 19 - 2)); set(E.cell, 'x', cursor[1] ? '1040' : '886'); set(E.cell, 'width', cursor[1] ? '92' : '154'); }
      op(E.typo, err ? 1 : 0);
      var arrived = count(ARRIVE, t), fr = ARRIVE.map(function (a) { return out(c01((t - a) / 260)); });
      E.rows.forEach(function (r, k) {
        if (t < T3 || fr[k] <= 0) { op(r.g, 0); return; }
        var shift = 0; for (var j = k + 1; j < ITEMS; j++) shift += fr[j];
        op(r.g, fr[k]); move(r.g, 0, 22 * (shift - (1 - fr[k])));
        set(r.tick, 'stroke-dashoffset', String(f2(1 - c01((t - ARRIVE[k] - 120) / 220))));
        op(r.hl, fr[k] * (1 - c01((t - ARRIVE[k] - 350) / 700)));
      });
      text(E.count, arrived + ' / ' + ITEMS);
      var st = seg(t, 11550, 11900, back);
      op(E.stamp, t >= 11550 ? c01((t - 11550) / 150) : 0); set(E.stamp, 'transform', 'translate(1012 306) scale(' + f2(1.6 - .6 * st) + ')');

      // 小特效：叹气、“啊”、星星
      var puff = t < T3 ? seg(t, 5000, 5200) * (1 - seg(t, 5450, 5650)) : seg(t, 12900, 13050) * (1 - seg(t, 13250, 13450));
      var mouth = bp(m, 14, -48), rise = t < T3 ? seg(t, 5000, 5650, lin) : seg(t, 12900, 13450, lin);
      op(E.puff, puff); move(E.puff, mouth[0] + 10 + 14 * rise, mouth[1] - 6 - 30 * rise, ' scale(' + f2(.7 + .5 * rise) + ')');
      E.sparks.forEach(function (el, i) {
        var k = seg(t, 13500 + i * 90, 13750 + i * 90, back), tw = 1 + .15 * Math.sin(t / 140 + i);
        var base = el._base || (el._base = el.getAttribute('transform'));
        op(el, k); set(el, 'transform', base + ' scale(' + f2(Math.max(.01, k) * tw) + ') rotate(' + f((t / 20 + i * 40) % 360) + ')');
      });

      // 转场：黄色记号笔从左刷到右
      move(E.swipe, -2500 + 4200 * seg(t, 5900, 6600, lin), 0);

      // 下面一行手写字：按剧情逐字写出来
      var cap = '';
      CAPS.forEach(function (c) { if (t >= c[0]) cap = Array.from(c[1]).slice(0, Math.max(0, Math.floor((t - c[0]) / 40))).join(''); });
      text(capEl, cap || ' ');
    }

    // 手机上只取中间一段（单据堆 → 屏幕）
    function fit() {
      var view = svg.getAttribute(card.clientWidth < 640 ? 'data-view-sm' : 'data-view'), v = view.split(' ');
      svg.setAttribute('viewBox', view); set(E.frame, 'x', v[0]); set(E.frame, 'width', v[2]);
    }
    svg._render = render;   // 调试：在控制台里 document.querySelector('.st')._render(毫秒) 跳到任一帧
    fit();
    window.addEventListener('resize', fit);
    if (reduce) { render(END); return; }

    // ---- 播放：出现在屏幕里才开始，滑走或切到别的标签页就暂停，播完停在最后一帧，可以再看一遍 ----
    var start = 0, elapsed = 0, raf = 0, playing = false, done = false, visible = false;
    function frame(now) {
      raf = 0;
      if (!playing) return;
      var t = now - start;
      if (t >= END) { t = END; playing = false; done = true; }
      render(t);
      if (playing) raf = requestAnimationFrame(frame);
      else { replay.hidden = false; requestAnimationFrame(function () { replay.classList.add('show'); }); }
    }
    function play() { if (playing || done || !visible || document.hidden) return; playing = true; start = performance.now() - elapsed; raf = requestAnimationFrame(frame); }
    function pause() { if (!playing) return; playing = false; elapsed = performance.now() - start; if (raf) cancelAnimationFrame(raf); raf = 0; }
    replay.addEventListener('click', function () {
      replay.classList.remove('show'); replay.hidden = true; done = false; elapsed = 0; render(0); play();
    });
    document.addEventListener('visibilitychange', function () { if (document.hidden) pause(); else play(); });
    render(0);
    if (!('IntersectionObserver' in window)) { visible = true; play(); return; }
    new IntersectionObserver(function (e) { visible = e[0].isIntersecting; if (visible) play(); else pause(); }, { threshold: .3 }).observe(svg);
  }

  // ---- 预约演示按钮：手机上直接打开预填好消息的 LINE 对话；电脑上留在加好友页（扫码） ----
  function lineMessages() {
    if (!/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return;
    document.querySelectorAll('[data-line-message]').forEach(function (a) { a.href = a.getAttribute('data-line-message'); });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); mascots(); story(); peel(); lineMessages(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
