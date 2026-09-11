"""Separacao de tinta, normalizacao de imagem e deteccao de caixa vazia.

Primitiva compartilhada pelos dois pipelines de leitura (visual e Celery).
Ver docs/HTR_PLANO_EXECUCAO.md, itens 5 e 6.

Duas ideias sustentam este modulo:

1. **Tinta se separa por canal minimo, nao por luminancia.** A folha-resposta tem
   caixa CINZA. Caneta azul sobre cinza tem contraste cromatico alto e de
   luminancia baixo: converter para escala de cinza joga fora exatamente o canal
   que separava tinta de fundo. `min(R, G, B)` sobrevive a isso -- qualquer tinta
   saturada derruba pelo menos um canal, enquanto um cinza neutro nao derruba
   nenhum.

2. **Iluminacao se corrige dividindo pelo fundo, nao com autocontraste global.**
   Foto de celular chega com sombra de um lado. Autocontraste global satura um
   canto e estoura o outro; a divisao pelo fundo estimado (mediana de kernel
   grande) achata o gradiente antes de qualquer decisao.

Nao ha sharpen, unsharp mask nem binarizacao aqui de proposito: sao eles que
criam halo e quebram as ligaduras finas que definem a letra cursiva.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


# Fracao da menor dimensao usada como kernel da mediana que estima o fundo.
# Precisa ser bem maior que a espessura do traco para que a mediana "veja" papel.
_BACKGROUND_KERNEL_FRACTION = 0.08
_BACKGROUND_KERNEL_MIN = 11
_BACKGROUND_KERNEL_MAX = 81

# Acima disto no mapa de tinta [0,1] o pixel conta como traco.
DEFAULT_INK_THRESHOLD = 0.32
# Razao minima de pixels de tinta para a caixa deixar de ser considerada vazia.
DEFAULT_MIN_INK_RATIO = 0.004
# Componentes menores que isto (fracao da area) sao poeira/ruido de scanner.
_MIN_COMPONENT_AREA_FRACTION = 0.00015
# Faixa cinza abaixo do limiar: densidade baixa demais para afirmar que ha
# resposta, alta demais para afirmar que a caixa esta vazia. Zerar em silencio
# ai seria apostar contra o aluno -- essa faixa vai para revisao humana.
_MARGINAL_FACTOR = 3.0
# Recorte de borda antes de medir: a moldura impressa da caixa nao e resposta.
DEFAULT_BORDER_INSET = 0.05

DEFAULT_CLAHE_CLIP = 2.0
DEFAULT_CLAHE_TILE = 8


@dataclass(frozen=True)
class InkStats:
    """Resultado do detector de tinta deterministico (item 6)."""

    has_ink: bool
    ink_ratio: float
    ink_pixels: int
    components: int
    reason: str
    is_marginal: bool = False
    """Densidade na faixa cinza: tratar como vazio, mas mandar para revisao."""


def _odd(value: int) -> int:
    return value if value % 2 else value + 1


def _background_kernel(height: int, width: int) -> int:
    smallest = max(1, min(height, width))
    kernel = int(smallest * _BACKGROUND_KERNEL_FRACTION)
    kernel = max(_BACKGROUND_KERNEL_MIN, min(_BACKGROUND_KERNEL_MAX, kernel))
    kernel = min(kernel, smallest if smallest % 2 else smallest - 1)
    return max(3, _odd(kernel))


def flatten_illumination(rgb: np.ndarray) -> np.ndarray:
    """Divide a imagem pelo fundo estimado, achatando sombra e iluminacao desigual.

    O fundo e estimado por mediana de kernel grande, que ignora o traco fino e
    devolve o papel. Dividir por ele leva o papel para branco uniforme sem tocar
    na razao tinta/papel.
    """
    height, width = rgb.shape[:2]
    if height < 3 or width < 3:
        return rgb.astype(np.float32)

    kernel = _background_kernel(height, width)
    work = rgb.astype(np.uint8)
    background = cv2.medianBlur(work, kernel).astype(np.float32)
    # Evita divisao por zero em regioes genuinamente pretas.
    background = np.maximum(background, 1.0)
    flattened = work.astype(np.float32) / background * 255.0
    return np.clip(flattened, 0.0, 255.0)


def ink_map(rgb: np.ndarray, *, flatten: bool = True) -> np.ndarray:
    """Mapa de tinta em [0, 1] -- 1 e traco, 0 e papel.

    Usa o canal MINIMO, nao a luminancia: e o que preserva caneta azul sobre a
    caixa cinza da folha-resposta.
    """
    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)
    if rgb.shape[2] == 4:
        rgb = rgb[:, :, :3]

    flattened = flatten_illumination(rgb) if flatten else rgb.astype(np.float32)
    darkest = flattened.min(axis=2)
    return np.clip(1.0 - darkest / 255.0, 0.0, 1.0).astype(np.float32)


def normalize_for_reading(
    image: Image.Image,
    *,
    clahe_clip: float = DEFAULT_CLAHE_CLIP,
    clahe_tile: int = DEFAULT_CLAHE_TILE,
    upscale_below_px: int = 0,
) -> Image.Image:
    """Prepara a imagem para o modelo de visao sem destruir o traco (item 5).

    EXIF -> divisao pelo fundo -> CLAHE local na luminancia -> RGB.
    Sem sharpen, sem unsharp mask, sem binarizacao.

    `upscale_below_px` faz upscale 2x Lanczos quando a menor dimensao fica abaixo
    do valor dado -- util para crops pequenos, onde a altura-de-x da cursiva cai
    abaixo do que qualquer leitor consegue resolver. 0 desliga.
    """
    image = ImageOps.exif_transpose(image).convert("RGB")
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]

    if height < 3 or width < 3:
        return image

    flattened = flatten_illumination(rgb).astype(np.uint8)

    # CLAHE so na luminancia: o contraste local sobe sem girar as cores, e a
    # informacao cromatica que separa tinta azul do cinza continua intacta.
    lab = cv2.cvtColor(flattened, cv2.COLOR_RGB2LAB)
    tile = max(1, min(clahe_tile, height, width))
    clahe = cv2.createCLAHE(clipLimit=float(clahe_clip), tileGridSize=(tile, tile))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    result = Image.fromarray(out, mode="RGB")
    if upscale_below_px and min(height, width) < upscale_below_px:
        result = result.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
        logger.debug("Crop pequeno (%dx%d) upscaled 2x para leitura.", width, height)
    return result


def _inset(rgb: np.ndarray, border_inset: float) -> np.ndarray:
    height, width = rgb.shape[:2]
    dy = int(height * border_inset)
    dx = int(width * border_inset)
    if height - 2 * dy < 3 or width - 2 * dx < 3:
        return rgb
    return rgb[dy : height - dy, dx : width - dx]


def _suppress_horizontal_guides(mask: np.ndarray) -> np.ndarray:
    """Remove linhas-guia impressas longas e finas, preservando o manuscrito."""
    height, width = mask.shape[:2]
    if height < 8 or width < 24:
        return mask

    kernel_width = max(15, width // 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    guides = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    if cv2.countNonZero(guides) == 0:
        return mask

    count, labels, stats, _ = cv2.connectedComponentsWithStats(guides, connectivity=8)
    guide_mask = np.zeros_like(mask)
    max_guide_height = max(4, int(height * 0.06))
    min_guide_width = int(width * 0.35)
    for label in range(1, count):
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if component_width >= min_guide_width and component_height <= max_guide_height:
            guide_mask[labels == label] = 1
    if cv2.countNonZero(guide_mask) == 0:
        return mask
    return (mask & (~guide_mask)).astype(np.uint8)


def detect_ink(
    image: Image.Image | np.ndarray,
    *,
    ink_threshold: float = DEFAULT_INK_THRESHOLD,
    min_ink_ratio: float = DEFAULT_MIN_INK_RATIO,
    border_inset: float = DEFAULT_BORDER_INSET,
    suppress_horizontal_guides: bool = False,
) -> InkStats:
    """Diz se ha traco manuscrito no recorte, sem chamar modelo nenhum (item 6).

    Serve para separar **caixa vazia** de **leitura falhada** -- distincao que a
    autoavaliacao do VLM nao faz (ele reporta confianca "alta" ao alucinar texto
    em caixa vazia). Caixa vazia sai daqui sem custo de LLM.
    """
    if isinstance(image, Image.Image):
        rgb = np.asarray(image.convert("RGB"))
    else:
        rgb = np.asarray(image)

    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)
    if rgb.ndim != 3 or rgb.shape[0] < 3 or rgb.shape[1] < 3:
        return InkStats(False, 0.0, 0, 0, "recorte pequeno demais para avaliar")

    region = _inset(rgb, border_inset)
    mask = (ink_map(region) >= ink_threshold).astype(np.uint8)
    if suppress_horizontal_guides:
        mask = _suppress_horizontal_guides(mask)

    total = int(mask.size)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area = max(2, int(total * _MIN_COMPONENT_AREA_FRACTION))

    ink_pixels = 0
    components = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        ink_pixels += area
        components += 1

    ratio = ink_pixels / total if total else 0.0
    if ratio < min_ink_ratio:
        marginal = ratio >= min_ink_ratio / _MARGINAL_FACTOR
        reason = f"densidade de tinta {ratio:.4f} abaixo do limiar {min_ink_ratio:.4f}"
        if marginal:
            reason += " (faixa marginal: pode ser resposta curta ou lápis fraco)"
        return InkStats(
            has_ink=False,
            ink_ratio=ratio,
            ink_pixels=ink_pixels,
            components=components,
            reason=reason,
            is_marginal=marginal,
        )

    return InkStats(
        has_ink=True,
        ink_ratio=ratio,
        ink_pixels=ink_pixels,
        components=components,
        reason=f"densidade de tinta {ratio:.4f} em {components} componente(s)",
        is_marginal=False,
    )
