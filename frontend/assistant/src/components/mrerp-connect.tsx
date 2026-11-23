// MR.ERP 的「连接」（用户 11-07：连接设置里加 MR.ERP 一行；MR.ERP 连接只留这一个入口。评估
// docs/work/2026-11-07-cc-connect-company-picker-评估.md §1.4 与 docs/work/2026-11-07-mrerp-connect-one-entry-评估.md）。
//
//   MrerpRow      — 设置 → 连接最上面一行「MR.ERP」：状态（未连接 / 未授权 / 已授权：用户名 · 电脑 · 时间）+「连接」。只在产品
//                   声明了 connect.mrerp 时出（共用层 projects() 的 connect：Cowork 给 connections 接口；ERP 只在 MR.ERP 账本开着时
//                   给），页面不按产品名分支。CC 桌面版「此电脑 → 连接」与浏览器的「连接」同一行。
//   MrerpConnect  — 点「连接」的弹窗（原定时任务里的那一个搬来；定时任务、Cowork Desk 的「连接」都改成打开设置的连接一节）：
//                   系统地址（存下走 configure，只给管理员，服务端判）+ 用户名 + 密码，「连接」一步存地址并授权（11-23 用户
//                   「第一个弹窗不行吗」：不再另弹原生小窗；密码只经本机桥交出，见 mrerp-login.tsx；浏览器、手机里两格灰）。存下或
//                   授权成功后请服务端读一次这条连接的账套目录（声明接口的 request_refresh），选择公司弹窗的 MR.ERP 标签下就有这些公司。
// 不提供断开 / 删除连接（产品本来就不允许删连接）。

import { Dialog, DialogClose, DialogHeading, DialogOverlay, DialogPortal, DialogSurface } from "@/components/ui/dialog";
import { XIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { authorizeConnection, authorizedDetail, type MrErpCredential, MrErpLoginStatus } from "@/components/mrerp-login";
import { co, st, t } from "@/i18n";
import { openSettings, useDesktopFeatures } from "@/lib/desktop";
import { getMethod, postMethod, shownError } from "@/lib/frappe";
import { useProjects } from "@/lib/managed-agents/projects";
import { useProfile } from "@/lib/profile";
import { Marquee } from "@/components/ui/marquee";
import { reportCaught, withReport } from "../lib/caught";

export type MrerpConnection = {
  name: string; adapter: string; enabled?: number | boolean; revision?: number; main?: boolean;
  config?: Record<string, string>; credential?: MrErpCredential;
};

const DEFAULT_URL = "https://www.mrerp4sme.com";
const requestKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
/** 存下或授权后请服务端读一次这条连接的账套目录（声明的连接接口的 request_refresh，同「刷新账套」；不按产品写死接口）。 */
const refreshCatalog = (api: string, name: string) => postMethod(api + "request_refresh", { name, request_key: requestKey() });

/** 产品声明的 MR.ERP 连接接口前缀（"" = 这个站点不出 MR.ERP 一行）。 */
export const useMrerpApi = (): string => useProjects((s) => s.connect.mrerp ?? "");

/** 主连接（本归属下的那一条，服务端在 records 里标 main）。 */
export const mainMrerp = (rows: readonly MrerpConnection[] | null | undefined): MrerpConnection | null =>
  (rows ?? []).find((row) => row.adapter === "mrerp" && row.main) ?? null;

/** 这一行的状态：没有连接「未连接」；有连接没授权「未授权」；授权过「已授权：用户名 · 电脑 · 时间」。 */
export function mrerpStatus(row: Pick<MrerpConnection, "credential"> | null): string {
  if (!row) return st.notConnected;
  const detail = authorizedDetail(row.credential);
  return detail ? st.mrerpAuthorized(detail) : st.mrerpNotAuthorized;
}

// map: mrerp-connect@cowork
/** 设置 → 连接最上面一行「MR.ERP」。 */
export const MrerpRow: FC = () => {
  const api = useMrerpApi();
  const [row, setRow] = useState<MrerpConnection | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const reload = useCallback(() => {
    if (!api) return;
    void getMethod<MrerpConnection[]>(api + "records", { limit: 100 })
      .then((rows) => setRow(mainMrerp(rows)), withReport(() => setRow(null), "mrerp-connect"));
  }, [api]);
  useEffect(reload, [reload]);
  if (!api || row === undefined) return null;
  return (
    <>
      <div data-marquee-host="" className="cc-settings-row" data-slot="cc-mrerp-row">
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-body">MR.ERP</Marquee>
          <Marquee as="div" className="truncate text-muted-foreground text-caption" data-slot="cc-mrerp-status">{mrerpStatus(row)}</Marquee>
        </div>
        <button type="button" className="cc-btn" data-slot="cc-mrerp-connect" onClick={() => setOpen(true)}>
          {st.connect}
        </button>
      </div>
      {open && (
        <MrerpConnect
          api={api}
          connection={row}
          onClose={(changed, name) => {
            setOpen(false);
            reload();
            if (changed && name) void refreshCatalog(api, name).catch(withReport(() => undefined, "mrerp-connect"));
          }}
        />
      )}
    </>
  );
};

/** 「连接 MR.ERP」弹窗：系统地址 + 用户名 + 密码，「连接」一步存地址并授权（11-23 用户「第一个弹窗不行吗」）。onClose(changed, name)：
 *  存了地址或授权成功（要读一次目录）与那条连接。用户名、密码都空着只存地址；只填一样不能点。 */
export const MrerpConnect: FC<{ api: string; connection: MrerpConnection | null; onClose: (changed: boolean, name: string) => void }> = ({
  api,
  connection,
  onClose,
}) => {
  const product = useProfile()?.product;
  const { app, mrerpLoginInline } = useDesktopFeatures();
  const key = useMemo(requestKey, []);
  const saved = connection?.config?.system_url || DEFAULT_URL;
  const [url, setUrl] = useState(saved);
  const [username, setUsername] = useState(connection?.credential?.username ?? "");
  // 密码只在这个 state 里：交给本机桥后立即清空；不存、不进错误上报（authorizeConnection 只经本机桥交出去）
  const [password, setPassword] = useState("");
  const [current, setCurrent] = useState<MrerpConnection | null>(connection);
  const [local, setLocal] = useState<MrErpCredential>(null);
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // 浏览器、手机里没有本机桥：两格不可用；旧桌面版没有这项能力：提示更新
  const canLogin = app && !!mrerpLoginInline;
  const typed = canLogin && !!password;
  // 地址没改、已有连接时不再存；没有连接先建一条
  const save = async (): Promise<MrerpConnection | null> => {
    if (current && url === (current.config?.system_url || DEFAULT_URL)) return current;
    const next = await postMethod<MrerpConnection>(api + "configure", {
      adapter: "mrerp", request_key: key, label: "MR.ERP", config: { system_url: url },
      ...(current ? { name: current.name, expected_revision: current.revision } : {}),
    });
    setCurrent(next);
    setChanged(true);
    return next;
  };
  // 双击：busy 要等下一次渲染才灰，同一刻点两下都会进来；这里当场挡住第二下（11-23 testerp 多出一条空连接，服务端也按系统地址复用）
  const running = useRef(false);
  const submit = async () => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    const login = typed ? { username, password } : null;
    setPassword("");
    try {
      const next = await save();
      if (!login) return onClose(true, next?.name ?? "");
      const result = next ? await authorizeConnection(product, next.name, login.username, login.password) : null;
      if (!result?.ok) return setError(st.mrerpAuthorizeFailed);
      // 使用成员只存了这台电脑：站点那行不变，弹窗里多一行本机这次的；管理员的站点也写了，关弹窗由那一行显示
      if (!result.written) {
        setChanged(true);
        return setLocal(result.authorized);
      }
      onClose(true, next?.name ?? "");
    } catch (e) {
      reportCaught(e, "mrerp-connect");
      setError(shownError(e));
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose(changed, current?.name ?? "")}>
      <DialogPortal>
        <DialogOverlay className="z-(--cc-z-confirm)" />
        <DialogSurface
          aria-describedby={undefined}
          data-slot="cc-mrerp-connect-dialog"
          data-size="sm"
          className="cc-dialog cc-enter cc-modal fixed left-1/2 z-(--cc-z-confirm) flex -translate-x-1/2 -translate-y-1/2 flex-col"
        >
          <div className="cc-dialog-head">
            <DialogHeading className="cc-dialog-title">{`${st.connect} MR.ERP`}</DialogHeading>
            <DialogClose className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogClose>
          </div>
          <div className="cc-dialog-body flex flex-col gap-(--cc-space-16)">
            <label className="flex flex-col gap-(--cc-space-6)">
              <span className="text-body">{st.systemUrl}</span>
              <input className="cc-input" value={url} onChange={(e) => setUrl(e.target.value)} data-slot="cc-mrerp-url" />
            </label>
            <MrErpLoginStatus credential={current?.credential} local={local} />
            <label className="flex flex-col gap-(--cc-space-6)">
              <span className="text-body">{st.username}</span>
              <input className="cc-input" value={username} disabled={!canLogin || busy} autoComplete="off" spellCheck={false}
                onChange={(e) => setUsername(e.target.value)} data-slot="cc-mrerp-username" />
            </label>
            <label className="flex flex-col gap-(--cc-space-6)">
              <span className="text-body">{st.password}</span>
              <input className="cc-input" type="password" value={password} disabled={!canLogin || busy} autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)} data-slot="cc-mrerp-password" />
            </label>
            {app && !mrerpLoginInline && (
              <button type="button" className="cc-btn self-start" onClick={() => openSettings("system")}>
                {co.updateDesktop}
              </button>
            )}
          </div>
          <div className="cc-dialog-foot">
            {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
            <DialogClose asChild>
              <button type="button" className="cc-btn">{t("cancel")}</button>
            </DialogClose>
            <button type="button" className="cc-btn" data-primary="" disabled={busy || !url || (typed && !username)}
              onClick={() => void submit()}>
              {st.connect}
            </button>
          </div>
        </DialogSurface>
      </DialogPortal>
    </Dialog>
  );
};
