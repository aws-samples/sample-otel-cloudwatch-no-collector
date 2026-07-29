# Sending OTel Metrics Directly to CloudWatch (No Collector)

This guide covers every configuration needed to send OpenTelemetry metrics directly to CloudWatch's native OTLP endpoint.

## Endpoint

```
https://monitoring.<region>.amazonaws.com/v1/metrics
```

Example: `https://monitoring.us-east-1.amazonaws.com/v1/metrics`

The full path `/v1/metrics` must be included in the endpoint URL.

## SigV4 Authentication

| Setting | Value |
|---------|-------|
| Service name | `monitoring` |
| Region | Your AWS region (e.g., `us-east-1`) |
| Credentials | Instance profile, environment variables, or `~/.aws/credentials` |

```python
session = SigV4Session(service="monitoring", region="us-east-1")
```

> **Important:** The service name is `"monitoring"`, not `"cloudwatch"`.

## Required Headers

None. Unlike logs, metrics do not require any custom routing headers. The metric namespace in CloudWatch is derived from the `service.name` resource attribute.

## Compression

```python
compression = Compression.NoCompression
```

Same as logs — SigV4 signs the raw body, so no compression to avoid signature mismatches.

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "cloudwatch:PutMetricData"
  ],
  "Resource": "*"
}
```

This is the only permission needed. The `Resource` must be `"*"` because `PutMetricData` does not support resource-level restrictions.

## Pre-Created Resources

None. Unlike logs, you don't need to create anything beforehand. Metrics are automatically routed to the correct namespace based on your resource attributes.

## Python Dependencies

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
botocore
requests
```

No extra packages needed beyond the standard OTel SDK and OTLP exporter.

## Complete Exporter Setup

```python
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http import Compression

metric_exporter = OTLPMetricExporter(
    endpoint="https://monitoring.us-east-1.amazonaws.com/v1/metrics",
    compression=Compression.NoCompression,
    session=SigV4Session(service="monitoring", region="us-east-1"),
)
```

## Wiring the MeterProvider

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

resource = Resource.create({
    "service.name": "otel-cloudwatch-demo",
    "service.version": "1.0.0",
})

metric_reader = PeriodicExportingMetricReader(
    metric_exporter,
    export_interval_millis=30000,  # Export every 30 seconds
)

meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
```

The `export_interval_millis` controls how often metrics are flushed to CloudWatch. Default is 60 seconds; we use 30 seconds for faster feedback during demos.

## Creating and Using Instruments

```python
meter = metrics.get_meter("otel-cloudwatch-demo", "1.0.0")

# Counter: increments on each request
request_counter = meter.create_counter(
    name="http.server.request.count",
    description="Total HTTP requests",
    unit="1",
)

# Histogram: tracks request duration distribution
request_duration = meter.create_histogram(
    name="http.server.request.duration",
    description="HTTP request duration",
    unit="ms",
)

# Usage in your handler:
request_counter.add(1, {"http.route": "/api/hello", "http.method": "GET"})
request_duration.record(42.5, {"http.route": "/api/hello"})
```

### Available Instrument Types

| Type | Use Case | Example |
|------|----------|---------|
| Counter | Monotonically increasing values | Request count, errors, bytes sent |
| Histogram | Distribution of values | Latency, request size, order values |
| UpDownCounter | Values that go up and down | Active connections, queue depth |
| Gauge | Point-in-time measurements | CPU usage, memory, temperature |

## Where to View in AWS Console

**CloudWatch > Metrics > All metrics**

Look for your metrics under the namespace derived from `service.name`. Metrics will have labels (dimensions) from the attributes you pass when recording values.

You can also use:
- **CloudWatch > Metrics > Explorer** for graphing
- **CloudWatch > Alarms** to alert on thresholds

### Metrics Available in This Demo

| Metric Name | Type | Labels |
|-------------|------|--------|
| `http.server.request.count` | Counter | `http.route`, `http.method` |
| `http.server.request.duration` | Histogram | `http.route` |
| `http.server.error.count` | Counter | `http.route`, `error.type` |
| `app.orders.count` | Counter | `order.status` |
| `app.orders.value` | Histogram | `order.currency` |

## Configuration Checklist

| # | Item | Value |
|---|------|-------|
| 1 | Endpoint | `https://monitoring.<region>.amazonaws.com/v1/metrics` |
| 2 | SigV4 service | `"monitoring"` |
| 3 | Headers | None required |
| 4 | Compression | `NoCompression` |
| 5 | IAM | `cloudwatch:PutMetricData` |
| 6 | Pre-created resources | None |
| 7 | Export interval | Configurable (default 60s, demo uses 30s) |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| No metrics appearing | Export interval hasn't elapsed | Wait 30-60 seconds after generating traffic |
| 403 Forbidden | IAM missing `cloudwatch:PutMetricData` | Add permission to the role |
| 403 Signature error | Wrong service name | Must be `"monitoring"` (not `"cloudwatch"`) |
| 403 Signature error | Compression enabled | Use `NoCompression` |
| Metrics in wrong namespace | Wrong `service.name` in Resource | Update Resource attributes |
| Metrics not aggregating | Missing labels/attributes | Ensure consistent attribute keys |

## Notes

- Metrics are aggregated by the SDK before export. The `PeriodicExportingMetricReader` batches data points.
- CloudWatch OTLP metrics use labels (key-value pairs) instead of traditional CloudWatch dimensions. They appear under a different section in the metrics explorer.
- The metric namespace is derived from your OTel Resource attributes, not from a header like logs use.
