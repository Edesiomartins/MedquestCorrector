# HTR V2 Multimodal Batch Design

## Goal

Replace the production handwritten-reading hot path of one vision call per answer crop with a multimodal per-student/bounded-batch read that preserves high-resolution crops while adding page-level visual context. The goal is higher cursive-reading accuracy and substantially lower latency for practical/discursive handwritten exams, without changing grading logic.

## Scope

This design changes only the HTR/vision-reading layer. Existing practical matching, discursive grading, rubric handling, review workflow, export, and scoring semantics must remain unchanged.

The implementation must support exams with up to 15 handwritten questions per student. The number of questions must be derived dynamically from the current manifest/rubric/question mapping. No code may assume exactly 10 questions.

## Architecture

For each logical student answer sheet, the existing pipeline continues to render the source page(s) and produce high-resolution answer crops. HTR V2 then builds one or two labeled contact sheets from those crops and sends them, together with contextual source page imagery, in a single multimodal OpenRouter request for that logical student batch.

Question grouping rule:

- 1-8 questions: one contact sheet.
- 9-15 questions: two contact sheets, Q1-Q8 and Q9-Q15 using the actual ordered question numbers present.

The contact-sheet limit exists to preserve handwriting scale. A 15-question sheet must never be squeezed into one low-resolution mosaic solely to reduce API calls.

If a logical student answer sheet spans multiple physical pages, the HTR V2 request may include the relevant contextual page images, but each question must still map to exactly one high-resolution crop and one question number. The implementation must follow the repository's existing page/student/manifest mapping rather than inventing a new identity model.

## Multimodal request

The vision model receives:

1. Context page image(s) for the student answer sheet.
2. Contact sheet for the first group of answer crops.
3. Optional second contact sheet for questions beyond the first eight.
4. A short blind-transcription instruction.

The request must not receive answer keys, expected answers, rubrics, correction criteria, grading outcomes, or semantic hints derived from the answer key. Existing answer-key stripping guarantees remain mandatory.

The contact sheet must visibly label each crop with its question number using system-generated labels placed outside the student's handwriting region. The model is instructed to use the contact sheets as the primary evidence for transcription and the page image(s) only as visual context.

## Output contract

HTR V2 returns one structured object containing one entry for every expected question in the batch. Each entry contains at least:

- `number`
- `answer_transcription`
- `reading_confidence`
- `has_answer`
- `reading_notes`

Optional audit metadata may include `source_sheet`, but downstream grading must continue to receive the same normalized question shape used by HTR V1.

Missing, duplicate, or unexpected question numbers are validation errors. The pipeline must never silently shift an answer from one question number to another.

## Feature flag and rollback

Add configuration:

- `HTR_BATCH_PAGE_ENABLED: bool = False`
- `HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET: int = 8`

The default in source code must remain `False` until production validation is complete. The Coolify environment can enable it explicitly.

When HTR V2 is disabled, current HTR V1 behavior is unchanged.

If the HTR V2 request fails at the API/transport/schema level, the system falls back to the existing per-crop HTR V1 flow for that logical student batch. A low-quality transcription by itself must not automatically invoke the old flow unless an explicit future quality-gate policy is introduced.

## Model routing

HTR V2 uses the existing `OPENROUTER_VISION_MODEL` as the primary model and the existing vision fallback chain only for API/model-call failures. This design does not introduce additional model-selection UI or change the textual grading model.

The first production candidate is `openai/gpt-5.6-sol`, with the existing OpenRouter configuration supplying the fallback chain.

## Privacy and anti-bias constraints

The vision stage remains blind to the answer key.

Do not add the expected answer, rubric, anatomy vocabulary generated from the correct answer, or grading hints to the HTR request.

Reuse the existing student/page privacy behavior. Do not increase retention of original student-identifying imagery beyond the current system's behavior.

## Contact-sheet generation

Create a focused helper module responsible only for assembling contact sheets from existing crop files. It should:

- preserve crop aspect ratio;
- avoid downscaling handwriting below the current readable crop scale unless absolutely necessary;
- add padding and a system-generated `Q<n>` label outside the handwriting content;
- use a neutral background;
- produce one sheet for up to eight crops;
- produce multiple sheets deterministically when needed;
- return metadata mapping every question number to its sheet index and crop source.

The helper must be independently unit-testable without OpenRouter access.

## Error handling

HTR V2 is accepted only when the response schema is valid and every expected question number is represented exactly once.

If the response is malformed, omits expected questions, duplicates numbers, or introduces unknown question numbers, record a warning and fall back to HTR V1 for that student batch.

Do not convert a malformed multimodal response into zero scores.

Do not alter the existing grading/matcher result to compensate for an HTR failure.

## Observability

For each HTR V2 request, log non-secret audit data sufficient to compare V1 and V2:

- student/page correlation id already used by the pipeline;
- requested vision model;
- actual model used when available;
- number of questions;
- number of context images;
- number of contact sheets;
- elapsed time;
- whether fallback to HTR V1 occurred and why.

If the OpenRouter response exposes token usage, capture input/output/total token counts in logs or returned audit metadata without logging API keys or raw sensitive request payloads.

## Testing requirements

At minimum, automated tests must prove:

1. 1-8 questions create one contact sheet.
2. 9-15 questions create two contact sheets.
3. Question numbering and order are preserved for non-contiguous question numbers as well as Q1-Q15.
4. No answer-key/rubric fields reach the multimodal prompt/context.
5. A valid V2 response normalizes to the same downstream question structure expected by the current grading pipeline.
6. Missing/duplicate/unexpected question numbers trigger V1 fallback rather than grading with misaligned data.
7. `HTR_BATCH_PAGE_ENABLED=false` leaves current V1 behavior unchanged.
8. A transport/API failure in V2 triggers V1 fallback for that student batch.
9. The existing practical matcher tests continue to pass unchanged.
10. No production test calls OpenRouter; HTTP/model calls must be mocked.

## Non-goals

This change does not:

- rewrite `practical_match.py`;
- change anatomical aliases;
- change scoring or review thresholds;
- enable TTA/consensus by default;
- replace the textual grading model;
- introduce batch-provider asynchronous APIs;
- parallelize Celery workers;
- change the frontend unless a minimal status/telemetry field is strictly required by an existing API contract.

## Acceptance criteria

The implementation is ready for manual validation when all targeted tests pass and the full relevant backend test suite has no new failures. No commit or deployment of implementation code should be performed by the coding agent until the user reviews its reported diff/test results.

Manual validation uses the same known PDF previously tested, first with one student/page if the UI supports page selection, then with the full set. Success is determined by both latency and transcription quality, with special attention to previously difficult anatomical terms such as `Artéria basilar`, `Veia interventricular posterior`, `Artéria frontobasilar medial esquerda`, `Artéria comunicante posterior direita`, and `Músculo papilar anterior do ventrículo esquerdo`.
