"""Geometria da folha-resposta, compartilhada pelos dois pipelines de leitura.

Antes deste modulo havia duas rotas divergentes: o worker Celery lia
`Exam.layout_manifest_json` e recortava por caixa, enquanto o pipeline visual
mandava a **pagina inteira** ao modelo e ignorava o manifesto. Ver
docs/HTR_PLANO_EXECUCAO.md, item 4.

O motivo de recortar e resolucao efetiva, e a conta e direta. A altura-de-x da
letra cursiva de um aluno fica em torno de 2,5 mm no papel:

- pagina inteira a 220 DPI, depois reduzida para caber no limite do modelo:
  sobram ~8 px de altura-de-x;
- recorte da caixa a 380 DPI, enviado inteiro: ~37 px.

Abaixo de ~16-20 px ninguem le cursiva de forma confiavel -- nem pessoa nem
modelo. Entregar 8 px e a causa numero um das falhas de leitura.

`render_pdf_box` rasteriza **so o retangulo da caixa**, via
`page.get_pixmap(clip=...)` do PyMuPDF. Isso da DPI alto sem nunca materializar
a pagina inteira em alta resolucao, entao o custo de memoria cai junto com o
ganho de qualidade.

Convencao de coordenadas: o manifesto guarda pontos PDF com origem no canto
INFERIOR esquerdo (convencao do ReportLab, que gera a folha). Imagens usam origem
no canto SUPERIOR esquerdo. A inversao do eixo Y acontece aqui, num lugar so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pymupdf as fitz
from PIL import Image
from reportlab.lib.pagesizes import A4

logger = logging.getLogger(__name__)

# Manifesto versão 1 não gravava o tamanho da página; a folha sempre foi A4.
DEFAULT_PAGE_WIDTH_PT, DEFAULT_PAGE_HEIGHT_PT = (float(A4[0]), float(A4[1]))

# DPI do recorte enviado ao modelo de visao. 380 poe a altura-de-x da cursiva
# perto de 37 px, com folga sobre o piso de legibilidade.
DEFAULT_CROP_DPI = 380
# Folga em volta da caixa: descendentes ("g", "j", "p") e alunos que escrevem
# um pouco fora da area cinza.
DEFAULT_PAD_FRAC = 0.04


@dataclass(frozen=True)
class AnswerBox:
    question_number: int
    x_pt: float
    y_bottom_pt: float
    width_pt: float
    height_pt: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_number": self.question_number,
            "x_pt": self.x_pt,
            "y_bottom_pt": self.y_bottom_pt,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
        }


# Estilos de fiducial. Provas antigas trazem quadrados sólidos; provas novas
# trazem ArUco. O manifesto grava qual é, e o detector escolhe por ele.
FIDUCIAL_STYLE_SQUARE = "square"
FIDUCIAL_STYLE_ARUCO = "aruco"


@dataclass(frozen=True)
class Fiducial:
    x_pt: float
    y_pt: float
    width_pt: float
    height_pt: float
    marker_id: int | None = None

    @property
    def center_pt(self) -> tuple[float, float]:
        return self.x_pt + self.width_pt / 2.0, self.y_pt + self.height_pt / 2.0


@dataclass(frozen=True)
class ManifestPageGeometry:
    physical_index: int
    exam_id: str
    student_id: str
    page_in_student: int
    total_pages_for_student: int
    boxes: list[AnswerBox] = field(default_factory=list)
    fiducials: list[Fiducial] = field(default_factory=list)
    fiducial_style: str = FIDUCIAL_STYLE_SQUARE
    page_width_pt: float = DEFAULT_PAGE_WIDTH_PT
    page_height_pt: float = DEFAULT_PAGE_HEIGHT_PT

    @property
    def has_aruco(self) -> bool:
        return self.fiducial_style == FIDUCIAL_STYLE_ARUCO and any(
            f.marker_id is not None for f in self.fiducials
        )

    @property
    def has_boxes(self) -> bool:
        return bool(self.boxes)

    def box(self, question_number: int) -> AnswerBox | None:
        for box in self.boxes:
            if box.question_number == question_number:
                return box
        return None


@dataclass(frozen=True)
class SheetManifest:
    version: int
    pages: dict[int, ManifestPageGeometry]

    def page(self, physical_index: int) -> ManifestPageGeometry | None:
        return self.pages.get(int(physical_index))

    @property
    def has_boxes(self) -> bool:
        return any(page.has_boxes for page in self.pages.values())


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_box(raw: Any) -> AnswerBox | None:
    if not isinstance(raw, dict):
        return None
    try:
        number = int(raw["question_number"])
    except (KeyError, TypeError, ValueError):
        return None
    values = [_float(raw.get(key)) for key in ("x_pt", "y_bottom_pt", "width_pt", "height_pt")]
    if any(value is None for value in values):
        return None
    x, y, w, h = values  # type: ignore[misc]
    if w <= 0 or h <= 0:
        return None
    return AnswerBox(question_number=number, x_pt=x, y_bottom_pt=y, width_pt=w, height_pt=h)


def _parse_fiducial(raw: Any) -> Fiducial | None:
    if not isinstance(raw, dict):
        return None
    values = [_float(raw.get(key)) for key in ("x_pt", "y_pt", "width_pt", "height_pt")]
    if any(value is None for value in values):
        return None
    x, y, w, h = values  # type: ignore[misc]
    marker = raw.get("marker_id")
    try:
        marker_id = int(marker) if marker is not None else None
    except (TypeError, ValueError):
        marker_id = None
    return Fiducial(x_pt=x, y_pt=y, width_pt=w, height_pt=h, marker_id=marker_id)


def _parse_page(raw: Any) -> ManifestPageGeometry | None:
    if not isinstance(raw, dict):
        return None
    try:
        physical_index = int(raw["physical_index"])
    except (KeyError, TypeError, ValueError):
        return None
    boxes = [box for box in (_parse_box(item) for item in raw.get("boxes") or []) if box]
    fiducials = [f for f in (_parse_fiducial(item) for item in raw.get("fiducials") or []) if f]
    # Manifesto versão 1 não gravava o estilo: era sempre quadrado.
    style = str(raw.get("fiducial_style") or FIDUCIAL_STYLE_SQUARE)
    return ManifestPageGeometry(
        physical_index=physical_index,
        exam_id=str(raw.get("exam_id") or ""),
        student_id=str(raw.get("student_id") or ""),
        page_in_student=int(_float(raw.get("page_in_student")) or 0),
        total_pages_for_student=int(_float(raw.get("total_pages_for_student")) or 0),
        boxes=boxes,
        fiducials=fiducials,
        fiducial_style=style,
        page_width_pt=_float(raw.get("page_width_pt")) or DEFAULT_PAGE_WIDTH_PT,
        page_height_pt=_float(raw.get("page_height_pt")) or DEFAULT_PAGE_HEIGHT_PT,
    )


def load_manifest(raw: str | dict | None) -> SheetManifest | None:
    """Le o manifesto de `Exam.layout_manifest_json`, tolerando lixo.

    Devolve None quando nao ha manifesto utilizavel -- os chamadores tratam isso
    como "cai no modo pagina inteira", nunca como erro. Paginas malformadas sao
    descartadas individualmente: uma pagina ruim nao pode derrubar a leitura das
    outras.
    """
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Manifest JSON inválido na prova; usando fallback de página inteira.")
            return None
    else:
        data = raw

    if not isinstance(data, dict):
        return None
    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        return None

    pages: dict[int, ManifestPageGeometry] = {}
    for item in raw_pages:
        page = _parse_page(item)
        if page is None:
            logger.warning("Página malformada no manifest; ignorada.")
            continue
        pages[page.physical_index] = page

    if not pages:
        return None
    return SheetManifest(version=int(_float(data.get("version")) or 1), pages=pages)


def box_to_pixels(
    box: AnswerBox,
    page_height_pt: float,
    dpi: float,
    pad_frac: float = 0.0,
) -> tuple[int, int, int, int]:
    """Retângulo PDF (origem inferior esquerda) → caixa PIL (origem superior esquerda).

    Retorna (left, upper, right, lower) em pixels.
    """
    scale = dpi / 72.0
    pad_x = box.width_pt * pad_frac
    pad_y = box.height_pt * pad_frac

    x0 = box.x_pt - pad_x
    x1 = box.x_pt + box.width_pt + pad_x
    y_top_pt = box.y_bottom_pt + box.height_pt + pad_y
    y_bottom_pt = box.y_bottom_pt - pad_y

    left = int(round(x0 * scale))
    right = int(round(x1 * scale))
    upper = int(round((page_height_pt - y_top_pt) * scale))
    lower = int(round((page_height_pt - y_bottom_pt) * scale))
    return left, upper, right, lower


def page_size_pt(pdf_path: str, page_index: int) -> tuple[float, float]:
    """Tamanho real da página em pontos PDF.

    Ler do arquivo em vez de assumir A4: PDF digitalizado costuma vir em Letter,
    e meio centímetro de diferença desloca todos os recortes.
    """
    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"Página {page_index} não existe no PDF ({doc.page_count} páginas).")
        rect = doc.load_page(page_index).rect
        return float(rect.width), float(rect.height)
    finally:
        doc.close()


def render_pdf_box(
    pdf_path: str,
    page_index: int,
    box: AnswerBox,
    dpi: int = DEFAULT_CROP_DPI,
    pad_frac: float = DEFAULT_PAD_FRAC,
) -> Image.Image:
    """Rasteriza **apenas** a caixa de resposta, direto do PDF, no DPI pedido.

    `get_pixmap(clip=...)` recorta em coordenadas PDF, então a página inteira
    nunca chega a existir em alta resolução — o ganho de nitidez não custa
    memória.
    """
    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"Página {page_index} não existe no PDF ({doc.page_count} páginas).")
        page = doc.load_page(page_index)
        page_height_pt = float(page.rect.height)

        pad_x = box.width_pt * pad_frac
        pad_y = box.height_pt * pad_frac
        # fitz usa origem superior esquerda, como as imagens.
        top = page_height_pt - (box.y_bottom_pt + box.height_pt) - pad_y
        clip = fitz.Rect(
            max(page.rect.x0, box.x_pt - pad_x),
            max(page.rect.y0, top),
            min(page.rect.x1, box.x_pt + box.width_pt + pad_x),
            min(page.rect.y1, top + box.height_pt + 2 * pad_y),
        )

        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def crop_box_from_image(
    page_image: Image.Image,
    box: AnswerBox,
    page_height_pt: float,
    dpi: float,
    pad_frac: float = DEFAULT_PAD_FRAC,
) -> Image.Image:
    """Recorta a caixa de uma página **já rasterizada**.

    Usado quando a origem não é PDF vetorial (scan já convertido em imagem, ou
    página que passou por alinhamento por homografia). Quando o PDF está à mão,
    `render_pdf_box` entrega mais resolução pelo mesmo custo.
    """
    left, upper, right, lower = box_to_pixels(box, page_height_pt, dpi, pad_frac=pad_frac)
    left = max(0, left)
    upper = max(0, upper)
    right = min(page_image.width, right)
    lower = min(page_image.height, lower)
    if right <= left or lower <= upper:
        raise ValueError(f"Recorte vazio para a questão {box.question_number}.")
    return page_image.crop((left, upper, right, lower))
