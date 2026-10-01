#!/usr/bin/env bash
# 构建并部署 Cowork 发票识别的 Cloud Run 服务（ADR-015）。在开发机上跑，需要 gcloud 已登录且对 pearnly 项目有权限。
# 首次建资源（镜像仓库、存储桶 7 天删除、服务账号的桶权限与调用权限）见 docs/work/2026-10-01-ocr-cloud-run.md。
# 只做构建、推送、更新服务；不改最少实例数（必须是 0：空闲零花费，用户规矩），不开任何定时任务。
set -euo pipefail
cd "$(dirname "$0")/../.."
PROJECT=pearnly
REGION=asia-southeast1
# 测试环境用单独的服务（PEARNLY_OCR_SERVICE=cowork-ocr-test，ADR-016）：试未合入的识别代码时不碰线上那个服务。
SERVICE=${PEARNLY_OCR_SERVICE:-cowork-ocr}
ACCOUNT=pearnly-ocr-sa@pearnly.iam.gserviceaccount.com
BUCKET=pearnly-ocr-checkpoints-sg
ORIGINALS=pearnly-cowork-originals-sg   # 浏览器直传的原件（ADR-016）：Cloud Run 只读、核对不通过的删掉
[ -z "$(git status --porcelain -- apps/pearnly_core apps/pearnly_cowork infrastructure/cloud-run)" ] || {
  echo "识别代码有未提交改动：只部署已提交的版本" >&2; exit 1; }
TAG="$REGION-docker.pkg.dev/$PROJECT/pearnly/$SERVICE:$(git rev-parse --short=12 HEAD)"
docker buildx build --platform linux/amd64 --load -t "$TAG" -f infrastructure/cloud-run/cowork-ocr.Dockerfile .
docker push "$TAG"
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$TAG" --service-account "$ACCOUNT" \
  --no-allow-unauthenticated --cpu 2 --memory 4Gi --concurrency 8 --min-instances 0 --max-instances 20 \
  --timeout 600 --cpu-boost --set-env-vars "PEARNLY_OCR_BUCKET=$BUCKET,PEARNLY_ORIGINALS_BUCKET=$ORIGINALS,OCR_THREADS=8,OCR_PAGE_THREADS=8" \
  --labels app=pearnly,role=ocr --quiet
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url,spec.template.metadata.annotations['autoscaling.knative.dev/minScale'])"
