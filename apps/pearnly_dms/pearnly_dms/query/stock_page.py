# -*- coding: utf-8 -*-
"""库存停放报表的下载页（卡片「下载文件」在手机系统浏览器里打开）：勾选文件 → 分享或下载。

用户 11-27：卡片上四个按钮要一个一个点，改成一个入口，可单选、多选、全选。LINE 官方号发不了文件消息，文件只能走地址；
「分享」走手机系统的分享面板（Web Share），按钮上的数字始终等于勾选数：
只勾一个、浏览器又肯分享这个文件时分享文件本身；其余情况分享一条带地址的文字（标题 + 这一页的地址，地址里带着勾选的那几个，
对方打开只看到这几个文件）。三星真机 11-27 实测的两条限制决定了这个分法：安卓 Chrome 只放行 PDF（Excel / 压缩包 `canShare`
说可以、`share` 却拒绝），LINE 一次只收一个文件（两个以上文件时分享面板里没有 LINE）。浏览器没有分享功能时不出按钮。
下载：勾几个就连续下几个文件（每个文件一个隐藏框架去取签名地址，不打压缩包——用户 11-27：不要打包）。

不是产品 Web 的一页：没有登录态、不进 Desk，只凭 24 小时签名地址打开，所以是一份静态页：样式直接引 Frappe 官方网站样式表
（按钮 `btn btn-primary` / `btn btn-default`、原生复选框），自己只写排版；文字全部来自清单（生成报表时按发起人的 LINE 语言取好），页面本身不带文案。
"""

from __future__ import annotations

import json
from html import escape
from typing import Dict

# 只管这一页的排版（行、勾选框在右、底部按钮栏）；颜色、字体、按钮与勾选框的样子和尺寸都来自 Frappe 官方样式表（`btn btn-primary` /
# `btn btn-default`、原生复选框），不自己定（用户 11-27：不要品牌紫，按钮尺寸样式用官方原生组件）。
_CSS = """
.sr-page{width:100%;max-width:480px;margin:0 auto;padding:20px 16px 96px}
.sr-page h1{font-size:var(--text-xl,20px);margin:0 0 4px}
.sr-page p{margin:0 0 12px;color:var(--text-muted,#525252)}
.sr-page label{display:flex;align-items:center;gap:12px;width:100%;margin:0;padding:14px 4px;border-bottom:1px solid var(--border-color,#ededed);font-weight:inherit}
.sr-page label.all{font-weight:var(--weight-semibold,600)}
.sr-page label span{flex:1}
.sr-page label input{flex:none;margin:0}
.sr-page small{display:block;color:var(--text-muted,#7c7c7c)}
.sr-bar{position:fixed;left:0;right:0;bottom:0;background:var(--bg-color,#fff);border-top:1px solid var(--border-color,#ededed);padding:12px 16px calc(12px + env(safe-area-inset-bottom))}
.sr-bar div{max-width:480px;margin:0 auto;display:flex;gap:8px}
.sr-bar .btn{flex:1;margin:0}
[hidden]{display:none!important}
"""

_JS = """
(function(){
var data=JSON.parse(document.getElementById('data').textContent),files=data.files;
var boxes=[].slice.call(document.querySelectorAll('input[data-i]')),all=document.getElementById('all');
var share=document.getElementById('share'),down=document.getElementById('down'),blobs=[];
function picked(){return boxes.filter(function(b){return b.checked}).map(function(b){return +b.getAttribute('data-i')})}
var shown=boxes.length;
var pdfOnly=/Chrome\\//.test(navigator.userAgent)&&!/CriOS/.test(navigator.userAgent);
function asFile(list){
  if(list.length!==1||!navigator.canShare)return false;
  var i=list[0];if(!blobs[i]||(pdfOnly&&files[i].type!=='application/pdf'))return false;
  try{return navigator.canShare({files:[blobs[i]]})?[blobs[i]]:false}catch(err){return false}}
function refresh(){
  var list=picked();all.checked=list.length===shown;
  down.disabled=!list.length;down.querySelector('span').textContent=list.length;
  share.hidden=!navigator.share;share.disabled=!list.length;share.querySelector('span').textContent=list.length}
boxes.forEach(function(b){b.addEventListener('change',refresh)});
all.addEventListener('change',function(){boxes.forEach(function(b){b.checked=all.checked});refresh()});
down.addEventListener('click',function(){
  var list=picked();
  list.forEach(function(i,n){setTimeout(function(){
    var frame=document.createElement('iframe');frame.hidden=true;frame.src=files[i].url;document.body.appendChild(frame)},n*800)})});
share.addEventListener('click',function(){
  var list=picked();if(!list.length)return;
  var one=asFile(list);
  var what=one?{files:one}:{title:data.title,text:data.title+'\\n'+data.subtitle,url:data.page+'&k='+list.join(',')+'&openExternalBrowser=1'};
  navigator.share(what).catch(function(){})});
if(navigator.canShare&&window.File){files.forEach(function(f,i){
  if(!f.url)return;
  fetch(f.url).then(function(r){if(!r.ok)throw 0;return r.blob()}).then(function(b){
    blobs[i]=new File([b],f.name,{type:f.type});refresh()}).catch(function(){})})}
refresh();
})();
"""


def html_page(manifest: Dict, stylesheet: str = "", page_url: str = "", only=None) -> str:
    """``only``：只列这些序号的文件（别人分享过来的地址带着当时勾选的那几个）；序号始终是清单里的原序号。"""
    def text(key: str) -> str:
        return escape(str(manifest.get(key) or ""))

    rows = []
    for index, entry in enumerate(manifest.get("files") or []):
        if only is not None and index not in only:
            continue
        rows.append('<label><span>%s<small>%s</small></span><input type="checkbox" data-i="%d" checked></label>' % (
            escape(str(entry.get("label") or "")), escape(_size(entry.get("size"))), index))
    data = {"page": page_url, "title": manifest.get("title"), "subtitle": manifest.get("subtitle"),
            # 没列出的文件不带地址（分享给别人的只是勾选的那几个）；位置留空对象，序号不变
            "files": [{"url": entry.get("url"), "name": entry.get("name"), "type": entry.get("type")}
                      if only is None or index in only else {}
                      for index, entry in enumerate(manifest.get("files") or [])]}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return ("<!doctype html><html lang=%s><head><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\"><meta name=robots content=noindex>"
            "<title>%s</title>%s<style>%s</style></head><body><div class=sr-page><h1>%s</h1><p>%s</p>"
            "<label class=all><span>%s</span><input type=checkbox id=all checked></label>%s</div>"
            "<div class=sr-bar><div><button id=share class=\"btn btn-primary btn-md\" hidden>%s <span></span></button>"
            "<button id=down class=\"btn btn-default btn-md\">%s <span></span></button></div></div>"
            "<script type=application/json id=data>%s</script><script>%s</script></body></html>"
            % (text("lang") or "th", text("title"),
               ('<link rel=stylesheet href="%s">' % escape(stylesheet)) if stylesheet else "", _CSS, text("title"), text("subtitle"), text("select_all"),
               "".join(rows), text("share"), text("download"), payload, _JS))


def _size(value) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{max(1, round(size / 1024))} KB"


__all__ = ["html_page"]
