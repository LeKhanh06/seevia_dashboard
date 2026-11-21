"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.admin import router as admin_router
from app.api.admin_stats import router as admin_stats_router
from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.care import router as care_router
from app.api.device import router as device_router
from app.api.glasses import router as glasses_router
from app.api.health import router as health_router
from app.api.me_stats import router as me_stats_router
from app.api.messages import router as messages_router
from app.api.preferences import router as preferences_router
from app.api.service import router as service_router
from app.api.status import router as status_router
from app.api.support import router as support_router
from app.api.usage import UsageBodyLimitMiddleware
from app.api.usage import router as usage_router
from app.config import Settings
from app.errors import register_exception_handlers
from app.lifespan import application_lifespan
from app.logging import RequestCorrelationMiddleware, setup_logging
from app.workers.wake import WorkerWakeSignals


def create_app(settings: Settings) -> FastAPI:
    """Create an application with validated settings and registered routers."""
    setup_logging(settings.log_level)

    application = FastAPI(
        title="App Communication Server",
        version=__version__,
        lifespan=application_lifespan,
    )
    application.state.settings = settings
    application.state.ready = False
    application.state.worker_wake_signals = WorkerWakeSignals()

    # Middleware
    application.add_middleware(RequestCorrelationMiddleware)
    application.add_middleware(UsageBodyLimitMiddleware)
    if settings.admin_cors_origins:
        # Chỉ các origin dashboard đã cấu hình; token đi qua header, không dùng cookie.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.admin_cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Accept"],
            allow_credentials=False,
            max_age=600,
        )

    # Exception handlers
    register_exception_handlers(application)

    # Routers
    application.include_router(health_router)
    application.include_router(status_router)
    application.include_router(service_router)
    application.include_router(device_router)
    application.include_router(glasses_router)
    application.include_router(auth_router)
    application.include_router(preferences_router)
    application.include_router(support_router)
    application.include_router(care_router)
    application.include_router(calls_router)
    application.include_router(messages_router)
    application.include_router(usage_router)
    application.include_router(admin_router)
    application.include_router(admin_stats_router)
    application.include_router(me_stats_router)

    return application
