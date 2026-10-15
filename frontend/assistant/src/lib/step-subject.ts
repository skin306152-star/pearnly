// 展开后的步骤行，本站工具的那一行后面带一个短的对象（同 Claude Code 每步各有一句具体说明）：「<做完的说法> · <对象>」，
// 例「查看了推送前要选的项 · IV6810-00012」，同一个工具连着调几次也分得清是哪张、哪份账套。
// 纯函数、只看这一步的参数与结果（再加同一条回复里别的结果认出的记录名字），不调模型、不加工具参数；只取给人看的字段，
// 记录的内部名字（随机串、CWH-… 之类）一律不显示；什么都认不出就只写做完的说法。摘要行（按类计数）不用它。
//
// 取对象的顺序（先到先用）：
//   1. 参数点名的记录：document / task / count / card / board / job / draft / readback，或一串 documents / files / batches /
//      tasks / counts / drafts —— 用结果里认出的名字（单号 > 标题 > 卖方 > 文件名）；一串时「第一张 +其余张数」
//   2. 参数里人给的字：title、search、keyword、period（YYYY-MM）
//   3. 账套的文件夹名（路径最后一段，如 T9NIEXCP）：参数 account_set_key，否则结果的 account_set / push_account_set；
//      都没有才用结果的 account_label（公司名，长）
//   4. 结果里的公司范围（scope.company，公司名）

/** 对象最多几个字（多了截断加「…」；行本身也会按宽度省略）。 */
export const SUBJECT_MAX = 40;

type Json = Record<string, unknown>;

/** 记录在结果里的键（值是它的内部名字）。 */
const ID_KEYS = ["name", "document", "file", "batch", "task", "count", "card", "board", "job", "draft", "readback"] as const;
/** 记录给人看的名字，按先后取。 */
const LABEL_KEYS = ["invoice_no", "invoice_number", "title", "seller_name", "payee_name", "filename"] as const;
/** 参数里点名一条记录的键。 */
const REF_KEYS = ["document", "task", "count", "card", "board", "job", "draft", "readback"] as const;
/** 参数里点名一串记录的键。 */
const REF_LIST_KEYS = ["documents", "files", "batches", "tasks", "counts", "drafts"] as const;
/** 参数里人给的字。 */
const TEXT_KEYS = ["title", "search", "keyword", "period"] as const;

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown) => (typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "");

/** 像内部名字的不显示：Frappe 的随机名（十六进制，字母数字混着）、CW 开头带横线的单号式名字。 */
export function looksInternal(value: string): boolean {
  return (/^[0-9a-f]{8,32}$/.test(value) && /[a-f]/.test(value) && /\d/.test(value)) || /^CW[A-Z]*-/.test(value);
}

const shown = (value: unknown) => {
  const plain = text(value);
  return plain && !looksInternal(plain) ? plain : "";
};

/** 路径最后一段（Windows 与 / 都认），账套键即它的文件夹名。 */
export const folderName = (path: unknown) => shown(text(path).split(/[\\/]+/).filter(Boolean).pop());

/** 一条记录给人看的名字；识别结果那种「附件 → 识别出的票」用第一张票的名字。 */
function labelOf(record: Json): string {
  for (const key of LABEL_KEYS) {
    const label = shown(record[key]);
    if (label) return label;
  }
  const documents = record.documents;
  return Array.isArray(documents) && isObject(documents[0]) ? labelOf(documents[0]) : "";
}

/** 结果的 JSON（不是 JSON 的是空的）。 */
export function parseResult(resultText: string | undefined): unknown {
  if (!resultText) return undefined;
  try {
    return JSON.parse(resultText);
  } catch {
    return undefined;
  }
}

/** 结果里认得出名字的记录：内部名字 → 给人看的名字（只走前几层、有上限，大结果也很快）。 */
export function recordLabels(result: unknown): Array<[string, string]> {
  const found: Array<[string, string]> = [];
  let budget = 2000;
  const walk = (node: unknown, depth: number) => {
    if (depth > 5 || budget-- <= 0) return;
    if (Array.isArray(node)) {
      for (const item of node) walk(item, depth + 1);
      return;
    }
    if (!isObject(node)) return;
    const label = labelOf(node);
    if (label) {
      for (const key of ID_KEYS) {
        const id = text(node[key]);
        if (id && id !== label) found.push([id, label]);
      }
    }
    for (const value of Object.values(node)) if (typeof value === "object") walk(value, depth + 1);
  };
  walk(result, 0);
  return found;
}

const clip = (value: string, max = SUBJECT_MAX) => ([...value].length > max ? `${[...value].slice(0, max - 1).join("")}…` : value);

/** 一串记录：第一张的名字，后面「+其余张数」；第一张认不出就不写。 */
function listSubject(ids: readonly unknown[], known: ReadonlyMap<string, string>): string {
  const first = known.get(text(ids[0])) ?? "";
  if (!first) return "";
  const rest = ids.length > 1 ? ` +${ids.length - 1}` : "";
  return `${clip(first, SUBJECT_MAX - rest.length)}${rest}`;
}

/**
 * 一步的对象。args：工具参数；result：这一步结果的文字（JSON）；known：同一条回复里各结果认出的记录名字
 * （recordLabels 合起来，这一步自己的结果也会并进来）。认不出就是空字符串。
 */
export function stepSubject(
  args: Readonly<Json> | undefined,
  resultText: string | undefined,
  known: ReadonlyMap<string, string> = new Map(),
): string {
  const input = args ?? {};
  const result = parseResult(resultText);
  const names = new Map(known);
  for (const [id, label] of recordLabels(result)) if (!names.has(id)) names.set(id, label);

  for (const key of REF_KEYS) {
    const label = names.get(text(input[key]));
    if (label) return clip(label);
  }
  for (const key of REF_LIST_KEYS) {
    const ids = input[key];
    if (Array.isArray(ids) && ids.length) {
      const subject = listSubject(ids, names);
      if (subject) return subject;
    }
  }
  for (const key of TEXT_KEYS) {
    const value = shown(input[key]);
    if (value) return clip(value);
  }
  const out = isObject(result) ? result : {};
  const accountSet = folderName(input.account_set_key) || folderName(out.account_set) || folderName(out.push_account_set)
    || shown(out.account_label);
  if (accountSet) return clip(accountSet);
  const scope = out.scope;
  return isObject(scope) ? clip(shown(scope.company)) : "";
}
