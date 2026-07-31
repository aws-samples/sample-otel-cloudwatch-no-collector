# OpenTelemetry to CloudWatch - No Collector Required

Send OpenTelemetry **logs, metrics, and traces** directly to Amazon CloudWatch using native OTLP HTTP endpoints. No collector sidecar, no agent, no extra infrastructure.

## Architecture

```
                                    ┌──────────────────────────────────────┐
                                    │         Amazon CloudWatch            │
┌─────────────────┐                 │                                      │
│                 │  OTLP/HTTP      │  ┌────────────────────────────────┐  │
│   Flask App     │  + SigV4        │  │  CloudWatch Logs               │  │
│  (OTel SDK)     │────────────────►│  │  /otel/demo/direct-logs        │  │
│                 │  /v1/logs        │  └────────────────────────────────┘  │
│                 │                 │                                      │
│                 │  OTLP/HTTP      │  ┌────────────────────────────────┐  │
│                 │  + SigV4        │  │  CloudWatch Metrics            │  │
│                 │────────────────►│  │  (namespace from service.name) │  │
│                 │  /v1/metrics     │  └────────────────────────────────┘  │
│                 │                 │                                      │
│                 │  OTLP/HTTP      │  ┌────────────────────────────────┐  │
│                 │  + SigV4        │  │  X-Ray Traces                  │  │
│                 │────────────────►│  │  (Transaction Search)          │  │
│                 │  /v1/traces      │  └────────────────────────────────┘  │
└─────────────────┘                 │                                      │
                                    └──────────────────────────────────────┘
     No collector needed!
     No sidecar needed!
```

## How It Works

1. The app uses the **OpenTelemetry Python SDK** with **OTLP HTTP Exporters** for all three signals
2. A custom `SigV4Session` signs every outgoing request using AWS IAM credentials
3. Each signal goes to its own dedicated CloudWatch endpoint
4. No compression is used (SigV4 requires signing the raw payload body)
5. Only a **minimal header set** (`Content-Type` plus the SigV4-managed headers) is signed. If you sign every default header `requests` adds (`User-Agent`, `Accept-Encoding`, `Connection`, `Content-Length`, ...), `urllib3` is free to normalize them between signing and sending. Logs and Metrics happen to tolerate this; X-Ray's validator does not and will reject the request with `403 The request signature we calculated does not match`. See `SigV4Session.send()` in `application.py`.
6. OTel SDK diagnostics are attached to both an OTLP log exporter (routed to CloudWatch) and a local `StreamHandler`. This means an exporter failure is visible in `/var/log/web.stdout.log` even if the OTLP log path itself is broken, avoiding a chicken-and-egg debugging problem.

## Signal-Specific Configuration Guides

Each signal has unique endpoint, authentication, IAM, and setup requirements. See the detailed guides:

| Signal | Guide | Endpoint | Where to View |
|--------|-------|----------|---------------|
| Logs | [docs/LOGS.md](docs/LOGS.md) | `https://logs.<region>.amazonaws.com/v1/logs` | CloudWatch > Log groups |
| Metrics | [docs/METRICS.md](docs/METRICS.md) | `https://monitoring.<region>.amazonaws.com/v1/metrics` | CloudWatch > Metrics > All metrics |
| Traces | [docs/TRACES.md](docs/TRACES.md) | `https://xray.<region>.amazonaws.com/v1/traces` | CloudWatch > X-Ray traces > Transaction Search |

## Quick Comparison

| Configuration | Logs | Metrics | Traces |
|---------------|------|---------|--------|
| Endpoint | `logs.<region>.amazonaws.com/v1/logs` | `monitoring.<region>.amazonaws.com/v1/metrics` | `xray.<region>.amazonaws.com/v1/traces` |
| SigV4 service name | `logs` | `monitoring` | `xray` |
| Routing headers | `x-aws-log-group`, `x-aws-log-stream` | None | None |
| IAM permissions | `logs:PutLogEvents` | `cloudwatch:PutMetricData` | `xray:PutTraceSegments` |
| Pre-created resources | Log group + stream | None | Transaction Search enabled |
| Compression | NoCompression | NoCompression | NoCompression |
| ID Generator | Default | Default | **AwsXRayIdGenerator** (required) |
| Extra dependency | None | None | `opentelemetry-sdk-extension-aws` |

## Prerequisites

- Python 3.11+
- AWS CLI configured (`aws configure`)
- EB CLI installed (`pip install awsebcli==3.20.10`)
- **Transaction Search enabled** in CloudWatch (one-time, account-level)

## Quick Start: Deploy to Elastic Beanstalk

You can deploy this to **any AWS region** — the app auto-detects the region at runtime via instance metadata. Just pass your desired region to `eb init`:

```bash
# 1. Initialize (use any supported region)
eb init -p python-3.11 otel-cloudwatch-demo --region <your-region>
# Example: eb init -p python-3.11 otel-cloudwatch-demo --region us-east-1
# Example: eb init -p python-3.11 otel-cloudwatch-demo --region eu-west-1

# 2. Create environment (provisions everything automatically)
eb create otel-demo-env --instance-type t3.micro --single

# 3. Generate traffic
curl http://<your-eb-url>/api/hello
curl http://<your-eb-url>/api/process
curl http://<your-eb-url>/api/order
curl http://<your-eb-url>/api/error
```

The `.ebextensions` configs automatically handle:
- IAM permissions for all three signals
- Log group and stream creation
- Environment variables

> **No code changes needed per region.** The app resolves `AWS_REGION` from the instance metadata at runtime, so the OTLP endpoints and SigV4 signing adapt automatically.

## Verify All Three Signals

**Logs** (immediate):
- CloudWatch Console > Log groups > `/otel/demo/direct-logs` > `flask-app`

**Metrics** (wait ~30 seconds):
- CloudWatch Console > Metrics > All metrics > look for `otel-cloudwatch-demo` namespace

**Traces** (wait ~15 seconds):
- CloudWatch Console > X-Ray traces > Transaction Search
- Query: `service("otel-cloudwatch-demo")`

## Demo Endpoints

| Endpoint | Description | Signals |
|----------|-------------|---------|
| `/` | Home page with links | Log + Trace + Metric |
| `/api/hello` | Simple greeting | Log + Trace + Metric (counter, duration) |
| `/api/process` | Simulated pipeline | Log + Trace (nested spans) + Metric (duration) |
| `/api/error` | Error simulation | Log (stack trace) + Trace (error + exception event) + Metric (error counter) |
| `/api/order` | Order lifecycle | Log (structured) + Trace (nested spans) + Metric (order count, value) |
| `/health` | Health check | None |

## Project Structure

```
otel-cloudwatch-demo/
├── application.py                        # Flask app + OTel logs/metrics/traces + SigV4
├── requirements.txt                      # Python dependencies
├── Procfile                              # Gunicorn config for EB (port 8000)
├── docs/
│   ├── LOGS.md                           # Detailed logs configuration guide
│   ├── METRICS.md                        # Detailed metrics configuration guide
│   └── TRACES.md                         # Detailed traces configuration guide
├── .ebextensions/
│   ├── 01-environment.config             # Environment variables
│   └── 02-cloudwatch-permissions.config  # IAM policies + log group creation
├── .platform/
│   └── nginx/conf.d/proxy.conf           # Nginx proxy settings
├── .ebignore                             # Files excluded from EB deployment
├── .gitignore                            # Files excluded from git
└── README.md
```

## Local Development

```bash
# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
export AWS_REGION=us-east-1
export CW_LOG_GROUP=/otel/demo/direct-logs
export CW_LOG_STREAM=flask-app-local

# 4. Create log group/stream (first time only)
aws logs create-log-group --log-group-name /otel/demo/direct-logs 2>/dev/null || true
aws logs create-log-stream --log-group-name /otel/demo/direct-logs --log-stream-name flask-app-local 2>/dev/null || true

# 5. Run the app
python application.py
```

## Redeployment

```bash
eb deploy
```

## Cleanup

```bash
# Terminate the EB environment
eb terminate otel-demo-env

# Delete the log group (specify the region you deployed to)
aws logs delete-log-group --log-group-name /otel/demo/direct-logs --region <your-region>
```

> **Note:** Transaction Search is an account-level setting and will persist after terminating the environment.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| 502 Bad Gateway | Port mismatch | Ensure Procfile uses `--bind :8000` |
| No logs in CloudWatch | Missing `/v1/logs` in endpoint | Use full URL including path |
| No traces in X-Ray | Transaction Search not enabled | Enable in CloudWatch > Settings > Traces |
| No traces (log group error) | Transaction Search log group not provisioned | Toggle Transaction Search off/on in console |
| No metrics appearing | Export interval not elapsed | Wait 30+ seconds, check CloudWatch Metrics |
| 403 from any endpoint | IAM permissions missing | Check role policies for the specific signal |
| 403 Signature mismatch | Wrong SigV4 service name | Use `"logs"`, `"monitoring"`, or `"xray"` |
| 403 Signature mismatch | Compression enabled | Use `Compression.NoCompression` |
| 403 Signature mismatch on `/v1/traces` only (logs/metrics work) | Signing too many mutable headers (User-Agent, Accept-Encoding, Connection, ...); urllib3 mutates them before send | Sign a minimal header set — only `Content-Type` + the SigV4-managed headers. See `SigV4Session.send()` |
| Exporter errors hidden | Only OTLP LoggingHandler attached, no stdout handler | Add a `StreamHandler` in `setup_logging_bridge` so failures also land in `/var/log/web.stdout.log` |
| Traces silently dropped | Missing AwsXRayIdGenerator | Add `id_generator=AwsXRayIdGenerator()` to TracerProvider |
| "Platform version isn't recommended" alert on the env | Platform version was locked at `eb create` time; a newer patch has shipped | Run `eb upgrade` on the existing env. `default_platform: Python 3.11` in `.elasticbeanstalk/config.yml` is a branch alias, so fresh `eb create` runs pick up the latest recommended version automatically |
| Deployment fails | `.venv` in bundle | Ensure `.ebignore` excludes `.venv/` |

## References

- [CloudWatch OTLP Endpoint](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLPEndpoint.html)
- [Send metrics using OpenTelemetry](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/metrics-otel-send.html)
- [CloudWatch Logs OTLP Endpoint](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_HTTP_Endpoints_OTLP.html)
- [X-Ray OTLP Traces Endpoint](https://aws.amazon.com/about-aws/whats-new/2024/11/application-signals-otel-x-ray-otlp-endpoint-traces)
- [Enable Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Enable-TransactionSearch.html)
- [Collector-less telemetry with ADOT SDK](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLP-UsingADOT.html)
- [AWS X-Ray ID Generator (Python)](https://opentelemetry-python-contrib.readthedocs.io/en/latest/sdk-extension/aws/aws.html)
- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)
