import pytest

from app.services.entity_kind_classifier import EntityKind, classify_entity_kind


@pytest.mark.parametrize(
    "entity_type",
    ["Person", "CompanyExecutive", "BoardDirector", "SecuritiesAnalyst", "Investor"],
)
def test_person_like_custom_types_are_individuals(entity_type):
    assert classify_entity_kind(entity_type) is EntityKind.INDIVIDUAL


@pytest.mark.parametrize(
    ("entity_type", "expected"),
    [
        ("ListedCompany", EntityKind.INSTITUTION),
        ("MediaOutlet", EntityKind.INSTITUTION),
        ("StockExchange", EntityKind.INSTITUTION),
        ("GovernmentAgency", EntityKind.INSTITUTION),
        ("Region", EntityKind.REGION),
        ("Country", EntityKind.REGION),
        ("MarketRegion", EntityKind.REGION),
        ("PressConference", EntityKind.EVENT),
        ("BoardMeeting", EntityKind.EVENT),
        ("GenericEntity", EntityKind.OTHER),
    ],
)
def test_classifies_non_person_entity_families(entity_type, expected):
    assert classify_entity_kind(entity_type) is expected


def test_attributes_and_description_can_disambiguate_generic_types():
    assert classify_entity_kind(
        "GenericEntity",
        {"occupation": "证券分析师", "employer": "Example Capital"},
        "一位长期跟踪上市公司的分析师",
    ) is EntityKind.INDIVIDUAL
    assert classify_entity_kind(
        "GenericEntity",
        {"organization_type": "上市公司"},
        "依法注册并运营的企业",
    ) is EntityKind.INSTITUTION


def test_institutional_investor_is_not_forced_to_be_a_person():
    assert classify_entity_kind(
        "Investor", {"investor_category": "机构投资者"}, "A fund account"
    ) is EntityKind.INSTITUTION
