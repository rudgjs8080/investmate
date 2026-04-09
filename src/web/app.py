"""FastAPI 웹 대시보드 애플리케이션."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app() -> FastAPI:
    """FastAPI 앱을 생성한다."""
    app = FastAPI(
        title="Investmate Dashboard",
        description="S&P 500 AI 투자 분석 대시보드",
    )

    # 정적 파일 + 템플릿
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 템플릿 엔진을 app.state에 저장 (라우트에서 접근용)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # 전역 예외 핸들러
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        import logging

        logging.getLogger("src.web").error(
            "처리되지 않은 오류: %s %s — %s", request.method, request.url.path, exc
        )
        return JSONResponse(status_code=500, content={"error": "서버 내부 오류"})

    # 라우트 등록
    from src.web.routes.dashboard import router as dashboard_router
    from src.web.routes.api import router as api_router
    from src.web.routes.recommendations import router as rec_router
    from src.web.routes.performance import router as perf_router
    from src.web.routes.market import router as market_router
    from src.web.routes.stock import router as stock_router
    from src.web.routes.ai_accuracy import router as ai_acc_router
    from src.web.routes.chat import router as chat_router

    from src.web.routes.heatmap import router as heatmap_router
    from src.web.routes.api_export import router as export_router
    from src.web.routes.screener import router as screener_router
    from src.web.routes.portfolio import router as portfolio_router
    from src.web.routes.weekly_report import router as weekly_router
    from src.web.routes.factors import router as factors_router
    from src.web.routes.personal import router as personal_router

    app.include_router(dashboard_router)
    app.include_router(api_router, prefix="/api")
    app.include_router(rec_router)
    app.include_router(perf_router)
    app.include_router(market_router)
    app.include_router(stock_router)
    app.include_router(ai_acc_router)
    app.include_router(chat_router)
    app.include_router(heatmap_router)
    app.include_router(export_router, prefix="/api")
    app.include_router(screener_router)
    app.include_router(portfolio_router)
    app.include_router(weekly_router)
    app.include_router(factors_router)
    app.include_router(personal_router)

    return app
