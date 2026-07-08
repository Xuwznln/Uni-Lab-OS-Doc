"""Plan 09 Task 3: mock drivers for initializer tests."""


class MockBackend:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port


class MockDeck:
    def __init__(self, name: str):
        self.name = name


class SharedDevice:
    def __init__(self, backend: MockBackend, deck: MockDeck, name: str, channels: int):
        self.backend = backend
        self.deck = deck
        self.name = name
        self.channels = channels
