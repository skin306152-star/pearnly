// 代录开始前那张引导卡的卡头（A4，共享笔记「A4-代录开始前题卡-约定-11-16」第 2 节；原 operator-start.tsx 要保留的那几行）：
//   写入 公司 · 账套 · 电脑（operator.targets，writeLine 同一口径）；Express 登录：页面自己问本机 CC 桌面版（loginHere，服务端拿不到），
//   已设置写登录名，没设置一行 +「设置」（authorizeHere 后再问一次、重取 view）；
//   没电脑能写时服务端那句；documents 题卡上的事实行（card.facts：停下 / 等待的单，带原因的那句）。
// 确定之后的进度、等电脑空出来、未连接仍是代录事件，不归这张卡。

import { type FC, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { op } from "@/i18n";
import { reportCaught } from "@/lib/caught";
import { type GuideView, viewGuide } from "@/lib/guide-run";
import { authorizeHere, loginHere } from "./operator-shared";

type Login = { set: boolean; user: string } | null;
/** 问本机 CC 桌面版 Express 登录（展示页换成替身）。 */
let askLogin: () => Promise<Login> = loginHere;
export const setGuideLogin = (next: () => Promise<Login>) => { askLogin = next; };
import { writeLine } from "./operator-start";

type Act = (call: () => Promise<GuideView>) => void;

export const GuideOperatorHead: FC<{ view: GuideView; busy: boolean; act: Act }> = ({ view, busy, act }) => {
  const block = view.operator;
  const facts = Object.values(view.card?.facts ?? {}).flat().filter(Boolean);
  const [login, setLogin] = useState<Login>(null);
  const operator = !!block;
  useEffect(() => {
    if (!operator) return;
    let alive = true;
    void askLogin().then((found) => alive && setLogin(found), (err) => reportCaught(err, "guide-login"));
    return () => { alive = false; };
  }, [operator]);
  if (!block && !facts.length) return null;
  const setUp = () => act(async () => {
    try {
      if (await authorizeHere()) setLogin(await askLogin());
    } catch (err) {
      reportCaught(err, "guide-authorize");
    }
    return viewGuide(view.run);
  });
  return (
    <ItemGroup data-slot="cc-guide-operator" className="cc-guide-body">
      {block && (
        <Item size="sm" data-slot="cc-op-target">
          <ItemContent>
            <ItemTitle className="break-all">
              {writeLine({ company: view.company ?? "", company_title: block.targets[0]?.connection_title }, block.targets)}
            </ItemTitle>
          </ItemContent>
        </Item>
      )}
      {block && login?.set && login.user && (
        <Item size="sm" data-slot="cc-op-login-user">
          <ItemContent><ItemDescription>{op.expressLogin}</ItemDescription></ItemContent>
          <ItemActions><span>{login.user}</span></ItemActions>
        </Item>
      )}
      {block && login?.set === false && (
        <Item size="sm" data-slot="cc-op-login">
          <ItemContent><ItemDescription>{op.reason.login_missing}</ItemDescription></ItemContent>
          <ItemActions>
            <Button type="button" variant="outline" size="xs" data-slot="cc-op-authorize" disabled={busy} onClick={setUp}>{op.setUp}</Button>
          </ItemActions>
        </Item>
      )}
      {block?.computer && (
        <Item size="sm" data-slot="cc-op-computer"><ItemContent><ItemDescription>{block.computer}</ItemDescription></ItemContent></Item>
      )}
      {facts.map((fact) => (
        <Item key={fact} size="sm" data-slot="cc-guide-fact"><ItemContent><ItemDescription>{fact}</ItemDescription></ItemContent></Item>
      ))}
    </ItemGroup>
  );
};
