// 「云端 Express」（第二阶段，用户 11-24 审定草图 express-cloud-连接页草图.html）：CC 桌面版「设置 → 连接 → 账本」里第三行。
//
//   CloudExpressRow     — 「云端 Express」，名下小字是云端主机名；右边「● 状态 + ···」（还没连过时是「连接」）。点行展开：云端那份
//                         CC 报上来的数据文件夹（一行一个，下面小字账套数）+「重新查找」「添加文件夹…」——同本地 Express 展开后的样子，
//                         区别只在「添加文件夹」是弹窗里手填云端主机上的路径（这台电脑选不到那里的文件夹）。「···」：重新登录、实操登录、
//                         云端安装命令（自动安装做好前的兜底）、主机 · 账号与云端 CC 版本（只读两行）、断开。
//   CloudExpressConnect — 连接弹窗，同 MR.ERP 一样一个弹窗里填地址、用户名、密码，点一次「连接」，正在连接 / 已连接 / 失败原因都写在弹窗里；
//                         输过一次就记住（桥存本机凭据管理器），以后开机自动连。桌面版没有 connect_dialog（旧版）时不出密码框，
//                         桥照旧弹原生小窗收。
//   CloudOperatorLogin  — 「实操登录」（界面术语同左上角「实操」；内部仍叫代录）：云端那本 Express 的用户名、密码，同一种弹窗，只存这台电脑（桥本机凭据管理器）。
//   CloudAddPlace       — 「添加文件夹…」：手填云端主机上的路径，云端那份 CC 去核（在不在、是不是 Express），结果写在弹窗里。
//   CloudPairing        — 云端安装命令块（兜底）：桥 cloud_pair_begin 回的一行命令、复制、剩余时间；过期「重新生成」；配好收起。
// 密码只经本机桥交出，不存页面状态以外的地方、不进地址栏、不发服务端；关弹窗即丢。

import { ChevronRightIcon, FolderIcon, MoreHorizontalIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockFooter,
} from "@/components/ui/code-block";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Marquee } from "@/components/ui/marquee";
import { Spinner } from "@/components/ui/spinner";
import { op, st, t } from "@/i18n";
import { type Desktop, useDesktopFeatures } from "@/lib/desktop";

export type CloudState = "idle" | "connecting" | "connected" | "login_failed" | "replaced";
export type CloudPlace = { path: string; ledgers: number };
export type CloudSummary = { version: string; members: string; scan: string; add: { id?: string; code?: string } };
export type CloudStatus = {
  state: CloudState;
  link: { host: string; port: number; user: string; name?: string } | null;
  places: CloudPlace[];
  remembered: boolean;
  reason: string;
  cloud: CloudSummary | null;
};

const DEFAULT_PORT = 3389;

/** 桥的 cloud_status 回包 → 这一行要的状态；旧版桥（没有这个动作）回 null，不出这一行。 */
export function cloudStatus(raw: unknown): CloudStatus | null {
  const value = (raw ?? {}) as {
    ok?: boolean; state?: string; link?: CloudStatus["link"]; places?: unknown; remembered?: unknown; reason?: unknown;
    cloud?: Partial<CloudSummary> | null;
  };
  if (!value.ok) return null;
  const states: CloudState[] = ["idle", "connecting", "connected", "login_failed", "replaced"];
  const places = (Array.isArray(value.places) ? value.places : [])
    .map((item) => (typeof item === "string" ? { path: item, ledgers: -1 } : (item as CloudPlace)))
    .filter((item): item is CloudPlace => !!item && typeof item.path === "string" && !!item.path);
  const cloud = value.cloud && typeof value.cloud === "object"
    ? { version: String(value.cloud.version ?? ""), members: String(value.cloud.members ?? ""), scan: String(value.cloud.scan ?? ""),
        add: value.cloud.add && typeof value.cloud.add === "object" ? value.cloud.add : {} }
    : null;
  return {
    state: states.includes(value.state as CloudState) ? (value.state as CloudState) : "idle",
    link: value.link && typeof value.link.host === "string" ? value.link : null,
    places, remembered: value.remembered === true, reason: typeof value.reason === "string" ? value.reason : "", cloud,
  };
}

/** 状态行：字 + 圆点颜色（同电脑列表的 cc-status-line）。登录失败写桥给的同一个原因（同连接弹窗，``loginReason``）。 */
export function cloudStatusLine(state: CloudState, reason = ""): { text: string; tone: string } {
  if (state === "connecting") return { text: st.status.connecting, tone: "connecting" };
  if (state === "connected") return { text: st.status.online, tone: "online" };
  if (state === "login_failed") return { text: loginReason(reason), tone: "attention" };
  if (state === "replaced") return { text: op.reason.account_in_use, tone: "attention" };
  return { text: st.notConnected, tone: "offline" };
}

/** 登录失败的原因（桥的 reason）：账号或密码不对 / 密码过期 / 账号不可用；认不出写「Express 登录失败」。 */
export const loginReason = (reason: string) =>
  (st.loginReason as Record<string, string>)[reason] ?? op.reason.login_failed;

/** 「添加文件夹」的结果（云端 CC 回的 code）→ 弹窗里一行；ok 回空串。 */
export const addPlaceFailure = (code: string) =>
  code === "ok" ? "" : code === "not_found" ? st.expressNotFound : st.pathNotUsable;

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

/** 跟着桥的 cloud_status：挂上取一次、桥说变了再取。 */
export function useCloudStatus(api: Desktop): [CloudStatus | null, () => void] {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const reload = useCallback(() => void api.call("cloud_status").then((raw) => setStatus(cloudStatus(raw))), [api]);
  useEffect(() => {
    reload();
    return api.onChanged(reload);
  }, [api, reload]);
  return [status, reload];
}

// map: cloud-express
/** 设置 → 连接 → 账本里的「云端 Express」一行（只在 CC 桌面版里）。 */
export const CloudExpressRow: FC<{ api: Desktop }> = ({ api }) => {
  const [status, reload] = useCloudStatus(api);
  const [dialog, setDialog] = useState<"" | "connect" | "operator" | "place">("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // 配对块：null = 不显示；数字变了 = 重新挂一块（重新生成命令）
  const [pairing, setPairing] = useState<number | null>(null);
  if (!status) return null;
  const { link, state, places, cloud } = status;
  const line = cloudStatusLine(state, status.reason);
  const pair = () => setPairing((n) => (n ?? 0) + 1);
  const disconnect = () => {
    setPairing(null);
    setBusy(true);
    void api.call("cloud_disconnect").then(() => {
      setBusy(false);
      reload();
    });
  };
  const rescan = () => void api.call("cloud_rescan").then(reload);
  return (
    <>
      <div data-marquee-host="" className="cc-settings-row" data-slot="cc-cloud-express-row">
        <Button
          variant="ghost"
          size="icon-sm"
          className="cc-ledger-caret cc-tap"
          aria-expanded={open}
          aria-label={st.cloudExpress}
          disabled={!link}
          onClick={() => setOpen((v) => !v)}
          data-slot="cc-cloud-express-toggle"
        >
          <ChevronRightIcon />
        </Button>
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-body">{st.cloudExpress}</Marquee>
          {link && (
            <Marquee as="div" className="truncate text-muted-foreground text-caption" data-slot="cc-cloud-express-host">
              {link.name || link.host}
            </Marquee>
          )}
        </div>
        {link && (
          <span className="cc-status-line text-body" data-status={line.tone} data-slot="cc-cloud-express-status">
            <span className="cc-status-dot" />
            {line.text}
          </span>
        )}
        {!link ? (
          <Button variant="outline" disabled={busy} onClick={() => setDialog("connect")} data-slot="cc-cloud-express-connect">
            {st.connect}
          </Button>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t("more")} className="cc-tap">
                <MoreHorizontalIcon />
              </Button>
            </DropdownMenuTrigger>
            {/* 同设置里其他菜单：盖在设置弹窗（z 71）上面 */}
            <DropdownMenuContent align="end" className="z-(--cc-z-confirm) min-w-(--cc-menu-w-md)">
              <DropdownMenuItem onSelect={() => setDialog("connect")} data-slot="cc-cloud-express-relogin">
                {st.relogin}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setDialog("operator")} data-slot="cc-cloud-express-operator">
                {st.operatorLogin}
              </DropdownMenuItem>
              {state === "connected" && (
                <DropdownMenuItem onSelect={pair} data-slot="cc-cloud-express-pair">
                  {st.installCommand}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="font-normal text-muted-foreground text-caption" data-slot="cc-cloud-express-detail">
                {`${link.name || link.host} · ${link.user}`}
                {cloud?.version && <div>{`CC ${cloud.version}`}</div>}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={disconnect}>
                {st.disconnect}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {pairing !== null && <CloudPairing key={pairing} api={api} onDone={() => setPairing(null)} />}
      {/* 展开：云端那份 CC 报上来的数据文件夹（小字账套数）+「重新查找」「添加文件夹…」，同本地 Express 展开后的样子 */}
      {open && link && (
        <div data-slot="cc-cloud-express-places">
          {places.map((place) => (
            <div data-marquee-host="" key={place.path} className="cc-settings-row text-body" data-slot="cc-cloud-express-place">
              <FolderIcon className="size-(--cc-icon-sm) shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <Marquee as="div" className="truncate" title={place.path}>
                  {place.path}
                </Marquee>
                {place.ledgers >= 0 && <div className="text-muted-foreground text-caption">{st.ledgerCount(place.ledgers)}</div>}
              </div>
            </div>
          ))}
          {!places.length && cloud?.members === "no_books" && (
            <p className="py-(--cc-space-12) text-muted-foreground text-body">{st.expressNotFound}</p>
          )}
          <div className="cc-settings-row justify-end">
            <Button variant="outline" disabled={state !== "connected"} onClick={rescan} data-slot="cc-cloud-express-rescan">
              {st.findAgain}
            </Button>
            <Button variant="outline" disabled={state !== "connected"} onClick={() => setDialog("place")}
              data-slot="cc-cloud-express-add-place">
              {st.addFolder}
            </Button>
          </div>
        </div>
      )}
      {dialog === "connect" && (
        <CloudExpressConnect
          api={api}
          onClose={(connected) => {
            setDialog("");
            // 云端那份 CC 还没报过状态（没装好）：给出安装命令（自动安装做好前的兜底）
            if (connected && !cloud) pair();
            reload();
          }}
        />
      )}
      {dialog === "operator" && <CloudOperatorLogin api={api} onClose={() => setDialog("")} />}
      {dialog === "place" && <CloudAddPlace api={api} onClose={() => { setDialog(""); reload(); }} />}
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

/** 连接弹窗：地址、用户名由桥预填（已存的连接，或本机 TSplus 的 Session.rdp），密码在这里填；新桌面版（connect_dialog）一个弹窗点一次
 *  「连接」，结果写在弹窗里；记住了的密码不用再填（桥用存的）。旧桌面版不出密码框，桥弹原生小窗收。 */
export const CloudExpressConnect: FC<{ api: Desktop; onClose: (connected: boolean) => void }> = ({ api, onClose }) => {
  const inline = !!useDesktopFeatures().connectDialog;
  const [status] = useCloudStatus(api);
  const [address, setAddress] = useState("");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  // 点「连接」那一刻的状态：重新登录时它还是「已连接」，要等桥换新连接后报来的新状态，不拿旧的当结果
  const before = useRef<CloudStatus | null>(null);
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
  // 点了「连接」之后跟着会话：连上就关；登录失败 / 被别处登录挤下就把原因写在弹窗里
  const state = status?.state;
  useEffect(() => {
    if (!waiting || !status || status === before.current) return;
    if (state === "connected") return onClose(true);
    if (state === "login_failed") {
      setWaiting(false);
      setPassword("");
      setError(loginReason(status.reason));
    } else if (state === "replaced") {
      setWaiting(false);
      setError(op.reason.account_in_use);
    }
  }, [waiting, state, status, onClose]);
  const submit = async () => {
    setBusy(true);
    setError("");
    before.current = status;
    const { host, port } = splitAddress(address);
    const result = inline
      ? await api.call("cloud_connect", { host, port, user: user.trim(), inline: true, ...(password ? { password } : {}) })
      : await api.call("cloud_connect", { host, port, user: user.trim() });
    setBusy(false);
    // 原生小窗里点了取消：留在弹窗，不报错
    if (result.cancelled) return;
    if (!result.ok) return setError(result.code === "password_required" ? "" : st.linkFailed);
    if (!inline) return onClose(true);
    setPassword("");
    setWaiting(true);
  };
  const progress = waiting || busy;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose(false)}>
      <DialogContent
        aria-describedby={undefined}
        data-slot="cc-cloud-express-dialog"
        className="z-(--cc-z-confirm) max-w-(--cc-dialog-w-sm)"
      >
        <DialogHeader>
          <DialogTitle>{st.cloudExpress}</DialogTitle>
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
          <FieldLabel htmlFor="cc-cloud-express-user">{st.username}</FieldLabel>
          <Input
            id="cc-cloud-express-user"
            value={user}
            autoComplete="off"
            onChange={(e) => setUser(e.target.value)}
            data-slot="cc-cloud-express-user"
          />
        </Field>
        {inline && (
          <Field>
            <FieldLabel htmlFor="cc-cloud-express-password">{st.password}</FieldLabel>
            <Input
              id="cc-cloud-express-password"
              type="password"
              value={password}
              autoComplete="off"
              placeholder={status?.remembered ? "••••••••" : undefined}
              onChange={(e) => setPassword(e.target.value)}
              data-slot="cc-cloud-express-password"
            />
          </Field>
        )}
        <DialogFooter>
          {progress && !error && (
            <span className="me-auto text-muted-foreground text-body" data-slot="cc-cloud-express-progress">
              {st.connecting}
            </span>
          )}
          {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
          <Button variant="outline" onClick={() => onClose(false)}>
            {t("cancel")}
          </Button>
          <Button disabled={progress || !address.trim() || !user.trim()} onClick={() => void submit()}>
            {st.connect}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/** 「实操登录」：云端那本 Express 的用户名、密码，只存这台电脑（桥本机凭据管理器），云端代录要登录时经隧道交过去。 */
export const CloudOperatorLogin: FC<{ api: Desktop; onClose: () => void }> = ({ api, onClose }) => {
  const inline = !!useDesktopFeatures().connectDialog;
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    setBusy(true);
    setError("");
    const result = inline
      ? await api.call("cloud_operator_authorize", { connection: "default", inline: true, username: user.trim(), password })
      : await api.call("cloud_operator_authorize", { connection: "default" });
    setBusy(false);
    setPassword("");
    if (result.cancelled) return;
    if (!result.ok) return setError(st.mrerpAuthorizeFailed);
    onClose();
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent aria-describedby={undefined} data-slot="cc-cloud-operator-dialog"
        className="z-(--cc-z-confirm) max-w-(--cc-dialog-w-sm)">
        <DialogHeader>
          <DialogTitle>{st.operatorLogin}</DialogTitle>
        </DialogHeader>
        {inline && (
          <>
            <Field>
              <FieldLabel htmlFor="cc-cloud-operator-user">{st.username}</FieldLabel>
              <Input id="cc-cloud-operator-user" value={user} autoComplete="off" onChange={(e) => setUser(e.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="cc-cloud-operator-password">{st.password}</FieldLabel>
              <Input id="cc-cloud-operator-password" type="password" value={password} autoComplete="off"
                onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </>
        )}
        <DialogFooter>
          {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
          <Button variant="outline" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button disabled={busy || (inline && (!user.trim() || !password))} onClick={() => void submit()}>
            {st.connect}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/** 「添加文件夹…」：手填云端主机上的数据文件夹，云端那份 CC 去核；按编号等它回的结果（随 cloud_status），不对就写在弹窗里。 */
export const CloudAddPlace: FC<{ api: Desktop; onClose: () => void }> = ({ api, onClose }) => {
  const [status] = useCloudStatus(api);
  const [path, setPath] = useState("");
  const [asked, setAsked] = useState("");
  const [error, setError] = useState("");
  const answer = status?.cloud?.add;
  useEffect(() => {
    if (!asked || answer?.id !== asked) return;
    const failure = addPlaceFailure(String(answer.code ?? ""));
    if (!failure) return onClose();
    setAsked("");
    setError(failure);
  }, [asked, answer, onClose]);
  const submit = async () => {
    setError("");
    const result = await api.call("cloud_add_place", { path: path.trim() });
    if (!result.ok || typeof result.id !== "string") return setError(st.pathNotUsable);
    setAsked(result.id);
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent aria-describedby={undefined} data-slot="cc-cloud-add-place-dialog"
        className="z-(--cc-z-confirm) max-w-(--cc-dialog-w-sm)">
        <DialogHeader>
          <DialogTitle>{st.addFolder}</DialogTitle>
        </DialogHeader>
        <Field>
          <FieldLabel htmlFor="cc-cloud-add-place-path">{st.path}</FieldLabel>
          <Input id="cc-cloud-add-place-path" value={path} autoComplete="off" onChange={(e) => setPath(e.target.value)}
            data-slot="cc-cloud-add-place-path" />
        </Field>
        <DialogFooter>
          {asked && !error && <Spinner className="me-auto" />}
          {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
          <Button variant="outline" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button disabled={!!asked || !path.trim()} onClick={() => void submit()}>
            {st.addFolder}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
