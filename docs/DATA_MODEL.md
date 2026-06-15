# medquestcorrector - Modelo de Dados Inicial

Este documento define as entidades principais que sustentarão o MVP e o pipeline assíncrono. O uso de PostgreSQL é o recomendado.

## Entidades Core & Auth

### `users`
Tabela de cadastro e autenticação de professores e administradores.
- `id` (UUID, PK)
- `email` (String, Unique)
- `password_hash` (String)
- `role` (Enum: PROFESSOR, ADMIN)
- `created_at` (Timestamp)

### `organizations` & `classes` (Para escalabilidade futura)
Permite dividir o acesso em instituições e turmas.
- `id` (UUID, PK)
- `name` (String)

## Entidades de Prova e Template

### `exam_templates`
Representa a versão master do esqueleto da prova, definindo onde ficam os boxes físicos de resposta.
- `id` (UUID, PK)
- `name` (String)
- `pdf_blueprint_url` (String) - Referência ao arquivo base.
- `page_width` (Float)
- `page_height` (Float)

### `exams`
Uma aplicação real de um template a uma turma, com nota máxima fechada.
- `id` (UUID, PK)
- `template_id` (UUID, FK)
- `class_id` (UUID, FK)
- `name` (String)
- `max_score` (Float)

### `exam_questions`
Estrutura de uma questão dentro de um exame e suas coordenadas na página.
- `id` (UUID, PK)
- `exam_id` (UUID, FK)
- `question_text` (Text)
- `max_score` (Float)
- `expected_answer` (Text) - O "Espelho".
- `page_number` (Int) - Em qual folha do PDF ela se encontra.
- `box_x`, `box_y`, `box_w`, `box_h` (Float) - Coordenadas de crop do gabarito.

### `question_rubrics`
Os critérios avaliativos repassados à IA.
- `id` (UUID, PK)
- `question_id` (UUID, FK)
- `criteria` (Text) - Ex: "Deve mencionar mitose".
- `score_impact` (Float) - Quanto vale/subtrai.
- `is_mandatory` (Boolean) - Se não tiver, zera?

## Entidades de Pipeline e Upload

### `upload_batches`
A remessa de 1 arquivo PDF gigantesco de provas.
- `id` (UUID, PK)
- `exam_id` (UUID, FK)
- `file_url` (String)
- `status` (Enum: PENDING, PARSING, CROPPING, OCR, GRADING, REVIEW_PENDING, DONE, FAILED)
- `total_pages_detected` (Int)

### `detected_exam_instances`
Um conjunto físico que pertence a UM aluno (uma prova individual agrupada dentro do Batch).
- `id` (UUID, PK)
- `batch_id` (UUID, FK)
- `student_identifier_text` (String) - Matrícula ou nome.
- `review_status` (Enum: PENDING, APPROVED)

### `answer_regions`
O crop exato de uma resposta vinculada a uma questão e aluno.
- `id` (UUID, PK)
- `instance_id` (UUID, FK)
- `question_id` (UUID, FK)
- `cropped_image_url` (String)
- `ocr_status` (Enum: PENDING, SUCCESS, FAILED, NEEDS_FALLBACK)

## Entidades de IA e Revisão

### `ocr_results`
O texto retornado pela engine de extração.
- `id` (UUID, PK)
- `answer_region_id` (UUID, FK)
- `provider_used` (String) - Ex: "AZURE_READ_V3"
- `extracted_text` (Text)
- `confidence_avg` (Float)
- `needs_fallback_flag` (Boolean)

### `grading_results`
A correção efetuada pela OpenRouter (A "nota sugerida").
- `id` (UUID, PK)
- `ocr_result_id` (UUID, FK)
- `model_used` (String) - Ex: "claude-3.5-sonnet"
- `suggested_score` (Float)
- `criteria_met_json` (JSONB)
- `justification` (Text)
- `requires_manual_review` (Boolean) - Se a IA teve dúvidas ou confidence caiu.

### `manual_reviews`
Log de auditoria do professor quando ele "dá o ok" na resposta.
- `id` (UUID, PK)
- `grading_result_id` (UUID, FK)
- `reviewer_id` (UUID, FK - User)
- `final_score` (Float)
- `reviewer_comments` (Text)
- `created_at` (Timestamp)

## Rollups e Consolidação

### `final_scores`
Tabela/View consolidada para exportação.
- `instance_id` (UUID, FK)
- `student_identifier_text` (String)
- `total_score` (Float)
- `reviewed_by_all` (Boolean)
