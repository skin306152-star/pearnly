# iPhone 照片（HEIC / HEIF）上传支持（2026-10-02，本批）

分支 `codex/heic-uploads`，基线 main c5092f48。来源：测试机点页发现 Cowork 录入对 HEIC 回「不支持此文件类型」；压测窗口转用户口径：
「iPhone 默认就是 HEIC，客户把照片传到电脑再上传就会被拒，这是真实场景」，按体验改进做，三个产品收照片的入口统一处理。

## 做法

- 共用层 `pearnly_core/desk/heic.py`：按文件头认 HEIC / HEIF（主品牌 heic/heix/hevc/hevx/heim/heis/mif1/msf1，不按扩展名）→ 按照片方向
  转正、去透明 → JPEG（质量 92）；读不出（损坏、截断）报人话「这张照片读不出来，请另存为 JPEG 后重新上传」。
- `File.before_insert` 钩子（core hooks）：带内容上传的 HEIC 在存之前转成 JPEG、文件名改 `.jpg`——ERP 附件、DMS、Cowork 工作附件等
  所有走 Frappe 原生上传的入口都经过这里。
- Cowork 原件：`files.service.create` 在类型校验前先转；HEIC 不走浏览器直传（原样 PUT 进原件桶就没法先转），`begin_direct` 回
  `{"direct": 0}` 让页面经服务器上传——Cloud Run 不用改。
- ERP 采购 / 销售识别：钩子上线前存下的 HEIC 附件，读之前转成 JPEG。
- LINE 送来的照片 LINE 已经是 JPEG，不经这里。
- 镜像：`pi-heif==1.4.0`（pillow-heif 的只解码版，BSD-3-Clause；轮子自带 libheif / libde265，LGPL，不带 x265 编码器；cp314
  manylinux_2_28 x86_64），登记进兼容矩阵。

## 验证

- `tests/test_heic_uploads.py`（进候选离线道）：按文件头认、转出同尺寸 JPEG、损坏拒收、原生上传钩子（bytes / base64 两种）、
  其他文件不动、三个产品与镜像都接上。样本 `tests/fixtures/synthetic-64x48.heic` 是 sips 生成的合成纯色图，不含任何真实内容。
- 生产镜像（无 pi-heif）：6 项通过、2 项跳过；同镜像一次性容器里加装 pi-heif 1.4.0：6 项全过，`pip check` 无新增冲突。
- 还缺：新镜像构建后在测试机点 Cowork 录入、ERP 采购 / 销售附件各传一张 HEIC（识别暂停，只看是否转成 JPEG 存下）。
