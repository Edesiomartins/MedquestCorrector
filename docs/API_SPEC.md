# medquestcorrector - Especificação da API

Esta API foi desenhada de forma RESTful focando primariamente em servir a aplicação Next.js para controle do pipeline. As respostas de rotas REST pesadas usarão paginação.

## 1. Authentication
Os endpoints de auth deverão retornar JWT, idealmente configurando `HttpOnly` cookies.

- **`POST /api/v1/auth/register`**
  Cria a conta do professor.
- **`POST /api/v1/auth/login`**
  Autenticação.
- **`GET /api/v1/auth/me`**
  Recupera sessão e profile.

## 2. Configuração de Provas e Templates

- **`GET /api/v1/exams`**
  Lista as provas do usuário logado.
- **`POST /api/v1/exams`**
  Cria um exame e associa um layout/template básico.
- **`GET /api/v1/exams/{id}`**
  Recupera detalhes, questões e rubricas.
- **`PUT /api/v1/exams/{id}`**
  Atualiza cabeçalhos ou pontuação máxima.

- **`POST /api/v1/exams/{id}/questions`**
  Adiciona uma questão, incluindo espelho, e os bounding boxes (`box_x`, `box_y`, `box_w`, `box_h`).
- **`PUT /api/v1/questions/{id}/rubrics`**
  Atualiza os critérios em lote (JSON payload com as rubricas da questão).

## 3. Upload & Pipeline Tracking

- **`POST /api/v1/batches/upload`**
  - **Type:** `multipart/form-data` (recebe um `file` PDF grande)
  - **Payload Adicional:** `exam_id`
  - **Retorno:** `202 Accepted` - `{ "batch_id": "uuid", "status": "PENDING" }`
  - *Comportamento:* Faz upload para o Storage e dispara a primeira task (Ingestão) no Celery/Redis.

- **`GET /api/v1/batches/{id}`**
  Status completo do Batch. Devolve quantas páginas foram processadas, provas detectadas, e progresso (ex: `percent_complete: 85.0`).

- **`GET /api/v1/batches/{id}/instances`**
  Lista todos os alunos (instâncias) encontrados dentro daquele Batch (paginado).

## 4. Revisão e Correção Humana

- **`GET /api/v1/exam-instances/{id}/review-data`**
  Rota consolidada para o Front-end montar a tela de "Side-by-side" do professor.
  - Retorna o array das respostas contendo: URL da imagem, Texto extraído (OCR), Justificativa da IA, Nota sugerida, Máxima, e status da auditoria.

- **`PUT /api/v1/grading-results/{id}/review`**
  Salva a decisão do professor.
  - **Payload:** 
    ```json
    {
      "approved_score": 8.5,
      "comments": "Foi bem no final, ajustando a nota pra cima.",
      "force_regrade": false
    }
    ```

## 5. Reprocessamento (Escape Hatches)

- **`POST /api/v1/answer-regions/{id}/ocr/retry`**
  Caso o OCR tenha vindo extremamente ilegível, permite ao professor forçar reprocessamento pontual (pode usar o `VisualFallbackService` da OpenRouter nesta chamada forçada).

- **`POST /api/v1/answer-regions/{id}/grade`**
  Reenvia apenas essa resposta pontual para a IA (útil se o professor alterou a rubrica no meio da correção e quer testar).

## 6. Resultados e Relatórios

- **`GET /api/v1/batches/{id}/summary`**
  Consolidado para exportar médias, maior e menor nota, desvio padrão, para o dashboard final.

- **`GET /api/v1/reports/exams/{id}/csv`**
  Gera e faz o download de um `.csv` com colunas de Aluno, Q1, Q2, Total, e Status.
