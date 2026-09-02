import copy
import pytest

from app.services.graphiti_ontology import compile_graphiti_ontology


def test_compiles_entity_edge_models_and_map_without_mutating_input():
    entities = [{"name": "Company Executive", "description": "A person leading a company", "attributes": [{"name": "full_name", "description": "Legal name"}]}, {"name": "ListedCompany", "description": "A public company", "attributes": []}]
    edges = [{"name": "MANAGES", "description": "Leadership relationship", "attributes": [{"name": "details", "description": "Context"}], "source_targets": [{"source": "Company Executive", "target": "ListedCompany"}]}]
    original = copy.deepcopy((entities, edges))

    bundle = compile_graphiti_ontology(entities, edges)

    assert set(bundle.entity_types) == {"Company_Executive", "ListedCompany"}
    assert bundle.entity_types["Company_Executive"].__doc__ == "A person leading a company"
    assert bundle.entity_types["Company_Executive"].model_fields["full_name"].description == "Legal name"
    assert bundle.edge_types["MANAGES"].__doc__ == "Leadership relationship"
    assert bundle.edge_type_map == {("Company_Executive", "ListedCompany"): ["MANAGES"]}
    assert "most specific" in bundle.custom_extraction_instructions
    assert (entities, edges) == original


def test_rejects_unknown_edge_entity_and_normalized_collisions():
    with pytest.raises(ValueError, match="unknown entity type"):
        compile_graphiti_ontology([{"name": "Person"}], [{"name": "KNOWS", "source_targets": [{"source": "Person", "target": "Missing"}]}])
    with pytest.raises(ValueError, match="duplicate normalized entity type"):
        compile_graphiti_ontology([{"name": "Board Director"}, {"name": "Board-Director"}], [])
