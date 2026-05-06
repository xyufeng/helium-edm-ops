#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${EB_APP_NAME:-helium-edm-ops}"
ENV_NAME="${EB_ENV_NAME:-helium-edm-ops-prod}"
REGION="${AWS_REGION:-ap-southeast-1}"
BUCKET="${EB_DEPLOY_BUCKET:-${APP_NAME}-${REGION}-deployments}"
VERSION_LABEL="${VERSION_LABEL:-$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)}"
ZIP_PATH="dist/${APP_NAME}-${VERSION_LABEL}.zip"

if ! aws sts get-caller-identity --region "$REGION" >/dev/null 2>&1; then
  echo "AWS credentials not configured. Run: aws login" >&2
  exit 1
fi

mkdir -p dist
rm -f "$ZIP_PATH"

zip -qr "$ZIP_PATH" . \
  -x ".git/*" \
  -x ".venv/*" \
  -x ".env" \
  -x "runs/*" \
  -x "dist/*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x ".pytest_cache/*"

if ! aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  aws s3 mb "s3://${BUCKET}" --region "$REGION"
fi

aws s3 cp "$ZIP_PATH" "s3://${BUCKET}/${APP_NAME}/${VERSION_LABEL}.zip" --region "$REGION"

aws elasticbeanstalk describe-applications \
  --application-names "$APP_NAME" \
  --region "$REGION" \
  --query "Applications[0].ApplicationName" \
  --output text >/tmp/eb_app_name.txt 2>/dev/null || true

if [[ "$(cat /tmp/eb_app_name.txt 2>/dev/null || true)" != "$APP_NAME" ]]; then
  aws elasticbeanstalk create-application \
    --application-name "$APP_NAME" \
    --region "$REGION" >/dev/null
fi

aws elasticbeanstalk create-application-version \
  --application-name "$APP_NAME" \
  --version-label "$VERSION_LABEL" \
  --source-bundle S3Bucket="$BUCKET",S3Key="${APP_NAME}/${VERSION_LABEL}.zip" \
  --region "$REGION" >/dev/null

ENV_STATUS="$(aws elasticbeanstalk describe-environments \
  --application-name "$APP_NAME" \
  --environment-names "$ENV_NAME" \
  --region "$REGION" \
  --query "Environments[0].Status" \
  --output text 2>/dev/null || true)"

if [[ "$ENV_STATUS" == "None" || -z "$ENV_STATUS" ]]; then
  cat >&2 <<EOF
Application version uploaded, but the Elastic Beanstalk environment does not exist yet.

Create the environment once in AWS Console or CLI with the required IAM instance profile,
then rerun this script:
  EB_APP_NAME=$APP_NAME EB_ENV_NAME=$ENV_NAME AWS_REGION=$REGION scripts/deploy_aws_eb.sh

Version label ready: $VERSION_LABEL
EOF
  exit 0
fi

aws elasticbeanstalk update-environment \
  --application-name "$APP_NAME" \
  --environment-name "$ENV_NAME" \
  --version-label "$VERSION_LABEL" \
  --region "$REGION" >/dev/null

echo "Deploy started: $APP_NAME / $ENV_NAME / $VERSION_LABEL"
