// 无头 Chrome 的调试端口：起 Chrome 时用 `--remote-debugging-port=0`（系统分配空闲端口），Chrome 把实际端口写进本次新建的
// profile 里的 DevToolsActivePort；profile 是刚建的空目录，读到的端口只能属于这个进程。
//
// 以前各驱动自己在 9300–9899 / 9900–9989 里随机挑一个：流程 jobs 3 并行再加上别的窗口同时在跑的 Chrome，两格常挑到同一个端口，
// 后起的那个绑不上，驱动却连到了别格的 Chrome 上；那一格跑完把自己的 Chrome 关掉，这一格就报 "debug connection closed
// (page hung, browser hung)"，或者在别格的 Chrome 里打开页面（域名解析规则、Cookie 都是别格的）。11-16 列车 A、D 并行几轮
// 反复挂的 upload-on-send、mrerp-login、company.actions 等格都是这样，串行补跑就过。
//
//   const chrome = spawn(chromePath, ["--headless=new", DEVTOOLS_PORT_FLAG, `--user-data-dir=${profile}`, ...]);
//   const info = await devtoolsVersion(profile, chrome);   // /json/version：webSocketDebuggerUrl 等；起不来返回 null
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const DEVTOOLS_PORT_FLAG = "--remote-debugging-port=0";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function devtoolsVersion(profile, chrome, tries = 150) {
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < tries; i++) {  // 每 200 毫秒看一次，默认最多 30 秒（假摄像头文件要先读进来）
    if (chrome && chrome.exitCode !== null) return null;
    try {
      if (existsSync(portFile)) {
        const port = Number(readFileSync(portFile, "utf8").split("\n")[0]);
        if (port > 0) return await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
      }
    } catch { /* 文件写到一半或端口还没开 */ }
    await sleep(200);
  }
  return null;
}
