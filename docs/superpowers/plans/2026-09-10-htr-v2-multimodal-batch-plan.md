# HTR V2 Multimodal Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manifest hot path of one OpenRouter vision call per handwritten answer with one multimodal HTR request per student page, using the contextual page plus one or two high-resolution contact sheets, while preserving the current downstream grading contract and HTR V1 rollback path.

**Architecture:** Keep the current crop renderer, ink detector, student identification, practical matcher, discursive grader, review workflow, and exports. Add a focused contact-sheet helper plus a multi-image OpenRouter client call. When `HTR_BATCH_PAGE_ENABLED=true` and a manifest page has answer boxes, the pipeline renders/detects/prepares all crops first, groups nonblank crops in deterministic sheets of at most 8 questions, sends the contextual page and sheets in one blind multimodal request, validates exact question-number coverage, merges blank-answer metadata, then feeds the same normalized question objects downstream. Any V2 transport/schema/alignment failure falls back to the existing `_read_page_by_crops` path for that page.

**Tech Stack:** Python 3.11, FastAPI backend, Pillow, PyMuPDF, httpx, pytest, OpenRouter Chat Completions multimodal messages.

**Spec:** `docs/superpowers/specs/2026-09-10-htr-v2-multimodal-batch-design.md`

## Global Constraints

- Support 1-15 handwritten questions per student page dynamically; never hardcode exactly 10 questions.
- Default `HTR_BATCH_PAGE_ENABLED` to `False` in source until manual production validation.
- Default `HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET` to `8`; clamp unsafe values to a sensible range instead of crashing.
- HTR V2 receives contextual page image(s) plus one or two labeled contact sheets; contact sheets are the primary transcription evidence.
- Vision remains blind to `expected_answer`, `rubric`, `correction_criteria`, `gabarito`, answer keys, grading outcomes, and answer-key-derived vocabulary.
- Do not modify `practical_match.py`, anatomical aliases, scoring semantics, review thresholds, textual grading model, frontend, Celery concurrency, or provider batch APIs.
- Do not enable TTA/consensus by default as part of this change.
- OpenRouter/network calls in tests must be mocked.
- Preserve HTR V1 as a working rollback/fallback path.
- The coding agent MUST NOT commit, push, or deploy implementation changes. It must stop after reporting `git diff`, `git status`, and test results for human review.

---

### Task 1: Configuration and deterministic contact-sheet builder

**Files:**
- Modify: `backend/app/core/config.py`
- Create: `backend/app/services/vision/contact_sheet.py`
- Create: `backend/tests/test_htr_contact_sheet.py`

**Interfaces:**
- Produces: `build_contact_sheets(items: list[ContactSheetItem], output_dir: Path, max_questions_per_sheet: int = 8) -> list[ContactSheet]`
- Produces dataclasses `ContactSheetItem(question_number: int, crop_path: str)` and `ContactSheet(path: str, question_numbers: list[int])`.
- Later tasks consume the generated sheet paths and question-number mapping.

- [ ] **Step 1: Add failing configuration tests or assertions in the new contact-sheet test module**

Test the source defaults without relying on Coolify:

```python
from app.core.config import settings


def test_htr_batch_defaults_are_safe():
    assert settings.HTR_BATCH_PAGE_ENABLED is False
    assert settings.HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET == 8
```

- [ ] **Step 2: Add failing contact-sheet grouping tests**

Create synthetic crops with Pillow and assert deterministic grouping:

```python
def test_eight_questions_create_one_sheet(tmp_path):
    items = make_items(tmp_path, range(1, 9))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9))]


def test_fifteen_questions_create_two_sheets(tmp_path):
    items = make_items(tmp_path, range(1, 16))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9)), list(range(9, 16))]


def test_non_contiguous_numbers_keep_order(tmp_path):
    items = make_items(tmp_path, [1, 3, 7, 11, 15])
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert sheets[0].question_numbers == [1, 3, 7, 11, 15]
```

Also open the generated image with Pillow and assert it exists, has positive dimensions, and did not mutate source crops.

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
cd backend
pytest tests/test_htr_contact_sheet.py -v
```

Expected: FAIL because settings/helper do not exist yet.

- [ ] **Step 4: Implement configuration defaults**

Add to `Settings` in `backend/app/core/config.py` near existing HTR settings:

```python
HTR_BATCH_PAGE_ENABLED: bool = False
HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET: int = 8
```

Do not change the existing OpenRouter model settings.

- [ ] **Step 5: Implement `contact_sheet.py`**

Requirements for the minimal implementation:

```python
@dataclass(frozen=True)
class ContactSheetItem:
    question_number: int
    crop_path: str


@dataclass(frozen=True)
class ContactSheet:
    path: str
    question_numbers: list[int]
```

`build_contact_sheets` must sort by `question_number`, chunk by a sanitized `max_questions_per_sheet` (default 8, maximum 8 for this V2), preserve each crop aspect ratio, place a visible `Q<n>` label in a separate label band above the crop, use Pillow only, and save PNGs under `output_dir`. Do not resize source files in place. Prefer a vertical or two-column layout that keeps handwriting large; do not create a 15-cell micro-grid.

- [ ] **Step 6: Run contact-sheet tests**

```bash
cd backend
pytest tests/test_htr_contact_sheet.py -v
```

Expected: PASS.

- [ ] **Step 7: Do not commit**

Record changed files for the final report. Do not run `git commit`, `git push`, or deploy.

---

### Task 2: Multi-image OpenRouter blind transcription client

**Files:**
- Modify: `backend/app/services/openrouter_vision_client.py`
- Create: `backend/tests/test_htr_batch_transcription.py`

**Interfaces:**
- Produces: `transcribe_answer_batch(*, context_image_paths: list[str], contact_sheet_paths: list[str], expected_question_numbers: list[int], vision_model: str | None = None, allow_fallback: bool = True) -> dict`
- Returns `{questions, model_used, fallback_used, usage}` where each question uses the same keys downstream already expects: `number`, `prompt_detected`, `answer_transcription`, `reading_confidence`, `ocr_confidence`, `reading_notes`, `has_answer`, `image_region`, `model_used`, `fallback_used`.

- [ ] **Step 1: Write failing tests for the multimodal request shape**

Patch the HTTP/model call so no network occurs. Assert one model request contains one text part followed by all context-page and contact-sheet image parts, rather than one request per question.

```python
def test_batch_uses_one_multimodal_call_for_all_images(monkeypatch, image_paths):
    seen = {}
    # fake low-level call captures model/messages and returns valid JSON
    result = transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=image_paths[1:],
        expected_question_numbers=[1, 2, 3],
    )
    assert [q["number"] for q in result["questions"]] == [1, 2, 3]
    assert seen["call_count"] == 1
```

- [ ] **Step 2: Write failing blindness tests**

The batch prompt must contain only transcription instructions and the explicit list of question numbers. Assert forbidden strings/fields are absent:

```python
for forbidden in ("expected_answer", "gabarito", "resposta esperada", "correction_criteria", "rubric"):
    assert forbidden not in captured_prompt.lower()
```

The model may be told `expected_question_numbers=[1,2,...]`; it may not be told expected answer text.

- [ ] **Step 3: Write failing schema/alignment tests**

Provide mocked model JSON for these cases and require `OpenRouterVisionError` (or a focused subclass) before anything reaches grading:

```python
# missing 2
{"questions":[{"number":1,...},{"number":3,...}]}
# duplicate 2
{"questions":[{"number":1,...},{"number":2,...},{"number":2,...}]}
# unknown 99
{"questions":[{"number":1,...},{"number":2,...},{"number":99,...}]}
```

Also test valid non-contiguous expected numbers such as `[1, 3, 7]`.

- [ ] **Step 4: Run the tests and verify they fail**

```bash
cd backend
pytest tests/test_htr_batch_transcription.py -v
```

- [ ] **Step 5: Implement a reusable low-level multi-image call**

Do not break existing `_call_openrouter_vision` or `transcribe_answer_crop`. Add a sibling helper that constructs Chat Completions `messages[0].content` as:

```python
[
    {"type": "text", "text": prompt},
    {"type": "image_url", "image_url": {"url": encode_image_to_data_url(context_path)}},
    {"type": "image_url", "image_url": {"url": encode_image_to_data_url(sheet_1)}},
    # optional sheet_2
]
```

Use `temperature=0`, structured JSON output, existing auth headers/base URL/timeout, and the existing vision model candidate/fallback chain. Preserve actual `model_used`/`fallback_used`.

If the OpenRouter JSON response includes `usage`, normalize it to optional integer fields `prompt_tokens`, `completion_tokens`, and `total_tokens`; absence of usage must not fail the request.

- [ ] **Step 6: Implement the batch prompt and parser**

The prompt must say that contact sheets are the primary evidence, page images are context only, labels `Q<n>` are system labels, transcription is literal and blind, crossed-out text is omitted, uncertain words use `[?]`, illegible text uses `[ilegível]`, and output must contain exactly the supplied question numbers once each.

Normalize confidence through existing `_normalize_confidence`. Set `ocr_confidence=None` rather than inventing a numeric confidence.

- [ ] **Step 7: Run batch-client and existing crop-client tests**

```bash
cd backend
pytest tests/test_htr_batch_transcription.py tests/test_answer_crop_transcription.py -v
```

Expected: PASS. Existing HTR V1 client behavior must remain unchanged.

- [ ] **Step 8: Do not commit**

Record diff/test output only.

---

### Task 3: Integrate HTR V2 into the manifest pipeline with V1 fallback

**Files:**
- Modify: `backend/app/services/visual_exam_pipeline.py`
- Create: `backend/tests/test_htr_batch_pipeline.py`

**Interfaces:**
- Consumes: `build_contact_sheets(...)` and `transcribe_answer_batch(...)`.
- Produces: the existing page result contract `{student, physical_page, questions, model_used, fallback_used, read_strategy}`.
- Keep `_read_page_by_crops(...)` intact as HTR V1 fallback.

- [ ] **Step 1: Write failing test that the feature flag preserves V1**

Mock `_read_page_by_crops` and the V2 reader. With `HTR_BATCH_PAGE_ENABLED=False`, assert only V1 is called and `read_strategy` remains the current V1 strategy.

- [ ] **Step 2: Write failing test that V2 does one batch call for a manifest page**

Use a fake manifest page with 10 boxes. Mock `render_pdf_box`, `detect_ink`, `normalize_for_reading`, student identification, contact-sheet builder, and `transcribe_answer_batch`. Assert the batch transcriber is called exactly once for the page, with question numbers derived from manifest boxes rather than a constant range.

- [ ] **Step 3: Write blank-answer merge test**

For a page with Q1, Q2, Q3 where Q2 has no ink, assert Q2 is resolved locally through `_blank_answer_question`, excluded from the LLM `expected_question_numbers`, and restored into the final sorted question list. The model must not be charged to read known-empty crops.

- [ ] **Step 4: Write V2 failure fallback tests**

Mock V2 to raise on transport failure and separately on schema/alignment failure. Assert `_read_page_by_crops` is invoked once for that page and a warning records the V2 fallback reason. Never emit zero grades because V2 failed.

- [ ] **Step 5: Run pipeline tests and verify failure**

```bash
cd backend
pytest tests/test_htr_batch_pipeline.py -v
```

- [ ] **Step 6: Implement `_read_page_by_batch` as a sibling of `_read_page_by_crops`**

Algorithm:

```text
for each manifest box in question-number order:
    render crop at existing CROP_DPI
    detect ink
    save blank crops and resolve them locally
    normalize/save nonblank crops
build 1-2 contact sheets from nonblank crops
normalize/copy contextual page image for context
call transcribe_answer_batch once
validate returned question numbers (client already validates; pipeline defends again)
attach answer_crop_path and ink_ratio from locally prepared crop metadata
merge blank questions
sort by question number
identify page/student with existing _identify_page
return existing page result shape with read_strategy="manifest_batch_v2"
```

Do not run `_maybe_escalate` in the V2 path in this change; the V2 request itself is the new primary strategy and TTA/consensus remains a separate future quality-gate decision.

- [ ] **Step 7: Gate `_read_page` with the feature flag**

When a manifest page has boxes:

```python
if bool(options.get("htr_batch_page_enabled", settings.HTR_BATCH_PAGE_ENABLED)):
    try:
        return _read_page_by_batch(...)
    except Exception as exc:
        warnings.append(...)
        return _read_page_by_crops(...)
return _read_page_by_crops(...)
```

Avoid catching programming errors too broadly inside lower-level helpers; the page-level boundary is the intended safe rollback boundary. Include the exception class/message in server logs but not secrets.

- [ ] **Step 8: Add observability**

Log for each V2 page request: page/student correlation fields available at that stage, requested/actual model, question count, context image count, contact sheet count, elapsed seconds, fallback status/reason, and token usage when returned. Do not log image data URLs, API keys, raw student images, or the complete model prompt.

Expose optional usage only in internal/raw audit metadata if an existing suitable field exists; do not change public API schemas solely for telemetry.

- [ ] **Step 9: Run targeted pipeline tests**

```bash
cd backend
pytest tests/test_htr_batch_pipeline.py tests/test_answer_crop_transcription.py tests/test_htr_contact_sheet.py tests/test_htr_batch_transcription.py -v
```

Expected: PASS.

- [ ] **Step 10: Do not commit**

Record status and diff only.

---

### Task 4: Regression protection for grading and manifest geometry

**Files:**
- Test only unless a V2-caused regression requires a minimal targeted fix.
- Existing: `backend/tests/test_practical_match.py`
- Existing geometry tests under `backend/tests/` that cover `sheet_geometry.py` / manifest reading.

**Interfaces:**
- Confirms HTR V2 did not alter grading semantics or crop geometry.

- [ ] **Step 1: Run practical matcher tests unchanged**

```bash
cd backend
pytest tests/test_practical_match.py -v
```

Expected: PASS with no matcher edits.

- [ ] **Step 2: Run tests that reference sheet geometry / manifest cropping**

Discover exact filenames first:

```bash
cd backend
pytest --collect-only -q | grep -E "sheet_geometry|manifest|crop"
```

On Windows PowerShell use:

```powershell
pytest --collect-only -q | Select-String -Pattern "sheet_geometry|manifest|crop"
```

Run the matching existing test modules. Any failure introduced by V2 must be fixed minimally without changing crop coordinates or DPI semantics.

- [ ] **Step 3: Run all HTR-focused tests**

```bash
cd backend
pytest tests/test_htr_contact_sheet.py tests/test_htr_batch_transcription.py tests/test_htr_batch_pipeline.py tests/test_answer_crop_transcription.py tests/test_practical_match.py -v
```

Expected: PASS.

- [ ] **Step 4: Do not commit**

Do not alter unrelated pre-existing flaky tests to make the suite green.

---

### Task 5: Full verification and human handoff

**Files:**
- No additional production files unless verification exposes a V2-specific defect.

**Interfaces:**
- Produces the evidence the human reviewer needs before any commit/deploy decision.

- [ ] **Step 1: Run the full backend suite**

```bash
cd backend
pytest -q
```

Report exact totals. If there is a failure, classify it as introduced by this diff or pre-existing using focused reruns/history; do not label a failure pre-existing without evidence.

- [ ] **Step 2: Verify forbidden scope did not move**

Run:

```bash
git diff -- backend/app/services/grading/practical_match.py backend/app/services/exam_grading_client.py
```

Expected: no V2-driven matcher/scoring changes. If `exam_grading_client.py` has unrelated pre-existing local changes, call that out rather than overwriting them.

- [ ] **Step 3: Inspect final working tree**

```bash
git status --short
git diff --stat
git diff
```

- [ ] **Step 4: Report implementation evidence and STOP**

Report:

```text
HTR V2 implementation status
- Files created:
- Files modified:
- Feature flag default:
- Max questions/sheet:
- 1-8 grouping test:
- 9-15 grouping test:
- Non-contiguous numbering test:
- Blindness test:
- V2 one-call test:
- Blank-answer local handling test:
- Schema mismatch -> V1 fallback test:
- API failure -> V1 fallback test:
- Practical matcher regression test:
- Full suite result:
- git diff --stat:
- Known issues / assumptions:
```

Do not commit, push, change Coolify environment variables, or deploy. Wait for human review.
