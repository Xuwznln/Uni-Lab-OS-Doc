"""图层级只由 ``parent`` 表达：``children`` 是派生字段，读取忽略、导出不写。"""

from __future__ import annotations

from uuid import uuid4

import pytest

graphio = pytest.importorskip(
    "unilabos.resources.graphio",
    reason="GraphIO 依赖 ROS Jazzy 生成的 unilabos_msgs",
    exc_type=ImportError,
)


def _node(node_id: str, parent: str | None = None, **overrides):
    payload = {
        "id": node_id,
        "uuid": str(uuid4()),
        "name": node_id,
        "type": "container",
        "class": "",
        "template_name": "RegularContainer",
        "config": {"type": "RegularContainer"},
        "data": {},
        "extra": {},
        "parent": parent,
    }
    payload.update(overrides)
    return payload


def test_canonicalize_builds_hierarchy_from_parent_only():
    tree = graphio.canonicalize_nodes_data(
        [_node("root"), _node("child", parent="root"), _node("leaf", parent="child")]
    )

    (root,) = tree.root_nodes
    assert [child.res_content.id for child in root.children] == ["child"]
    assert [leaf.res_content.id for leaf in root.children[0].children] == ["leaf"]
    assert all("children" not in dumped for dumped in tree.dump()[0])


def test_canonicalize_ignores_legacy_children_lists():
    """旧图 children 列表与 parent 不一致时以 parent 为准，且 children 不会落进 config。"""
    tree = graphio.canonicalize_nodes_data(
        [
            _node("root", children=["stranger"]),
            _node("child", parent="root", children=[]),
            _node("stranger", children=[]),
        ]
    )

    roots = {node.res_content.id: node for node in tree.root_nodes}
    assert set(roots) == {"root", "stranger"}
    assert [child.res_content.id for child in roots["root"].children] == ["child"]
    assert all("children" not in node.res_content.config for node in tree.all_nodes)


def test_tree_to_list_and_dict_to_tree_roundtrip_via_parent():
    nested = [
        {
            "id": "root",
            "type": "deck",
            "children": [
                {"id": "a", "type": "plate", "children": [{"id": "a1", "type": "well", "children": []}]},
                {"id": "b", "type": "plate", "children": []},
            ],
        }
    ]

    flat = graphio.tree_to_list(nested)
    assert [node["id"] for node in flat] == ["root", "a", "a1", "b"]
    assert all("children" not in node for node in flat)
    assert {node["id"]: node["parent"] for node in flat} == {
        "root": None,
        "a": "root",
        "a1": "a",
        "b": "root",
    }

    rebuilt = graphio.dict_to_tree({node["id"]: dict(node) for node in flat})
    assert [node["id"] for node in rebuilt] == ["root"]
    assert [child["id"] for child in rebuilt[0]["children"]] == ["a", "b"]
    assert [leaf["id"] for leaf in rebuilt[0]["children"][0]["children"]] == ["a1"]


def test_dict_to_tree_falls_back_to_legacy_children_lists():
    legacy = {
        "root": {"id": "root", "type": "deck", "children": ["a"]},
        "a": {"id": "a", "type": "plate", "children": []},
    }

    rebuilt = graphio.dict_to_tree(legacy)
    assert [node["id"] for node in rebuilt] == ["root"]
    assert [child["id"] for child in rebuilt[0]["children"]] == ["a"]
