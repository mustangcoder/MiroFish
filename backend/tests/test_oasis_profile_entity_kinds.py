from app.services.entity_kind_classifier import EntityKind
from app.services.oasis_profile_generator import OasisProfileGenerator


def _generator():
    return OasisProfileGenerator.__new__(OasisProfileGenerator)


def test_custom_person_types_use_individual_prompt(monkeypatch):
    generator = _generator()
    monkeypatch.setattr(generator, "_build_individual_persona_prompt", lambda *args: "individual")
    monkeypatch.setattr(generator, "_build_group_persona_prompt", lambda *args: "institution")
    monkeypatch.setattr(generator, "_build_contextual_persona_prompt", lambda *args, **kwargs: "contextual")

    assert generator._build_persona_prompt("Alice", "CompanyExecutive", "", {}, "") == (EntityKind.INDIVIDUAL, "individual")
    assert generator._build_persona_prompt("ACME", "ListedCompany", "", {}, "") == (EntityKind.INSTITUTION, "institution")
    assert generator._build_persona_prompt("Europe", "Region", "", {}, "") == (EntityKind.REGION, "contextual")


def test_rule_fallback_treats_custom_people_as_people():
    result = _generator()._generate_profile_rule_based(
        "Alice", "SecuritiesAnalyst", "Tracks public companies", {"occupation": "Analyst"}
    )
    assert result["gender"] in {"male", "female"}
    assert result["profession"] == "Analyst"


def test_neutral_fallback_does_not_invent_china_or_force_istj():
    generator = _generator()
    region = generator._generate_profile_rule_based("Europe", "Region", "A region", {})
    unknown = generator._generate_profile_rule_based("Topic X", "GenericEntity", "An unresolved topic", {})

    assert region["gender"] == "other"
    assert region["country"] == "未明确"
    assert unknown["gender"] == "other"
    assert unknown["country"] == "未明确"
    assert unknown["mbti"] != "ISTJ"


def test_reddit_serialization_uses_neutral_missing_metadata(tmp_path):
    from app.services.oasis_profile_generator import OasisAgentProfile
    import json

    output = tmp_path / "profiles.json"
    _generator()._save_reddit_json(
        [OasisAgentProfile(0, "topic", "Topic", "Topic", "Topic")], str(output)
    )
    profile = json.loads(output.read_text(encoding="utf-8"))[0]
    assert profile["mbti"] == "ISFP"
    assert profile["country"] == "未明确"
