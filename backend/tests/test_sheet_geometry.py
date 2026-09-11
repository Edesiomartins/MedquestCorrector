"""Item 4 do docs/HTR_PLANO_EXECUCAO.md: geometria da folha, compartilhada.

Os dois pipelines (visual e Celery) passam a ler o manifesto pelo mesmo servico,
e o recorte da caixa de resposta e rasterizado direto do PDF em DPI alto --
`page.get_pixmap(clip=...)` -- em vez de rasterizar a pagina inteira e recortar
depois. E a diferenca entre entregar ~8 px de altura-de-x ao modelo e entregar
~37 px.
"""

import json

import pymupdf as fitz
import pytest
from reportlab.lib.pagesizes import A4

from app.services.vision.sheet_geometry import (
    AnswerBox,
    SheetManifest,
    box_to_pixels,
    crop_box_from_image,
    load_manifest,
    page_size_pt,
    render_pdf_box,
)


PAGE_W_PT, PAGE_H_PT = A4

MANIFEST = {
    "version": 1,
    "pages": [
        {
            "physical_index": 0,
            "exam_id": "exam-1",
            "student_id": "student-1",
            "page_in_student": 1,
            "total_pages_for_student": 2,
            "boxes": [
                {"question_number": 1, "x_pt": 56.7, "y_bottom_pt": 600.0, "width_pt": 481.9, "height_pt": 80.0},
                {"question_number": 2, "x_pt": 56.7, "y_bottom_pt": 480.0, "width_pt": 481.9, "height_pt": 80.0},
            ],
            "fiducials": [{"x_pt": 40.0, "y_pt": 56.7, "width_pt": 11.3, "height_pt": 11.3}],
        },
        {
            "physical_index": 1,
            "exam_id": "exam-1",
            "student_id": "student-1",
            "page_in_student": 2,
            "total_pages_for_student": 2,
            "boxes": [],
            "fiducials": [],
        },
    ],
}


@pytest.fixture
def pdf_path(tmp_path):
    """PDF A4 com um retangulo preto exatamente sobre a caixa da questao 1."""
    path = tmp_path / "folha.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    box = MANIFEST["pages"][0]["boxes"][0]
    # fitz usa origem superior esquerda; o manifesto usa inferior esquerda.
    top = PAGE_H_PT - (box["y_bottom_pt"] + box["height_pt"])
    page.draw_rect(
        fitz.Rect(box["x_pt"], top, box["x_pt"] + box["width_pt"], top + box["height_pt"]),
        color=(0, 0, 0),
        fill=(0, 0, 0),
    )
    doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    doc.save(str(path))
    doc.close()
    return path


# --- carregamento do manifesto ------------------------------------------------


def test_load_manifest_accepts_json_string_and_dict():
    from_str = load_manifest(json.dumps(MANIFEST))
    from_dict = load_manifest(MANIFEST)

    assert isinstance(from_str, SheetManifest)
    assert from_str.pages.keys() == from_dict.pages.keys()


@pytest.mark.parametrize("raw", [None, "", "{", "nao e json", [], {}])
def test_load_manifest_returns_none_for_unusable_input(raw):
    assert load_manifest(raw) is None


def test_manifest_page_lookup_is_by_physical_index():
    manifest = load_manifest(MANIFEST)

    page = manifest.page(0)
    assert page is not None
    assert page.student_id == "student-1"
    assert [b.question_number for b in page.boxes] == [1, 2]
    assert manifest.page(99) is None


def test_manifest_reports_whether_a_page_has_boxes():
    manifest = load_manifest(MANIFEST)

    assert manifest.page(0).has_boxes is True
    assert manifest.page(1).has_boxes is False


def test_manifest_box_lookup_by_question_number():
    page = load_manifest(MANIFEST).page(0)

    assert page.box(2).y_bottom_pt == 480.0
    assert page.box(7) is None


def test_load_manifest_skips_malformed_pages_without_failing():
    payload = {"version": 1, "pages": [{"physical_index": "nao-e-int"}, MANIFEST["pages"][0]]}

    manifest = load_manifest(payload)

    assert list(manifest.pages) == [0]


def test_load_manifest_reads_external_discursive_source():
    manifest = load_manifest({**MANIFEST, "source": "external_discursive"})

    assert manifest.source == "external_discursive"
    assert manifest.is_external_discursive is True
    assert load_manifest(MANIFEST).is_external_discursive is False


# --- conversao de coordenadas -------------------------------------------------


def test_box_to_pixels_flips_the_vertical_axis():
    box = AnswerBox(question_number=1, x_pt=0.0, y_bottom_pt=0.0, width_pt=72.0, height_pt=72.0)

    left, upper, right, lower = box_to_pixels(box, page_height_pt=720.0, dpi=72)

    assert (left, right) == (0, 72)
    # A caixa esta no rodape: em pixels ela fica no fim da pagina, nao no topo.
    assert (upper, lower) == (648, 720)


def test_box_to_pixels_scales_with_dpi():
    box = AnswerBox(question_number=1, x_pt=72.0, y_bottom_pt=72.0, width_pt=72.0, height_pt=72.0)

    at_72 = box_to_pixels(box, page_height_pt=720.0, dpi=72)
    at_144 = box_to_pixels(box, page_height_pt=720.0, dpi=144)

    assert [v * 2 for v in at_72] == list(at_144)


def test_box_to_pixels_padding_grows_the_crop():
    box = AnswerBox(question_number=1, x_pt=100.0, y_bottom_pt=100.0, width_pt=100.0, height_pt=100.0)

    tight = box_to_pixels(box, page_height_pt=720.0, dpi=72)
    padded = box_to_pixels(box, page_height_pt=720.0, dpi=72, pad_frac=0.10)

    assert padded[0] < tight[0] and padded[1] < tight[1]
    assert padded[2] > tight[2] and padded[3] > tight[3]


# --- rasterizacao do recorte --------------------------------------------------


def test_page_size_pt_reads_the_real_page(pdf_path):
    width, height = page_size_pt(str(pdf_path), 0)

    assert round(width) == round(PAGE_W_PT)
    assert round(height) == round(PAGE_H_PT)


def test_render_pdf_box_returns_only_the_box_at_the_requested_dpi(pdf_path):
    box = load_manifest(MANIFEST).page(0).box(1)

    crop = render_pdf_box(str(pdf_path), 0, box, dpi=380, pad_frac=0.0)

    assert abs(crop.width - round(box.width_pt * 380 / 72.0)) <= 2
    assert abs(crop.height - round(box.height_pt * 380 / 72.0)) <= 2


def test_render_pdf_box_pads_the_crop_by_default(pdf_path):
    """Descendentes de 'g'/'j'/'p' e quem escreve fora da area cinza."""
    box = load_manifest(MANIFEST).page(0).box(1)

    tight = render_pdf_box(str(pdf_path), 0, box, dpi=150, pad_frac=0.0)
    padded = render_pdf_box(str(pdf_path), 0, box, dpi=150)

    assert padded.width > tight.width
    assert padded.height > tight.height


def test_render_pdf_box_lands_on_the_right_region(pdf_path):
    """O retangulo preto foi desenhado exatamente sobre a caixa da questao 1."""
    manifest = load_manifest(MANIFEST)

    on_target = render_pdf_box(str(pdf_path), 0, manifest.page(0).box(1), dpi=150)
    off_target = render_pdf_box(str(pdf_path), 0, manifest.page(0).box(2), dpi=150)

    assert _mean_luma(on_target) < 40
    assert _mean_luma(off_target) > 200


def test_render_pdf_box_beats_full_page_rendering_in_resolution(pdf_path):
    """O ponto do item 4: DPI alto no recorte sem estourar o tamanho da pagina."""
    box = load_manifest(MANIFEST).page(0).box(1)
    doc = fitz.open(str(pdf_path))
    full_page_h = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(220 / 72, 220 / 72)).height
    doc.close()

    crop = render_pdf_box(str(pdf_path), 0, box, dpi=380)

    # O recorte cabe folgado no orcamento de pixels que a pagina inteira gastava...
    assert crop.height < full_page_h
    # ...e ainda assim entrega mais pixels por ponto de PDF.
    assert crop.height / box.height_pt > 220 / 72.0


def test_render_pdf_box_raises_for_missing_page(pdf_path):
    box = load_manifest(MANIFEST).page(0).box(1)

    with pytest.raises(IndexError):
        render_pdf_box(str(pdf_path), 99, box, dpi=150)


def test_crop_box_from_image_matches_box_to_pixels(pdf_path):
    from PIL import Image

    box = load_manifest(MANIFEST).page(0).box(1)
    doc = fitz.open(str(pdf_path))
    pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
    page_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()

    crop = crop_box_from_image(page_img, box, page_height_pt=PAGE_H_PT, dpi=150)

    assert _mean_luma(crop) < 40


def _mean_luma(image) -> float:
    import numpy as np

    return float(np.asarray(image.convert("L")).mean())
