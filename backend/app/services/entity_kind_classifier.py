"""Deterministically classify ontology entity types for persona generation."""

from enum import Enum
import json
import re
from typing import Any, Mapping, Optional


class EntityKind(str, Enum):
    INDIVIDUAL = "individual"
    INSTITUTION = "institution"
    REGION = "region"
    EVENT = "event"
    OTHER = "other"


def _tokens(value: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value or "")
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


_INDIVIDUAL = {
    "person", "individual", "people", "student", "alumni", "professor",
    "expert", "faculty", "official", "journalist", "activist", "executive",
    "director", "analyst", "investor", "founder", "officer", "employee",
    "manager", "leader", "spokesperson", "author", "researcher",
}
_INSTITUTION = {
    "company", "corporation", "organization", "organisation", "institution",
    "agency", "university", "ngo", "outlet", "platform", "exchange", "bank",
    "fund", "government", "committee", "association", "community", "group",
    "media", "enterprise", "firm",
}
_REGION = {
    "region", "country", "city", "province", "state", "continent", "territory",
    "market", "location", "area", "district",
}
_EVENT = {
    "event", "conference", "meeting", "summit", "election", "hearing",
    "announcement", "launch", "incident", "crisis", "protest", "war",
    "filing", "transaction",
}


def classify_entity_kind(
    entity_type: str,
    attributes: Optional[Mapping[str, Any]] = None,
    description: str = "",
) -> EntityKind:
    """Classify by type first, then use attributes/description as a fallback."""
    type_tokens = _tokens(entity_type)
    evidence = f"{json.dumps(attributes or {}, ensure_ascii=False)} {description}".lower()
    # Investor may represent either a natural person or an institutional account.
    if "investor" in type_tokens and any(term in evidence for term in ("机构", "institutional", "fund", "基金")):
        return EntityKind.INSTITUTION
    for kind, vocabulary in (
        (EntityKind.INDIVIDUAL, _INDIVIDUAL),
        (EntityKind.INSTITUTION, _INSTITUTION),
        (EntityKind.REGION, _REGION),
        (EntityKind.EVENT, _EVENT),
    ):
        if type_tokens & vocabulary:
            return kind

    evidence_tokens = _tokens(evidence)
    if evidence_tokens & _INDIVIDUAL or any(term in evidence for term in ("一位", "个人", "分析师", "董事", "高管", "投资者", "职业")):
        return EntityKind.INDIVIDUAL
    if evidence_tokens & _INSTITUTION or any(term in evidence for term in ("机构", "公司", "企业", "组织", "政府部门")):
        return EntityKind.INSTITUTION
    if evidence_tokens & _REGION or any(term in evidence for term in ("地区", "国家", "城市", "区域")):
        return EntityKind.REGION
    if evidence_tokens & _EVENT or any(term in evidence for term in ("事件", "会议", "发布会")):
        return EntityKind.EVENT
    return EntityKind.OTHER
