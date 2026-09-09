from __future__ import annotations

import logging
from pathlib import Path

import pymupdf as fitz
from PIL import Image

logger = logging.getLogger(__name__)


def render_pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 220) -> list[str]:
    """
    Converte um PDF em imagens PNG, uma por página, com nomes previsíveis.

    Usa PyMuPDF para não depender de binários externos. O DPI padrão mantém boa
    legibilidade sem criar imagens excessivamente grandes para APIs multimodais.
    """
    source = Path(pdf_path)
    if not source.is_file():
        raise FileNotFoundError(f"PDF não encontrado: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("Arquivo inválido: apenas PDF é aceito.")
    if dpi < 150 or dpi > 350:
        raise ValueError("DPI inválido. Use um valor entre 150 e 350.")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(source))
    except Exception as exc:
        raise ValueError(f"PDF inválido ou ilegível: {exc}") from exc

    try:
        if doc.page_count == 0:
            raise ValueError("PDF sem páginas.")

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        generated: list[str] = []

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            image = _resize_if_too_large(image)

            output_path = destination / f"page_{page_index + 1:03d}.png"
            image.save(output_path, format="PNG", optimize=True)
            generated.append(str(output_path))
            logger.info(
                "PDF page converted",
                extra={
                    "page": page_index + 1,
                    "output_path": str(output_path),
                    "width": image.width,
                    "height": image.height,
                    "dpi": dpi,
                },
            )

        return generated
    finally:
        doc.close()


def _resize_if_too_large(image: Image.Image, max_side: int = 3200) -> Image.Image:
    if max(image.size) <= max_side:
        return image

    ratio = max_side / float(max(image.size))
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


convert_pdf_to_page_images = render_pdf_to_images
