from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "medquestcorrector"
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = "postgresql://user:password@localhost/medquest_corrector"
    REDIS_URL: str = "redis://localhost:6379/0"

    OPENROUTER_API_KEY: str = ""

    @field_validator("OPENROUTER_API_KEY", mode="before")
    @classmethod
    def normalize_openrouter_api_key(cls, value: object) -> str:
        """
        Evita 401 quando o valor veio com aspas ou com prefixo 'Bearer ' — o cliente HTTP já envia Bearer.
        """
        if value is None:
            return ""
        text = str(value).strip().strip('"').strip("'")
        if text.lower().startswith("bearer "):
            text = text[7:].strip()
        return text
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_VISION_MODEL: str = "google/gemma-4-31b-it:free"
    OPENROUTER_VISION_FALLBACKS: str = (
        "dots-studio/dots-3-note-preview:free,google/gemini-2.5-flash"
    )
    OPENROUTER_TEXT_MODEL: str = "z-ai/glm-5.2:free"
    OPENROUTER_TEXT_FALLBACKS: str = (
        "nvidia/nemotron-3-super-120b-a12b:free,openrouter/free"
    )
    OPENROUTER_HTTP_REFERER: str = ""
    OPENROUTER_APP_TITLE: str = "medquestcorrector"
    OPENROUTER_TIMEOUT_SECONDS: float = 90.0
    # --- Escalonamento de leituras duvidosas (itens 8 e 12 do plano de HTR) ---
    # Cada escalonamento e uma chamada extra. Fica DESLIGADO por padrao: o projeto
    # opera sob restricao de custo, e ligar isto por padrao multiplicaria o custo
    # de toda prova para melhorar as poucas questoes que precisam. Quando ligado,
    # so entra em questao cuja leitura declarou confianca baixa.
    HTR_ESCALATION_ENABLED: bool = False
    # Variantes do mesmo recorte (TTA). 0 desliga o TTA.
    HTR_TTA_VARIANTS: int = 2
    # Modelo de familia diferente para segunda opiniao. Vazio desliga o consenso.
    HTR_CONSENSUS_MODEL: str = "google/gemini-2.5-flash"
    # HTR V2: uma chamada multimodal por aluno/pagina (contact sheets).
    # Default False ate validacao manual em producao.
    HTR_BATCH_PAGE_ENABLED: bool = False
    # Maximo de crops por contact sheet. Valores inseguros sao limitados no builder.
    HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET: int = 8

    # Importação universal de provas discursivas externas. Mantida atrás de flag
    # para permitir rollout e rollback sem tocar no fluxo de provas práticas.
    DISCURSIVE_UNIVERSAL_IMPORT_ENABLED: bool = False

    OCR_PROVIDER: str = "mistral,google_vision"
    MISTRAL_API_KEY: str = ""
    MISTRAL_OCR_MODEL: str = "mistral-ocr-latest"
    GOOGLE_VISION_API_KEY: str = ""

    CELERY_WORKER_CONCURRENCY: int = 1
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 20

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def normalize_cors_origins(cls, value: object) -> str:
        if value is None:
            return ""
        text = str(value).strip().strip('"').strip("'")
        return text

    UPLOAD_DIR: Path = Path("uploads")
    MAX_UPLOAD_MB: int = 40

    MAX_CSV_MB: int = 5
    MAX_CSV_ROWS: int = 2000

    JWT_SECRET_KEY: str = "dev-only-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    def cors_origin_list(self) -> List[str]:
        raw = self.CORS_ORIGINS.replace(";", ",")
        parts = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
        return parts if parts else ["http://localhost:3000"]


settings = Settings()
