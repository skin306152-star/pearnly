// 公司的「···」菜单项（用户 11-07：左侧「公司」与公司页删掉，公司页上的改名、刷新账套、在 ERP 中打开、删除公司搬到这里）。
//
//   侧栏公司组头的「···」（GroupSortMenu 上半段，直写、代录同一个侧栏）与对话右侧栏公司卡的「···」用同一份 CompanyMenuItems。
//   每项按产品声明出（rename / catalog / desk / delete；服务端按权限给，Cowork 改名、删除只给管理员），没声明的不出，页面不另判。
//   改名：就地改（同对话行的 InlineRename），输入框由调用方挂；菜单项只叫 onRename，等菜单关完再出输入框（useRenameAfterMenu：
//         菜单开着时有焦点陷阱，在选中那一下挂上输入框，它的自动聚焦会被拉回菜单，菜单卸下后焦点掉到 body）。
//   删除公司先确认一次（主会话 11-07 定案 #8，delete-chats.tsx 的同一个确认弹窗：「删除公司？」「取消 / 删除」，焦点在「取消」）；
//         删了侧栏这一组消失、它的对话回「最近」（服务端同样这样改）。出错显示在侧栏顶上那一行（服务端给的原文）。
//   在 ERP 中打开：这家的 desk_url（公司详情里的，打开菜单时取一次、缓存）。

import { ExternalLinkIcon, PencilIcon, RefreshCwIcon, TrashIcon } from "lucide-react";
import { type FC, useEffect, useRef, useState } from "react";
import { DropdownMenu } from "@/components/ui/menu";
import { askDelete } from "@/components/delete-chats";
import { co, t } from "@/i18n";
import { cachedDetail, deleteCompany, dropCachedDetail, openSitePage, refreshCatalogs, renameCompany } from "@/lib/companies";
import { forgetPanelDetail } from "@/lib/company-panel";
import { shownError } from "@/lib/frappe";
import { forgetProject, renameProjectLocally, setError, useSection } from "@/lib/managed-agents/projects";
import { reportCaught } from "../lib/caught";

/** 这家公司的「···」里有没有东西（产品一项都没声明时不出公司卡的「···」，组头只剩分组和排序）。 */
export function useCompanyMenu(): { any: boolean; canRename: boolean } {
  const canRename = useSection("rename");
  const canRefresh = useSection("catalog");
  const canDelete = useSection("delete");
  const desk = useSection("desk");
  return { any: canRename || canRefresh || canDelete || desk, canRename };
}

/** 改名存下（改了、不是空的才调）：成功就改侧栏与缓存里的名称；失败显示在侧栏顶上那一行。 */
export async function saveCompanyName(company: string, next: string): Promise<string | null> {
  try {
    const out = await renameCompany(company, next);
    const saved = (out as { title?: string } | null)?.title || next;
    renameProjectLocally(company, saved);
    dropCachedDetail(company);
    forgetPanelDetail(company);
    return saved;
  } catch (e) { reportCaught(e, "company-menu");
    setError(shownError(e));
    return null;
  }
}

/** 删除公司：先问一次（同删对话的确认弹窗），删了侧栏这一组消失、它的对话回「最近」。 */
export function askDeleteCompany(company: string) {
  askDelete([], async () => {
    try {
      await deleteCompany(company);
      forgetProject(company);
      dropCachedDetail(company);
      forgetPanelDetail(company);
    } catch (e) { reportCaught(e, "company-menu");
      setError(shownError(e));
    }
  }, { title: co.deleteCompanyAsk, current: () => "" });
}

/** 菜单里点了「重命名」：等菜单关完（onCloseAutoFocus）再出改名框，焦点不还给「···」。 */
export function renameAfterMenu(start: () => void) {
  let asked = false;
  return {
    request: () => {
      asked = true;
    },
    onCloseAutoFocus: (event: Pick<Event, "preventDefault">) => {
      if (!asked) return;
      asked = false;
      event.preventDefault();
      start();
    },
  };
}

export function useRenameAfterMenu(start: () => void) {
  const latest = useRef(start);
  latest.current = start;
  return useState(() => renameAfterMenu(() => latest.current()))[0];
}

/** 公司的菜单项（组头「···」的上半段、公司卡「···」）。separator：后面还有别的项（组头的分组和排序）时末尾加一条分隔线。 */
// map: company-menu
export const CompanyMenuItems: FC<{ company: string; onRename: () => void; onChanged?: () => void; separator?: boolean }> = ({
  company,
  onRename,
  onChanged,
  separator = false,
}) => {
  const canRename = useSection("rename");
  const canRefresh = useSection("catalog");
  const canDelete = useSection("delete");
  const withDesk = useSection("desk");
  const [desk, setDesk] = useState("");
  // 菜单内容只在打开时挂上：这时取一次这家的详情（缓存里有就不再问）拿 ERP 里的地址
  useEffect(() => {
    if (!withDesk) return;
    let alive = true;
    void cachedDetail(company).then((d) => alive && setDesk(d?.company.desk_url ?? ""));
    return () => {
      alive = false;
    };
  }, [company, withDesk]);
  const refresh = async () => {
    try {
      await refreshCatalogs();
      dropCachedDetail(company);
      forgetPanelDetail(company);
      onChanged?.();
    } catch (e) { reportCaught(e, "company-menu");
      setError(shownError(e));
    }
  };
  if (!(canRename || canRefresh || canDelete || desk)) return null;
  return (
    <>
      {canRename && (
        <DropdownMenu.Item className="cc-menu-item" data-slot="cc-company-rename" onSelect={onRename}>
          <PencilIcon />
          {t("rename")}
        </DropdownMenu.Item>
      )}
      {canRefresh && (
        <DropdownMenu.Item className="cc-menu-item" data-slot="cc-company-refresh" onSelect={() => void refresh()}>
          <RefreshCwIcon />
          {co.refreshSets}
        </DropdownMenu.Item>
      )}
      {desk && (
        <DropdownMenu.Item className="cc-menu-item" data-slot="cc-company-desk" onSelect={() => openSitePage(desk)}>
          <ExternalLinkIcon />
          {t("openInBrowser")}
        </DropdownMenu.Item>
      )}
      {canDelete && (
        <>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <DropdownMenu.Item className="cc-menu-item" data-danger="" data-slot="cc-company-delete" onSelect={() => askDeleteCompany(company)}>
            <TrashIcon />
            {co.deleteCompany}
          </DropdownMenu.Item>
        </>
      )}
      {separator && <DropdownMenu.Separator className="cc-menu-sep" />}
    </>
  );
};
