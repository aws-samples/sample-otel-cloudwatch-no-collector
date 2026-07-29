# Sending OTel Logs Directly to CloudWatch (No Collector)

This guide covers every configuration needed to send OpenTelemetry logs directly to CloudWatch's native OTLP endpoint.

## Endpoint

```
https://logs.<region>.amazonaws.com/v1/logs
```

Example: `https://logs.us-east-1.amazonaws.com/v1/logs`

The full path `/v1/logs` must be included. The `OTLPLogExporter` does not auto-append this path when you pass the endpoint directly to the constructor (auto-append only happens with the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable).

## SigV4 Authentication

| Setting | Value |
|---------|-------|
| Service name | `logs` |
| Region | Your AWS region (e.g., `us-east-1`) |
| Credentials | Instance profile, environment variables, or `~/.aws/credentials` |

```python
session = SigV4Session(service="logs", region="us-east-1")
```

## Required Headers

CloudWatch Logs requires two routing headers to know where to deliver log records:

```python
headers = {
    "x-aws-log-group": "/otel/demo/direct-logs",
    "x-aws-log-stream": "flask-app",
}
```

Without these headers, CloudWatch returns an error because it doesn't know which log group/stream to target.

## Compression

```python
compression = Compression.NoCompression
```

SigV4 computes a SHA-256 hash of the request body. Using `NoCompression` avoids edge cases with content-encoding headers during signing.

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "logs:DescribeLogGroups",
    "logs:DescribeLogStreams"
  ],
  "Resource": "arn:aws:logs:*:*:log-group:/otel/demo/*"
}
```

Minimum required for sending logs: `logs:PutLogEvents`. The `Create*` and `Describe*` actions are needed for bootstrapping the log group/stream.

## Pre-Created Resources

The log group and stream must exist before the first log is sent:

```bash
aws logs create-log-group --log-group-name /otel/demo/direct-logs --region us-east-1
aws logs create-log-stream --log-group-name /otel/demo/direct-logs --log-stream-name flask-app --region us-east-1
```

On Elastic Beanstalk, `.ebextensions/02-cloudwatch-permissions.config` handles this automatically during deployment.

## Python Dependencies

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
botocore
requests
```

## Complete Exporter Setup

```python
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http import Compression

log_exporter = OTLPLogExporter(
    endpoint="https://logs.us-east-1.amazonaws.com/v1/logs",
    headers={
        "x-aws-log-group": "/otel/demo/direct-logs",
        "x-aws-log-stream": "flask-app",
    },
    compression=Compression.NoCompression,
    session=SigV4Session(service="logs", region="us-east-1"),
)
```

## Wiring the LoggerProvider

```python
from opentelemetry import _logs as logs_api
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

resource = Resource.create({
    "service.name": "otel-cloudwatch-demo",
    "service.version": "1.0.0",
})

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
logs_api.set_logger_provider(logger_provider)
```

## Bridging Python Logging (Optional)

To route standard Python `logging` calls through OTel:

```python
from opentelemetry.sdk._logs import LoggingHandler
import logging

otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
logging.getLogger().addHandler(otel_handler)
logging.getLogger().setLevel(logging.INFO)

# Now this goes to CloudWatch via OTLP:
logger = logging.getLogger("my-app")
logger.info("Hello from OTel!", extra={"custom.key": "value"})
```

## Where to View in AWS Console

**CloudWatch > Log groups > /otel/demo/direct-logs > flask-app**

### Logs Insights Query

```
fields @timestamp, body, severity_text
| filter severity_text = "ERROR"
| sort @timestamp desc
| limit 20
```

## Configuration Checklist

| # | Item | Value |
|---|------|-------|
| 1 | Endpoint | `https://logs.<region>.amazonaws.com/v1/logs` |
| 2 | SigV4 service | `"logs"` |
| 3 | Header: x-aws-log-group | Your log group name |
| 4 | Header: x-aws-log-stream | Your log stream name |
| 5 | Compression | `NoCompression` |
| 6 | IAM | `logs:PutLogEvents` |
| 7 | Pre-created resources | Log group + stream must exist |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| No logs appearing | Log group/stream doesn't exist | Create them before sending |
| 403 Forbidden | IAM missing `logs:PutLogEvents` | Add permission to the role |
| 403 Signature error | Wrong service name in SigV4 | Must be `"logs"` |
| 403 Signature error | Compression enabled | Use `NoCompression` |
| Empty log entries | Not using `LoggingHandler` bridge | Attach OTel handler to Python root logger |
