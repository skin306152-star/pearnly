# cdx_reindex_runner —— Express CDX 后台重建器(Harbour DBFCDX)

`companion/cdx_reindex.py` 驱动这个原生 exe,在写完 DBF 后后台重建 Express 账套的结构化
CDX 索引并按 Express 自己的索引 key 回查,等价 PACK/Reindex 的索引效果,但不开 Express、
不需用户值守。预编译 `cdx_reindex_runner.exe` 随仓库提交,`release.ps1` 直接拷进 dist 打包。

## ★ 必须 GTNUL 编译

默认 GTWIN(Windows console 驱动)在 companion 后台**无控制台子进程**里启动即 **hang 死**
(抓不到控制台)。必须用 **GTNUL**(无终端)编译。这是真机踩过的坑,改了重编务必保持。

## 重建步骤

工具链 = 自带 MinGW 的 Harbour 3.0.0(`D:\pearnly-tools\harbour-cdx-engine-*\hb30`):

```powershell
$hb = 'D:\pearnly-tools\harbour-cdx-engine-20260625-160424\hb30'
$env:PATH = "$hb\comp\mingw\bin;$hb\bin;$env:PATH"
$env:HB_INSTALL_PREFIX = $hb
Set-Location packaging\native
& "$hb\bin\hbmk2.exe" cdx_reindex_runner.prg -gtnul -static -comp=mingw -ocdx_reindex_runner.exe
```

产物为 32 位静态 exe(~1.2MB),作为独立子进程被调,位数与 64 位 companion.exe 无关。

## 用法

```
cdx_reindex_runner.exe <REINDEX|SEEK|TAGS> <账套目录> <表> [<标签> <KEY> <日志路径>]
```

可选参数可省略(NIL 防御);companion 走显式本地日志路径(不在共享账套留垃圾)。
