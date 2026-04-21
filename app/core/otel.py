import os
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.attributes.service_attributes import SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
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

    # 2. TracerProvider 설정
    tracer_provider = TracerProvider(resource=resource)

    # 3. OTLP Exporter 설정 (Alloy/Tempo로 전송)
    # OTEL_EXPORTER_OTLP_ENDPOINT 환경변수가 설정되어 있으면 해당 주소로 전송
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)

    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)

    # 글로벌 TracerProvider 등록
    trace.set_tracer_provider(tracer_provider)

    # 4. W3C Trace Context Propagator 설정 (Spring 등 타 서비스와 연동을 위해 필수)
    set_global_textmap(TraceContextTextMapPropagator())

    # 5. FastAPI 자동 계측 (Instrumentation)
    FastAPIInstrumentor.instrument_app(app)

    # 6. HTTPX 클라이언트 자동 계측 (크롤러에서 사용)
    HTTPXClientInstrumentor().instrument()

    # 7. Logging 자동 계측 (표준 logging 라이브러리 연동)
    LoggingInstrumentor().instrument(set_logging_format=True)


def get_trace_id():
    """현재 컨텍스트의 Trace ID를 반환합니다."""
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, '032x')
    return None
