"""统一原件层的入口（ADR-024）：三个产品所有识别入口收原件、读原件、删原件都走这里。

存放按租户路线选（:func:`where`）：选了 Claude 识别 → 甲骨文（``originals.oracle``，ADR-023）；谷歌租户 → 谷歌存储
（``originals.gcs`` + Cloud Run 核对 / 预览，ADR-016）；哪种都没配的站点 → ``LOCAL``，产品照旧存本机（只是没配置时的样子）。
存放那边一时连不上抛 ``Deferred``（页面稍后再试、队列重试），不退回生产存。

原件引用（各产品记录的 ``storage_url``）：``oracle://<对象名>#<sha256>`` 或 ``gs://<桶>/<对象名>#<版本号>``；对象名
``<站点>/originals/<原件 id><扩展名>``，原件 id 由产品给（Cowork ``CWS-``、ERP ``ERO-`` + 48 位十六进制）。
"""
import frappe

from pearnly_core.originals import cloud_run, gcs, links, oracle

ORACLE, GCS, LOCAL = "oracle", "gcs", "local"
PREVIEW_KINDS = ("pdf", "image")


def where():
    """这个站点的新原件存哪：``ORACLE`` / ``GCS`` / ``LOCAL``。"""
    from pearnly_core.claude_ocr import client

    if client.uses_claude():
        return ORACLE if oracle.enabled() else LOCAL
    return GCS if gcs.bucket() and cloud_run.settings() else LOCAL


def object_name(original_id, suffix):
    return links.object_name(frappe.local.site, original_id, suffix)


def parse(ref):
    """原件引用 → ``(ORACLE, (对象名, sha256))`` / ``(GCS, (桶, 对象名, 版本号))``；不是存放处的引用（本机文件、空）返回 ``None``。"""
    found = links.parse_url(ref)
    if found:
        return ORACLE, found
    found = gcs.parse_url(ref)
    if found:
        return GCS, found
    return None


def begin_upload(original_id, suffix, byte_size):
    """浏览器直传第一步：``{"direct": 1, "url", "headers"}``；这个站点不直传（没配存放处）回 ``{"direct": 0}``。"""
    place, name = where(), object_name(original_id, suffix)
    if place == ORACLE:
        return {"direct": 1, **oracle.upload_target(name, int(byte_size))}
    if place == GCS:
        return {"direct": 1, **gcs.upload_target(gcs.bucket(), name, int(byte_size))}
    return {"direct": 0}


def _accepted(result, place, name):
    """存放处的核对结论 → 统一形状：``{"status": "ok", "meta", "md5", "ref"}`` 或 ``{"status": "rejected", "code"}``。"""
    if result.get("status") != "ok":
        return {"status": "rejected", "code": result.get("code") or "original_missing"}
    meta = result["meta"]
    ref = links.url_of(name, meta["sha256"]) if place == ORACLE else gcs.url_of(gcs.bucket(), name, result["generation"])
    return {"status": "ok", "meta": meta, "md5": result["md5"], "ref": ref}


def finish_upload(original_id, suffix, filename):
    """浏览器直传第二步：请存放处读一遍（类型、页数、sha256，同 ``originals.checks.inspect``；不通过那边就删掉）。
    存放处忙 / 连不上抛 ``Deferred``。"""
    place, name = where(), object_name(original_id, suffix)
    if place == ORACLE:
        return _accepted(oracle.inspect(name, filename), place, name)
    if place == GCS:
        return _accepted(cloud_run.inspect(name, filename), place, name)
    raise RuntimeError("direct upload is not available on this site")


def put(original_id, suffix, filename, content):
    """生产手上已有字节的原件（LINE 收单、CC 对话交来的文件）：直接交存放处存、核对。这个站点没配存放处返回 ``None``
    （调用方照旧存本机）。同一原件 id 再交一次（重试）算存过。"""
    place = where()
    if place == LOCAL:
        return None
    name = object_name(original_id, suffix)
    if place == ORACLE:
        return _accepted(oracle.put(name, filename, content), place, name)
    if place == GCS:
        gcs.upload(gcs.bucket(), name, content)
        return _accepted(cloud_run.inspect(name, filename), place, name)
    return None


def read(ref):
    """整份原件字节（只给表格解析、导出打包等少数地方；预览、下载、识别都不经过这里）。"""
    place, stored = parse(ref)
    return oracle.read(stored) if place == ORACLE else gcs.read(stored)


def download_url(ref, filename):
    place, stored = parse(ref)
    return oracle.download_url(stored, filename) if place == ORACLE else gcs.download_url(stored, filename)


def preview_url(ref, kind, page):
    """甲骨文的原件：一页预览的签名地址（甲骨文现画并存下）。谷歌存储的走产品原有的存好预览 / Cloud Run 现画（:func:`render`），
    这里回 ``None``。"""
    found = parse(ref)
    if not found or found[0] != ORACLE or kind not in PREVIEW_KINDS:
        return None
    return oracle.preview_url(found[1], kind, int(page))


def render(ref, sha256, kind, page):
    """谷歌存储的原件：在 Cloud Run 上画一页预览，只传回图（``{"png": 字节}`` 或 ``{"code": 错误代码}``）。"""
    place, stored = parse(ref)
    if place != GCS:
        raise ValueError("render is for originals in Google storage")
    return cloud_run.preview(stored[1], stored[2], sha256, kind, int(page))


def recognition_reference(ref):
    """交识别时带的原件引用（存放处那边自己读，生产不经手字节）：甲骨文 ``(对象名, sha256)``；谷歌存储 ``(桶, 对象名, 版本号)``。"""
    found = parse(ref)
    return found[1] if found else None


def delete(refs):
    """删掉这些原件与它们的预览（删票后的后台任务调用）。"""
    oracle_names, gcs_prefixes = [], []
    for ref in refs or []:
        found = parse(ref)
        if not found:
            continue
        place, stored = found
        if place == ORACLE:
            oracle_names.append(stored[0])
        else:
            bucket_name, name, _generation = stored
            gcs_prefixes.append((bucket_name, name))
            gcs_prefixes.append((bucket_name, links.preview_prefix(name)))
    if oracle_names:
        oracle.delete(oracle_names)
    for bucket_name, prefix in gcs_prefixes:
        gcs.delete_prefix(bucket_name, prefix)


__all__ = ["GCS", "LOCAL", "ORACLE", "begin_upload", "delete", "download_url", "finish_upload", "object_name", "parse",
           "preview_url", "put", "read", "recognition_reference", "render", "where"]
