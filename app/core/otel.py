import os
import logging
from opentelemetry import trace, metrics, _logs
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.attributes.service_attributes import SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def setup_otel(app):
    # 1. 서비스 리소스 설정
    service_name = os.getenv("OTEL_SERVICE_NAME", "crawler-service")
    resource = Resource.create({
        SERVICE_NAME: service_name,
        "environment": os.getenv("ENVIRONMENT", "development")
    })

    # 공통 OTLP 엔드포인트 설정 (Base URL)
    otlp_base_url = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    # 2. TracerProvider 설정 (Traces)
    tracer_provider = TracerProvider(resource=resource)
    otlp_trace_exporter = OTLPSpanExporter(endpoint=f"{otlp_base_url}/v1/traces")
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))
    trace.set_tracer_provider(tracer_provider)

    # 3. MeterProvider 설정 (Metrics)
    otlp_metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_base_url}/v1/metrics")
    reader = PeriodicExportingMetricReader(otlp_metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    # 4. LoggerProvider 설정 (Logs)
    otlp_log_exporter = OTLPLogExporter(endpoint=f"{otlp_base_url}/v1/logs")
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
    _logs.set_logger_provider(logger_provider)

    # 표준 logging 핸들러 추가 (표준 logging 로그를 OTLP로 전달)
    logging_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    logging.getLogger().addHandler(logging_handler)

    # 5. W3C Trace Context Propagator 설정
    set_global_textmap(TraceContextTextMapPropagator())

    # 6. 자동 계측 (Instrumentation)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    
    # loguru와 연동을 위해 LoggingInstrumentor도 유지
    LoggingInstrumentor().instrument(set_logging_format=True)


def get_trace_id():
    """현재 컨텍스트의 Trace ID를 반환합니다."""
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, '032x')
    return None
