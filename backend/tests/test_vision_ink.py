"""Itens 5 e 6 do docs/HTR_PLANO_EXECUCAO.md.

Item 5 - pre-processamento que nao destroi o traco: correcao de iluminacao por
divisao pelo fundo, separacao de tinta por canal minimo (nao luminancia) e CLAHE
local, sem sharpen/unsharp/binarizacao.

Item 6 - detector de tinta deterministico: distinguir caixa VAZIA de leitura
falhada antes de gastar chamada de LLM.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.vision.ink import (
    InkStats,
    detect_ink,
    ink_map,
    normalize_for_reading,
)


BOX = (400, 120)
# Cinza da caixa de resposta desenhada pelo gerador (sheet_layout).
GRAY_BOX = (222, 222, 222)
BLUE_PEN = (28, 42, 120)
PENCIL = (140, 140, 140)


def _canvas(color=(255, 255, 255), size=BOX) -> Image.Image:
    return Image.new("RGB", size, color)


def _with_handwriting(background=GRAY_BOX, ink=BLUE_PEN, width=3) -> Image.Image:
    """Aproxima um traco manuscrito: curvas continuas, nao blocos solidos."""
    img = _canvas(background)
    draw = ImageDraw.Draw(img)
    for offset in (0, 34, 68):
        points = [
            (20 + i * 12, 30 + offset + int(12 * np.sin(i / 1.6)))
            for i in range(30)
        ]
        draw.line(points, fill=ink, width=width, joint="curve")
    return img


def _uneven_illumination(image: Image.Image, drop=0.45) -> Image.Image:
    """Simula foto de celular: gradiente de sombra da esquerda para a direita."""
    arr = np.asarray(image).astype(np.float32)
    ramp = np.linspace(1.0, 1.0 - drop, arr.shape[1], dtype=np.float32)
    arr *= ramp[None, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# --- Item 6: detector de tinta ------------------------------------------------


def test_blank_white_crop_has_no_ink():
    assert detect_ink(_canvas()).has_ink is False


def test_blank_gray_answer_box_has_no_ink():
    """A caixa de resposta e cinza: fundo uniforme escuro nao pode virar tinta."""
    assert detect_ink(_canvas(GRAY_BOX)).has_ink is False


def test_blue_pen_on_gray_box_is_detected():
    stats = detect_ink(_with_handwriting())

    assert stats.has_ink is True
    assert stats.ink_ratio > 0.004


def test_pencil_on_white_is_detected():
    assert detect_ink(_with_handwriting(background=(255, 255, 255), ink=PENCIL)).has_ink is True


def test_uneven_illumination_on_blank_box_is_not_ink():
    """Divisao pelo fundo tem de matar o gradiente de sombra."""
    assert detect_ink(_uneven_illumination(_canvas(GRAY_BOX))).has_ink is False


def test_uneven_illumination_still_finds_real_handwriting():
    assert detect_ink(_uneven_illumination(_with_handwriting())).has_ink is True


def test_printed_border_alone_is_not_ink():
    """A borda impressa da caixa nao pode ser confundida com resposta."""
    img = _canvas(GRAY_BOX)
    ImageDraw.Draw(img).rectangle([0, 0, BOX[0] - 1, BOX[1] - 1], outline=(90, 90, 90), width=2)

    assert detect_ink(img).has_ink is False


def test_detect_ink_returns_stats():
    stats = detect_ink(_with_handwriting())

    assert isinstance(stats, InkStats)
    assert 0.0 <= stats.ink_ratio <= 1.0
    assert stats.ink_pixels > 0
    assert stats.components >= 1
    assert stats.reason


def test_detect_ink_threshold_is_configurable():
    img = _with_handwriting()

    assert detect_ink(img, min_ink_ratio=0.9).has_ink is False


def test_detect_ink_handles_tiny_image():
    assert detect_ink(_canvas(size=(3, 3))).has_ink is False


# --- Item 5: normalizacao -----------------------------------------------------


def _ink_contrast(image: Image.Image) -> float:
    """Separacao entre tinta e fundo no mapa de tinta."""
    m = ink_map(np.asarray(image.convert("RGB")))
    return float(np.percentile(m, 99.5) - np.percentile(m, 50))


def test_normalize_preserves_rgb_and_size():
    out = normalize_for_reading(_with_handwriting())

    assert out.mode == "RGB"
    assert out.size == BOX


def test_normalize_flattens_uneven_illumination():
    shaded = _uneven_illumination(_canvas(GRAY_BOX))
    before = np.asarray(shaded.convert("L")).astype(np.float32)
    after = np.asarray(normalize_for_reading(shaded).convert("L")).astype(np.float32)

    assert after.std() < before.std()


def test_normalize_does_not_destroy_blue_ink_on_gray_box():
    """O grayscale antigo apagava caneta azul sobre cinza; a nova rota nao pode."""
    img = _with_handwriting()

    assert _ink_contrast(normalize_for_reading(img)) >= _ink_contrast(img) * 0.9


def test_ink_map_uses_minimum_channel_not_luminance():
    """Azul saturado tem canal R baixo; o cinza do fundo nao derruba canal nenhum.

    Sem `flatten` de proposito: aqui se mede so a escolha de canal. Com blocos
    solidos deste tamanho o estimador de fundo trataria o azul como papel -- o que
    esta certo para um bloco, mas nao e o que este teste avalia.
    """
    gray_bg = np.full((8, 8, 3), GRAY_BOX, dtype=np.uint8)
    blue = np.full((8, 8, 3), BLUE_PEN, dtype=np.uint8)
    frame = np.concatenate([gray_bg, blue], axis=1)

    m = ink_map(frame, flatten=False)

    assert m[:, 8:].mean() > m[:, :8].mean() + 0.3


def test_ink_map_separates_thin_blue_stroke_on_gray_box_after_flattening():
    """O caso que importa de verdade: traco fino de caneta azul sobre a caixa cinza."""
    m = ink_map(np.asarray(_with_handwriting()))

    assert m.max() > 0.5
    assert float(np.median(m)) < 0.1


def test_normalize_applies_no_sharpening_halo():
    """Sharpen/unsharp criam halo claro em volta do traco e quebram ligaduras."""
    out = np.asarray(normalize_for_reading(_with_handwriting()).convert("L")).astype(np.int16)

    assert out.max() <= 255
    # Sem halo: nenhum pixel fica mais claro que o fundo normalizado (~255).
    assert np.count_nonzero(out > 254) < out.size


@pytest.mark.parametrize("size", [(1, 1), (5, 200), (200, 5)])
def test_normalize_handles_degenerate_sizes(size):
    assert normalize_for_reading(_canvas(size=size)).size == size


def test_clearly_blank_box_is_not_marginal():
    """Caixa vazia de verdade: zerar sem revisao humana e seguro."""
    stats = detect_ink(_canvas(GRAY_BOX))

    assert stats.has_ink is False
    assert stats.is_marginal is False


def test_faint_trace_falls_in_the_marginal_band():
    """Pouca tinta nao autoriza zerar em silencio: manda para revisao."""
    img = _canvas(GRAY_BOX)
    ImageDraw.Draw(img).line([(30, 60), (95, 60)], fill=PENCIL, width=2)

    stats = detect_ink(img)

    assert stats.has_ink is False
    assert stats.is_marginal is True
    assert "marginal" in stats.reason


def test_real_handwriting_is_never_marginal():
    assert detect_ink(_with_handwriting()).is_marginal is False


def _lined_answer_crop(with_handwriting: bool = False) -> Image.Image:
    img = _canvas((255, 255, 255), size=(480, 220))
    draw = ImageDraw.Draw(img)
    for y in (36, 64, 92, 120, 148, 176):
        draw.line([(16, y), (464, y)], fill=(50, 50, 50), width=2)
    if with_handwriting:
        for offset in (0, 40, 80):
            points = [(28 + i * 14, 50 + offset + int(10 * np.sin(i / 1.8))) for i in range(28)]
            draw.line(points, fill=BLUE_PEN, width=3, joint="curve")
    return img


def test_blank_white_crop_has_no_ink_with_guide_suppression():
    assert detect_ink(_canvas(), suppress_horizontal_guides=True).has_ink is False


def test_printed_horizontal_guides_are_not_ink_when_suppressed():
    lined = _lined_answer_crop()
    assert detect_ink(lined, suppress_horizontal_guides=True).has_ink is False


def test_handwriting_on_printed_guides_is_still_ink_when_suppressed():
    assert detect_ink(_lined_answer_crop(with_handwriting=True), suppress_horizontal_guides=True).has_ink is True


def test_detect_ink_without_suppress_keeps_current_behavior():
    blank = detect_ink(_canvas(GRAY_BOX))
    handwriting = detect_ink(_with_handwriting())
    assert blank.has_ink is False
    assert handwriting.has_ink is True
    assert detect_ink(_canvas(GRAY_BOX), suppress_horizontal_guides=False).has_ink is False
    assert detect_ink(_with_handwriting(), suppress_horizontal_guides=False).has_ink is True
