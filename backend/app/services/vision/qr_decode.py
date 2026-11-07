"""Decodificação de QR Code nas páginas escaneadas (OpenCV).

O QR carrega `MQPC|exam_id|student_id|page|total_pages` e é a única fonte de
identidade **conferível** da página. Quando ele falha, o pipeline cai para regex
sobre o nome que o modelo leu do cabeçalho — adivinhação, havendo um QR impresso
ali mesmo. Por isso vale insistir na leitura.

Medição que motivou o código abaixo: com uma única tentativa de
`detectAndDecodeMulti` sobre a página inteira, **22% das folhas** ficavam com ao
menos um QR ilegível a 200 DPI, que é justamente o DPI do worker. O detector do
OpenCV é sensível à escala: o mesmo QR que falha na página inteira decodifica
sem esforço num recorte ampliado.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

PREFIX = "MQPC"

# O QR fica no cabeçalho da folha (ver sheet_layout). Recortar essa faixa e
# ampliá-la é o que mais aumenta a taxa de leitura.
_HEADER_FRACTION = 0.30
# Lado mínimo, em pixels, da variante ampliada entregue ao detector.
_MIN_DETECTION_SIDE = 1800
_MAX_DETECTION_SIDE = 4000


@dataclass
class PageQrPayload:
    exam_id: str
    student_id: str
    page_in_student: int
    total_pages_for_student: int


def decode_sheet_qr_from_pdf(pdf_path: str, page_index: int, dpi: int = 300) -> PageQrPayload | None:
    """Segunda leitura do QR, rasterizando a página direto do PDF.

    A imagem de trabalho do pipeline fica em 220 DPI. Num scan ruim esse
    tamanho perde o QR, e ampliar a imagem já degradada não o recupera.
    Renderizar de novo o PDF a 300 DPI usa os pixels da origem. Nesta medição,
    220 DPI não lê o QR degradado e 320 DPI ou mais também deixa de ler.
    """
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        pixmap = page.get_pixmap(dpi=dpi)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    finally:
        doc.close()
    return decode_sheet_qr(image)


def decode_sheet_qr(image: Image.Image) -> PageQrPayload | None:
    """
    Lê o QR no formato `MQPC|<exam_id>|<student_id>|<page>|<total_pages>`.
    Retorna None se não encontrar ou se o formato for inválido.
    """
    try:
        import cv2  # noqa: PLC0415 — opcional no ambiente de teste sem opencv
    except ImportError:
        logger.warning("opencv não instalado; QR não será decodificado.")
        return None

    detector = cv2.QRCodeDetector()
    for attempt, variant in enumerate(_detection_variants(image, cv2), start=1):
        for raw in _decode_candidates(detector, variant, cv2):
            payload = _parse_payload(raw)
            if payload:
                if attempt > 1:
                    logger.info("QR decodificado na variante %d da página.", attempt)
                return payload

    logger.info("Nenhum QR válido encontrado na página após %d variantes.", attempt)
    return None


def _detection_variants(image: Image.Image, cv2) -> Iterator[np.ndarray]:
    """Variantes da página, da mais barata para a mais insistente.

    A ordem importa: a maioria das páginas resolve na primeira e nunca paga o
    custo das outras.
    """
    rgb = np.array(image.convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()

    # 1. Página como veio.
    yield bgr

    # 2. Só o cabeçalho, ampliado — onde o QR de fato está.
    header = bgr[: max(1, int(bgr.shape[0] * _HEADER_FRACTION)), :]
    upscaled_header = _rescale(header, cv2)
    if upscaled_header is not None:
        yield upscaled_header

    # 3. Página inteira reescalada, para o caso de o layout ter mudado e o QR
    #    não estar mais no topo.
    upscaled_page = _rescale(bgr, cv2)
    if upscaled_page is not None:
        yield upscaled_page

    # 4. Binarização adaptativa do cabeçalho: recupera QR lavado por scanner
    #    claro ou impressão fraca, onde o contraste local é o que falta.
    source = upscaled_header if upscaled_header is not None else header
    gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    yield cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def _rescale(bgr: np.ndarray, cv2) -> np.ndarray | None:
    """Leva o menor lado para perto de `_MIN_DETECTION_SIDE`, sem estourar o maior."""
    height, width = bgr.shape[:2]
    smallest = min(height, width)
    if smallest <= 0:
        return None

    factor = _MIN_DETECTION_SIDE / float(smallest)
    factor = min(factor, _MAX_DETECTION_SIDE / float(max(height, width)))
    if factor <= 1.01:
        return None

    return cv2.resize(
        bgr,
        (max(1, int(width * factor)), max(1, int(height * factor))),
        interpolation=cv2.INTER_CUBIC,
    )


def _decode_candidates(detector, bgr: np.ndarray, cv2) -> Iterator[str]:
    """Tenta o detector múltiplo e, se ele não achar nada, o de QR único.

    Os dois usam caminhos diferentes internamente e falham em situações
    diferentes; rodar ambos custa pouco e cobre mais casos.
    """
    try:
        ok, decoded, _, _ = detector.detectAndDecodeMulti(bgr)
        if ok and decoded:
            for raw in decoded:
                if raw:
                    yield raw
    except cv2.error as exc:
        logger.debug("detectAndDecodeMulti falhou: %s", exc)

    try:
        raw, _, _ = detector.detectAndDecode(bgr)
        if raw:
            yield raw
    except cv2.error as exc:
        logger.debug("detectAndDecode falhou: %s", exc)


def _parse_payload(raw: str) -> PageQrPayload | None:
    parts = raw.strip().split("|")
    if len(parts) != 5 or parts[0] != PREFIX:
        return None
    try:
        _, exam_id, student_id, page_s, total_s = parts
        return PageQrPayload(
            exam_id=exam_id.strip(),
            student_id=student_id.strip(),
            page_in_student=int(page_s),
            total_pages_for_student=int(total_s),
        )
    except (ValueError, IndexError):
        return None


def format_qr_payload(
    exam_id: str,
    student_id: str,
    page_in_student: int,
    total_pages_for_student: int,
) -> str:
    return f"{PREFIX}|{exam_id}|{student_id}|{page_in_student}|{total_pages_for_student}"
