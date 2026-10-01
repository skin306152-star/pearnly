#!/usr/bin/env bash
# 部署 Cowork 多页对账文档的 Cloud Workflows（分页预热 + 整份读；任务记录 docs/work/2026-10-01-cowork-ocr-preview-tasks.md）。
# 在开发机上跑，需要 gcloud 已登录。首次要先开通 Workflows / Workflow Executions / Cloud Tasks 接口、建队列、给服务账号授权
# （见任务记录「要开通的外部配置」，需用户确认）。Workflows 按执行的步骤计费、没有常驻费用；不建定时器。
set -euo pipefail
cd "$(dirname "$0")/../.."
PROJECT=pearnly
REGION=asia-southeast1
WORKFLOW=${PEARNLY_OCR_WORKFLOW:-cowork-document}
ACCOUNT=pearnly-ocr-sa@pearnly.iam.gserviceaccount.com
[ -z "$(git status --porcelain -- infrastructure/cloud-workflows)" ] || {
  echo "Workflows 定义有未提交改动：只部署已提交的版本" >&2; exit 1; }
gcloud workflows deploy "$WORKFLOW" --project "$PROJECT" --location "$REGION" \
  --source infrastructure/cloud-workflows/cowork-document.yaml --service-account "$ACCOUNT" \
  --call-log-level log-errors-only --labels app=pearnly,role=ocr \
  --description "Cowork recon documents: warm pages in parallel, then read whole (rev $(git rev-parse --short=12 HEAD))"
gcloud workflows describe "$WORKFLOW" --project "$PROJECT" --location "$REGION" --format="value(name,revisionId,state)"
