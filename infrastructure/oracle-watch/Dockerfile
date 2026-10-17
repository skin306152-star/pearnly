# 甲骨文那台（ARM）上的守望与演练工具镜像：只有 Python 标准库 + 读谷歌存储要的两个包。在那台上原生构建（scripts/oracle-watch/deploy.sh）。
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN pip install --no-cache-dir google-auth==2.40.3 requests==2.32.4
COPY watch.py /app/watch.py
ENTRYPOINT ["python", "/app/watch.py"]
