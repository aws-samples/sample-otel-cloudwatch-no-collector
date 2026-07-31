# Sending OTel Traces Directly to CloudWatch / X-Ray (No Collector)

This guide covers every configuration needed to send OpenTelemetry traces directly to the X-Ray OTLP endpoint. Traces appear in CloudWatch under X-Ray traces / Transaction Search.

## Endpoint

```
https://xray.<region>.amazonaws.com/v1/traces
```

Example: `https://xray.us-east-1.amazonaws.com/v1/traces`

The full path `/v1/traces` must be included in the endpoint URL.

## SigV4 Authentication

| Setting | Value |
|---------|-------|
| Service name | `xray` |
| Region | Your AWS region (e.g., `us-east-1`) |
| Credentials | Instance profile, environment variables, or `~/.aws/credentials` |

```python
session = SigV4Session(service="xray", region="us-east-1")
```

## Required Headers

None. Unlike logs, traces do not require any custom routing headers.

## Compression

```python
compression = Compression.NoCompression
```

Same as other signals — SigV4 signs the raw body.

## AWS X-Ray ID Generator (Critical)

This is the most important configuration for traces. X-Ray requires trace IDs where the first 4 bytes are a Unix timestamp. The default OpenTelemetry random ID generator produces fully random trace IDs that **X-Ray silently drops** — no errors, just no traces appearing.

```python
from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator

tracer_provider = TracerProvider(
    resource=resource,
    id_generator=AwsXRayIdGenerator(),  # REQUIRED for X-Ray
)
```

Without this, traces are sent successfully (no 403, no errors in logs) but never appear in the console. This is the single most common reason traces "don't work" with X-Ray.

### Extra dependency required:

```
opentelemetry-sdk-extension-aws>=2.0.0
```

## Transaction Search (Account-Level Prerequisite)

Transaction Search must be enabled in your AWS account for the X-Ray OTLP endpoint to work. This is a **one-time account-level setup** that persists even if you destroy and recreate environments.

### How to Enable

1. Go to **CloudWatch > Settings** (bottom of left sidebar)
2. Click the **Traces** tab
3. Enable **Transaction Search**
4. Wait 2-3 minutes for the internal log group to be provisioned

### How to Verify

```bash
aws xray get-trace-segment-destination --region us-east-1
```

Expected output:
```json
{
    "Destination": "CloudWatchLogs",
    "Status": "ACTIVE"
}
```

If Status is not `ACTIVE`, wait a few minutes or toggle Transaction Search off and back on.

### The "Log Group Does Not Exist" Error

After enabling Transaction Search, you may see this error in your application logs:

```
ERROR opentelemetry.exporter.otlp.proto.http.trace_exporter: Failed to export batch
code: 400, reason: The specified log group does not exist.
(Service: CloudWatchLogs, Status Code: 400)
```

This means Transaction Search's internal vended log group hasn't been created yet. **Fix:**

1. Go to **CloudWatch > Settings > Traces**
2. **Disable** Transaction Search → Save
3. Wait 60 seconds
4. **Re-enable** Transaction Search → Save
5. Wait 2-3 minutes for full provisioning

The service will auto-create the required vended log group. You cannot manually create this log group — the service manages it internally.

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "xray:PutTraceSegments",
    "xray:PutTelemetryRecords"
  ],
  "Resource": "*"
}
```

Alternatively, attach the AWS managed policy: `AWSXrayWriteOnlyPolicy`

## Pre-Created Resources

None (beyond enabling Transaction Search at the account level). The X-Ray service handles all storage internally.

## Python Dependencies

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
opentelemetry-sdk-extension-aws    # For AwsXRayIdGenerator
botocore
requests
```

## Complete Exporter Setup

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http import Compression

span_exporter = OTLPSpanExporter(
    endpoint="https://xray.us-east-1.amazonaws.com/v1/traces",
    compression=Compression.NoCompression,
    session=SigV4Session(service="xray", region="us-east-1"),
)
```

## Wiring the TracerProvider

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
from opentelemetry.sdk.resources import Resource

resource = Resource.create({
    "service.name": "otel-cloudwatch-demo",
    "service.version": "1.0.0",
})

tracer_provider = TracerProvider(
    resource=resource,
    id_generator=AwsXRayIdGenerator(),  # REQUIRED
)
tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
trace.set_tracer_provider(tracer_provider)
```

## Creating Spans

```python
tracer = trace.get_tracer("otel-cloudwatch-demo", "1.0.0")

# Simple span
with tracer.start_as_current_span("my-operation") as span:
    span.set_attribute("http.method", "GET")
    span.set_attribute("http.route", "/api/hello")
    # ... your code ...

# Nested spans (appear as child spans in the trace timeline)
with tracer.start_as_current_span("parent-operation") as parent:
    parent.set_attribute("request.id", "abc-123")

    with tracer.start_as_current_span("child-step-1") as child:
        child.set_attribute("step", "validation")
        # ...

    with tracer.start_as_current_span("child-step-2") as child:
        child.set_attribute("step", "processing")
        # ...

# Recording errors
with tracer.start_as_current_span("risky-operation") as span:
    try:
        result = 1 / 0
    except ZeroDivisionError as exc:
        span.set_status(trace.StatusCode.ERROR, "Division by zero")
        span.record_exception(exc)
```

## Where to View in AWS Console

**Primary:** CloudWatch > X-Ray traces > Transaction Search

You can also find traces at:
- **CloudWatch > X-Ray traces > Traces** — filter by `service("otel-cloudwatch-demo")`
- **CloudWatch > X-Ray traces > Service Map** — visualize service topology

### Useful Trace Queries

```
# Find all traces from your service
service("otel-cloudwatch-demo")

# Find error traces
service("otel-cloudwatch-demo") { error = true }

# Find slow traces (>500ms)
service("otel-cloudwatch-demo") { responsetime > 0.5 }
```

## Configuration Checklist

| # | Item | Value |
|---|------|-------|
| 1 | Endpoint | `https://xray.<region>.amazonaws.com/v1/traces` |
| 2 | SigV4 service | `"xray"` |
| 3 | Headers | None required |
| 4 | Compression | `NoCompression` |
| 5 | ID Generator | `AwsXRayIdGenerator()` **(critical)** |
| 6 | IAM | `xray:PutTraceSegments`, `xray:PutTelemetryRecords` |
| 7 | Transaction Search | Must be enabled (account-level, one-time) |
| 8 | Extra dependency | `opentelemetry-sdk-extension-aws` |
| 9 | SigV4 signed header set | Minimal (`Content-Type` only) — X-Ray rejects mismatches from urllib3 header normalization if you sign everything |

## SigV4 Header-Signing Gotcha (X-Ray Only)

The X-Ray OTLP endpoint enforces SigV4 more strictly than the Logs and Metrics endpoints. If your `SigV4Session` copies every header from the already-prepared `requests` object into the AWS signature — the natural implementation — you will see:

```
Failed to export batch code: 403, reason:
The request signature we calculated does not match the signature you provided.
```

...on traces only, while logs and metrics keep working. The reason: `requests` adds session defaults (`User-Agent`, `Accept-Encoding`, `Connection`, `Accept`) and a computed `Content-Length` before `send()` runs. If those all land in the signed header set, `urllib3` may normalize or drop some of them (e.g. `Connection` on HTTP/1.1) before the bytes leave the box, and the server's re-computed signature no longer matches.

**Fix:** sign only a minimal, stable header set. `Content-Type` is enough — botocore's `add_auth()` will inject `Host`, `X-Amz-Date`, `X-Amz-Content-SHA256`, `X-Amz-Security-Token` (if using temp creds), and `Authorization` itself. Everything else stays unsigned and can be mutated freely.

```python
def send(self, prepared_request, **kwargs):
    body = prepared_request.body or b""

    content_type = prepared_request.headers.get(
        "Content-Type", "application/x-protobuf"
    )
    aws_request = AWSRequest(
        method=prepared_request.method,
        url=prepared_request.url,
        headers={"Content-Type": content_type},   # minimal signed set
        data=body,
    )

    credentials = self._credentials.get_frozen_credentials()
    SigV4Auth(credentials, self._service, self._region).add_auth(aws_request)

    prepared_request.headers.update(dict(aws_request.headers))
    return super().send(prepared_request, **kwargs)
```

This mirrors the pattern used in the AWS Distro for OpenTelemetry (ADOT) Python distribution — see `AwsAuthSession` in [aws-otel-python-instrumentation](https://github.com/aws-observability/aws-otel-python-instrumentation).

## Debugging Exporter Failures

The OTLP trace exporter reports failures through Python's `logging` module. If your logging bridge only attaches an OTel `LoggingHandler` (routing everything back through the OTLP log pipeline to CloudWatch), you have a chicken-and-egg problem: the message telling you *why traces are failing* also has to survive a working OTLP path.

Attach a stdout `StreamHandler` alongside the OTel handler so exporter errors show up in `/var/log/web.stdout.log` (or `journalctl -u web`) regardless of pipeline state:

```python
otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s"
))

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(otel_handler)
root.addHandler(stdout_handler)
```

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Traces silently disappear (no errors) | Missing `AwsXRayIdGenerator` | Add `id_generator=AwsXRayIdGenerator()` to TracerProvider |
| 400 "log group does not exist" | Transaction Search log group not provisioned | Toggle Transaction Search off/on in console, wait 2-3 min |
| No traces at all | Transaction Search not enabled | Enable in CloudWatch > Settings > Traces |
| 403 Forbidden | IAM missing X-Ray permissions | Add `xray:PutTraceSegments` or attach `AWSXrayWriteOnlyPolicy` |
| 403 Signature error | Wrong service name | Must be `"xray"` (not `"xray-traces"` or `"traces"`) |
| 403 Signature error | Compression enabled | Use `NoCompression` |
| 403 Signature mismatch on traces only, logs/metrics OK | Signing too many mutable headers (User-Agent, Accept-Encoding, ...) | Sign a minimal header set (`Content-Type`); let `add_auth()` add the rest. See "SigV4 Header-Signing Gotcha" above |
| Exporter errors invisible in logs | Only OTel LoggingHandler attached | Add a stdout StreamHandler so failures surface in `/var/log/web.stdout.log` |
| Traces appear but no nested spans | Not using context properly | Ensure child spans are created within `with` block of parent |
| Can't find traces in console | Wrong time range | Set time filter to "Last 1 hour" and click "Run query" |
| Can't find traces in console | Looking in wrong place | Use Transaction Search, not the old X-Ray console |

## Key Differences from Logs and Metrics

| Aspect | Traces | Logs | Metrics |
|--------|--------|------|---------|
| Endpoint service | `xray` | `logs` | `monitoring` |
| ID Generator | `AwsXRayIdGenerator` required | Default | Default |
| Routing headers | None | `x-aws-log-group/stream` | None |
| Pre-requisite | Transaction Search enabled | Log group exists | None |
| Extra dependency | `opentelemetry-sdk-extension-aws` | None | None |
| Console location | X-Ray traces > Transaction Search | Log groups | Metrics > All metrics |
| Latency to appear | ~15 seconds | ~5 seconds | ~30 seconds (export interval) |
| SigV4 header-set strictness | Strict — sign minimal set | Lenient | Lenient |
