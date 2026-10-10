(function(){var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
var canvas=document.querySelector('canvas.dots');if(canvas&&canvas.getContext){var ctx=canvas.getContext('2d'),GAP=22,R=1,REACH=150,w=0,h=0,dpr=1,mouse={x:-9999,y:-9999,tx:-9999,ty:-9999},t0=performance.now();
function size(){dpr=Math.min(window.devicePixelRatio||1,2);w=innerWidth;h=innerHeight;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);canvas.style.width=w+'px';canvas.style.height=h+'px';ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#141413'}
function draw(now){var t=(now-t0)/1000;mouse.x+=(mouse.tx-mouse.x)*.12;mouse.y+=(mouse.ty-mouse.y)*.12;ctx.clearRect(0,0,w,h);
for(var y=0;y<h+GAP;y+=GAP){for(var x=GAP/2;x<w;x+=GAP){var wave=reduce?0:.5+.5*Math.sin(t*.7-(x+y)*.008),a=.10+wave*.06,r=R,px=x,py=y,dx=x-mouse.x,dy=y-mouse.y,d=Math.sqrt(dx*dx+dy*dy);
if(d<REACH){var k=1-d/REACH;k=k*k;a+=k*.45;r+=k*1.3;if(d>.1){px+=dx/d*k*7;py+=dy/d*k*7}}ctx.globalAlpha=a;ctx.beginPath();ctx.arc(px,py,r,0,6.2832);ctx.fill()}}
if(!reduce&&!document.hidden)requestAnimationFrame(draw)}
size();draw(performance.now());addEventListener('resize',function(){size();draw(performance.now())});
if(!reduce){addEventListener('pointermove',function(e){mouse.tx=e.clientX;mouse.ty=e.clientY},{passive:true});document.addEventListener('pointerleave',function(){mouse.tx=mouse.ty=-9999});
document.addEventListener('visibilitychange',function(){if(!document.hidden)requestAnimationFrame(draw)})}}
var scene=document.querySelector('.scene'),all=[].slice.call(document.querySelectorAll('.hm'));
function play(m,cls,ms){m.classList.remove(cls);void m.getBoundingClientRect();m.classList.add(cls);setTimeout(function(){m.classList.remove(cls)},ms||1200)}
function act(m){var k=m.getAttribute('class');if(k.indexOf('hm-ai')>=0||k.indexOf('hm-cc')>=0)return 'hm-laughing';if(k.indexOf('hm-dms')>=0)return 'hm-snap';if(k.indexOf('hm-erp')>=0)return 'hm-beep';return Math.random()<.5?'hm-hopping':'hm-cheer'}
window.pearnlyCheer=function(){all.forEach(function(m,i){setTimeout(function(){play(m,'hm-cheer',2000);play(m,'hm-laughing',2000)},i*120)})};
if(!reduce){setInterval(function(){var m=all[Math.floor(Math.random()*all.length)];if(m&&m.getBoundingClientRect().width)play(m,act(m),1400)},2600)}
all.forEach(function(m){m.style.cursor='pointer';m.addEventListener('click',function(){play(m,'hm-hopping',700);play(m,'hm-laughing',1400)})});
document.addEventListener('focusin',function(e){if(e.target.type==='password')document.body.classList.add('shy')});
document.addEventListener('focusout',function(e){if(e.target.type==='password')document.body.classList.remove('shy')});
var looks=[].slice.call(document.querySelectorAll('.hm .m-look')),px=0,py=0,q=false;
function upd(){q=false;if(document.body.classList.contains('shy'))return;looks.forEach(function(g){var r=g.closest('.hm').getBoundingClientRect();if(!r.width)return;
var dx=px-(r.left+r.width/2),dy=py-(r.top+r.height*.3),d=Math.sqrt(dx*dx+dy*dy)||1,k=Math.min(1,d/240)*(g.closest('.hm-mrerp')?3:g.closest('.hm-cc')?7:5);g.setAttribute('transform','translate('+(dx/d*k).toFixed(2)+' '+(dy/d*k).toFixed(2)+')')})}
if(!reduce)addEventListener('pointermove',function(e){px=e.clientX;py=e.clientY;if(!q){q=true;requestAnimationFrame(upd)}},{passive:true});
})();
(function(){
  // 入口第一页带过来的密码 / 找回密码（# 后面，见入口页脚本）：先从地址栏抹掉，再自动提交或打开找回密码页。
  // 登录页出错（密码不对等）时不自动提交，照常显示出错的登录框。
  var h=location.hash;
  if(/^#pn/.test(h)){history.replaceState(null,'',location.pathname+location.search);
    var f=document.getElementById('kc-form-login'),p=document.getElementById('password'),u=document.getElementById('username'),a=document.querySelector('.forgot a');
    if(h==='#pn-forgot'&&a){location.replace(a.href);return}
    if(h.indexOf('#pn=')===0&&f&&p&&u&&u.value&&!document.querySelector('.pn-field-error,.auth-message.error')){
      var parts=h.slice(4).split('&'),rm=document.getElementById('rememberMe');
      try{p.value=decodeURIComponent(parts[0])}catch(e){p.value=''}
      if(rm)rm.checked=parts.indexOf('rm=1')>0;
      if(p.value){f.submit();return}}
    document.documentElement.classList.remove('pn-auto')}
  var change=document.getElementById('who-change');
  if(change)change.addEventListener('click',function(){var who=document.getElementById('who'),u=document.getElementById('username');
    var input=document.createElement('input');input.className='input';input.id='username';input.name='username';input.type='text';input.autocomplete='username';
    input.style.marginBottom='10px';input.value='';u.remove();who.replaceWith(input);input.focus()});
  document.querySelectorAll('[data-toggle-password]').forEach(function(b){b.addEventListener('click',function(){
    var i=document.getElementById(b.getAttribute('data-toggle-password'));if(i)i.type=i.type==='password'?'text':'password'})});
})();
