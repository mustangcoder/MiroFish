from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode


def _entity(uuid, name):
    return EntityNode(uuid=uuid, name=name, labels=["Entity", "Person"], summary=name, attributes={})


def _profile(user_id, uuid, name):
    return OasisAgentProfile(
        user_id=user_id,
        user_name=name.lower(),
        name=name,
        bio=name,
        persona=name,
        source_entity_uuid=uuid,
        source_entity_type="Person",
    )


def test_resume_skips_checkpointed_entities_and_preserves_order_and_user_ids(monkeypatch):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    generator.graph_id = None
    entities = [_entity("a", "Alice"), _entity("b", "Bob"), _entity("c", "Carol")]
    generated = []
    checkpoints = []

    def generate(entity, user_id, use_llm):
        generated.append(entity.uuid)
        return _profile(user_id, entity.uuid, entity.name)

    monkeypatch.setattr(generator, "generate_profile_from_entity", generate)
    monkeypatch.setattr(generator, "_print_generated_profile", lambda *args: None)

    profiles = generator.generate_profiles_from_entities(
        entities,
        use_llm=False,
        parallel_count=1,
        existing_profiles={"b": _profile(99, "b", "Restored Bob").to_dict()},
        checkpoint_callback=lambda index, entity, profile: checkpoints.append(
            (index, entity.uuid, profile.user_id)
        ),
    )

    assert generated == ["a", "c"]
    assert [profile.source_entity_uuid for profile in profiles] == ["a", "b", "c"]
    assert [profile.user_id for profile in profiles] == [0, 1, 2]
    assert profiles[1].name == "Restored Bob"
    assert checkpoints == [(0, "a", 0), (2, "c", 2)]
