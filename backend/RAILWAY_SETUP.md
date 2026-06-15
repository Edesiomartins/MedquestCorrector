# Deploy no Railway (Monorepo)

O repositório tem `/backend` e `/frontend`. No Railway, cada pasta vira um **serviço separado**.

Você vai criar **4 itens** no projeto Railway:

---

## Passo 1 — Criar os bancos de dados

No seu projeto Railway:

1. Clique em **+ New** → **Database** → **PostgreSQL**
2. Clique em **+ New** → **Database** → **Redis**

Eles geram automaticamente variáveis (`DATABASE_URL`, `REDIS_URL`) que você referencia nos serviços.

---

## Passo 2 — Serviço da API (FastAPI)

1. **+ New** → **GitHub Repo** → selecione `medquestcorrector`
2. Em **Settings**:
   - **Root Directory:** `/backend`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
3. Em **Variables** → **Raw Editor**, cole e preencha:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
CORS_ORIGINS=https://medquestcorrector.up.railway.app
OPENROUTER_API_KEY=sk-or-v1-SUA-CHAVE-AQUI
JWT_SECRET_KEY=GERE-UMA-CHAVE-FORTE
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
UPLOAD_DIR=/tmp/medquest_uploads
MAX_UPLOAD_MB=40
MAX_CSV_MB=5
MAX_CSV_ROWS=2000
CELERY_WORKER_CONCURRENCY=1
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_WORKER_MAX_TASKS_PER_CHILD=20
```

> Para gerar `JWT_SECRET_KEY`: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Passo 3 — Serviço do Worker (Celery)

1. **+ New** → **GitHub Repo** → mesmo repositório
2. Em **Settings**:
   - **Root Directory:** `/backend`
   - **Start Command:** `celery -A app.core.celery_app worker --loglevel=info --concurrency=1 --prefetch-multiplier=1 --max-tasks-per-child=20`
3. Em **Variables**: **copie exatamente as mesmas variáveis** do serviço da API.

> API e Worker precisam compartilhar `DATABASE_URL`, `REDIS_URL` e `OPENROUTER_API_KEY`.

> O worker processa PDFs/imagens e carrega bibliotecas pesadas como PyMuPDF, Pillow e OpenCV. No Railway, deixar a concorrência padrão do Celery pode multiplicar o uso de memória por processo. Para começar barato, use `concurrency=1` e aumente apenas se houver fila acumulada.

---

## Passo 4 — Serviço do Frontend (Next.js)

1. **+ New** → **GitHub Repo** → mesmo repositório
2. Em **Settings**:
   - **Root Directory:** `/frontend`
   - **Build Command:** `npm run build`
   - **Start Command:** `npm run start`
3. Em **Variables**:

```
NEXT_PUBLIC_API_URL=https://SEU-BACKEND-API.up.railway.app/api/v1
```

4. Copie a URL pública deste serviço frontend e cole em **`CORS_ORIGINS`** nos serviços do backend (API + Worker).

---

## Checklist final

- [ ] PostgreSQL rodando
- [ ] Redis rodando
- [ ] Serviço API com `uvicorn` rodando (verificar `/health`)
- [ ] Serviço Worker com `celery` rodando (ver logs)
- [ ] Frontend abrindo no navegador
- [ ] `CORS_ORIGINS` no backend = URL exata do frontend (com `https://`, sem `/` no final)
- [ ] `NEXT_PUBLIC_API_URL` no frontend = URL do backend + `/api/v1`
- [ ] `OPENROUTER_API_KEY` preenchida (necessária para correção funcionar)
- [ ] `JWT_SECRET_KEY` com valor forte (não o default `dev-only-change-me`)

### Erro de CORS no login

Se o navegador mostrar *"blocked by CORS policy"* ao chamar a API:

1. No serviço **API** (não no frontend), defina exatamente:
   ```
   CORS_ORIGINS=https://medquestcorrector.up.railway.app
   ```
   (use a URL pública do **frontend**, sem barra no final)
2. Faça **Redeploy** da API após salvar a variável.
3. Confirme que a API está no ar: abra `https://medquestcorrector-api.up.railway.app/health` — deve retornar `{"status":"ok",...}`.
4. Nos logs da API após o deploy, procure `CORS allow_origins:` — a URL do frontend deve aparecer na lista.

> `CORS_ORIGINS` só precisa existir no serviço **FastAPI**. O worker Celery não atende o browser.

---

## Arquitetura no Railway

```
┌─────────────────────────────────────────────┐
│               Projeto Railway               │
│                                             │
│  ┌──────────┐  ┌──────────┐                │
│  │ Postgres │  │  Redis   │                │
│  └────┬─────┘  └────┬─────┘                │
│       │              │                      │
│  ┌────┴──────────────┴────┐                │
│  │   Backend API (FastAPI) │ ← /health     │
│  │   uvicorn main:app      │               │
│  └────────────────────────┘                │
│                                             │
│  ┌────────────────────────┐                │
│  │   Worker (Celery)       │ ← processa    │
│  │   celery -A ... worker  │   PDFs        │
│  └────────────────────────┘                │
│                                             │
│  ┌────────────────────────┐                │
│  │   Frontend (Next.js)    │ ← o que o     │
│  │   npm run start         │   professor   │
│  └────────────────────────┘   acessa       │
└─────────────────────────────────────────────┘
```
