"""Vincula cada folha física ao aluno sem colapsar identidades parecidas.

O QR identifica o cadastro. O manifesto só descreve a geometria da folha que
estava naquela posição quando as folhas foram geradas. Uma digitalização fora
de ordem não herda o aluno dessa posição.

Nomes genéricos (`ALUNO 09`) não substituem o nome manuscrito. Sufixo de
matrícula, código `ALUNO XX` e a ordem do manifesto não são identidade global.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_GENERIC_NAME = re.compile(r"(?i)^\s*aluno(?:\s*\d{1,4})?\s*$")
_PRINTED_LABEL = re.compile(r"(?i)\baluno\s*(\d{1,4})\b")


@dataclass
class SheetIdentity:
    physical_page: int
    sheet_key: str
    student_id: str | None = None
    registered_name: str = ""
    registered_registration: str = ""
    printed_label: str = ""
    handwritten_name: str = ""
    confirmed_name: str = ""
    detected_registration: str = ""
    class_name: str = ""
    identity_source: str = "unlinked"
    conflicts: list[str] = field(default_factory=list)
    uncertain: bool = True
    manifest_student_id: str = ""
    qr_exam_id: str = ""
    qr_page_in_student: int | None = None

    @property
    def display_name(self) -> str:
        return display_name(
            confirmed_name=self.confirmed_name,
            handwritten_name=self.handwritten_name,
            registered_name=self.registered_name,
            printed_label=self.printed_label,
            physical_page=self.physical_page,
        )


def is_generic_student_name(name: str | None) -> bool:
    text = str(name or "").strip()
    if not text:
        return True
    return _GENERIC_NAME.match(text) is not None


def printed_label_from(*sources: str | None) -> str:
    for source in sources:
        match = _PRINTED_LABEL.search(str(source or ""))
        if match:
            return f"ALUNO {int(match.group(1)):02d}"
    return ""


def display_name(
    *,
    confirmed_name: str | None = None,
    handwritten_name: str | None = None,
    registered_name: str | None = None,
    printed_label: str | None = None,
    physical_page: int | None = None,
) -> str:
    """Confirmação humana, depois o manuscrito, por último o cadastro genérico."""
    for candidate in (confirmed_name, handwritten_name):
        text = str(candidate or "").strip()
        if text and not is_generic_student_name(text):
            return text
    registered = str(registered_name or "").strip()
    if registered and not is_generic_student_name(registered):
        return registered
    label = str(printed_label or "").strip()
    if label:
        return label
    if registered:
        return registered
    if physical_page:
        return f"Folha {physical_page}"
    return "Folha sem identificação"


def lexical_registration_order(registrations: list[str]) -> list[str]:
    """Mesma ordem de `ORDER BY registration_number` em texto, não numérica."""
    return sorted((str(item) for item in registrations), key=str)


def resolve_sheet_identity(
    *,
    physical_page: int,
    qr_exam_id: str | None = None,
    qr_student_id: str | None = None,
    qr_page_in_student: int | None = None,
    expected_exam_id: str | None = None,
    roster: dict[str, dict[str, Any]] | None = None,
    manifest_student_id: str | None = None,
    header: dict[str, Any] | None = None,
) -> SheetIdentity:
    """Resolve a folha. O manifesto nunca vira o aluno escolhido."""
    header = header or {}
    roster = roster if isinstance(roster, dict) else {}
    conflicts: list[str] = []
    handwritten = str(header.get("handwritten_name") or header.get("name") or "").strip()
    header_registration = str(header.get("registration") or "").strip()
    class_name = str(header.get("class") or header.get("class_name") or "").strip()
    printed = printed_label_from(
        header.get("printed_label"),
        handwritten,
        header.get("name"),
    )
    # Nome manuscrito de pessoa não é o rótulo impresso ALUNO XX.
    if is_generic_student_name(handwritten):
        if not printed:
            printed = printed_label_from(handwritten)
        handwritten_person = ""
    else:
        handwritten_person = handwritten

    identity = SheetIdentity(
        physical_page=int(physical_page),
        sheet_key=f"page:{int(physical_page)}",
        printed_label=printed,
        handwritten_name=handwritten_person,
        detected_registration=header_registration,
        class_name=class_name,
        manifest_student_id=str(manifest_student_id or ""),
        qr_exam_id=str(qr_exam_id or ""),
        qr_page_in_student=qr_page_in_student,
    )

    qr_id = str(qr_student_id or "").strip()
    exam_ok = True
    if qr_id and expected_exam_id:
        exam_ok = str(qr_exam_id or "") == str(expected_exam_id)
        if not exam_ok:
            conflicts.append(
                "QR de outra prova. O vínculo do QR foi ignorado."
            )
            qr_id = ""

    if identity.manifest_student_id:
        conflicts.append(
            "Manifesto registra o aluno da posição original da folha gerada, "
            "não da página digitalizada. Esse aluno não foi adotado."
        )
        if qr_id and identity.manifest_student_id != qr_id:
            conflicts.append("QR e manifesto apontam para alunos diferentes. Prevalece o QR conferido.")

    record = roster.get(qr_id) if qr_id else None
    if qr_id and isinstance(record, dict):
        identity.student_id = qr_id
        identity.registered_name = str(record.get("name") or "")
        identity.registered_registration = str(record.get("registration") or "")
        identity.class_name = identity.class_name or str(record.get("class") or "")
        identity.identity_source = "qr"
        identity.uncertain = False
        if is_generic_student_name(identity.registered_name) and not identity.handwritten_name:
            identity.uncertain = True
            conflicts.append(
                "Cadastro com nome genérico e nome manuscrito ainda não lido."
            )
        if (
            identity.printed_label
            and identity.registered_name
            and printed_label_from(identity.registered_name)
            and identity.printed_label != printed_label_from(identity.registered_name)
        ):
            identity.uncertain = True
            conflicts.append(
                "Rótulo impresso e cadastro do QR discordam. A folha permanece na página física."
            )
            identity.student_id = None
            identity.identity_source = "conflict"
    elif qr_id and roster:
        identity.identity_source = "qr_unknown"
        identity.uncertain = True
        conflicts.append("QR não corresponde a um aluno desta prova.")
    elif qr_id:
        identity.student_id = qr_id
        identity.identity_source = "qr"
        identity.uncertain = False
    else:
        identity.identity_source = "header" if (handwritten_person or header_registration or printed) else "unlinked"
        identity.uncertain = True

    if header_registration and identity.registered_registration:
        if _registration_conflict(header_registration, identity.registered_registration):
            identity.uncertain = True
            identity.student_id = None
            identity.identity_source = "conflict"
            conflicts.append("Matrícula do cabeçalho difere da matrícula do QR.")

    identity.conflicts = conflicts
    return identity


def _registration_conflict(header_registration: str, registered: str) -> bool:
    left = re.sub(r"\s+", "", header_registration)
    right = re.sub(r"\s+", "", registered)
    if not left or not right:
        return False
    if left == right:
        return False
    # "9" e "09" são o mesmo número completo, não um sufixo de outra matrícula.
    if left.isdigit() and right.isdigit() and int(left) == int(right) and len(left) == len(right):
        return False
    if left.isdigit() and right.isdigit() and int(left) == int(right):
        return False
    return True


def can_merge_sheets(left: SheetIdentity, right: SheetIdentity, question_numbers: dict[str, set[int]]) -> bool:
    """Continuação legítima: mesmo aluno de QR, sem conflito e sem questão repetida."""
    if left.physical_page == right.physical_page and left.sheet_key == right.sheet_key:
        return True
    if left.uncertain or right.uncertain:
        return False
    if left.identity_source != "qr" or right.identity_source != "qr":
        return False
    if not left.student_id or left.student_id != right.student_id:
        return False
    left_questions = question_numbers.get(left.sheet_key, set())
    right_questions = question_numbers.get(right.sheet_key, set())
    if left_questions & right_questions:
        return False
    return True


def group_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Uma folha por grupo, salvo continuação de QR sem questão repetida.

    Questão repetida na mesma identidade não sobrescreve a anterior: as folhas
    permanecem separadas e o conflito fica explícito.
    """
    by_sheet: dict[str, dict[str, Any]] = {}
    for row in rows:
        page = int(row.get("physical_page") or row.get("page_number") or 0)
        key = str(row.get("sheet_key") or f"page:{page}")
        bucket = by_sheet.setdefault(
            key,
            {
                "sheet_key": key,
                "physical_page": page,
                "pages": [],
                "answers": [],
                "question_numbers": set(),
                "identity": row.get("identity"),
                "display_name": row.get("display_name")
                or display_name(
                    confirmed_name=row.get("confirmed_name"),
                    handwritten_name=row.get("handwritten_name") or row.get("student_name"),
                    registered_name=row.get("registered_name"),
                    printed_label=row.get("printed_label"),
                    physical_page=page,
                ),
                "registration_number": row.get("registration_number") or row.get("registration") or "",
                "registered_name": row.get("registered_name") or "",
                "handwritten_name": row.get("handwritten_name") or "",
                "confirmed_name": row.get("confirmed_name") or "",
                "printed_label": row.get("printed_label") or "",
                "turma": row.get("turma") or row.get("class_name") or "",
                "identity_source": row.get("identity_source") or "",
                "conflicts": list(row.get("conflicts") or []),
                "uncertain": bool(row.get("uncertain", True)),
                "student_id": row.get("student_id") or "",
                "needs_review": False,
                "warnings": [],
            },
        )
        if page and page not in bucket["pages"]:
            bucket["pages"].append(page)
        qnum = int(row.get("question_number") or 0)
        if qnum and qnum in bucket["question_numbers"]:
            bucket["conflicts"].append(
                f"Questão {qnum} repetida na folha {key}. Os dois registros foram preservados."
            )
            bucket["uncertain"] = True
        if qnum:
            bucket["question_numbers"].add(qnum)
        bucket["answers"].append(row)
        bucket["needs_review"] = bucket["needs_review"] or bool(row.get("needs_review"))
        if row.get("review_reason"):
            bucket["warnings"].append(str(row["review_reason"]))

    sheets = list(by_sheet.values())
    merged: list[dict[str, Any]] = []
    used: set[str] = set()
    for sheet in sheets:
        if sheet["sheet_key"] in used:
            continue
        host = sheet
        used.add(host["sheet_key"])
        if host["uncertain"] or host["identity_source"] != "qr" or not host["student_id"]:
            merged.append(host)
            continue
        for other in sheets:
            if other["sheet_key"] in used:
                continue
            if other["uncertain"] or other["identity_source"] != "qr":
                continue
            if other["student_id"] != host["student_id"]:
                continue
            overlap = host["question_numbers"] & other["question_numbers"]
            if overlap:
                other["conflicts"].append(
                    "Mesmo aluno do QR com questões repetidas. As folhas não foram unidas."
                )
                other["uncertain"] = True
                continue
            host["answers"].extend(other["answers"])
            host["question_numbers"] |= other["question_numbers"]
            host["pages"].extend(page for page in other["pages"] if page not in host["pages"])
            host["needs_review"] = host["needs_review"] or other["needs_review"]
            host["warnings"].extend(other["warnings"])
            host["conflicts"].extend(other["conflicts"])
            used.add(other["sheet_key"])
        merged.append(host)

    # Folhas que ficaram de fora por conflito de questão entram sozinhas.
    for sheet in sheets:
        if sheet["sheet_key"] not in used:
            merged.append(sheet)
            used.add(sheet["sheet_key"])

    merged.sort(key=lambda item: (_sort_name(item["display_name"]), item["physical_page"], item["sheet_key"]))
    return merged


def _sort_name(value: str) -> tuple[str, str]:
    return (str(value or "").casefold(), str(value or ""))


def same_account(owner_id: Any, user_id: Any) -> bool:
    """Dono ausente em dado antigo continua acessível; outra conta, não."""
    if owner_id is None:
        return True
    return str(owner_id) == str(user_id)
