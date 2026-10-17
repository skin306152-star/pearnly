# 甲骨文 free-arm（ARM）上的服务（ADR-022 / ADR-023）：Claude 识别 + 原件存储（ADR-024 各产品共用：存、核对、预览、下载）。
# 只带纯计算的包：pearnly_core.claude_ocr、pearnly_core.ocr_models（方向模型）、pearnly_core.originals 的检查与原件服务；
# 依赖版本与生产镜像一致。在那台上原生构建（scripts/claude-ocr/deploy.sh 把包拷进构建目录）。
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
RUN pip install --no-cache-dir "anthropic==1.8.0" "pymupdf==1.28.2" "pillow==12.3.0" "numpy==2.5.3" "onnxruntime==1.30.0"
COPY app /app
WORKDIR /app
USER 65534:65534
ENTRYPOINT ["python", "/app/serve.py"]
