# AWS Elastic Beanstalk Deployment

This project is prepared for a simple AWS Elastic Beanstalk Python deployment.

## Required Environment Variables

Set these in Elastic Beanstalk configuration, not in git:

```text
HELIUM_ENV=production
DASHBOARD_PASSWORD=...
FLASK_SECRET_KEY=...
SENDY_BASE_URL=https://helium.sg
SENDY_API_KEY=...
EMAILLISTVERIFY_API_KEY=...
DASHBOARD_COOKIE_SECURE=true
MAX_UPLOAD_MB=100
```

Set the Elastic Beanstalk load balancer health check path to:

```text
/healthz
```

Optional:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
SENDY_DEFAULT_FROM_NAME=Helium
SENDY_DEFAULT_FROM_EMAIL=hello@helium.sg
SENDY_DEFAULT_REPLY_TO=hello@helium.sg
```

`DASHBOARD_COOKIE_SECURE=true` is enabled because the app is served through CloudFront HTTPS.

## Deploy

Authenticate AWS locally first:

```bash
aws login
```

Then deploy an application version:

```bash
AWS_REGION=ap-southeast-1 \
EB_APP_NAME=helium-edm-ops \
EB_ENV_NAME=helium-edm-ops-prod \
./scripts/deploy_aws_eb.sh
```

The script packages the app, uploads it to S3, creates an Elastic Beanstalk application version, and updates the target environment if it already exists.

Current deployed environment:

```text
Application: helium-edm-ops
Environment: helium-edm-ops-prod
Region: ap-southeast-1
URL: http://helium-edm-ops-prod.eba-tfa3sapy.ap-southeast-1.elasticbeanstalk.com
Version: 97f8733-20260506144713
```

Current HTTPS front door:

```text
Domain: https://demo.helium.sg
CloudFront distribution: E3I851YFE9Y1Y7
ACM certificate: arn:aws:acm:us-east-1:930382914692:certificate/9ca37e94-8617-4fcb-9c67-00f884cbe81c
Route 53 zone: helium.sg
```

## Storage Note

Run artifacts are stored on the instance filesystem under `runs/`. For long-term production retention, add S3 artifact storage later.
