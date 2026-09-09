"""Comparação determinística entre a resposta prática do aluno e o gabarito.

Prova prática pede um nome anatômico curto, escrito com as abreviações de sala
de aula ("M. Sóleo E.", "O. Calcâneo D."). A comparação acontece em três eixos
independentes, e não por semelhança da frase inteira:

- classe estrutural (osso, músculo, artéria, veia, nervo, ...);
- lateralidade (direito/esquerdo);
- núcleo, o que sobra depois de tirar as duas anteriores e as palavras vazias.

Comparar a frase inteira confunde "artéria femoral" com "nervo femoral" e
reprova "osso calcâneo do pé D." contra "O. Calcâneo D.". Separar os eixos
resolve os dois casos: o aluno pode acrescentar contexto à vontade, mas não
pode trocar a estrutura nem o lado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

CORRECT = "correta"
PENDING = "revisao_pendente"
WRONG = "incorreta"

# Palavras que só ligam a frase e nunca identificam a estrutura.
STOPWORDS = frozenset(
    {"o", "a", "os", "as", "do", "da", "de", "dos", "das", "no", "na", "em", "ao", "aos", "e"}
)

# Forma canônica de cada classe estrutural, já sem acento e em minúsculas.
STRUCTURE_CLASSES = {
    "osso": "osso",
    "ossos": "osso",
    "musculo": "musculo",
    "musculos": "musculo",
    "arteria": "arteria",
    "arterias": "arteria",
    "veia": "veia",
    "veias": "veia",
    "nervo": "nervo",
    "nervos": "nervo",
    "ligamento": "ligamento",
    "ligamentos": "ligamento",
    "tendao": "tendao",
    "tendoes": "tendao",
}

LATERALITY_WORDS = frozenset({"esquerdo", "esquerda", "direito", "direita"})

# Qualificadores anatômicos críticos, agrupados por eixo. Dentro de um grupo os
# termos são mutuamente exclusivos: "papilar anterior" e "papilar posterior" são
# músculos diferentes, não duas leituras do mesmo. Trocar um pelo outro é erro —
# mandar isso para revisão humana gasta o professor com uma decisão que a regra
# já sabe tomar.
#
# Direito/esquerdo também são críticos, mas moram no eixo de lateralidade, que já
# tem tratamento e razão próprios (`_laterality_compatible`).
QUALIFIER_GROUPS = {
    # Posição no eixo ântero-posterior. "média" entra aqui porque a série
    # anterior/média/posterior é a que nomeia cerebrais e meníngeas; "septal"
    # porque é a terceira posição dos músculos papilares.
    "anterior": "antero_posterior",
    "posterior": "antero_posterior",
    "media": "antero_posterior",
    "medio": "antero_posterior",
    "septal": "antero_posterior",
    "medial": "medial_lateral",
    "lateral": "medial_lateral",
    "superior": "superior_inferior",
    "inferior": "superior_inferior",
}
CRITICAL_QUALIFIERS = frozenset(QUALIFIER_GROUPS)

# Abreviações de qualificador. Só valem como token inteiro: "ant" e "post" não
# aparecem sozinhos no nome de estrutura nenhuma.
_QUALIFIER_ABBREVIATIONS = {"ant": "anterior", "post": "posterior"}

# Câmaras cardíacas: o gabarito escreve "VE"/"VD", o aluno escreve por extenso.
_CHAMBER_ABBREVIATIONS = {"ve": "ventriculo esquerdo", "vd": "ventriculo direito"}

# Siglas de uma letra só expandem quando vêm com ponto: "a." é artéria, "a"
# sozinho é artigo. Sem essa distinção, "a cabeça longa do bíceps" viraria uma
# artéria e conflitaria com o gabarito muscular.
_DOTTED_ABBREVIATIONS = {
    "m": "musculo",
    "a": "arteria",
    "v": "veia",
    "n": "nervo",
    "l": "ligamento",
    "t": "tendao",
    "o": "osso",
}

# Siglas de duas ou mais letras não são ambíguas e dispensam o ponto.
_PLAIN_ABBREVIATIONS = {
    "mm": "musculos",
    "musc": "musculo",
    "aa": "arterias",
    "vv": "veias",
    "nn": "nervos",
    "ll": "ligamentos",
    "tt": "tendoes",
    "oss": "ossos",
    "os": "osso",
}

# Sinônimos anatômicos explícitos: nomes diferentes para a MESMA estrutura.
# Cada entrada canoniza os dois lados da comparação para a mesma forma, então o
# alias vale nos dois sentidos (gabarito e resposta) sem abrir tolerância
# genérica nenhuma: só a grafia listada aqui passa, e "média" continua diferente
# de "magna".
_ANATOMIC_SYNONYMS = (
    # A veia cardíaca média é a veia interventricular posterior.
    (r"\bveia cardiaca media\b", "veia interventricular posterior"),
    # Frontobasilar, fronto-basilar e frontobasal nomeiam a mesma artéria. O
    # hífen já virou espaço na limpeza de pontuação, daí o \s* no meio. O
    # prefixo "fronto" é obrigatório: "basilar" sozinho é outra artéria.
    (r"\bfronto\s*bas(?:ilar|al)\b", "frontobasilar"),
)

# Estruturas medianas, em que acrescentar lado é anatomicamente impossível.
# Lista curta e explícita, comparada contra a chave "classe + núcleo +
# qualificadores" do gabarito — nunca uma regra genérica para toda estrutura que
# o gabarito escreveu sem lado.
NON_LATERALIZED_STRUCTURES = frozenset({"arteria basilar"})

_COMPACT_PREFIXES = sorted(
    [*_PLAIN_ABBREVIATIONS, *_DOTTED_ABBREVIATIONS], key=len, reverse=True
)


@dataclass
class MatchResult:
    status: str
    reason: str
    similarity: float = 0.0
    normalized_answer: str = ""
    expected_used: str = ""
    matched_core: list[str] = field(default_factory=list)
    missing_core: list[str] = field(default_factory=list)


def strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", str(value or ""))
        if unicodedata.category(char) != "Mn"
    )


def separate_compact_anatomy_abbreviations(text: str) -> str:
    """Aceita escrita compacta comum, como M.Soleo, A.Braquial ou O.Calcaneo."""
    pattern = r"\b(" + "|".join(_COMPACT_PREFIXES) + r")\.(?=[a-z])"
    return re.sub(pattern, r"\1. ", str(text or ""))


def expand_anatomy_abbreviations(text: str) -> str:
    out = f" {separate_compact_anatomy_abbreviations(strip_accents(text).lower())} "
    for short, full in _PLAIN_ABBREVIATIONS.items():
        out = re.sub(rf"\b{short}\.?(?=\s|$)", f" {full} ", out)
    for short, full in _DOTTED_ABBREVIATIONS.items():
        out = re.sub(rf"\b{short}\.(?=\s|$)", f" {full} ", out)
    return re.sub(r"\s+", " ", out).strip()


def normalize_practical_answer(value: str) -> str:
    text = strip_accents(value).lower()
    text = separate_compact_anatomy_abbreviations(text)
    text = expand_anatomy_abbreviations(text)
    text = re.sub(r"\besq(?:\.|uerda|uerdo)?\b", " esquerdo ", text)
    text = re.sub(r"\bdir(?:\.|eita|eito)?\b", " direito ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _expand_qualifier_and_chamber_abbreviations(text)
    text = " ".join(_singularize(token) for token in text.split())
    text = _normalize_trailing_laterality_token(text)
    text = _canonicalize_practical_aliases(text)
    return text


def _expand_qualifier_and_chamber_abbreviations(text: str) -> str:
    """"Ant."/"Post." e "VE"/"VD" para a forma escrita por extenso.

    Roda depois da limpeza de pontuação (o ponto já caiu) e antes do marcador
    final de lateralidade, senão "VD" seria lido como sigla desconhecida em vez
    de "ventrículo direito".
    """
    tokens = []
    for token in text.split():
        expanded = _QUALIFIER_ABBREVIATIONS.get(token) or _CHAMBER_ABBREVIATIONS.get(token)
        tokens.append(expanded or token)
    return " ".join(tokens)


def _singularize(token: str) -> str:
    """Plural anatômico comum para o singular.

    O gabarito diz "Músculos papilares" e o aluno diz "músculo papilar" — mesma
    estrutura. Aplicada aos dois lados, a regra é simétrica: mesmo quando erra
    (como em "bíceps"), erra igual dos dois lados e a comparação não se perde.
    """
    if len(token) < 4 or not token.endswith("s") or token.endswith(("ss", "ps")):
        return token
    if token.endswith("oes"):
        return token[:-3] + "ao"
    if token[-3:] in {"res", "les", "nes", "zes", "ses"}:
        return token[:-2]
    return token[:-1]


def expected_answer_variants(expected: str) -> list[str]:
    """Formas aceitas do gabarito, incluindo o que está entre parênteses.

    "Artéria interventricular posterior (PDA)" aceita três leituras: a frase sem
    a sigla, a sigla sozinha, e a frase inteira. Tratar "(PDA)" como palavra
    obrigatória reprova quem escreveu o nome completo — que é o nome certo.
    """
    expected = str(expected or "")
    aliases = re.findall(r"\(([^)]*)\)", expected)
    without_aliases = re.sub(r"\([^)]*\)", " ", expected)

    parts = re.split(r"\s*(?:;|\||/|\n|, ou | ou )\s*", without_aliases)
    variants = [normalize_practical_answer(part) for part in parts if str(part).strip()]
    for alias in aliases:
        normalized_alias = normalize_practical_answer(alias)
        if normalized_alias and normalized_alias not in variants:
            variants.append(normalized_alias)

    normalized_full = normalize_practical_answer(expected)
    if normalized_full and normalized_full not in variants:
        variants.append(normalized_full)
    return [item for item in variants if item]


def practical_similarity(answer_norm: str, expected_norm: str) -> float:
    """Mantida apenas como número informativo no JSON de depuração."""
    if not answer_norm or not expected_norm:
        return 0.0
    ratio = SequenceMatcher(None, answer_norm, expected_norm).ratio()
    answer_tokens = set(answer_norm.split())
    expected_tokens = set(expected_norm.split())
    overlap = len(answer_tokens & expected_tokens) / max(1, len(expected_tokens))
    return (0.65 * ratio) + (0.35 * overlap)


def match_answer(answer_raw: str, expected_raw: str) -> MatchResult:
    """Compara a resposta do aluno com cada variante aceita do gabarito."""
    answer_norm = normalize_practical_answer(answer_raw)
    variants = expected_answer_variants(expected_raw)
    if not answer_norm or not variants:
        return MatchResult(status=WRONG, reason="nao_confere", normalized_answer=answer_norm)

    results = [_match_variant(answer_norm, variant) for variant in variants]
    best = max(results, key=_result_rank)
    best.normalized_answer = answer_norm
    return best


def _match_variant(answer_norm: str, expected_norm: str) -> MatchResult:
    answer_class, answer_core, answer_qualifiers = _split_axes(answer_norm)
    expected_class, expected_core, expected_qualifiers = _split_axes(expected_norm)
    similarity = practical_similarity(answer_norm, expected_norm)

    matched: list[str] = []
    missing: list[str] = []
    approximate = False
    for token in expected_core:
        kind = _find_token(token, answer_core)
        if kind is None:
            missing.append(token)
            continue
        matched.append(token)
        approximate = approximate or kind == "aproximado"

    result = MatchResult(
        status=WRONG,
        reason="nao_confere",
        similarity=similarity,
        expected_used=expected_norm,
        matched_core=matched,
        missing_core=missing,
    )

    # Sem núcleo (gabarito só com estrutura e lado), os dois eixos restantes decidem.
    core_hit = bool(matched) or not expected_core

    if core_hit and not _laterality_compatible(answer_norm, expected_norm):
        result.reason = "lateralidade"
        return result
    if core_hit and expected_class and answer_class and answer_class != expected_class:
        result.reason = "estrutura"
        return result

    # Qualificador contraditório decide antes do núcleo parcial: "papilar
    # anterior" e "papilar posterior" compartilham todo o resto do nome, e é
    # justamente o que NÃO compartilham que separa as duas estruturas.
    conflict = _conflicting_qualifiers(expected_qualifiers, answer_qualifiers)
    if core_hit and conflict:
        result.reason = "qualificador"
        result.missing_core = [conflict[0]]
        return result

    if missing and matched:
        result.status = PENDING
        result.reason = "nucleo_parcial"
        return result
    if missing:
        return result

    missing_qualifiers = [item for item in expected_qualifiers if item not in answer_qualifiers]
    if missing_qualifiers:
        # Sem contradição, mas o aluno não disse a posição que o gabarito pede:
        # é omissão, e omissão o professor decide.
        result.status = PENDING
        result.reason = "nucleo_parcial"
        result.missing_core = missing_qualifiers
        return result

    if _side_dropped_for_foreign_context(answer_norm, expected_norm, answer_core, expected_core):
        result.status = PENDING
        result.reason = "contexto_extra"
        return result

    if _side_added_to_median_structure(
        answer_norm, expected_norm, expected_class, expected_core, expected_qualifiers
    ):
        result.status = PENDING
        result.reason = "lateralidade_indevida"
        return result

    if approximate:
        result.status = PENDING
        result.reason = "leitura_aproximada"
        return result

    result.status = CORRECT
    result.reason = "confere"
    return result


def _side_dropped_for_foreign_context(
    answer_norm: str,
    expected_norm: str,
    answer_core: list[str],
    expected_core: list[str],
) -> bool:
    """O aluno calou o lado que o gabarito pede e pôs outro contexto no lugar.

    Omitir a lateralidade sozinha é tolerado — o aluno viu a peça, o lado estava
    na mesa. Mas omitir o lado E acrescentar estrutura que o gabarito não cita
    ("cordas tendíneas anteriores **da valva semilunar pulmonar**") é outra
    coisa: pode ser contexto inofensivo ou pode ser região errada, e a regra não
    tem como saber qual. Aí a resposta é do professor, não da máquina.
    """
    if not extract_laterality(expected_norm) or extract_laterality(answer_norm):
        return False
    return any(_find_token(token, expected_core) is None for token in answer_core)


def _side_added_to_median_structure(
    answer_norm: str,
    expected_norm: str,
    expected_class: str,
    expected_core: list[str],
    expected_qualifiers: list[str],
) -> bool:
    """O aluno lateralizou uma estrutura mediana da lista de não lateralizadas.

    A artéria basilar é única e mediana: "artéria basilar direita" não existe. O
    núcleo está certo, então zerar é duro demais; dar nota cheia é aceitar uma
    afirmação anatomicamente impossível. A decisão fica com o professor.

    Vale só para `NON_LATERALIZED_STRUCTURES`: em todas as outras estruturas,
    gabarito sem lado continua aceitando resposta com lado.
    """
    if extract_laterality(expected_norm) or not extract_laterality(answer_norm):
        return False
    key = " ".join([expected_class, *expected_core, *expected_qualifiers]).strip()
    return key in NON_LATERALIZED_STRUCTURES


_RANK = {WRONG: 0, PENDING: 1, CORRECT: 2}
# Entre respostas erradas, a razão específica explica melhor do que "não confere".
_REASON_RANK = {"nao_confere": 0, "estrutura": 1, "lateralidade": 2, "qualificador": 3}


def _result_rank(result: MatchResult) -> tuple[int, int, float]:
    return (
        _RANK[result.status],
        _REASON_RANK.get(result.reason, 0),
        result.similarity,
    )


def _split_axes(normalized: str) -> tuple[str, list[str], list[str]]:
    """Separa classe estrutural, núcleo e qualificadores, descartando lado e vazias.

    O qualificador sai do núcleo porque ele não identifica a estrutura: bater
    "média" em "artéria cerebral média" e "comissura cerebelar média" não é
    acerto parcial nenhum — é coincidência de posição entre estruturas
    diferentes. Separado, ele vira um eixo com regra própria.
    """
    structure = ""
    core: list[str] = []
    qualifiers: list[str] = []
    for token in normalized.split():
        if token in STRUCTURE_CLASSES:
            structure = structure or STRUCTURE_CLASSES[token]
            continue
        if token in LATERALITY_WORDS or token in STOPWORDS:
            continue
        if token in CRITICAL_QUALIFIERS:
            if token not in qualifiers:
                qualifiers.append(token)
            continue
        core.append(token)
    return structure, core, qualifiers


def _conflicting_qualifiers(expected: list[str], answer: list[str]) -> tuple[str, str] | None:
    """Devolve o par contraditório, se houver, dentro de um mesmo eixo."""
    answer_by_group = {QUALIFIER_GROUPS[token]: token for token in answer}
    for token in expected:
        rival = answer_by_group.get(QUALIFIER_GROUPS[token])
        if rival is not None and rival != token:
            return (token, rival)
    return None


def _find_token(expected_token: str, answer_core: list[str]) -> str | None:
    if expected_token in answer_core:
        return "exato"
    for candidate in answer_core:
        if _is_typo_of(expected_token, candidate):
            return "aproximado"
    return None


def _is_typo_of(first: str, second: str) -> bool:
    """Tolera ruído de OCR sem aproximar termos anatômicos distintos.

    Uma letra de diferença em palavra longa é quase sempre leitura ruim
    ("bucinafor" por "bucinador"). Duas letras só valem em palavras bem longas,
    o que mantém pares como anterior/posterior e radial/medial separados.
    """
    shortest = min(len(first), len(second))
    if shortest < 6:
        return False
    distance = _edit_distance(first, second)
    return distance <= (2 if shortest >= 10 else 1)


def _edit_distance(first: str, second: str) -> int:
    if abs(len(first) - len(second)) > 2:
        return 3
    previous = list(range(len(second) + 1))
    for i, char_a in enumerate(first, start=1):
        current = [i]
        for j, char_b in enumerate(second, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def _laterality_compatible(answer: str, expected: str) -> bool:
    expected_lat = extract_laterality(expected)
    if not expected_lat:
        return True
    answer_lat = extract_laterality(answer)
    if not answer_lat:
        return True
    return answer_lat == expected_lat


def extract_laterality(value: str) -> str:
    text = normalize_practical_answer(value)
    has_left = " esquerdo" in f" {text}" or " esquerda" in f" {text}"
    has_right = " direito" in f" {text}" or " direita" in f" {text}"
    if has_left and not has_right:
        return "left"
    if has_right and not has_left:
        return "right"
    return ""


def _normalize_trailing_laterality_token(text: str) -> str:
    """Mapeia apenas marcador final isolado (E/D) para lateralidade."""
    tokens = text.split()
    if not tokens:
        return ""
    tail = tokens[-1]
    if tail == "e":
        tokens[-1] = "esquerdo"
    elif tail == "d":
        tokens[-1] = "direito"
    return " ".join(tokens)


def _canonicalize_practical_aliases(text: str) -> str:
    if not text:
        return ""
    out = f" {text} "
    # Remove ruído comum de OCR que não define a estrutura.
    out = re.sub(r"\bilegivel\b", " ", out)
    # Sinônimos recorrentes nas provas práticas.
    out = re.sub(r"\bgrande dorsal\b", " latissimo do dorso ", out)
    out = re.sub(r"\banconea?\b", " anconeo ", out)
    out = re.sub(r"\bbucinator\b", " bucinador ", out)
    out = re.sub(r"\bhalix\b", " halux ", out)
    out = re.sub(r"\bvleo\b", " soleo ", out)
    for pattern, canonical in _ANATOMIC_SYNONYMS:
        out = re.sub(pattern, f" {canonical} ", out)
    return re.sub(r"\s+", " ", out).strip()
