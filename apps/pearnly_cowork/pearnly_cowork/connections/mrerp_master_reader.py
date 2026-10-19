"""在已登录、已选好账套的 MR.ERP 会话里读这家公司的主档（只读：只 POST 列表接口、GET 两个表单页）。

规则见 `targets/mrerp_masters.py`。后台读取任务（给录入前的选择弹窗）与推送（核对与取值）共用这里。

- 列表接口 `*/component/bshlistboxdata.php`：POST、不带参数，回 JSON；只要会话里有公司与年度就落到这家公司的库
  （`connectdb/connect.php:15`），不查菜单权限。掉线时仍回 200，内容是弹窗脚本 + `No database selected`
  —— 不是 JSON 对象一律当读不到，不当「主档是空的」。
- 参考科目与称谓只有网页（`acreffile/acreffileform.php`、`armas/allform.php`）。这两页会改写会话里的当前菜单，
  所以只能在导入页（formupload）打开**之前**读；推送里导入前本来就会再打开一次导入页。
- 各接口带的大表（全部客户 / 商品）只在用得到时才读：赊销接口带客户档的员工 / 区域 / 运输，只在销项推送时读。
"""
from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from pearnly_cowork.targets import mrerp_masters as rules

# 模块 → 列表接口。小主档最轻的读法（MR.ERP 源码核对，2026-10-08）：
#   fabillrev：部门 / 工作 / 本公司分店 / 员工，不带大表；armas：销售区 / 运输 / 客户类型 / 科目表（带全部客户的码与名）；
#   apmas：供应商类型（带全部供应商）；stktraniss：仓库（带全部商品）；stkmas：商品分类 / 单位（带全部商品与供应商）；
#   artran：客户档里的员工 / 区域 / 运输（带全部客户与商品，最重）。
LISTS = {
    "fabillrev": "fabillrev/component/bshlistboxdata.php",
    "armas": "armas/component/bshlistboxdata.php",
    "apmas": "apmas/component/bshlistboxdata.php",
    "stktraniss": "stktraniss/component/bshlistboxdata.php",
    "stkmas": "stkmas/component/bshlistboxdata.php",
    "artran": "artran/component/bshlistboxdata.php",
}
# 给选择弹窗读的（全部字段）；推送时按要用到的另取。
FOR_CHOICES = ("fabillrev", "armas", "apmas", "stktraniss", "stkmas")
REFERENCE_PAGE = "acreffile/acreffileform.php"
PREFIX_PAGE = "armas/allform.php"
HEAD_BRANCH_PAGE = "artran/allform.php"  # 赊销新建单据页：预填的本公司分店 = 总店
# 本公司分店档案的查看页：总店那一行登记的税号 = 这家公司的税号（MR.ERP 打印税票表头用的同一个值，
# `component/php/showreportfile.php`）。不带 idmenu，不改会话里的当前菜单，只查登录（MR.ERP 源码 2026-10-19 核对）。
COMPANY_PAGE = "combrh/allform.php"
# 「最常用的那组」抽样：每类取最新的这么多份现有档案（`component/dupdata.php`，MR.ERP「复制现有档案」同源）。
SAMPLE_SIZE = 30

Request = Callable[..., object]


class MasterReader:
    """一次会话里的主档读取：同一个接口只读一次。`request(method, path, **kw)` 是会话的业务请求。"""

    def __init__(self, request: Request, base: str):
        self._request, self._base = request, base.rstrip("/")
        self.lists: Dict[str, Optional[dict]] = {}
        self._reference: Optional[Dict[int, str]] = None
        self._prefixes: Optional[list] = None
        self._head_branch = ""
        self._company_tax = ""
        self._pages_read = set()
        self._samples: Dict[str, list] = {}

    def listbox(self, module: str) -> Optional[dict]:
        if module not in self.lists:
            response = self._request("POST", LISTS[module], data={},
                                     headers={"X-Requested-With": "XMLHttpRequest", "Origin": self._base,
                                              "Referer": f"{self._base}/login/mainmenu.php"})
            self.lists[module] = rules.parse_listbox(getattr(response, "text", "") or "")
        return self.lists[module]

    def fields(self, *modules: str) -> Dict[str, list]:
        """读这几个接口，合成各字段的可用值；有一个读不到就只合读到的（读不到的字段 resolve 时报 UNREADABLE）。"""
        return rules.masters([payload for payload in (self.listbox(m) for m in modules) if payload])

    def reference(self) -> Optional[Dict[int, str]]:
        """参考科目 {id: 科目代码}；只能在导入页打开之前读（见文件头）。"""
        if "reference" not in self._pages_read:
            self._pages_read.add("reference")
            response = self._request("GET", REFERENCE_PAGE, headers={"Referer": f"{self._base}/login/mainmenu.php"})
            self._reference = rules.parse_reference(getattr(response, "text", "") or "")
        return self._reference

    def prefixes(self) -> Optional[list]:
        if "prefix" not in self._pages_read:
            self._pages_read.add("prefix")
            response = self._request("GET", PREFIX_PAGE, headers={"Referer": f"{self._base}/login/mainmenu.php"})
            self._prefixes = rules.parse_prefixes(getattr(response, "text", "") or "")
        return self._prefixes


    def head_branch(self) -> str:
        """总店分店码；只能在导入页打开之前读（见文件头）。"""
        if "head" not in self._pages_read:
            self._pages_read.add("head")
            response = self._request("GET", HEAD_BRANCH_PAGE, headers={"Referer": f"{self._base}/login/mainmenu.php"})
            self._head_branch = rules.parse_head_branch(getattr(response, "text", "") or "")
        return self._head_branch

    def company_tax(self) -> str:
        """这家公司在 MR.ERP 里登记的税号（总店分店档案，只留数字，没核校验位）；读不到是空串。总店按 `head_branch` 认，
        所以也只能在导入页打开之前读。"""
        if "company" not in self._pages_read:
            self._pages_read.add("company")
            branch = rules.head_branch_id((self.listbox("fabillrev") or {}).get("txtothcombrh"), self.head_branch())
            if branch:
                response = self._request("GET", f"{COMPANY_PAGE}?id={branch}&status=vie",
                                         headers={"Referer": f"{self._base}/login/mainmenu.php"})
                self._company_tax = rules.parse_company_tax(getattr(response, "text", "") or "")
        return self._company_tax

    def known_head_branch(self) -> str:
        """已经读到的总店分店码（不去读网页：推送里网页只能在导入页打开前读）。"""
        return self._head_branch

    def samples(self, module: str) -> list:
        """这类档案里最新的 SAMPLE_SIZE 份的完整资料（dupdata 回应），读不到的跳过。"""
        if module not in self._samples:
            rows = [r for r in (self.listbox(module) or {}).get("inpdupdata") or []
                    if isinstance(r, list) and r and str(r[0]).isdigit()]
            newest = sorted(rows, key=lambda r: int(r[0]), reverse=True)[:SAMPLE_SIZE]
            found = []
            for row in newest:
                response = self._request("POST", f"{module}/component/dupdata.php", data={"hiddupdata": str(row[0])},
                                         headers={"X-Requested-With": "XMLHttpRequest", "Origin": self._base,
                                                  "Referer": f"{self._base}/{module}/allform.php"})
                try:
                    value = json.loads(getattr(response, "text", "") or "")
                except ValueError:
                    continue
                if isinstance(value, list):
                    found.append(value)
            self._samples[module] = found
        return self._samples[module]

    def suggested(self, *modules: str) -> Dict[str, str]:
        return rules.suggest({m: self.samples(m) for m in modules})


def read_for_choices(request: Request, base: str):
    """给选择弹窗：读全部小主档、总店、现有档案最常用的那组 → (各字段可用值, None) 或 (None, 原因码)。"""
    reader = MasterReader(request, base)
    fields = reader.fields(*FOR_CHOICES)
    if not fields:
        return None, rules.UNREADABLE
    fields["head_branch"] = reader.head_branch()
    fields["suggested"] = reader.suggested("armas", "apmas", "stkmas")
    return fields, None
