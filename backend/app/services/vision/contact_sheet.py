"""Monta contact sheets a partir de recortes de resposta ja existentes.

Os crops continuam na resolucao do pipeline. Esta folha so os organiza com
etiquetas de sistema `Q<n>` fora da area manuscrita, para uma chamada
multimodal de HTR V2. Nao recorta, nao altera o arquivo-fonte e nao inventa
numeracao sequencial.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_DEFAULT_MAX_PER_SHEET = 8
_MAX_PER_SHEET = 8
_LABEL_BAND_PX = 48
_PADDING_PX = 16
_GUTTER_PX = 16
_BACKGROUND = (245, 245, 245)
_LABEL_FILL = (32, 32, 32)
_COLUMNS = 2


@dataclass(frozen=True)
class ContactSheetItem:
    question_number: int
    crop_path: str


@dataclass(frozen=True)
class ContactSheet:
    path: str
    question_numbers: list[int]


def question_label(question_number: int) -> str:
    return f"Q{int(question_number)}"


def sanitize_max_questions_per_sheet(value: int | None) -> int:
    try:
        parsed = int(value) if value is not None else _DEFAULT_MAX_PER_SHEET
    except (TypeError, ValueError):
        parsed = _DEFAULT_MAX_PER_SHEET
    if parsed < 1:
        return _DEFAULT_MAX_PER_SHEET
    return min(parsed, _MAX_PER_SHEET)


def build_contact_sheets(
    items: list[ContactSheetItem],
    output_dir: Path,
    max_questions_per_sheet: int = 8,
) -> list[ContactSheet]:
    ordered = sorted(items, key=lambda item: int(item.question_number))
    chunk_size = sanitize_max_questions_per_sheet(max_questions_per_sheet)
    if not ordered:
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sheets: list[ContactSheet] = []
    for sheet_index, chunk in enumerate(_chunks(ordered, chunk_size), start=1):
        sheet_path = output_dir / f"contact_sheet_{sheet_index:02d}.png"
        _render_sheet(chunk, sheet_path)
        sheets.append(
            ContactSheet(
                path=str(sheet_path),
                question_numbers=[int(item.question_number) for item in chunk],
            )
        )
    return sheets


def _chunks(items: list[ContactSheetItem], size: int) -> list[list[ContactSheetItem]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _render_sheet(items: list[ContactSheetItem], destination: Path) -> None:
    crops = [_open_crop(item) for item in items]
    try:
        columns = 1 if len(items) <= 2 else _COLUMNS
        cell_width = max(image.width for image in crops) + (_PADDING_PX * 2)
        cell_heights = [image.height + _LABEL_BAND_PX + (_PADDING_PX * 2) for image in crops]
        rows = (len(items) + columns - 1) // columns
        row_heights = []
        for row in range(rows):
            start = row * columns
            row_items = cell_heights[start : start + columns]
            row_heights.append(max(row_items) if row_items else 0)

        sheet_width = (cell_width * columns) + (_GUTTER_PX * (columns + 1))
        sheet_height = sum(row_heights) + (_GUTTER_PX * (rows + 1))
        sheet = Image.new("RGB", (sheet_width, sheet_height), _BACKGROUND)
        draw = ImageDraw.Draw(sheet)
        font = _label_font()

        for index, (item, crop) in enumerate(zip(items, crops)):
            row = index // columns
            column = index % columns
            x0 = _GUTTER_PX + column * (cell_width + _GUTTER_PX)
            y0 = _GUTTER_PX + sum(row_heights[:row]) + (row * _GUTTER_PX)

            label = question_label(item.question_number)
            draw.text(
                (x0 + _PADDING_PX, y0 + 8),
                label,
                fill=_LABEL_FILL,
                font=font,
            )
            crop_x = x0 + _PADDING_PX
            crop_y = y0 + _LABEL_BAND_PX + _PADDING_PX
            sheet.paste(crop.convert("RGB"), (crop_x, crop_y))
        sheet.save(destination, format="PNG", optimize=True)
    finally:
        for image in crops:
            image.close()


def _open_crop(item: ContactSheetItem) -> Image.Image:
    path = Path(item.crop_path)
    if not path.is_file():
        raise FileNotFoundError(f"Crop nao encontrado: {path}")
    with Image.open(path) as image:
        return image.convert("RGB").copy()


def _label_font() -> ImageFont.ImageFont:
    candidates = (
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "arial.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, 28)
        except OSError:
            continue
    return ImageFont.load_default()
