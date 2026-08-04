"""
Demo: Send OpenTelemetry Logs, Metrics & Traces Directly to CloudWatch
No Collector Required!

This Flask application demonstrates CloudWatch's native OTLP endpoints that
allow you to send all three observability signals directly:
  - Logs   → https://logs.<region>.amazonaws.com/v1/logs
  - Metrics → https://monitoring.<region>.amazonaws.com/v1/metrics
  - Traces  → https://xray.<region>.amazonaws.com/v1/traces

No OpenTelemetry Collector sidecar or agent is needed.
"""

import os
import logging
import time
import random
from flask import Flask, jsonify, request

# --- OpenTelemetry Imports (Logs) ---
from opentelemetry import _logs as logs_api
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http import Compression

# --- OpenTelemetry Imports (Traces) ---
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# --- OpenTelemetry Imports (Metrics) ---
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

# --- OpenTelemetry Common ---
from opentelemetry.sdk.resources import Resource

# --- AWS X-Ray ID Generator (required for X-Ray compatible trace IDs) ---
from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator

# --- AWS SigV4 Auth ---
import requests as req_lib
from botocore.session import Session as BotocoreSession
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.utils import InstanceMetadataRegionFetcher
from botocore.exceptions import BotoCoreError

# ============================================================
# Configuration
# ============================================================
# Region resolution order:
#   1. AWS_REGION / AWS_DEFAULT_REGION env vars (explicit override)
#   2. ~/.aws/config profile default
#   3. EC2 IMDS (this is what makes "deploy to any region" work on EB/EC2)
#   4. us-east-1 as a last-resort fallback
#
# NOTE: botocore's Session.get_config_variable("region") does NOT hit IMDS —
# it only reads env vars and ~/.aws/config. On an EB instance where none of
# those are set, we'd silently default to us-east-1 and ship all telemetry
# there regardless of where the app is actually running. We use
# InstanceMetadataRegionFetcher explicitly to close that gap.
def _resolve_region():
    for env_var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(env_var)
        if value:
            return value

    config_region = BotocoreSession().get_config_variable("region")
    if config_region:
        return config_region

    try:
        imds_region = InstanceMetadataRegionFetcher().retrieve_region()
        if imds_region:
            return imds_region
    except BotoCoreError:
        # IMDS unavailable (local dev, disabled, network error) — fall through.
        pass

    return "us-east-1"


AWS_REGION = _resolve_region()
LOG_GROUP_NAME = os.environ.get("CW_LOG_GROUP", "/otel/demo/direct-logs")
LOG_STREAM_NAME = os.environ.get("CW_LOG_STREAM", "flask-app")

# CloudWatch OTLP Endpoints (no collector needed!)
OTLP_LOGS_ENDPOINT = f"https://logs.{AWS_REGION}.amazonaws.com/v1/logs"
OTLP_METRICS_ENDPOINT = f"https://monitoring.{AWS_REGION}.amazonaws.com/v1/metrics"
OTLP_TRACES_ENDPOINT = f"https://xray.{AWS_REGION}.amazonaws.com/v1/traces"


# ============================================================
# SigV4 Authenticated Session
# ============================================================
class SigV4Session(req_lib.Session):
    """A requests session that signs all outgoing requests with SigV4.

    Each AWS service requires its own service name for signing:
      - Logs:    service="logs"
      - Metrics: service="monitoring"
      - Traces:  service="xray"
    """

    def __init__(self, service="logs", region=None):
        super().__init__()
        self._service = service
        self._region = region or AWS_REGION
        self._botocore_session = BotocoreSession()
        self._credentials = self._botocore_session.get_credentials()

    def send(self, prepared_request, **kwargs):
        # Get the body bytes for signing
        body = prepared_request.body or b""

        # IMPORTANT: only sign a minimal, stable header set. If we include
        # every header that requests has already merged in (User-Agent,
        # Accept-Encoding, Connection, Content-Length, ...) they all end up
        # in the SignedHeaders list, and any downstream mutation by urllib3
        # (case, whitespace, dropping Connection on HTTP/1.1, etc.) will
        # break the signature. The X-Ray OTLP endpoint enforces this
        # strictly; CloudWatch Logs / Metrics happen to be more lenient,
        # which is why traces were the only signal returning 403.
        #
        # This mirrors ADOT's AwsAuthSession implementation.
        content_type = prepared_request.headers.get(
            "Content-Type", "application/x-protobuf"
        )
        aws_request = AWSRequest(
            method=prepared_request.method,
            url=prepared_request.url,
            headers={"Content-Type": content_type},
            data=body,
        )

        # Refresh credentials in case they rotated (instance profile)
        credentials = self._credentials.get_frozen_credentials()
        SigV4Auth(credentials, self._service, self._region).add_auth(aws_request)

        # Merge the auth-related headers (Authorization, X-Amz-Date,
        # X-Amz-Content-SHA256, X-Amz-Security-Token, Host) back onto the
        # prepared request. Other headers (User-Agent, Accept-Encoding, ...)
        # remain unsigned and can be mutated by urllib3 without breaking
        # the signature.
        prepared_request.headers.update(dict(aws_request.headers))

        return super().send(prepared_request, **kwargs)


# ============================================================
# Shared Resource (used by all three signal providers)
# ============================================================
resource = Resource.create(
    {
        "service.name": "otel-cloudwatch-demo",
        "service.version": "1.0.0",
        "deployment.environment": "demo",
    }
)


# ============================================================
# Setup OpenTelemetry Logging (Direct to CloudWatch)
# ============================================================
def setup_otel_logging():
    """
    Configure OpenTelemetry to send logs directly to CloudWatch
    via the OTLP HTTP endpoint with SigV4 authentication.
    """
    log_exporter = OTLPLogExporter(
        endpoint=OTLP_LOGS_ENDPOINT,
        headers={
            "x-aws-log-group": LOG_GROUP_NAME,
            "x-aws-log-stream": LOG_STREAM_NAME,
        },
        compression=Compression.NoCompression,
        session=SigV4Session(service="logs", region=AWS_REGION),
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(log_exporter)
    )

    logs_api.set_logger_provider(logger_provider)
    return logger_provider


# ============================================================
# Setup OpenTelemetry Tracing (Direct to X-Ray via OTLP)
# ============================================================
def setup_otel_tracing():
    """
    Configure OpenTelemetry to send traces directly to X-Ray
    via the OTLP HTTP endpoint with SigV4 authentication.

    Traces appear in CloudWatch > X-Ray traces > Traces tab.
    Requires Transaction Search to be enabled in your account.
    """
    span_exporter = OTLPSpanExporter(
        endpoint=OTLP_TRACES_ENDPOINT,
        compression=Compression.NoCompression,
        session=SigV4Session(service="xray", region=AWS_REGION),
    )

    tracer_provider = TracerProvider(resource=resource, id_generator=AwsXRayIdGenerator())
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    trace.set_tracer_provider(tracer_provider)
    return tracer_provider


# ============================================================
# Setup OpenTelemetry Metrics (Direct to CloudWatch)
# ============================================================
def setup_otel_metrics():
    """
    Configure OpenTelemetry to send metrics directly to CloudWatch
    via the OTLP HTTP endpoint with SigV4 authentication.

    Metrics appear in CloudWatch > Metrics > All metrics under
    the namespace derived from service.name resource attribute.
    """
    metric_exporter = OTLPMetricExporter(
        endpoint=OTLP_METRICS_ENDPOINT,
        compression=Compression.NoCompression,
        session=SigV4Session(service="monitoring", region=AWS_REGION),
    )

    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=30000,  # Export every 30 seconds
    )

    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    return meter_provider


# ============================================================
# Bridge Python logging to OpenTelemetry
# ============================================================
def setup_logging_bridge(logger_provider):
    """
    Bridge standard Python logging to OpenTelemetry so that
    regular log statements are exported via OTLP.

    Also attaches a StreamHandler so that OTel SDK diagnostics
    (e.g. OTLP exporter failures) are visible in the local
    stdout log (gunicorn / EB /var/log/web.stdout.log). Without
    this, exporter errors only reach CloudWatch via the OTLP
    log pipeline, which is circular when that pipeline is the
    thing that's failing.
    """
    from opentelemetry.sdk._logs import LoggingHandler

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    otel_handler = LoggingHandler(
        level=logging.INFO, logger_provider=logger_provider
    )
    root.addHandler(otel_handler)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    )
    root.addHandler(stdout_handler)


# ============================================================
# Initialize All Three Signals
# ============================================================
logger_provider = setup_otel_logging()
tracer_provider = setup_otel_tracing()
meter_provider = setup_otel_metrics()
setup_logging_bridge(logger_provider)

logger = logging.getLogger("otel-cloudwatch-demo")

# Get a tracer and meter for instrumentation
tracer = trace.get_tracer("otel-cloudwatch-demo", "1.0.0")
meter = metrics.get_meter("otel-cloudwatch-demo", "1.0.0")

# ============================================================
# Define Metrics Instruments
# ============================================================
# Counter: Total number of requests per endpoint
request_counter = meter.create_counter(
    name="http.server.request.count",
    description="Total number of HTTP requests",
    unit="1",
)

# Histogram: Request duration in milliseconds
request_duration = meter.create_histogram(
    name="http.server.request.duration",
    description="HTTP request duration",
    unit="ms",
)

# Counter: Total errors
error_counter = meter.create_counter(
    name="http.server.error.count",
    description="Total number of HTTP errors",
    unit="1",
)

# Counter: Orders processed
orders_counter = meter.create_counter(
    name="app.orders.count",
    description="Total number of orders processed",
    unit="1",
)

# Histogram: Order value
order_value_histogram = meter.create_histogram(
    name="app.orders.value",
    description="Order values in USD",
    unit="USD",
)


# ============================================================
# Flask Application
# ============================================================
# NOTE: The /api/* endpoints below are intentionally unauthenticated for
# demonstration purposes only. Do NOT deploy this pattern with real business
# data. Before adapting for production, add authentication — e.g., an API
# key check in a `before_request` handler, Amazon Cognito, or front with
# API Gateway / ALB using IAM or Cognito authorizers. See SECURITY.md.
app = Flask(__name__)


@app.route("/")
def home():
    """Home page with links to demo endpoints."""
    with tracer.start_as_current_span("home-page") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/")

        request_counter.add(1, {"http.route": "/", "http.method": "GET"})

        logger.info("Home page accessed", extra={"visitor": "demo-user"})
        return """
        <html>
        <head>
            <title>OTel to CloudWatch - Full Observability Demo</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
                h1 { color: #232f3e; }
                .endpoint { background: #f4f4f4; padding: 15px; margin: 10px 0; border-radius: 5px; }
                a { color: #ff9900; text-decoration: none; font-weight: bold; }
                a:hover { text-decoration: underline; }
                .badge { background: #ff9900; color: white; padding: 3px 8px; border-radius: 3px; font-size: 12px; }
                .signal { display: inline-block; background: #232f3e; color: white; padding: 2px 6px;
                          border-radius: 3px; font-size: 11px; margin-left: 5px; }
                code { background: #e8e8e8; padding: 2px 6px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <h1>OpenTelemetry → CloudWatch <span class="badge">No Collector</span></h1>
            <p>This demo sends <strong>all three observability signals</strong> directly to Amazon CloudWatch
            using native OTLP HTTP endpoints. No collector sidecar or agent required!</p>

            <h2>Signals:</h2>
            <ul>
                <li><strong>Logs</strong> → CloudWatch Logs (<code>""" + OTLP_LOGS_ENDPOINT + """</code>)</li>
                <li><strong>Metrics</strong> → CloudWatch Metrics (<code>""" + OTLP_METRICS_ENDPOINT + """</code>)</li>
                <li><strong>Traces</strong> → X-Ray / CloudWatch Traces (<code>""" + OTLP_TRACES_ENDPOINT + """</code>)</li>
            </ul>

            <h2>Try These Endpoints:</h2>

            <div class="endpoint">
                <a href="/api/hello">/api/hello</a>
                <span class="signal">LOG</span><span class="signal">TRACE</span><span class="signal">METRIC</span>
                - Generates an INFO log, a trace span, and increments request counter
            </div>
            <div class="endpoint">
                <a href="/api/process">/api/process</a>
                <span class="signal">LOG</span><span class="signal">TRACE</span><span class="signal">METRIC</span>
                - Multi-step processing with nested spans and duration histogram
            </div>
            <div class="endpoint">
                <a href="/api/error">/api/error</a>
                <span class="signal">LOG</span><span class="signal">TRACE</span><span class="signal">METRIC</span>
                - Error with exception span event and error counter
            </div>
            <div class="endpoint">
                <a href="/api/order">/api/order</a>
                <span class="signal">LOG</span><span class="signal">TRACE</span><span class="signal">METRIC</span>
                - Order lifecycle with business metrics (order count, value histogram)
            </div>
            <div class="endpoint">
                <a href="/health">/health</a> - Health check (no telemetry)
            </div>
        </body>
        </html>
        """


@app.route("/api/hello")
def hello():
    """Simple endpoint that generates a log, trace, and metric."""
    start_time = time.time()

    with tracer.start_as_current_span("hello-endpoint") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/api/hello")
        span.set_attribute("custom.attribute", "demo-value")

        logger.info(
            "Hello endpoint called",
            extra={
                "endpoint": "/api/hello",
                "method": "GET",
                "custom.attribute": "demo-value",
            },
        )

        response = jsonify(
            {
                "message": "Hello! All three signals sent directly to CloudWatch via OTLP.",
                "signals": ["logs", "metrics", "traces"],
                "endpoints": {
                    "logs": OTLP_LOGS_ENDPOINT,
                    "metrics": OTLP_METRICS_ENDPOINT,
                    "traces": OTLP_TRACES_ENDPOINT,
                },
            }
        )

    duration_ms = (time.time() - start_time) * 1000
    request_counter.add(1, {"http.route": "/api/hello", "http.method": "GET"})
    request_duration.record(duration_ms, {"http.route": "/api/hello"})

    return response


@app.route("/api/process")
def process_data():
    """Simulates a processing pipeline with nested spans and metrics."""
    start_time = time.time()
    request_id = f"req-{int(time.time())}"

    with tracer.start_as_current_span("process-pipeline") as parent_span:
        parent_span.set_attribute("http.method", "GET")
        parent_span.set_attribute("http.route", "/api/process")
        parent_span.set_attribute("request.id", request_id)

        # Step 1: Validation
        with tracer.start_as_current_span("validate-data") as span:
            span.set_attribute("stage", "validation")
            time.sleep(0.05 + random.uniform(0, 0.05))
            logger.info(
                "Data validation complete",
                extra={"request_id": request_id, "stage": "validation", "records": 42},
            )

        # Step 2: Transform
        with tracer.start_as_current_span("transform-data") as span:
            span.set_attribute("stage", "transform")
            duration = 0.1 + random.uniform(0, 0.1)
            time.sleep(duration)
            if duration > 0.12:
                logger.warning(
                    "Processing took longer than expected",
                    extra={
                        "request_id": request_id,
                        "stage": "transform",
                        "duration_ms": int(duration * 1000),
                    },
                )
            span.set_attribute("transform.duration_ms", int(duration * 1000))

        # Step 3: Persist
        with tracer.start_as_current_span("persist-results") as span:
            span.set_attribute("stage", "persist")
            span.set_attribute("records.count", 42)
            time.sleep(0.03 + random.uniform(0, 0.02))
            logger.info(
                "Processing complete",
                extra={
                    "request_id": request_id,
                    "stage": "complete",
                    "records_processed": 42,
                },
            )

    duration_ms = (time.time() - start_time) * 1000
    request_counter.add(1, {"http.route": "/api/process", "http.method": "GET"})
    request_duration.record(duration_ms, {"http.route": "/api/process"})

    return jsonify(
        {
            "status": "processed",
            "request_id": request_id,
            "records": 42,
            "duration_ms": round(duration_ms, 2),
            "message": "Check CloudWatch for logs, X-Ray for traces, and Metrics for duration!",
        }
    )


@app.route("/api/error")
def trigger_error():
    """Generates an ERROR with exception info in logs, traces, and metrics."""
    start_time = time.time()

    with tracer.start_as_current_span("error-endpoint") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/api/error")

        try:
            # Intentionally cause an error
            result = 1 / 0
        except ZeroDivisionError as exc:
            # Record exception on the span
            span.set_status(trace.StatusCode.ERROR, "Division by zero")
            span.record_exception(exc)

            logger.error(
                "An error occurred during calculation",
                exc_info=True,
                extra={
                    "endpoint": "/api/error",
                    "error_type": "ZeroDivisionError",
                    "severity": "high",
                },
            )

    duration_ms = (time.time() - start_time) * 1000
    request_counter.add(1, {"http.route": "/api/error", "http.method": "GET"})
    request_duration.record(duration_ms, {"http.route": "/api/error"})
    error_counter.add(1, {"http.route": "/api/error", "error.type": "ZeroDivisionError"})

    return jsonify(
        {
            "status": "error_logged",
            "message": "Error captured in logs, traces (with exception event), and error counter metric!",
        }
    )


@app.route("/api/order")
def process_order():
    """Simulates order processing with structured logs, traces, and business metrics."""
    start_time = time.time()
    order_id = f"ORD-{int(time.time())}"
    order_total = round(random.uniform(10.0, 500.0), 2)
    items_count = random.randint(1, 10)

    with tracer.start_as_current_span("order-lifecycle") as parent_span:
        parent_span.set_attribute("http.method", "GET")
        parent_span.set_attribute("http.route", "/api/order")
        parent_span.set_attribute("order.id", order_id)
        parent_span.set_attribute("order.total", order_total)

        # Step 1: Receive order
        with tracer.start_as_current_span("receive-order") as span:
            span.set_attribute("order.items_count", items_count)
            logger.info(
                "New order received",
                extra={
                    "order.id": order_id,
                    "order.customer": "demo-customer-123",
                    "order.total": order_total,
                    "order.items_count": items_count,
                    "order.currency": "USD",
                },
            )
            time.sleep(0.02)

        # Step 2: Process payment
        with tracer.start_as_current_span("process-payment") as span:
            span.set_attribute("payment.method", "credit_card")
            span.set_attribute("payment.amount", order_total)
            time.sleep(0.05 + random.uniform(0, 0.03))
            logger.info(
                "Payment processed successfully",
                extra={
                    "order.id": order_id,
                    "payment.method": "credit_card",
                    "payment.status": "approved",
                },
            )

        # Step 3: Ship order
        with tracer.start_as_current_span("ship-order") as span:
            tracking = f"1Z{int(time.time())}"
            span.set_attribute("shipping.carrier", "UPS")
            span.set_attribute("shipping.tracking", tracking)
            time.sleep(0.03)
            logger.info(
                "Order shipped",
                extra={
                    "order.id": order_id,
                    "shipping.carrier": "UPS",
                    "shipping.tracking": tracking,
                },
            )

    duration_ms = (time.time() - start_time) * 1000
    request_counter.add(1, {"http.route": "/api/order", "http.method": "GET"})
    request_duration.record(duration_ms, {"http.route": "/api/order"})
    orders_counter.add(1, {"order.status": "completed"})
    order_value_histogram.record(order_total, {"order.currency": "USD"})

    return jsonify(
        {
            "status": "order_complete",
            "order_id": order_id,
            "total": order_total,
            "items": items_count,
            "duration_ms": round(duration_ms, 2),
            "message": "Order lifecycle traced, logged, and metered!",
        }
    )


@app.route("/health")
def health():
    """Health check endpoint - no telemetry emitted."""
    return jsonify({"status": "healthy", "service": "otel-cloudwatch-demo"})


# ============================================================
# Graceful Shutdown
# ============================================================
import atexit


@atexit.register
def shutdown():
    """Ensure all telemetry is flushed on shutdown."""
    logger_provider.shutdown()
    tracer_provider.shutdown()
    meter_provider.shutdown()


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    logger.info(
        "Application starting - OTel logs, metrics, and traces going directly to CloudWatch!"
    )
    # Local dev: bind to loopback by default. Elastic Beanstalk runs gunicorn
    # via the Procfile and never executes this __main__ block. Set
    # FLASK_RUN_HOST=0.0.0.0 explicitly if you need to bind all interfaces
    # (e.g., when running inside a container/VM).
    app.run(host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"), port=8000, debug=False)
