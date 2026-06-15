from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="medquestcorrector API",
    description="API para correção de provas discursivas assistida por IA",
    version="2.0.0",
    redirect_slashes=False,
)

_origins = settings.cors_origin_list()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.on_event("startup")
def _log_cors_origins() -> None:
    logger.info("CORS allow_origins: %s", _origins)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Garante que mesmo erros inesperados retornem JSON com CORS headers."""
    origin = request.headers.get("origin", "")
    headers = {}
    if origin and (origin in _origins or "*" in _origins):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno do servidor."},
        headers=headers,
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "message": "medquestcorrector API is running"}


upload_dir = settings.UPLOAD_DIR.resolve()
upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(upload_dir)), name="static")

from app.api.v1 import auth, exams, uploads, classes, reviews, visual_exam_analysis, history

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(exams.router, prefix="/api/v1/exams", tags=["Exams"])
app.include_router(uploads.router, prefix="/api/v1/batches", tags=["Uploads"])
app.include_router(classes.router, prefix="/api/v1")
app.include_router(reviews.router, prefix="/api/v1/reviews", tags=["Reviews"])
app.include_router(history.router, prefix="/api/v1/history", tags=["History"])
app.include_router(visual_exam_analysis.router, prefix="/api/exams", tags=["Correção visual discursiva"])
