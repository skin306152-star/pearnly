# -*- coding: utf-8 -*-
"""DMS 订车单业务操作：建单 / 编号 / 查询 / 主档解析 / 客户查找。

照搬老产品 `services/erp/mrerp_dms_client_ops.py`。这里的**每个数字和字段名**
都是真机勘察来的，不要"顺手优化"。

三件必须记住的事：

1. **单号是 DMS autonum 出的**（符合公司编号规则、零手填），但它的计数器按分店分段，
   跟全局唯一约束会失步 → 撞"单号重复"就顺号重试；
2. **顾问栏是销售提成归属，绝不回落名册首行** —— 落错人只有月底对账才看得出来，
   所以宁可当场报错；
3. **主档要翻页取全**：只取第一页会让第二页起的选项静默消失，而"这个人不在名册"
   这种判断一旦说出口就是错的。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from pearnly_dms import dms_docno, dms_employees
from pearnly_dms.dms_booking_org import resolve_booking_org
from pearnly_dms.dms_booking_payload import payload_from_refs
from pearnly_dms.dms_booking_submit import submit_booking
from pearnly_dms.dms_client_base import DmsClientError, to_be_date
from pearnly_dms.dms_master_rows import memo as _memo
from pearnly_dms.dms_master_rows import parse_rows as _parse_bshsd_rows
from pearnly_dms.dms_master_rows import row_by_id  # noqa: F401  既有对外名（dms_advisor）
from pearnly_dms.dms_models import (
    BookingDefaults,
    DMSBookingPayload,
    DMSMasterRef,
    ThaiIdCardPayload,
)
from pearnly_dms.dms_payments import payment_form_fields

logger = logging.getLogger(__name__)

# DMS 订车单（drfcbc）菜单 id/层级 —— autonum 自动编号的键（MR.ERP DMS 标准实例）。
# 走原生表单建单时带上，让 new.php 推进对应 BK 流水计数器。
_DRFCBC_IDMENU = "25"
_DRFCBC_MENULV = "2"

# 顾问名册翻页页大小（真机探过：服务端尊重 bshsdamt，200 行一页覆盖单店全部销售）。
_ADVISOR_PAGE_SIZE = 200


class DmsClientOpsMixin:
    def _apply_booking_form_fields(
        self,
        data: Dict[str, str],
        *,
        customer_id: str,
        booking: DMSBookingPayload,
        card: ThaiIdCardPayload,
    ) -> None:
        """填订车单业务字段（顾问/车/颜色/客户/身份/地址…）· 建（new）与改（edit）共用。

        **不含** `stsel`/`idsel`/`txtdocno` —— 那三个由调用方按建/改设置。
        """
        # 顾问电话是原生表单的必填项。以前这里缺了就填一个 "."，等于**编一个不存在的
        # 电话**推进业务单据；现在缺了就拦下（顾问名册里没电话 = 归属没配好）。
        advisor_phone = str((booking.advisor.extra or ("",))[0] or "").strip()
        if not advisor_phone:
            raise DmsClientError("DMS advisor phone is missing", "ERR_DMS_ADVISOR_UNMATCHED")
        data.update(
            {
                "usersval": booking.advisor.id,
                "txtusers": booking.advisor.name,
                "txtuserstel": advisor_phone,
                "placebookval": booking.place_book.id,
                "txtplacebook": booking.place_book.name,
                "cusval": customer_id,
                "txtcus": card.full_name,
                "prefixval": card.prefix_id,
                "txtprefix": card.prefix_name,
                "txtpeopleid": card.people_id,
                "txtbirthday": card.birthday_be,
                "txttel": card.phone,
                "carval": booking.car.id,
                "txtcar": booking.car.code,
                "carbrandval": self._extra(booking.car, 0),
                "txtcarbrand": self._extra(booking.car, 1),
                "typecarval": self._extra(booking.car, 2),
                "txttypecar": self._extra(booking.car, 3),
                "typecardescval": self._extra(booking.car, 4),
                "txttypecardesc": self._extra(booking.car, 5),
                "gradeval": self._extra(booking.car, 6),
                "txtgrade": self._extra(booking.car, 7),
                "cargearval": self._extra(booking.car, 8),
                "txtcargear": self._extra(booking.car, 9),
                "txtmanuyear": self._extra(booking.car, 10),
                "enginepowerval": self._extra(booking.car, 11),
                "txtenginepower": self._extra(booking.car, 12),
                "carpaintval": booking.paint.id,
                "txtcarpaint": booking.paint.code,
                "carpaintname": booking.paint.name,
                "txtprice": self._extra(booking.car, 13),
                "txtcardeliverydate": booking.delivery_date_be,
                "termsaleval": booking.term_sale.id,
                "txttermsale": booking.term_sale.name,
                "branch_bookval": booking.branch.id,
                "txtbranch_book": booking.branch.name,
                "team_bookval": booking.team.id,
                "txtteam_book": booking.team.name,
                "branch_sellval": booking.branch.id,
                "txtbranch_sell": booking.branch.name,
                "team_sellval": booking.team.id,
                "txtteam_sell": booking.team.name,
                "txtearnestmoney": "0.00",
                "regisbehalfval": booking.regis_behalf.id,
                "txtregisbehalf": booking.regis_behalf.name,
                "txtregisname": booking.regis_name or card.full_name,
            }
        )
        # 有订金渠道时覆盖上面的 0.00 默认并补渠道字段；空 payments 则保持现状。
        data.update(payment_form_fields(booking.payments))
        # 顾问归属带出来的**原生组织字段**（订车/销售两侧分店与组 + 四级主管）。
        # 组织只能按顾问 id 问出来 —— 分店列表的首行不是这个顾问的分店。
        data.update(dict(booking.organization_fields))
        self._apply_address_to_booking_form(data, card.address)

    def create_booking_via_form(
        self,
        *,
        customer_id: str,
        booking: DMSBookingPayload,
        card: ThaiIdCardPayload,
        on_attempt=None,
    ) -> tuple:
        """走 DMS **原生订车单表单**建单 · 由 DMS autonum 出 BK 号（符合公司规则·零手填）。
        返回 `(booking_id, booking_no)`。

        autonum 计数器与全局唯一约束失步时会回已占用号 → 提交即「单号重复」。
        建单前先**只读扫列表**，从实际最大流水之后选号，并把 `natn` 同量前移；
        撞重复仍往后顺号重试（数字主体与 `natn` 同步 +1），跳过被占用的号。
        `booking_no` / 回读 / 台账一律用**完整号**（DMS 返回的前缀 + 数字主体）。
        """
        form_html = self._post_text("drfcbc/form.php", {"status": "n"})
        base = self._parse_form_defaults(form_html)
        base["stsel"] = "n"
        base["idsel"] = ""
        base["idmenu"] = _DRFCBC_IDMENU
        base["menulv"] = _DRFCBC_MENULV
        self._apply_booking_form_fields(base, customer_id=customer_id, booking=booking, card=card)

        state = self._next_booking_docno(booking.branch.id)
        scan = dms_docno.scan_unoccupied_docno(state.docno, state.digits, self._post_text)
        if scan.docno != state.docno:
            # **换号的唯一入口**：`with_docno` 只把 `natn` 前移"所选尾号 − 原候选尾号"
            # 的 delta。计数器基数（`natn`）可能不等于候选尾号，绝不能被新尾号绝对值
            # 覆盖，也不能在调用前先手动 align 一次 —— 那会把同一个 delta 推两次。
            state = state.with_docno(scan.docno)
        return submit_booking(
            self,
            base,
            state,
            on_attempt=on_attempt,
            write_payload=self._booking_write_payload,
            readback_payload=self._booking_readback_payload,
        )

    def _next_booking_docno(self, branch_id: str) -> dms_docno.DMSBookingAutonumState:
        """取 DMS **原生**订车单取号状态（前缀 + 完整号 + 隐藏字段）。

        `autonum.php` / `autonumdetail.php` 缺字段或形状不符原生协议时 **fail closed**
        （`ERR_DMS_IMPORT`），绝不回退成空串或自造单号 —— 旧版丢掉 `idatndt`/`natn`
        导致计数器永不推进，正是这次要修的根因。
        """
        try:
            cfg_body = self._post_text(
                "component/php/autonum.php",
                {"menulv": _DRFCBC_MENULV, "idmenu": _DRFCBC_IDMENU},
            )
            cfg = json.loads(cfg_body)
            detail_body = self._post_text(
                "component/php/autonumdetail.php",
                {
                    "idautonum": cfg[0],
                    "prefixautonum": cfg[2],
                    "digitautonum": cfg[3],
                    "idautonumformat": cfg[4],
                    "idbranch": branch_id or "",
                },
            )
            return dms_docno.autonum_state(cfg_body, detail_body)
        except Exception as exc:
            raise DmsClientError(
                f"booking auto number protocol unavailable: {type(exc).__name__}",
                "ERR_DMS_IMPORT",
            ) from exc

    def _booking_write_payload(self, base: Dict[str, str], state) -> Dict[str, str]:
        """`drfcbc/new.php` 的提交体 —— **完全模拟原生表单**：

        `txtprefixautonum` = 配置前缀，`txtdocno` = 完整号去掉精确前缀后的数字主体，
        外加保存用隐藏字段 `idatndt`（detail[0]）与 `natn`（与实际选号对齐的下一个流水）。
        单号整串**不**塞进 `txtdocno`，否则前缀栏为空、计数器也不推进。
        """
        return {
            **base,
            "txtprefixautonum": state.prefix,
            "txtdocno": state.body,
            dms_docno.HIDDEN_IDAUTNUMDETAIL: state.idautonumdetail,
            dms_docno.HIDDEN_NEXTAUTONUM: str(state.nextautonum),
        }

    def _booking_readback_payload(self, base: Dict[str, str], state) -> Dict[str, str]:
        """回读比对用的 submitted 视图 —— 必须带**完整号**，不能退化成纯数字主体。

        真实 POST 的 `txtdocno` 是纯数字，直接拿它比对会把正确的单判成不一致；
        这里构造一份**只用于比对**的副本（不参与任何写入）。
        """
        return {
            **base,
            "txtprefixautonum": state.prefix,
            "txtdocno": state.docno,
        }

    def attach_booking_files(self, *, booking_id: str, files: list) -> dict:
        """给已存在的订车单挂附件 —— 逐文件独立一轮 `edit.php` POST，失败不中断。

        DMS 的上传契约（老产品真机抓包确认）：上传 = POST `drfcbc/edit.php` 提交
        **完整编辑表单**的 FormData，不是独立小接口。**只发新文件字段会把订车单既有
        字段冲空**。所以先载入编辑表单解析全部现值，再逐件追加 `fulnew[]`/`fulnewname[]`；
        全部跑完回读校验（HTTP 200 不算数）。

        `files` 元素：`{"display_name", "filename", "content_type", "content"}`。
        返回 `{"ok", "attached", "failed": [{"display_name", "error"}]}`。
        """
        if not files:
            return {"ok": True, "attached": 0, "failed": []}

        # 编辑表单的加载参数名以真机抓包为准。
        form_html = self._post_text("drfcbc/form.php", {"status": "e", "id": str(booking_id)})
        base = self._parse_form_defaults(form_html)
        if base.get("stsel") != "e" or base.get("idsel") != str(booking_id):
            raise DmsClientError(
                f"booking attach form mismatch: stsel={base.get('stsel')!r} "
                f"idsel={base.get('idsel')!r} vs booking_id={booking_id!r}",
                "ERR_DMS_IMPORT",
            )
        docno_before = base.get("txtdocno")

        attached = 0
        failed: List[Dict[str, str]] = []
        uploaded: List[str] = []  # POST 判绿的件，留待回读校验
        for item in files:
            display = str(item.get("display_name") or "")[:150]
            data = dict(base)
            data["fulnewname[]"] = display
            resp = self.transport.post(
                self._url("drfcbc/edit.php"),
                data=data,
                files={"fulnew[]": (item["filename"], item["content"], item["content_type"])},
                timeout_ms=120000,
            )
            body = (resp.text or "").strip()
            if resp.status_code == 200 and not body.startswith("err::"):
                uploaded.append(display)
            else:
                failed.append(
                    {
                        "display_name": display,
                        "error": f"edit.php http={resp.status_code} {body[:200]}",
                    }
                )

        # 回读校验：HTTP 200 不算数，附件必须真渲染在表单上、且没把单据冲坏。
        if uploaded:
            verify = self._parse_form_defaults(
                self._post_text("drfcbc/form.php", {"status": "e", "id": str(booking_id)})
            )
            current_names = {v for k, v in verify.items() if k.startswith("fulcurrname")}
            docno_ok = verify.get("txtdocno") == docno_before
            for display in uploaded:
                if not docno_ok:
                    failed.append({"display_name": display, "error": "readback docno changed"})
                elif display not in current_names:
                    failed.append(
                        {"display_name": display, "error": "readback missing display name"}
                    )
                else:
                    attached += 1

        return {"ok": not failed, "attached": attached, "failed": failed}

    def fetch_masters(self, *, strict: bool = False) -> Dict[str, List[List[Any]]]:
        """拉连接向导要的下拉列表。尽力而为：读不到/不可解析的列表回 `[]`。

        普通主档和顾问名册一样**翻页取全** —— 只取第一页会让第二页起的选项
        在逐问里静默消失。
        """
        out: Dict[str, List[List[Any]]] = {}
        for key, elem in (
            ("cars", "txtcar"),
            ("place_books", "txtplacebook"),
            ("term_sales", "txttermsale"),
            ("branches", "txtbranch_book"),
            ("regis_behalfs", "txtregisbehalf"),
        ):
            try:
                rows = self._bshsd_all(elem)
            except Exception as exc:
                if strict:
                    raise DmsClientError(
                        f"DMS master {elem} unavailable during snapshot refresh",
                        "ERR_DMS_MASTER_UNAVAILABLE",
                    ) from exc
                rows = None
            if rows is None and strict:
                raise DmsClientError(
                    f"DMS master {elem} unavailable during snapshot refresh",
                    "ERR_DMS_MASTER_UNAVAILABLE",
                )
            out[key] = rows or []
        try:
            # 顾问名册单独翻页取全：操作员账号按 code 列匹配，被分页截断就永远匹配不上。
            advisors = self._advisor_rows()
        except Exception as exc:
            if strict:
                raise DmsClientError(
                    "DMS advisor master unavailable during snapshot refresh",
                    "ERR_DMS_MASTER_UNAVAILABLE",
                ) from exc
            advisors = None
        if advisors is None and strict:
            raise DmsClientError(
                "DMS advisor master unavailable during snapshot refresh",
                "ERR_DMS_MASTER_UNAVAILABLE",
            )
        out["advisors"] = advisors or []
        try:
            out["prefixes"] = self.list_prefixes() or []
        except Exception:
            out["prefixes"] = []
        # 员工表带登录名，是顾问归属的**精确层**（顾问下拉的 code 列只是员工编号）。
        # 销售账号未必有员工页权限 → 取不到就落 []，调用方回落启发式匹配。
        out["employees"] = dms_employees.fetch_employees(self) or []
        return out

    def resolve_booking_payload(
        self, defaults: BookingDefaults, card: ThaiIdCardPayload, *, today: Optional[date] = None
    ) -> DMSBookingPayload:
        """由端点默认值 + 实时主档拼出订车单草稿；用户没钉死的主档引用现场解。

        **按需取数版**（单凭据兼容路径）：每个主档一次 `bshsd`。
        订车提交路径**不走这里** —— 那里吃权威预检已取到的快照
        （`build_booking_payload`），不重复取数。
        """
        advisor = self._advisor_ref_strict(defaults)
        car = self._ref_from_default("txtcar", defaults.car_id, defaults.car_code, "")
        paint = self._ref_from_default(
            "txtcarpaint", defaults.paint_id, defaults.paint_code, "", idcar=car.id
        )
        place = self._ref_from_default("txtplacebook", defaults.place_book_id, "", "")
        term = self._ref_from_default("txttermsale", defaults.term_sale_id, "", "")
        # 组织（分店 + 组 + 四级主管）只能**按顾问 id** 问出来 ——
        # 分店列表的首行不是这个顾问的分店。
        org = resolve_booking_org(self, advisor.id, defaults)
        regis = self._ref_from_default("txtregisbehalf", defaults.regis_behalf_id, "", "")

        return payload_from_refs(
            defaults,
            advisor=advisor,
            car=car,
            paint=paint,
            place=place,
            term=term,
            org=org,
            regis=regis,
            today=today,
        )

    def search_customer(self, text: str) -> Optional[str]:
        body = self._post_text(
            "cus/component/showdata.php",
            {
                "sdtamt": "10",
                "sdtpage": "1",
                "sd": text,
                "selcolsort": "1",
                "selcolsorttype": "1",
            },
        )
        return self._first_data_val(body)

    def search_booking(self, booking_no: str) -> Optional[str]:
        body = self._post_text(
            "drfcbc/component/showdata.php",
            {
                "sdtamt": "10",
                "sdtpage": "1",
                "sd": booking_no,
                "ftd": "1",
                "selcolsort": "1",
                "selcolsorttype": "1",
            },
        )
        return self._first_data_val(body)

    def _advisor_rows(self) -> Optional[List[List[Any]]]:
        """顾问名册（翻页取全）· 建单严格解析与 fetch_masters 共用同一次取数。"""
        return self._bshsd_all("txtusers", page_size=_ADVISOR_PAGE_SIZE)

    def _advisor_ref_strict(self, defaults: BookingDefaults) -> DMSMasterRef:
        """顾问栏（ที่ปรึกษาการขาย）= 销售提成归属，**只认调用方钉死的 id，绝不回落名册首行**。

        逐问开局已按操作员的 DMS 账号匹配好归属，到这里必有 id；
        **落错人只有月底对账才看得出来**，所以宁可当场报错也不猜。
        """
        if not defaults.advisor_id:
            raise DmsClientError(
                "booking advisor not pinned for operator", "ERR_DMS_ADVISOR_REQUIRED"
            )
        rows = self._advisor_rows()
        row = row_by_id(rows, defaults.advisor_id)
        if row is not None:
            return self._ref_from_row(row)
        if rows is None and defaults.advisor_name:
            # 名册取数失败（接口抖 / 200 里塞了不可解析的 body）：有名字才敢放行，
            # 按钉死标量提交，DMS 侧仍按 id 认人。名册**真空**（rows == []）不走这条
            # —— 那种单 DMS 必拒，降级只会把"建不了单"拖到更晚才暴露。
            return DMSMasterRef(
                id=defaults.advisor_id, code=defaults.advisor_code, name=defaults.advisor_name
            )
        raise DmsClientError(
            f"booking advisor id {defaults.advisor_id!r} not in DMS advisor master",
            "ERR_DMS_ADVISOR_UNMATCHED",
        )

    def _ref_from_default(
        self, elemname: str, pinned_id: str, pinned_code: str, pinned_name: str, **extra
    ) -> DMSMasterRef:
        """解析一个主档引用：钉了 id 就从**全量**（翻过页的）实时主档里取那一行；
        没钉就取第一行。

        只在第 1 页里找 pinned id，id 在第 2 页会被误判成"不存在"然后**悄悄回落首行**，
        建单就填错车/店。所以最终解析和 `fetch_masters` 一样走全量翻页。

        pinned id 一旦存在就**不许回落首行或钉死标量**：主档读不到/已变更时提交旧值，
        月底对账才会暴露填错。
        """
        rows = self._bshsd_all(elemname, **extra)
        if pinned_id:
            if rows is None:
                raise DmsClientError(
                    f"DMS master {elemname} unavailable while resolving pinned id {pinned_id!r}",
                    "ERR_DMS_MASTER_UNAVAILABLE",
                )
            chosen = row_by_id(rows, pinned_id)
            if chosen is None:
                raise DmsClientError(
                    f"pinned id {pinned_id!r} not in DMS master {elemname}",
                    "ERR_DMS_MASTER_UNMATCHED",
                )
            return self._ref_from_row(chosen)
        chosen = rows[0] if rows else None
        if chosen is None:
            # 没有实时行、也没钉 id —— 回落到钉死的标量，至少给调用方一个能用的 ref
            return DMSMasterRef(id=pinned_id, code=pinned_code, name=pinned_name)
        return self._ref_from_row(chosen)

    @staticmethod
    def _ref_from_row(row: list) -> DMSMasterRef:
        """bshsd 行 `[id, code, name, ...]` → `DMSMasterRef`（尾列进 extra 供表单原样回显）。"""
        return DMSMasterRef(
            id=str(row[0]),
            code=str(row[1]) if len(row) > 1 else str(row[0]),
            name=str(row[2]) if len(row) > 2 else (str(row[1]) if len(row) > 1 else ""),
            extra=tuple(row[3:]),
        )

    def _bshsd(self, elemname: str, **extra) -> Optional[List[List[Any]]]:
        """DMS 主档单页取数（取数失败 → None）。

        **同一会话内同参只打一次**：一次建单要解六七个主档、还会在成功后就地刷缓存，
        而主档在这一次登录期间不会变。失败不进备忘，允许下次重试。
        """
        key = (elemname, tuple(sorted(extra.items())))
        memo = _memo(self)
        if key not in memo:
            data = {"bshsdamt": "10", "bshsdcurrpage": "1", "elemname": elemname, "sdt": ""}
            data.update({k: str(v) for k, v in extra.items()})
            rows = _parse_bshsd_rows(elemname, self._post_text("drfcbc/component/bshsd.php", data))
            if rows is None:
                return None
            memo[key] = rows
        return memo[key]

    def _bshsd_all(
        self, elemname: str, *, page_size: int = 200, max_pages: int = 20, **extra
    ) -> Optional[List[List[Any]]]:
        """翻页拉全某主档：不满一页即最后一页。任一页取数失败 → 整体 None。

        **半份名册比取不到更危险** —— "这个人不在名册"的判断会变成瞎话。撞 `max_pages`
        说明主档比预期大一个量级，截断必须点名留痕（静默截断 = 又一个分页黑洞）。
        """
        key = (elemname, tuple(sorted(extra.items())), "all")
        memo = _memo(self)
        if key in memo:
            return memo[key]
        rows: List[List[Any]] = []
        for page in range(1, max_pages + 1):
            got = self._bshsd(elemname, bshsdamt=page_size, bshsdcurrpage=page, **extra)
            if got is None:
                return None
            rows.extend(got)
            if len(got) < page_size:
                break
        else:
            logger.warning(
                "[dms] 主档 %s 在第 %d 页被截断 / 已取 %d 行", elemname, max_pages, len(rows)
            )
        memo[key] = rows
        return rows
