"""상담 Q/A를 succeed3로 분석하는 외부 호출용 인터페이스.

입력은 기존 테스트 케이스와 같은 대화 목록이며, 반환값은 JSON으로 바로
직렬화할 수 있는 ``nouns``와 ``relations`` 두 필드만 포함한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import prompt_maker_succeed_3_span_experiment as succeed3


class CounselingInputError(ValueError):
    """상담 입력이 테스트 케이스 형식과 다를 때 발생한다."""


def _validate_conversations(conversations: Any) -> list[dict[str, str]]:
    if not isinstance(conversations, list) or not conversations:
        raise CounselingInputError("입력은 비어 있지 않은 대화 목록이어야 합니다.")
    if len(conversations) % 2 != 0:
        raise CounselingInputError("Q와 A가 한 쌍이 되도록 항목 수가 짝수여야 합니다.")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(conversations):
        if not isinstance(item, dict):
            raise CounselingInputError(f"{index + 1}번째 항목은 객체여야 합니다.")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CounselingInputError(
                f"{index + 1}번째 항목에 비어 있지 않은 text가 필요합니다."
            )

        text = text.strip()
        expected_prefix = "Q:" if index % 2 == 0 else "A:"
        if not text.startswith(expected_prefix):
            raise CounselingInputError(
                f"{index + 1}번째 항목은 {expected_prefix}로 시작해야 합니다."
            )
        normalized.append({"text": text})

    return normalized


def _name(value: Any) -> str | None:
    if value is None:
        return None
    noun = getattr(value, "noun", None)
    return str(noun if noun is not None else value)


def _names(values: Any) -> list[str]:
    return [_name(value) for value in (values or []) if _name(value) is not None]


def _serialize_relative_clause(clause: Any) -> dict[str, Any]:
    return {
        "subjects": [str(value) for value in getattr(clause, "subjects", [])],
        "verb": str(getattr(clause, "verb", "")),
        "object": getattr(clause, "obj", None),
        "direct_objects": [
            str(value) for value in getattr(clause, "direct_objects", [])
        ],
        "indirect_objects": [
            str(value) for value in getattr(clause, "indirect_objects", [])
        ],
        "object_complements": [
            str(value) for value in getattr(clause, "object_complements", [])
        ],
        "context": [str(value) for value in getattr(clause, "context_nouns", [])],
        "polarity_bit": int(getattr(clause, "polarity_bit", 1)),
        "negation": getattr(clause, "negation", None),
        "display": str(clause),
    }


def _serialize_position(position: Any) -> dict[str, str]:
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        return {"target": _name(position[0]), "location": str(position[1])}
    return {"target": str(position), "location": ""}


def _serialize_noun(noun_object: Any) -> dict[str, Any]:
    target = getattr(noun_object, "target", None)
    return {
        "noun": str(noun_object.noun),
        "adjectives": [str(value) for value in noun_object.adjectives],
        "positions": [
            _serialize_position(value) for value in noun_object.position
        ],
        "moderators": _names(noun_object.moderators),
        "target": _name(target),
        "relative_clauses": [
            _serialize_relative_clause(clause)
            for clause in noun_object.relative_clauses
        ],
    }


def _serialize_relation(relation: Any) -> dict[str, Any]:
    return {
        "subjects": _names(getattr(relation, "subjects", [])),
        "verb": str(relation.verb),
        "object": _name(relation.obj),
        "direct_objects": _names(relation.direct_objects),
        "indirect_objects": _names(relation.indirect_objects),
        "object_complements": _names(relation.object_complements),
        "context": _names(relation.context_nouns),
        "reason_clauses": [str(value) for value in relation.reason_clauses],
        "polarity_bit": int(relation.polarity_bit),
        "negation": relation.negation,
        "display": str(relation),
    }


def analyze(conversations: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """테스트 케이스 형식의 상담을 분석해 JSON 호환 결과를 반환한다.

    반환값에는 최종 명사 객체와 관계 객체 이외의 평가 정보나 원문이
    포함되지 않는다.
    """
    normalized = _validate_conversations(conversations)
    succeed3.DEBUG_MORPHS = False
    nouns, relations = succeed3.analyze_conversations(normalized)
    return {
        "nouns": [_serialize_noun(value) for value in nouns],
        "relations": [_serialize_relation(value) for value in relations],
    }


def analyze_file(json_file: str | Path) -> dict[str, list[dict[str, Any]]]:
    """테스트 케이스 JSON 파일을 읽어 :func:`analyze` 결과를 반환한다."""
    path = Path(json_file)
    with path.open("r", encoding="utf-8") as stream:
        conversations = json.load(stream)
    return analyze(conversations)


__all__ = ["CounselingInputError", "analyze", "analyze_file"]
