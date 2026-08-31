from types import SimpleNamespace

from app.services.zep_entity_reader import ZepEntityReader
from zep_cloud.graph.node.client import NodeClient


def test_pinned_zep_sdk_exposes_get_edges_not_get_entity_edges():
    assert hasattr(NodeClient, "get_edges")
    assert not hasattr(NodeClient, "get_entity_edges")


def test_get_node_edges_uses_the_supported_sdk_method():
    calls = []

    class NodeApi:
        def get_edges(self, *, node_uuid):
            calls.append(node_uuid)
            return [SimpleNamespace(
                uuid_="edge-1",
                name="KNOWS",
                fact="Alice knows Bob",
                source_node_uuid="node-1",
                target_node_uuid="node-2",
                attributes={"since": "2024"},
            )]

    class GraphApi:
        node = NodeApi()

    class Client:
        graph = GraphApi()

    reader = object.__new__(ZepEntityReader)
    reader.client = Client()

    assert reader.get_node_edges("node-1") == [{
        "uuid": "edge-1",
        "name": "KNOWS",
        "fact": "Alice knows Bob",
        "source_node_uuid": "node-1",
        "target_node_uuid": "node-2",
        "attributes": {"since": "2024"},
    }]
    assert calls == ["node-1"]


def test_graphiti_generic_entities_are_kept_when_custom_labels_are_absent():
    reader = object.__new__(ZepEntityReader)
    reader._backend = "graphiti"
    reader.get_all_nodes = lambda _graph_id: [
        {"uuid": "node-1", "name": "Alice", "labels": ["Entity"], "summary": "Founder", "attributes": {}},
        {"uuid": "node-2", "name": "Acme", "labels": ["Entity"], "summary": "Company", "attributes": {}},
    ]
    reader.get_all_edges = lambda _graph_id: []

    result = reader.filter_defined_entities("graph-1", defined_entity_types=["Person"])

    assert result.filtered_count == 2
    assert result.entity_types == {"GenericEntity"}
    assert all("GenericEntity" in entity.labels for entity in result.entities)


def test_cloud_generic_entities_remain_filtered_out():
    reader = object.__new__(ZepEntityReader)
    reader._backend = "cloud"
    reader.get_all_nodes = lambda _graph_id: [
        {"uuid": "node-1", "name": "Alice", "labels": ["Entity"], "summary": "Founder", "attributes": {}},
    ]
    reader.get_all_edges = lambda _graph_id: []

    result = reader.filter_defined_entities("graph-1")

    assert result.filtered_count == 0
