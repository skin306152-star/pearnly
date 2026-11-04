"""流程用例：上传框「拍照」（电脑）—— 相机打不开时给相机权限第 ③ 步的提示（去地址栏开权限），不是框架那句笼统的话。

产品地图 shared.scan.upload.take-photo（11-04 相机入口逐个成行后补的测试；ERP 与 Cowork 共用，这里在 ERP 站点上跑）。
照人的路径：打开框架上传框（`frappe.ui.FileUploader`）→ 点「相机」→ 框架的拍照（`frappe.ui.Capture`）在电脑上开网页相机；
页面里让取相机返回「没有权限」（`NotAllowedError`，同电脑上相机被拒），共用层 `public/desk/scan.js` 改写的 `show_for_desktop`
应当弹出术语表 `common.scan.denied` 那一句，并且**不进站点 Error Log**（11-05 主会话：相机被拒是正常情况，只提示、不上报；
run.py 每格自带的「client errors in Error Log」检查就是这条断言）。只在电脑宽度有意义（手机上「拍照」调手机自己的相机应用）。
不落库：库里核的是这一遍没有多出上传文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS  # noqa: E402

TITLE = "上传框拍照（电脑）：相机打不开给第 ③ 步提示"


def prepare(lab, manifest):
    files = lab.python(manifest["site"], "print(json.dumps({'files': frappe.db.count('File')}))")["files"]
    return {"files": files, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "拍照", "js": JS + """
frappe.set_route('pearnly-products'); await until(()=>pageOf('pearnly-products'), 20000); await idle();
// 电脑上相机被拒：取相机直接返回 NotAllowedError（同浏览器里关了相机权限）
navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), {name: 'NotAllowedError'}));
new frappe.ui.FileUploader({allow_multiple: false});
const camera=await until(()=>all('.modal.show button, .modal.show .btn').find(b=>b.textContent.trim()===__('Camera')), 15000);
if(!camera) return {error:'no camera button in the uploader'};
camera.click();
const shown=await until(()=>all('.modal.show .msgprint, .modal.show .modal-body').map(e=>e.textContent.trim()).find(t=>t.includes(__('common.scan.denied'))), 15000);
const texts=all('.modal.show .msgprint').map(e=>e.textContent.trim());
return {hint: !!shown, texts, expected: __('common.scan.denied')};"""},
    ]


def judge(params, results, camera_code=None):
    shown = next((r.get("value") or {} for r in results if r.get("step") == "拍照"), {})
    return [{"case": "相机打不开给第 ③ 步提示（不是笼统报错）", "seen": shown, "ok": bool(shown.get("hint"))}]


def verify(lab, manifest, params, camera_code=None):
    files = lab.python(manifest["site"], "print(json.dumps({'files': frappe.db.count('File')}))")["files"]
    return [{"case": "没有多出上传文件", "seen": {"before": params["files"], "after": files}, "ok": files == params["files"]}]
