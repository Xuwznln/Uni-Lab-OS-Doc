"""TwinPoller 逻辑单测(不依赖 ROS2)。"""

from unilabos.sim.twin_runtime import collect_twin_pairs, poll_twin_pairs


class _FakeBridge:
    def __init__(self, *, updated=True, raises=False):
        self.calls = 0
        self._updated = updated
        self._raises = raises

    def poll_once(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._updated


class _FakePair:
    def __init__(self, node_id, bridge):
        self.node_id = node_id
        self.bridge = bridge


class _PlainDevice:
    bridge = None


def test_collect_twin_pairs_from_dict():
    pair = _FakePair("ur5", _FakeBridge())
    devices = {"ur5": pair, "plain": _PlainDevice()}
    collected = collect_twin_pairs(devices)
    assert collected == [pair]


def test_collect_twin_pairs_from_list_and_none():
    pair = _FakePair("a", _FakeBridge())
    assert collect_twin_pairs([pair, _PlainDevice()]) == [pair]
    assert collect_twin_pairs(None) == []


def test_poll_twin_pairs_counts_updates_and_calls_each():
    b1, b2 = _FakeBridge(updated=True), _FakeBridge(updated=False)
    p1, p2 = _FakePair("a", b1), _FakePair("b", b2)
    updated = poll_twin_pairs([p1, p2])
    assert b1.calls == 1 and b2.calls == 1
    assert updated == 1  # only b1 reported an update


def test_poll_twin_pairs_isolates_failures():
    good = _FakeBridge(updated=True)
    bad = _FakeBridge(raises=True)
    # bad pair must not stop the good one
    updated = poll_twin_pairs([_FakePair("bad", bad), _FakePair("good", good)])
    assert good.calls == 1
    assert updated == 1


def test_poll_node_provider_picks_up_late_devices():
    from unilabos.sim.twin_runtime import _resolve

    store = {}
    provider = lambda: store.values()
    assert _resolve(provider) == []
    pair = _FakePair("x", _FakeBridge())
    store["x"] = pair
    assert list(_resolve(provider)) == [pair]
