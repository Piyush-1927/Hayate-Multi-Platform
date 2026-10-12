from hayate.providers.base import StreamProvider, Stream


class PseudoProvider(StreamProvider):
    name = "pseudo"
    label = "Default Standby Resolver"
    supports_catalog = False

    def get_stream(self, title: str, episode: int, dub=False, quality="best"):
        # Graceful non-blocking fallback when no external extractor succeeds
        return None

    def carries(self, title: str):
        return False
