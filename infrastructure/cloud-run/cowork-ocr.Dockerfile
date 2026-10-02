# Cowork 发票识别的 Cloud Run 镜像（ADR-0015）。构建上下文是仓库根目录：
#   docker buildx build --platform linux/amd64 -f infrastructure/cloud-run/cowork-ocr.Dockerfile .
# 只装识别要用的包、只拷识别要用的代码，不带 Frappe（冷启动要快）。依赖版本与生产 Frappe 镜像逐个一致
# （2026-10-01 从生产镜像 f578381a 读出），同一张原件在两边读出同样的结果；生产镜像升级这些包时这里同步改。
FROM python:3.14.7-slim@sha256:7bf6c3111fe094f8ee1a1cbcdc63c4cfb345b0e3df42d5aa9a90b3b4b022ab6d

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install \
      "pydantic==2.12.5" "pydantic_core==2.41.5" "typing_extensions==4.16.0" "annotated-types==0.8.0" \
      "typing-inspection==0.4.4" \
      "pymupdf==1.28.2" "pillow==12.3.0" "pypdf==6.15.0" \
      "requests==2.33.1" "urllib3==2.7.0" "certifi==2026.7.22" "charset-normalizer==3.5.1" "idna==3.19" \
      "onnxruntime==1.30.0" "numpy==2.5.3" "flatbuffers==25.12.19" "protobuf==7.36.1" "packaging==26.3" \
      "google-auth==2.36.0" "cachetools==5.5.2" "pyasn1==0.6.4" "pyasn1-modules==0.4.2" "rsa==4.9.1" \
      "gunicorn==23.0.0" \
 && useradd --uid 10001 --no-create-home ocr

WORKDIR /app
# 共用层：只要错误类型、本地转正（含方向模型文件）与税票码表 / 金额规则（发票结构校验用）
COPY apps/pearnly_core/pearnly_core/__init__.py pearnly_core/__init__.py
COPY apps/pearnly_core/pearnly_core/execution/__init__.py apps/pearnly_core/pearnly_core/execution/errors.py pearnly_core/execution/
COPY apps/pearnly_core/pearnly_core/ocr_models/__init__.py apps/pearnly_core/pearnly_core/ocr_models/orient.py apps/pearnly_core/pearnly_core/ocr_models/doc_ori.onnx pearnly_core/ocr_models/
# Cowork 识别域（引擎、输入、提供方适配、远程执行）；本域里依赖 Frappe 的模块镜像里不会被导入
COPY apps/pearnly_core/pearnly_core/tax_documents pearnly_core/tax_documents
COPY apps/pearnly_cowork/pearnly_cowork/__init__.py pearnly_cowork/__init__.py
COPY apps/pearnly_cowork/pearnly_cowork/ocr pearnly_cowork/ocr
RUN find /app -name "test*.py" -delete && python -c "import pearnly_cowork.ocr.remote.app" \
 && python -c "import sys; assert 'frappe' not in sys.modules"

USER ocr
ENV PYTHONPATH=/app
# 一个进程、多线程：每个线程跑一份识别（大部分时间在等谷歌）。线程数 = Cloud Run 的单实例并发数。
CMD ["sh", "-c", "exec gunicorn --bind :${PORT:-8080} --workers 1 --threads ${OCR_THREADS:-8} --timeout 0 --graceful-timeout 30 pearnly_cowork.ocr.remote.app:application"]
