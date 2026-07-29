# Security Policy

## Reporting a Vulnerability

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/) or directly via email to aws-security@amazon.com.

Please do **not** create a public GitHub issue for security-related concerns.

## Scope

This project is sample code that demonstrates how to send OpenTelemetry logs, metrics, and traces directly to Amazon CloudWatch using native OTLP HTTP endpoints, without an OpenTelemetry Collector. It uses the following AWS services and libraries:

- **Amazon CloudWatch Logs** (OTLP endpoint, SigV4-signed)
- **Amazon CloudWatch Metrics** (OTLP endpoint, SigV4-signed)
- **AWS X-Ray** (OTLP endpoint, SigV4-signed; Transaction Search)
- **AWS Elastic Beanstalk** (Python platform, single-instance environment)
- **AWS IAM** (instance profile permissions attached via `.ebextensions`)

Key dependencies:

- `Flask`, `gunicorn`
- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
- `opentelemetry-sdk-extension-aws` (for the X-Ray-compatible trace ID generator)
- `botocore`, `boto3`, `requests`

## Security Considerations for Users of This Sample

This is demonstration code intended for learning purposes. Before adapting it for production use, review the following:

- **Credentials**: The sample relies on the AWS default credential chain (instance profile on Elastic Beanstalk). Never hardcode access keys.
- **IAM scope**: The `.ebextensions/02-cloudwatch-permissions.config` policies use `Resource: "*"` for `cloudwatch:PutMetricData`, `xray:PutTraceSegments`, and `xray:PutTelemetryRecords` because those actions do not support resource-level restrictions. The CloudWatch Logs policy is scoped to `arn:aws:logs:*:*:log-group:/otel/demo/*`. Scope the log group ARN further to your account and region in production.
- **Log group encryption and retention**: The demo creates a CloudWatch log group without customer-managed KMS encryption or a retention policy. For production, configure a retention policy and consider a customer-managed KMS key.
- **HTTPS/TLS**: The sample deploys with Elastic Beanstalk defaults (HTTP). For any real workload, add HTTPS via a Load Balancer with an ACM certificate.
- **Public exposure**: The Flask endpoints are unauthenticated. Do not deploy real business data on this pattern without adding authentication and authorization.
- **Dependency vulnerabilities**: Run `pip-audit` or your preferred dependency scanner against `requirements.txt` before deploying.

## Supported Versions

This is sample code and does not maintain long-term support branches. The `main` branch is the only supported version.
