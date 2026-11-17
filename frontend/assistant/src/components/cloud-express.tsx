// 「云端 Express」（用户 11-16 同意的示意；施工短版步 E）：CC 桌面版「设置 → 连接」里 Express 那一组下面一行。
//
//   CloudExpressRow     — 「云端 Express」+ 主机，下面一行状态（未连接 / 正在连接 / 已连接 / Express 登录失败 / 账号已在别处登录），
//                         右边「连接」，有连接时行尾「···」：重新查找、断开连接。连上后云端那份 CC 报上来的位置是只读行（没有「添加文件夹」）；
//                         桥现在还没有这条数据（places 恒为空），只留结构。状态只读桥的 cloud_status，失败不自动重试。
//   CloudExpressConnect — 点「连接」的弹窗（同 MR.ERP 连接弹窗）：地址、账号 +「取消 / 连接」。地址、账号由桥预填
//                         （cloud_prefill：已存的连接，或本机 TSplus 的 Session.rdp），看得见、能改，读不到就空着。
// 密码不在网页上收（ADR-026 第 7 条，同 MR.ERP / Express 授权；tests/test_mrerp_login_pages.py 守着「对话页没有密码框」）：
// 点「连接」后桥（cloud_connect）弹 CC 桌面版原生小窗收账号和密码（账号预填这里的），只存这台电脑的凭据管理器；
// 页面只拿到成没成。旧版 CC 桌面版没有这几个桥动作（cloud_status 回 ok: false），不出这一行。
//   CloudPairing        — 连接成功后（或「···」→ 云端安装命令）行下面一块：桥 cloud_pair_begin 回的那一行命令（等宽、整行可选、框内换行）、
//                         复制、剩余时间（按 expires 倒计时）；到 0 或配对被拒显示「已过期」+「重新生成」（再调 cloud_pair_begin）；
//                         cloud_pair_status 报 paired 即收起。命令（含一次性令牌）只在这块的 state 里，不存、不记日志、不发服务端，卸载即丢。

import { CloudIcon, FolderIcon, MoreHorizontalIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockFooter,
} from "@/components/ui/code-block";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Marquee } from "@/components/ui/marquee";
import { Spinner } from "@/components/ui/spinner";
import { op, st, t } from "@/i18n";
import type { Desktop } from "@/lib/desktop";

export type CloudState = "idle" | "connecting" | "connected" | "login_failed" | "replaced";
export type CloudStatus = {
  state: CloudState;
  link: { host: string; port: number; user: string; name?: string } | null;
  places: string[];
};

const DEFAULT_PORT = 3389;

/** 桥的 cloud_status 回包 → 这一行要的状态；旧版桥（没有这个动作）回 null，不出这一行。 */
export function cloudStatus(raw: unknown): CloudStatus | null {
  const value = (raw ?? {}) as { ok?: boolean; state?: string; link?: CloudStatus["link"]; places?: unknown };
  if (!value.ok) return null;
  const states: CloudState[] = ["idle", "connecting", "connected", "login_failed", "replaced"];
  return {
    state: states.includes(value.state as CloudState) ? (value.state as CloudState) : "idle",
    link: value.link && typeof value.link.host === "string" ? value.link : null,
    places: Array.isArray(value.places) ? value.places.filter((p): p is string => typeof p === "string") : [],
  };
}

/** 状态行：字 + 圆点颜色（同电脑列表的 cc-status-line）。 */
export function cloudStatusLine(state: CloudState): { text: string; tone: string } {
  if (state === "connecting") return { text: st.status.connecting, tone: "connecting" };
  if (state === "connected") return { text: st.status.online, tone: "online" };
  if (state === "login_failed") return { text: op.reason.login_failed, tone: "attention" };
  if (state === "replaced") return { text: op.reason.account_in_use, tone: "attention" };
  return { text: st.notConnected, tone: "offline" };
}

/** 地址框：主机，端口不是 3389 时写成「主机:端口」。 */
export const addressOf = (host: string, port?: number) => (host && port && port !== DEFAULT_PORT ? `${host}:${port}` : host);

/** 「主机:端口」拆开；没写端口回 ""（桥按 3389）。 */
export function splitAddress(value: string): { host: string; port: number | "" } {
  const text = value.trim();
  const found = /^([^:\s]+):(\d{1,5})$/.exec(text);
  return found ? { host: found[1], port: Number(found[2]) } : { host: text, port: "" };
}

/** 配对块的几种样子：准备中 / 命令就绪 / 已过期（到时或被拒，令牌已作废）/ 准备失败（桥的 code）。 */
export type Pair =
  | { phase: "preparing" }
  | { phase: "ready"; command: string; expires: number }
  | { phase: "expired" }
  | { phase: "failed"; code: string };

/** 桥的 cloud_pair_begin 回包 → 配对块；令牌只随命令走，不单独留。 */
export function pairOutcome(raw: unknown): Pair {
  const value = (raw ?? {}) as { ok?: boolean; command?: unknown; expires?: unknown; code?: unknown };
  if (value.ok && typeof value.command === "string" && value.command && typeof value.expires === "number") {
    return { phase: "ready", command: value.command, expires: value.expires };
  }
  return { phase: "failed", code: typeof value.code === "string" ? value.code : "" };
}

/** 准备失败的一行：宿主没起 = 未连接；no_package / download_failed / checksum / write_failed（及其他）= 安装文件备不好。 */
export const pairFailure = (code: string) => (code === "not_running" ? st.notConnected : st.cloudPackageFailed);

/** 剩余秒数（expires 是墙上时钟秒，同一台电脑）。 */
export const secondsLeft = (expires: number, now: number) => Math.max(0, Math.ceil(expires - now / 1000));

/** 「1:05」。 */
export const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

// map: cloud-express
/** 设置 → 连接里 Express 那一组下面的「云端 Express」一行（只在 CC 桌面版里）。 */
export const CloudExpressRow: FC<{ api: Desktop }> = ({ api }) => {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // 配对块：null = 不显示；数字变了 = 重新挂一块（重新生成命令）
  const [pairing, setPairing] = useState<number | null>(null);
  const reload = useCallback(() => void api.call("cloud_status").then((raw) => setStatus(cloudStatus(raw))), [api]);
  useEffect(() => {
    reload();
    return api.onChanged(reload);
  }, [api, reload]);
  if (!status) return null;
  const { link, state, places } = status;
  const line = cloudStatusLine(state);
  const pair = () => setPairing((n) => (n ?? 0) + 1);
  const disconnect = () => {
    setPairing(null);
    setBusy(true);
    void api.call("cloud_disconnect").then(() => {
      setBusy(false);
      reload();
    });
  };
  return (
    <>
      <div data-marquee-host="" className="cc-settings-row" data-slot="cc-cloud-express-row">
        <CloudIcon className="size-(--cc-icon-sm) shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-body">
            {link ? `${st.cloudExpress} · ${link.name || link.host}` : st.cloudExpress}
          </Marquee>
          <span className="cc-status-line text-caption" data-status={line.tone} data-slot="cc-cloud-express-status">
            <span className="cc-status-dot" />
            {line.text}
          </span>
        </div>
        {state !== "connected" && state !== "connecting" && (
          <Button variant="outline" disabled={busy} onClick={() => setOpen(true)} data-slot="cc-cloud-express-connect">
            {st.connect}
          </Button>
        )}
        {link && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t("more")} className="cc-tap">
                <MoreHorizontalIcon />
              </Button>
            </DropdownMenuTrigger>
            {/* 同设置里其他菜单：盖在设置弹窗（z 71）上面 */}
            <DropdownMenuContent align="end" className="z-(--cc-z-confirm) min-w-(--cc-menu-w-md)">
              <DropdownMenuItem onSelect={reload}>{st.findAgain}</DropdownMenuItem>
              {state === "connected" && (
                <DropdownMenuItem onSelect={pair} data-slot="cc-cloud-express-pair">
                  {st.installCommand}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={disconnect}>
                {st.disconnect}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {pairing !== null && <CloudPairing key={pairing} api={api} onDone={() => setPairing(null)} />}
      {/* 云端那份 CC 报上来的 Express 位置：只读（位置在云端那台电脑上改） */}
      {state === "connected" &&
        places.map((path) => (
          <div data-marquee-host="" key={path} className="cc-settings-row text-body" data-slot="cc-cloud-express-place">
            <FolderIcon className="size-(--cc-icon-sm) shrink-0 text-muted-foreground" />
            <Marquee className="min-w-0 flex-1 truncate" title={path}>
              {path}
            </Marquee>
          </div>
        ))}
      {open && (
        <CloudExpressConnect
          api={api}
          onClose={(connected) => {
            setOpen(false);
            if (connected) pair();
            reload();
          }}
        />
      )}
    </>
  );
};

/** 行下面的配对块：挂上即调 cloud_pair_begin；只有命令、复制、剩余时间，过期后「重新生成」。 */
export const CloudPairing: FC<{ api: Desktop; onDone: () => void }> = ({ api, onDone }) => {
  const [pair, setPair] = useState<Pair>({ phase: "preparing" });
  const [now, setNow] = useState(() => Date.now());
  const alive = useRef(true);
  const done = useRef(onDone);
  done.current = onDone;
  const begin = useCallback(() => {
    setPair({ phase: "preparing" });
    void api.call("cloud_pair_begin").then((raw) => {
      if (!alive.current) return;
      setNow(Date.now());
      setPair(pairOutcome(raw));
    });
  }, [api]);
  useEffect(() => {
    alive.current = true;
    begin();
    return () => {
      alive.current = false;
    };
  }, [begin]);
  // 配对有进展时桥发 changed：配好了收起；被拒（令牌已作废）按过期
  useEffect(
    () =>
      api.onChanged(() => {
        void api.call("cloud_pair_status").then((raw) => {
          const found = raw as unknown as { ok?: boolean; state?: string };
          if (!alive.current || !found.ok) return;
          if (found.state === "paired") done.current();
          else if (found.state === "failed") setPair((p) => (p.phase === "ready" ? { phase: "expired" } : p));
        });
      }),
    [api],
  );
  const left = pair.phase === "ready" ? secondsLeft(pair.expires, now) : 0;
  // 倒计时：只在命令就绪时每秒走一次，到 0 丢掉命令
  useEffect(() => {
    if (pair.phase !== "ready") return;
    if (left === 0) return setPair({ phase: "expired" });
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [pair.phase, left]);
  return (
    <div className="cc-cloud-pair" data-slot="cc-cloud-express-pairing" data-phase={pair.phase}>
      {pair.phase === "preparing" && <Spinner />}
      {pair.phase === "failed" && (
        <div className="cc-cloud-pair-line">
          <span role="alert" className="min-w-0 flex-1 text-danger text-body" data-slot="cc-cloud-express-pair-error">
            {pairFailure(pair.code)}
          </span>
          <Button variant="outline" onClick={begin} data-slot="cc-cloud-express-retry">
            {st.retry}
          </Button>
        </div>
      )}
      {pair.phase === "expired" && (
        <div className="cc-cloud-pair-line">
          <span className="min-w-0 flex-1 text-muted-foreground text-body" data-slot="cc-cloud-express-expired">
            {st.expired}
          </span>
          <Button variant="outline" onClick={begin} data-slot="cc-cloud-express-regenerate">
            {st.regenerate}
          </Button>
        </div>
      )}
      {pair.phase === "ready" && (
        <CodeBlock size="sm">
          <CodeBlockContent wrap className="select-all">
            {pair.command}
          </CodeBlockContent>
          <CodeBlockFooter>
            <span className="cc-cloud-pair-left" data-slot="cc-cloud-express-left">
              {clock(left)}
            </span>
            <CodeBlockActions>
              <CodeBlockCopyButton value={pair.command} />
            </CodeBlockActions>
          </CodeBlockFooter>
        </CodeBlock>
      )}
    </div>
  );
};

/** 「连接 云端 Express」弹窗：地址、账号由桥预填；密码由桥弹的原生小窗收，页面不经手。 */
export const CloudExpressConnect: FC<{ api: Desktop; onClose: (connected: boolean) => void }> = ({ api, onClose }) => {
  const [address, setAddress] = useState("");
  const [user, setUser] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void api.call("cloud_prefill").then((raw) => {
      const found = raw as unknown as { ok?: boolean; host?: string; port?: number; user?: string };
      if (!alive || !found.ok) return;
      // 人已经开始填了就不盖掉
      setAddress((now) => now || addressOf(found.host ?? "", found.port));
      setUser((now) => now || (found.user ?? ""));
    });
    return () => {
      alive = false;
    };
  }, [api]);
  const submit = async () => {
    setBusy(true);
    setError("");
    const { host, port } = splitAddress(address);
    const result = await api.call("cloud_connect", { host, port, user: user.trim() });
    setBusy(false);
    // 原生小窗里点了取消：留在弹窗，不报错
    if (result.cancelled) return;
    if (!result.ok) return setError(st.linkFailed);
    onClose(true);
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose(false)}>
      <DialogContent
        aria-describedby={undefined}
        data-slot="cc-cloud-express-dialog"
        className="z-(--cc-z-confirm) max-w-(--cc-dialog-w-sm)"
      >
        <DialogHeader>
          <DialogTitle>{`${st.connect} ${st.cloudExpress}`}</DialogTitle>
        </DialogHeader>
        <Field>
          <FieldLabel htmlFor="cc-cloud-express-address">{st.address}</FieldLabel>
          <Input
            id="cc-cloud-express-address"
            value={address}
            autoComplete="off"
            onChange={(e) => setAddress(e.target.value)}
            data-slot="cc-cloud-express-address"
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="cc-cloud-express-user">{st.account}</FieldLabel>
          <Input
            id="cc-cloud-express-user"
            value={user}
            autoComplete="off"
            onChange={(e) => setUser(e.target.value)}
            data-slot="cc-cloud-express-user"
          />
        </Field>
        <DialogFooter>
          {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
          <Button variant="outline" onClick={() => onClose(false)}>
            {t("cancel")}
          </Button>
          <Button disabled={busy || !address.trim()} onClick={() => void submit()}>
            {st.connect}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
