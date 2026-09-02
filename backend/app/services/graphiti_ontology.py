"""Compile persisted MiroFish ontology JSON into Graphiti runtime models."""

from dataclasses import dataclass
import re
from typing import Optional

from pydantic import BaseModel, Field, create_model

from ..utils.ontology import RESERVED_ONTOLOGY_ATTRIBUTE_NAMES, normalize_ontology_attributes, normalize_ontology_source_targets


@dataclass(frozen=True)
class GraphitiOntologyBundle:
    entity_types: dict[str, type[BaseModel]]
    edge_types: dict[str, type[BaseModel]]
    edge_type_map: dict[tuple[str, str], list[str]]
    custom_extraction_instructions: str


def _identifier(value: str, prefix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"{prefix}_{normalized or 'Type'}"
    return normalized


def _model(name: str, description: str, attributes) -> type[BaseModel]:
    fields = {}
    for attribute in normalize_ontology_attributes(attributes or []):
        field_name = _identifier(attribute["name"], "field")
        if field_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES or hasattr(BaseModel, field_name):
            field_name = f"ontology_{field_name}"
        if field_name in fields:
            raise ValueError(f"duplicate normalized attribute: {field_name}")
        fields[field_name] = (Optional[str], Field(default=None, description=attribute["description"]))
    result = create_model(name, **fields)
    result.__doc__ = description
    return result


def compile_graphiti_ontology(entities, edges) -> GraphitiOntologyBundle:
    entity_types = {}
    entity_names = {}
    for definition in entities or []:
        original = definition["name"]
        name = _identifier(original, "Entity")
        if name in entity_types:
            raise ValueError(f"duplicate normalized entity type: {name}")
        entity_names[original] = name
        entity_types[name] = _model(name, definition.get("description", f"A {name} entity."), definition.get("attributes", []))

    edge_types = {}
    edge_type_map = {}
    for definition in edges or []:
        name = _identifier(definition["name"], "Edge")
        if name in edge_types:
            raise ValueError(f"duplicate normalized edge type: {name}")
        edge_types[name] = _model(name, definition.get("description", f"A {name} relationship."), definition.get("attributes", []))
        for source_target in normalize_ontology_source_targets(definition.get("source_targets", [])):
            source = entity_names.get(source_target["source"])
            target = entity_names.get(source_target["target"])
            if source is None or target is None:
                raise ValueError(f"edge {name} references unknown entity type: {source_target['source']} -> {source_target['target']}")
            values = edge_type_map.setdefault((source, target), [])
            if name not in values:
                values.append(name)

    instructions = "Select the most specific supplied entity type for every extracted entity. Use the default Entity type only when no supplied type matches."
    return GraphitiOntologyBundle(entity_types, edge_types, edge_type_map, instructions)
