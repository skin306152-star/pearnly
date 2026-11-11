// 取这段对话的会话编号，新对话就先建会话（runtime-provider 的 ensureSession：加附件、发第一句都走这里）。
//
// 建之前这段对话还没有会话编号的，会话就是这一下本页刚建的，历史本来是空的：控制器标成已并完历史（created），
// 不去整段补拉。只在发第一句时标、加附件时不标的话，新对话先加附件会被当成「打开已有对话」去补拉：补拉那一两秒里
// 对话区换成「正在加载消息」、输入框掉到最底下，补拉回来又回到欢迎页居中（11-11 testcowork 实测「上传后对话跳动」），
// 之后发第一句还要先等这次补拉（批 A ② 修的就是这个等待，只修了没先加附件的那条路）。

type Ids = { externalId?: string | undefined; remoteId?: string | undefined };

/** 对话列表里这一项（组件库的 threadListItem()）：只用到读当前编号与建会话。 */
export interface SessionItem {
  getState(): Ids;
  initialize(): Promise<Ids>;
}

/** 会话控制器里用到的两件事（session-controller.ts）。 */
export interface FreshController {
  created(): void;
  attach(): void;
}

export async function ensureSessionOf(item: SessionItem, controllerOf: (id: string) => FreshController): Promise<string> {
  const before = item.getState();
  const fresh = !(before.externalId ?? before.remoteId);
  const { externalId, remoteId } = await item.initialize();
  const id = externalId ?? remoteId;
  if (!id) throw new Error("session was not created");
  const controller = controllerOf(id);
  if (fresh) controller.created();
  // Warm the controller so the tail is up before anything happens.
  controller.attach();
  return id;
}
