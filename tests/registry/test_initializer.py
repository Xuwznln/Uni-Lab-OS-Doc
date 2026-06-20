"""Plan 09 Task 3: initializer resolver."""

from unilabos.registry.initializer import build_instance_from_registry_entry


def test_build_instance_from_registry_entry_constructs_nested_factories():
    entry = {
        "class": {
            "module": "tests.registry.fixtures.initializer_drivers:SharedDevice",
            "init": {
                "kwargs": {
                    "backend": {
                        "factory": "tests.registry.fixtures.initializer_drivers:MockBackend",
                        "kwargs": {
                            "host": "${config.host}",
                            "port": "${config.port}",
                        },
                    },
                    "deck": {
                        "factory": "tests.registry.fixtures.initializer_drivers:MockDeck",
                        "kwargs": {
                            "name": "opentrons-flex",
                        },
                    },
                    "name": "${node.id}",
                    "channels": 96,
                }
            },
        }
    }
    node = {"id": "lh1", "name": "Liquid Handler 1"}
    config = {"host": "127.0.0.1", "port": 31950}

    device = build_instance_from_registry_entry(entry, node=node, config=config)

    assert device.name == "lh1"
    assert device.channels == 96
    assert device.backend.host == "127.0.0.1"
    assert device.backend.port == 31950
    assert device.deck.name == "opentrons-flex"


def test_build_instance_from_registry_entry_supports_explicit_constant_value():
    entry = {
        "class": {
            "module": "tests.registry.fixtures.initializer_drivers:MockDeck",
            "init": {
                "kwargs": {
                    "name": {"value": "constant-deck"},
                }
            },
        }
    }

    deck = build_instance_from_registry_entry(entry, node={"id": "deck1"}, config={})

    assert deck.name == "constant-deck"
