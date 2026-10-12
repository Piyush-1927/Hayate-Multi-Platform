from dataclasses import dataclass, field
from typing import Protocol, Union, runtime_checkable

@dataclass(frozen=True)
class Stream:
    """What a provider hands the player. Only `url` is required."""
    url: str
    title: str = ""
    headers: dict = field(default_factory=dict)
    subtitles: tuple = ()
    referer: str = ""

def provider_label(prov) -> str:
    return getattr(prov, "label", "") or getattr(prov, "name", "Unknown Provider")

@runtime_checkable
class PlaybackProvider(Protocol):
    """The contract the player and UI rely on."""
    name: str
    label: str
    supports_catalog: bool

    def get_stream(
        self,
        title: str,
        episode: int,
        dub: Union[bool, str] = False,
        quality: str = "best"
    ) -> "Stream | None": ...

    def carries(self, title: str) -> "bool | None": ...

class StreamProvider:
    name = "base"
    label = ""
    supports_catalog = False

    def get_stream(
        self,
        title: str,
        episode: int,
        dub: Union[bool, str] = False,
        quality: str = "best"
    ) -> "Stream | None":
        raise NotImplementedError

    def carries(self, title: str) -> "bool | None":
        return None
