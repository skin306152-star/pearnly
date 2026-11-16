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

import { CloudIcon, FolderIcon, MoreHorizontalIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Marquee } from "@/components/ui/marquee";
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

// map: cloud-express
/** 设置 → 连接里 Express 那一组下面的「云端 Express」一行（只在 CC 桌面版里）。 */
export const CloudExpressRow: FC<{ api: Desktop }> = ({ api }) => {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const reload = useCallback(() => void api.call("cloud_status").then((raw) => setStatus(cloudStatus(raw))), [api]);
  useEffect(() => {
    reload();
    return api.onChanged(reload);
  }, [api, reload]);
  if (!status) return null;
  const { link, state, places } = status;
  const line = cloudStatusLine(state);
  const disconnect = () => {
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
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={disconnect}>
                {st.disconnect}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
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
          onClose={() => {
            setOpen(false);
            reload();
          }}
        />
      )}
    </>
  );
};

/** 「连接 云端 Express」弹窗：地址、账号由桥预填；密码由桥弹的原生小窗收，页面不经手。 */
export const CloudExpressConnect: FC<{ api: Desktop; onClose: () => void }> = ({ api, onClose }) => {
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
    onClose();
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
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
          <Button variant="outline" onClick={onClose}>
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
