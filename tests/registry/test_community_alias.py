"""Plan 09 Task 7: community alias resolution."""

import pytest

from unilabos.registry.community_alias import (
    CommunityAliasError,
    normalize_community_class,
    resolve_community_alias,
)


def test_normalize_community_class_strips_prefix():
    assert normalize_community_class("community.pylabrobot.lh.opentrons_flex") == "pylabrobot.lh.opentrons_flex"


def test_normalize_community_class_leaves_local_class_unchanged():
    assert normalize_community_class("pylabrobot.lh.opentrons_flex") == "pylabrobot.lh.opentrons_flex"


def test_resolve_community_alias_requires_registry_entry():
    registry = {"pylabrobot.lh.opentrons_flex": {"class": {"module": "x:Y"}}}

    resolved = resolve_community_alias("community.pylabrobot.lh.opentrons_flex", registry)

    assert resolved == "pylabrobot.lh.opentrons_flex"


def test_resolve_community_alias_raises_when_missing():
    with pytest.raises(CommunityAliasError):
        resolve_community_alias("community.unknown.device", {})
