# medquestcorrector — Resumo do Projeto e Deploy

Documento consolidado para entender o funcionamento do sistema, a estrutura de arquivos, o que roda hoje no **Railway** e como replicar no **Coolify** (ou outro PaaS).

> **Repositório:** monorepo com `backend/` (FastAPI + Celery) e `frontend/` (Next.js).

---

## 1. O que o sistema faz

Plataforma para **correção assistida por IA** de provas discursivas e práticas manuscritas em universidades/faculdades de medicina.

Fluxo típico do professor:

1. Cadastra **turmas** e importa alunos via **CSV**
2. Cria **provas** (discursivas ou práticas) com enunciado, gabarito e critérios
3. Gera **folhas-resposta em PDF** (com QR Code por aluno/página) para impressão
4. Escaneia as provas preenchidas e envia o PDF
5. O sistema **lê (OCR/visão)**, **corrige (LLM via OpenRouter)** e marca casos para **revisão humana**
6. O professor **revisa/aprova** notas e **exporta XLSX**

Existem **dois modos de correção** no código atual:

| Modo | Onde | Como funciona |
|------|------|----------------|
| **Pipeline por lote (template)** | Dashboard → upload PDF | PDF com folhas geradas pelo sistema; worker Celery processa página a página (QR, crop, OCR, LLM) |
| **Análise visual direta** | `/manuscritas` | Upload de PDF escaneado; processamento síncrono na API com visão OpenRouter + correção textual |

A IA é **co-piloto**: notas podem exigir revisão manual antes de consolidar.

---

## 2. Stack tecnológica

| Camada | Tecnologias |
|--------|-------------|
| **API** | Python 3.12+, FastAPI, SQLAlchemy, Alembic, Pydantic v2 |
| **Workers** | Celery 5 + Redis (broker e backend de resultados) |
| **Banco** | PostgreSQL 15 |
| **Frontend** | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4, Axios |
| **PDF/imagem** | PyMuPDF, Pillow, OpenCV (headless), ReportLab, qrcode |
| **LLM** | OpenRouter (`httpx`) — modelos de visão e texto configuráveis |
| **OCR** | Google Cloud Vision, Mistral OCR (cadeia configurável) |
| **Exportação** | openpyxl (XLSX), python-docx (import de provas) |
| **Auth** | JWT (Bearer token no frontend via `localStorage`) |

---

## 3. Estrutura de diretórios

```
medquestcorrector/
├── backend/                    # API FastAPI + worker Celery
│   ├── main.py                 # Entry point uvicorn
│   ├── requirements.txt
│   ├── alembic/                # Migrações PostgreSQL
│   ├── alembic.ini
│   ├── .env.example
│   ├── railway.env.template    # Template de vars para Railway
│   ├── RAILWAY_SETUP.md        # Guia de deploy Railway
│   ├── app/
│   │   ├── api/v1/             # Rotas REST
│   │   ├── core/               # config, database, celery, security, storage
│   │   ├── models/             # SQLAlchemy ORM
│   │   ├── schemas/            # Pydantic request/response
│   │   ├── services/           # Lógica de negócio
│   │   └── workers/            # Tasks Celery
│   ├── tests/
│   └── preview_outputs/        # Exemplos de folha-resposta gerada
├── frontend/                   # Next.js
│   ├── src/app/                # Páginas (App Router)
│   ├── src/components/
│   ├── src/lib/api.ts          # Clientes HTTP (api, uploadApi, visualExamAnalysisApi)
│   ├── package.json
│   └── .env.example
├── docs/                       # Documentação de arquitetura, API, OCR, etc.
├── scripts/                    # Scripts CLI de teste de pipeline
├── docker-compose.yml          # Apenas Postgres + Redis (dev local)
└── README.md                   # Título mínimo (detalhes neste doc)
```

---

## 4. Fluxos de funcionamento

### 4.1 Pipeline principal (lote + Celery)

```
Professor → Frontend upload PDF
         → POST /api/v1/batches/upload
         → PDF salvo em UPLOAD_DIR (filesystem local)
         → Registro UploadBatch (status PENDING)
         → Celery: process_upload_batch
              → Renderiza páginas (PyMuPDF)
              → Decodifica QR / identifica aluno
              → Alinha página (OpenCV)
              → Recorta regiões de resposta (layout manifest)
              → OCR (Google Vision / Mistral)
              → Correção LLM (OpenRouter)
              → Persiste StudentResult + QuestionScore
         → Status REVIEW_PENDING / DONE
         → Professor revisa em /review
         → Export XLSX
```

**Armazenamento de PDFs:** prefixo `local:` em `file_url` (ver `app/core/storage.py`). Não usa S3/R2 hoje — apenas disco local.

### 4.2 Prova manuscrita (análise visual)

```
Professor → /manuscritas
         → POST /api/exams/analyze-discursive-pdf (multipart)
         → visual_exam_pipeline.analyze_discursive_exam_pdf()
              → Render PDF → imagens
              → OpenRouter visão (extrai transcrições por questão)
              → OpenRouter texto (corrige com rubrica)
         → Persiste VisualExamRun + VisualExamAnswer
         → Resposta JSON na tela + export XLSX por run_id
```

**Prefixo de API diferente:** rotas visuais usam `/api/exams/*` (não `/api/v1`).

### 4.3 Modelos LLM (defaults em `config.py`)

| Papel | Variável | Default |
|-------|----------|---------|
| Visão (leitura da página) | `OPENROUTER_VISION_MODEL` | `qwen/qwen2.5-vl-72b-instruct` |
| Fallbacks visão | `OPENROUTER_VISION_FALLBACKS` | Qwen VL menores + Gemini Flash |
| Texto (correção) | `OPENROUTER_TEXT_MODEL` | `deepseek/deepseek-v4-flash:free` |
| Fallbacks texto | `OPENROUTER_TEXT_FALLBACKS` | Qwen 235B, Qwen 2.5 72B/32B |

Chave única: `OPENROUTER_API_KEY`.

---

## 5. Backend — rotas da API

**Health (sem auth):** `GET /health`

**Auth** — prefixo `/api/v1/auth`

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/register` | Cadastro |
| POST | `/login` | Login → JWT |
| GET | `/me` | Perfil do usuário logado |

**Provas** — `/api/v1/exams`

| Método | Rota | Descrição |
|--------|------|-----------|
| GET/POST | `/` | Listar / criar prova |
| GET/PUT/DELETE | `/{exam_id}` | CRUD prova |
| GET/POST/PUT/DELETE | `/{exam_id}/questions...` | Questões |
| GET | `/templates/discursive-docx` | Template DOCX |
| POST | `/import-discursive-docx` | Importar prova de DOCX |
| GET/POST | `/{exam_id}/answer-sheets` | Folhas-resposta discursivas |
| POST | `/{exam_id}/answer-sheets/practical` | Folhas práticas |

**Turmas** — `/api/v1/classes` (+ alunos e CSV)

**Uploads / lotes** — `/api/v1/batches`

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/upload` | Envia PDF → enfileira worker |
| GET | `/{batch_id}/status` | Status do processamento |
| POST | `/{batch_id}/reprocess` | Reprocessa mesmo PDF |
| POST | `/{batch_id}/reupload` | Substitui PDF do lote |
| POST | `/{batch_id}/process-now` | Dispara processamento |

**Revisão** — `/api/v1/reviews`

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/batch/{batch_id}` | Resultados do lote |
| GET | `/next` | Próximo item para revisar |
| POST | `/scores/{score_id}` | Ajuste de nota |
| POST | `/results/{result_id}/approve` | Aprovar aluno |
| GET | `/batch/{batch_id}/export` | Download XLSX |

**Histórico** — `/api/v1/history/corrections`

**Correção visual** — `/api/exams` (auth obrigatória)

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/analyze-discursive-pdf` | Análise completa do PDF |
| PATCH | `/runs/{run_id}/answers` | Edição pós-correção |
| GET | `/runs/{run_id}/export` | Export XLSX |

**Estáticos:** PDFs/uploads expostos em `/static` (monta `UPLOAD_DIR`).

---

## 6. Backend — modelos principais (PostgreSQL)

| Tabela | Uso |
|--------|-----|
| `users` | Professores/admins |
| `classes` | Turmas |
| `students` | Alunos (matrícula, curso) |
| `exams` | Provas (`is_practical`, `layout_manifest_json`) |
| `exam_questions` | Questões + coordenadas de crop (`box_x/y/w/h`) |
| `upload_batches` | Lotes de PDF escaneado |
| `student_results` | Resultado por aluno/página no lote |
| `question_scores` | Nota OCR/IA/final por questão |
| `visual_exam_runs` | Execuções da análise manuscrita |
| `visual_exam_answers` | Respostas por questão (run visual) |

Migrações Alembic em `backend/alembic/versions/` (9 revisions, abr/mai 2026).

---

## 7. Backend — serviços importantes

| Módulo | Responsabilidade |
|--------|------------------|
| `services/generator/answer_sheet.py` | Gera PDF folha-resposta + QR |
| `services/generator/sheet_layout.py` | Layout, manifest JSON das caixas |
| `services/vision/pdf_parser.py` | Parsing de PDF escaneado |
| `services/vision/page_align.py` | Alinhamento por fiduciais/QR |
| `services/vision/ocr.py` | Provedores Google Vision / Mistral |
| `services/vision/qr_decode.py` | Leitura de QR das folhas |
| `services/llm/grading.py` | Grading estruturado (visão + texto) |
| `services/exam_grading_client.py` | Correção discursiva via OpenRouter |
| `services/openrouter_vision_client.py` | Extração visual de respostas |
| `services/visual_exam_pipeline.py` | Pipeline síncrono manuscritas |
| `services/export/spreadsheet.py` | Export XLSX (resumo + aba/aluno + revisões) |
| `workers/pipeline.py` | Task Celery `process_upload_batch` |

---

## 8. Frontend — páginas

| Rota | Função |
|------|--------|
| `/login`, `/register` | Autenticação |
| `/` | Dashboard — upload PDF escaneado (lote) |
| `/classes` | Turmas e import CSV |
| `/classes/[id]` | Detalhe da turma / alunos |
| `/exams` | Lista provas discursivas |
| `/exams/new`, `/exams/[id]/edit` | Criar/editar prova |
| `/provas-praticas` | Provas práticas |
| `/provas-praticas/new` | Nova prova prática |
| `/review` | Revisão de notas (lotes) |
| `/historico` | Histórico + reexport XLSX |
| `/manuscritas` | Análise visual direta de PDF |

**Variável obrigatória em produção:** `NEXT_PUBLIC_API_URL=https://<api-host>/api/v1`

O cliente `visualExamAnalysisApi` deriva automaticamente a base `/api/exams` a partir dessa URL.

---

## 9. Deploy atual no Railway

Documentação oficial interna: `backend/RAILWAY_SETUP.md` e `backend/railway.env.template`.

### 9.1 Serviços (4 + 2 bancos)

| # | Tipo | Root Directory | Comando |
|---|------|----------------|---------|
| 1 | **PostgreSQL** | (plugin Railway) | — |
| 2 | **Redis** | (plugin Railway) | — |
| 3 | **API FastAPI** | `/backend` | `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` |
| 4 | **Worker Celery** | `/backend` | `celery -A app.core.celery_app worker --loglevel=info --concurrency=1 --prefetch-multiplier=1 --max-tasks-per-child=20` |
| 5 | **Frontend Next.js** | `/frontend` | build: `npm run build` · start: `npm run start` |

Não há `Dockerfile` nem `railway.toml` no repositório — o Railway usa **detecção automática (Nixpacks)** por pasta.

### 9.2 Diagrama Railway

```
┌─────────────────────────────────────────────┐
│               Projeto Railway               │
│  ┌──────────┐  ┌──────────┐                │
│  │ Postgres │  │  Redis   │                │
│  └────┬─────┘  └────┬─────┘                │
│       │              │                      │
│  ┌────┴──────────────┴────┐                │
│  │   Backend API (FastAPI) │ ← GET /health │
│  └────────────────────────┘                │
│  ┌────────────────────────┐                │
│  │   Worker (Celery)       │ ← PDFs/OCR/IA │
│  └────────────────────────┘                │
│  ┌────────────────────────┐                │
│  │   Frontend (Next.js)    │ ← professor   │
│  └────────────────────────┘                │
└─────────────────────────────────────────────┘
```

### 9.3 Variáveis — Backend (API **e** Worker, idênticas)

Copiar de `backend/railway.env.template` / `backend/.env.example`:

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
CORS_ORIGINS=https://SEU-FRONTEND.up.railway.app

OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_VISION_MODEL=qwen/qwen2.5-vl-72b-instruct
OPENROUTER_VISION_FALLBACKS=qwen/qwen2.5-vl-32b-instruct,qwen/qwen-2.5-vl-7b-instruct,google/gemini-2.5-flash
OPENROUTER_TEXT_MODEL=deepseek/deepseek-v4-flash:free
OPENROUTER_TEXT_FALLBACKS=qwen/qwen3-235b-a22b-2507,qwen/qwen2.5-72b-instruct,qwen/qwen2.5-32b-instruct
OPENROUTER_HTTP_REFERER=
OPENROUTER_APP_TITLE=medquestcorrector
OPENROUTER_TIMEOUT_SECONDS=90

OCR_PROVIDER=google_vision
GOOGLE_VISION_API_KEY=...
# MISTRAL_API_KEY=...  (se usar mistral no OCR_PROVIDER)

UPLOAD_DIR=/tmp/medquest_uploads
MAX_UPLOAD_MB=40
MAX_CSV_MB=5
MAX_CSV_ROWS=2000

JWT_SECRET_KEY=<openssl rand -hex 32>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

CELERY_WORKER_CONCURRENCY=1
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_WORKER_MAX_TASKS_PER_CHILD=20
```

### 9.4 Variáveis — Frontend

```env
NEXT_PUBLIC_API_URL=https://SEU-BACKEND.up.railway.app/api/v1
```

### 9.5 Checklist Railway

- [ ] Postgres e Redis ativos
- [ ] API responde em `/health`
- [ ] Worker Celery com logs sem erro de conexão Redis
- [ ] `CORS_ORIGINS` = URL exata do frontend (`https://`, sem barra final)
- [ ] `NEXT_PUBLIC_API_URL` termina em `/api/v1`
- [ ] `OPENROUTER_API_KEY` preenchida
- [ ] `JWT_SECRET_KEY` forte (não usar `dev-only-change-me`)
- [ ] Migrações Alembic aplicadas no Postgres (ver seção 11)

### 9.6 Pontos críticos no Railway

1. **Disco efêmero:** `UPLOAD_DIR=/tmp/medquest_uploads` — PDFs **somem** se o container reiniciar. Para produção séria, migrar para volume persistente ou object storage (S3/R2).
2. **Memória do worker:** PyMuPDF + OpenCV + Pillow — manter `CELERY_WORKER_CONCURRENCY=1`.
3. **Migrações:** não há hook automático no repo; rodar `alembic upgrade head` manualmente após deploy.
4. **Dois prefixos de API:** frontend precisa da URL base correta; rotas visuais usam `/api/exams`.

---

## 10. Migração para Coolify

O Coolify pode hospedar o mesmo monorepo criando **recursos separados** equivalentes aos do Railway.

### 10.1 Recursos sugeridos no Coolify

| Recurso | Tipo | Observação |
|---------|------|------------|
| `medquest-postgres` | PostgreSQL 15 | Ou serviço gerenciado externo |
| `medquest-redis` | Redis 7 | Broker Celery |
| `medquest-api` | App (Git) | Base directory: `backend` |
| `medquest-worker` | App (Git) | Mesmo repo/dir, comando Celery diferente |
| `medquest-web` | App (Git) | Base directory: `frontend`, Node ≥ 20.9 |

### 10.2 Build e start (sem Dockerfile — Nixpacks/buildpack)

**API (`backend`):**

- **Install:** `pip install -r requirements.txt`
- **Start:** `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1`
- **Health check:** `GET /health`
- **Porta exposta:** 8000 (ou variável `PORT` do Coolify)

**Worker (`backend`):**

- **Start:** `celery -A app.core.celery_app worker --loglevel=info --concurrency=1 --prefetch-multiplier=1 --max-tasks-per-child=20`
- **Sem porta pública** — apenas consome Redis
- **Mesmas env vars** da API

**Frontend (`frontend`):**

- **Build:** `npm ci && npm run build`
- **Start:** `npm run start`
- **Build-time env:** `NEXT_PUBLIC_API_URL` (Coolify: marcar como disponível no build)
- **Node:** 20.9+

### 10.3 Ordem de deploy recomendada

1. Subir Postgres + Redis
2. Aplicar migrações (`alembic upgrade head`) contra o `DATABASE_URL`
3. Deploy API → testar `/health`
4. Deploy Worker → verificar consumo da fila `medquest_queue`
5. Deploy Frontend com `NEXT_PUBLIC_API_URL` apontando para a API
6. Atualizar `CORS_ORIGINS` na API/Worker com URL pública do frontend
7. Testar login → upload → revisão → export XLSX

### 10.4 Melhorias recomendadas na migração

| Item | Por quê |
|------|---------|
| **Volume persistente** montado em `/data/uploads` + `UPLOAD_DIR=/data/uploads` | Não perder PDFs entre restarts |
| **Dockerfile multi-stage** (opcional) | Builds reproduzíveis; OpenCV headless já está no requirements |
| **Job de release** `alembic upgrade head` | Automatizar schema antes de subir API |
| **Object storage (S3/R2)** | Substituir `local:` em `storage.py` para escala |
| **Reverse proxy + TLS** | Coolify Traefik/Caddy — URLs HTTPS para CORS |
| **Secrets** | OpenRouter, Google Vision, JWT fora do git |

### 10.5 docker-compose local (já existente)

```bash
docker compose up -d   # sobe postgres:5432 e redis:6379
```

Não inclui API/worker/frontend — rodar manualmente em dev:

```bash
# Terminal 1 — backend
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env   # ajustar DATABASE_URL, REDIS_URL, chaves
alembic upgrade head
uvicorn main:app --reload --port 8000

# Terminal 2 — worker
celery -A app.core.celery_app worker --loglevel=info

# Terminal 3 — frontend
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

---

## 11. Banco de dados — migrações

```bash
cd backend
alembic upgrade head      # aplicar
alembic current           # versão atual
alembic history           # listar revisions
```

Revisions principais:

- `20260427_auto_grading_manifest` — manifest de layout
- `20260428_*` — scores únicos, visual exam OpenRouter, identidade
- `20260429_*` — critérios de correção, traceability, identity_source
- `20260504_exam_is_practical` — flag prova prática

---

## 12. Exportação XLSX

Implementado em `backend/app/services/export/spreadsheet.py`.

Com `include_details=true`:

1. **Resultado Final** — uma linha por aluno (resumo)
2. **Uma aba por aluno** — dados gerais + tabela completa das questões
3. **Revisões Necessárias** — itens que exigem revisão manual

Usado em `/reviews/batch/{id}/export` e `/api/exams/runs/{id}/export`.

---

## 13. Testes

```bash
cd backend
python -m pytest                    # suite completa
python -m pytest tests/test_docx_import_and_xlsx_export.py -v
```

Cobertura parcial: import DOCX, export XLSX, OCR providers, pipeline idempotency, grading resilience, layout de folhas.

---

## 14. Scripts auxiliares (`scripts/`)

| Script | Uso |
|--------|-----|
| `test_discursive_pdf_pipeline.py` | Teste end-to-end pipeline visual |
| `test_visual_page_reading.py` | Teste leitura de página |
| `test_grading_only.py` | Teste só correção textual |
| `test_upload_pipeline_student_link.py` | (em tests/) vinculação aluno |

---

## 15. Documentação complementar (pasta `docs/`)

| Arquivo | Conteúdo |
|---------|----------|
| `ARCHITECTURE.md` | Visão workers, diagrama Mermaid |
| `API_SPEC.md` | Especificação REST planejada |
| `DATA_MODEL.md` | Entidades conceituais |
| `OCR_STRATEGY.md` | Provedores OCR e fallback |
| `OPENROUTER_GRADING.md` | Estratégia de prompting LLM |
| `ROADMAP.md` | Fases MVP → produção |

---

## 16. Segurança e limites

- JWT com expiração configurável (default 7 dias)
- Upload PDF: máx. `MAX_UPLOAD_MB` (40 MB default)
- CSV turmas: `MAX_CSV_MB` / `MAX_CSV_ROWS`
- Rotas protegidas exigem `Authorization: Bearer <token>`
- `.env` / `.env.local` no `.gitignore` — nunca commitar chaves

---

## 17. Resumo executivo para handoff (Railway → Coolify)

| O quê | Onde está | Ação na migração |
|-------|-----------|------------------|
| API | `backend/main.py` | 1 serviço, porta 8000, health `/health` |
| Worker | `app/workers/pipeline.py` | 1 serviço, mesmo env, sem HTTP |
| Frontend | `frontend/` | 1 serviço Node 20+, build Next |
| Postgres | plugin / serviço | `DATABASE_URL` |
| Redis | plugin / serviço | `REDIS_URL` |
| PDFs | `UPLOAD_DIR` | **Adicionar volume persistente** |
| LLM | OpenRouter | `OPENROUTER_API_KEY` |
| OCR | Google Vision | `GOOGLE_VISION_API_KEY` |
| Schema DB | `backend/alembic/` | `alembic upgrade head` no deploy |
| CORS | `CORS_ORIGINS` | URL pública do frontend |
| Cliente web | `NEXT_PUBLIC_API_URL` | URL pública da API + `/api/v1` |

---

*Gerado a partir do estado do repositório medquestcorrector. Atualize este documento quando mudar serviços, variáveis ou fluxos de deploy.*
