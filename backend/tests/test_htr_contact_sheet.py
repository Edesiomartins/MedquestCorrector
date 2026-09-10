"""HTR V2: contact sheets agrupam crops em alta resolucao com etiquetas Q<n>."""

from hashlib import sha256
from pathlib import Path

from PIL import Image
from app.core.config import Settings
from app.services.vision.contact_sheet import (
    ContactSheetItem,
    build_contact_sheets,
    question_label,
)


def make_items(tmp_path: Path, numbers) -> list[ContactSheetItem]:
    items = []
    for number in numbers:
        path = tmp_path / f"q{number}.png"
        Image.new("RGB", (480, 160), (240, 240, 240)).save(path)
        items.append(ContactSheetItem(question_number=number, crop_path=str(path)))
    return items


def test_htr_batch_defaults_are_safe():
    source = Settings(_env_file=None)
    assert source.HTR_BATCH_PAGE_ENABLED is False
    assert source.HTR_BATCH_PAGE_MAX_QUESTIONS_PER_SHEET == 8


def test_eight_questions_create_one_sheet(tmp_path):
    items = make_items(tmp_path, range(1, 9))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9))]


def test_fifteen_questions_create_two_sheets(tmp_path):
    items = make_items(tmp_path, range(1, 16))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9)), list(range(9, 16))]


def test_nine_questions_create_two_sheets(tmp_path):
    items = make_items(tmp_path, range(1, 10))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9)), [9]]


def test_non_contiguous_numbers_keep_order(tmp_path):
    items = make_items(tmp_path, [1, 3, 7, 11, 15])
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)
    assert sheets[0].question_numbers == [1, 3, 7, 11, 15]


def test_labels_use_real_question_numbers_not_renumbered():
    assert question_label(1) == "Q1"
    assert question_label(3) == "Q3"
    assert question_label(11) == "Q11"
    assert [question_label(n) for n in [1, 3, 7, 11, 15]] == ["Q1", "Q3", "Q7", "Q11", "Q15"]


def test_sheet_image_exists_and_does_not_mutate_source_crops(tmp_path):
    items = make_items(tmp_path, [1, 2])
    originals = {item.crop_path: sha256(Path(item.crop_path).read_bytes()).hexdigest() for item in items}
    original_sizes = {item.crop_path: Image.open(item.crop_path).size for item in items}

    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=8)

    assert len(sheets) == 1
    sheet_path = Path(sheets[0].path)
    assert sheet_path.is_file()
    with Image.open(sheet_path) as sheet:
        assert sheet.width > 0
        assert sheet.height > 0
        crop_h = original_sizes[items[0].crop_path][1]
        assert sheet.height > crop_h, "a faixa de etiqueta Q<n> deve aumentar a altura da sheet"

    for path, digest in originals.items():
        assert sha256(Path(path).read_bytes()).hexdigest() == digest
        assert Image.open(path).size == original_sizes[path]


def test_unsafe_max_questions_per_sheet_is_clamped(tmp_path):
    items = make_items(tmp_path, range(1, 10))
    sheets = build_contact_sheets(items, tmp_path / "out", max_questions_per_sheet=99)
    assert [s.question_numbers for s in sheets] == [list(range(1, 9)), [9]]
