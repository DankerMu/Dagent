"""Graph routing preserves explicit conditional destinations without character splitting."""

from xagent.core.graph.node import CondDestNode, MultiDestNode


def test_conditional_destinations_include_default_and_branch_in_order() -> None:
    node = CondDestNode(
        id="router",
        next=[
            {"branch": "success"},
            {"default": "fallback", "branch": "discarded"},
            {"branch": "retry"},
            {"branch": None},
        ],
    )
    assert node.get_node_ids() == ["success", "fallback", "retry"]


def test_single_string_destination_remains_one_graph_node() -> None:
    assert MultiDestNode(id="entry", next="END").get_node_ids() == ["END"]
