# 甲骨文 free-arm（ARM）上的 claude-ocr 服务（ADR-022）：只有 pearnly_core.claude_ocr（纯计算）+ Anthropic SDK，与生产镜像同一版本。
# 在那台上原生构建（scripts/claude-ocr/deploy.sh 把包拷进构建目录）。
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN pip install --no-cache-dir "anthropic==1.8.0"
COPY pearnly_core /app/pearnly_core
WORKDIR /app
USER 65534:65534
ENTRYPOINT ["python", "-m", "pearnly_core.claude_ocr.server"]
