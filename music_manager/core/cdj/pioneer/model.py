"""The TrackAnalysis contract: the only shared vocabulary between DSP producers
(analysis/) and format writers (formats/).

All dataclasses are frozen and JSON-serializable; the JSON form doubles as the
on-disk analysis cache format. Times are float milliseconds from file start.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CueKind(str, Enum):
    MEMORY = "memory"
    HOT = "hot"


class CueSource(str, Enum):
    """Precedence invariant: DECK and MANUAL cues are user data and always win.
    HEURISTIC cues only fill slots the user hasn't claimed, and re-processing
    never overwrites a DECK/MANUAL cue."""

    HEURISTIC = "heuristic"
    DECK = "deck"
    MANUAL = "manual"


@dataclass(frozen=True)
class TrackMeta:
    source_path: str
    title: str
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: int = 0
    track_no: int = 0
    duration_ms: float = 0.0
    sample_rate: int = 0
    bitrate: int = 0
    file_size: int = 0
    sha1: str = ""


@dataclass(frozen=True)
class Beat:
    time_ms: float
    beat_in_bar: int  # 1..4


@dataclass(frozen=True)
class BeatGrid:
    bpm: float  # constant tempo (Stage 1 scope)
    first_beat_ms: float
    beats: tuple[Beat, ...] = ()
    confidence: float = 0.0

    @staticmethod
    def constant(bpm: float, first_beat_ms: float, duration_ms: float,
                 first_beat_in_bar: int = 1, confidence: float = 0.0) -> BeatGrid:
        """Expand a constant grid into explicit beats up to duration_ms."""
        interval = 60_000.0 / bpm
        beats = []
        t = first_beat_ms
        bib = first_beat_in_bar
        while t < duration_ms:
            beats.append(Beat(time_ms=t, beat_in_bar=bib))
            t += interval
            bib = bib % 4 + 1
        return BeatGrid(bpm=bpm, first_beat_ms=first_beat_ms,
                        beats=tuple(beats), confidence=confidence)


@dataclass(frozen=True)
class KeyResult:
    camelot: str  # e.g. "8A"
    standard: str = ""  # e.g. "A minor"
    confidence: float = 0.0
    source: str = ""  # essentia | keyfinder | manual


@dataclass(frozen=True)
class CuePoint:
    kind: CueKind
    time_ms: float
    slot: str = ""  # "A".."H" for hot cues, "" for memory cues
    label: str = ""
    color_id: int = -1  # Pioneer palette id; -1 = writer picks default
    source: CueSource = CueSource.HEURISTIC
    loop_out_ms: float | None = None  # set -> this cue is a loop (in=time_ms)


@dataclass(frozen=True)
class WaveformData:
    """Neutral high-resolution waveform: per-bin values in 0..1. The ANLZ
    quantizer owns all Pioneer bit layouts and resolutions; DSP never does."""

    bins_per_second: int
    peak: tuple[float, ...]
    rms: tuple[float, ...]
    low: tuple[float, ...]  # <150 Hz energy
    mid: tuple[float, ...]
    high: tuple[float, ...]


@dataclass(frozen=True)
class TrackAnalysis:
    meta: TrackMeta
    grid: BeatGrid | None = None
    key: KeyResult | None = None
    cues: tuple[CuePoint, ...] = ()
    waveform: WaveformData | None = None
    analyzer_versions: dict[str, str] = field(default_factory=dict)

    def replace_cues(self, cues: tuple[CuePoint, ...]) -> TrackAnalysis:
        from dataclasses import replace

        return replace(self, cues=cues)

    # -- JSON round-trip (cache format) ------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> TrackAnalysis:
        raw: dict[str, Any] = json.loads(text)
        meta = TrackMeta(**raw["meta"])
        grid = None
        if raw.get("grid"):
            g = raw["grid"]
            grid = BeatGrid(bpm=g["bpm"], first_beat_ms=g["first_beat_ms"],
                            beats=tuple(Beat(**b) for b in g.get("beats", [])),
                            confidence=g.get("confidence", 0.0))
        key = KeyResult(**raw["key"]) if raw.get("key") else None
        cues = tuple(
            CuePoint(kind=CueKind(c["kind"]), time_ms=c["time_ms"],
                     slot=c.get("slot", ""), label=c.get("label", ""),
                     color_id=c.get("color_id", -1),
                     source=CueSource(c.get("source", "heuristic")),
                     loop_out_ms=c.get("loop_out_ms"))
            for c in raw.get("cues", [])
        )
        waveform = None
        if raw.get("waveform"):
            w = raw["waveform"]
            waveform = WaveformData(
                bins_per_second=w["bins_per_second"],
                peak=tuple(w["peak"]), rms=tuple(w["rms"]),
                low=tuple(w["low"]), mid=tuple(w["mid"]), high=tuple(w["high"]))
        return cls(meta=meta, grid=grid, key=key, cues=cues, waveform=waveform,
                   analyzer_versions=raw.get("analyzer_versions", {}))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def load(cls, path: str | Path) -> TrackAnalysis:
        return cls.from_json(Path(path).read_text())


def merge_cues(existing: tuple[CuePoint, ...],
               incoming: tuple[CuePoint, ...]) -> tuple[CuePoint, ...]:
    """Merge cue lists under the precedence invariant.

    User cues (DECK/MANUAL) always survive. An incoming HEURISTIC cue is
    dropped if a user cue already claims the same hot-cue slot, or (for memory
    cues) sits within one beat's typical width (500 ms) of a user memory cue.
    Incoming user cues replace same-slot/same-position cues of any source.
    """
    user_kept = [c for c in existing if c.source != CueSource.HEURISTIC]
    result = list(user_kept)

    def user_claims(c: CuePoint) -> bool:
        for u in user_kept:
            if c.kind == CueKind.HOT and u.kind == CueKind.HOT and u.slot == c.slot:
                return True
            if (c.kind == CueKind.MEMORY and u.kind == CueKind.MEMORY
                    and abs(u.time_ms - c.time_ms) < 500.0):
                return True
        return False

    for c in incoming:
        if c.source == CueSource.HEURISTIC:
            if not user_claims(c):
                result.append(c)
        else:
            result = [
                r for r in result
                if not (r.kind == c.kind and (
                    (c.kind == CueKind.HOT and r.slot == c.slot)
                    or (c.kind == CueKind.MEMORY and abs(r.time_ms - c.time_ms) < 1.0)))
            ]
            result.append(c)
    return tuple(sorted(result, key=lambda c: (c.time_ms, c.kind.value, c.slot)))
