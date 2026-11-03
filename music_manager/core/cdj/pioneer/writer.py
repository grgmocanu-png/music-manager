"""Assemble ANLZ0000.DAT / .EXT / .2EX from a TrackAnalysis.

Section order matters and is confirmed from native Rekordbox exports:
  DAT: PPTH, PVBR, PQTZ, PWAV, PWV2, PCOB(hot A-C), PCOB(memory)
  EXT: PPTH, PWV3, PCOB(hot D+), PCOB(memory stub), PCO2(all hot), PCO2(stub),
       PQT2, PWV5, PWV4
  2EX: PPTH, PWV7, PWV6, PWVC
"""

from __future__ import annotations

from pathlib import Path

from .model import CueKind, CuePoint, TrackAnalysis
from . import quantize, sections
from .sections import PIONEER_PALETTE, anlz_folder_name

# Default hot-cue colors by slot (A..H) when a cue has no explicit color:
# A=green (first kick), B=red (drop), C=yellow (breakdown), D=blue (outro),
# then the remaining Pioneer palette entries.
DEFAULT_SLOT_COLORS = ["#00ff00", "#ff0000", "#ffff00", "#0080ff",
                       "#ff9900", "#00b4d8", "#cc00ff", "#ff6b35"]

_CODE_TO_HEX = {code: hex_ for hex_, code in PIONEER_PALETTE.items()}

# Memory cues and loops are written so deck-harvested user data round-trips.
# (Rekordbox 7 itself no longer writes memory cues into legacy ANLZ, but
# legacy players read them from here; byte layout hex-verified by the
# DjManager reference. Hardware gate: XZ/AZ round-trip test.)


def _slot_num(cue: CuePoint) -> int:
    """Model slot 'A'..'H' -> Pioneer hot_cue number 1..8 (0 = memory cue)."""
    if cue.kind == CueKind.MEMORY or not cue.slot:
        return 0
    return ord(cue.slot.upper()) - ord("A") + 1


def _color_hex(cue: CuePoint) -> str:
    if cue.color_id in _CODE_TO_HEX:
        return _CODE_TO_HEX[cue.color_id]
    num = _slot_num(cue)
    return DEFAULT_SLOT_COLORS[(num - 1) % len(DEFAULT_SLOT_COLORS)] if num else ""


def _hot_cues(analysis: TrackAnalysis) -> list[CuePoint]:
    return sorted((c for c in analysis.cues if c.kind == CueKind.HOT and c.slot),
                  key=_slot_num)


def _memory_cues(analysis: TrackAnalysis) -> list[CuePoint]:
    return sorted((c for c in analysis.cues if c.kind == CueKind.MEMORY),
                  key=lambda c: c.time_ms)


def _loop_out(c: CuePoint) -> int | None:
    return round(c.loop_out_ms) if c.loop_out_ms is not None else None


def _beats(analysis: TrackAnalysis) -> list[tuple[int, float]]:
    if analysis.grid is None:
        return []
    return [(b.beat_in_bar, b.time_ms) for b in analysis.grid.beats]


def _beat_amplitudes(analysis: TrackAnalysis) -> list[int] | None:
    """Per-beat peak amplitude in thousandths (PQT2 body semantics)."""
    if analysis.waveform is None or analysis.grid is None:
        return None
    peak = analysis.waveform.peak
    bps = analysis.waveform.bins_per_second
    interval_bins = max(1, round(60.0 / analysis.grid.bpm * bps))
    out = []
    for b in analysis.grid.beats:
        i0 = min(len(peak) - 1, max(0, int(b.time_ms / 1000.0 * bps)))
        window = peak[i0:i0 + interval_bins] or peak[i0:i0 + 1]
        out.append(round(max(0.0, min(1.0, max(window))) * 1000))
    return out


def build_dat(analysis: TrackAnalysis, usb_file_path: str) -> bytes:
    bpm = analysis.grid.bpm if analysis.grid else 0.0
    hot = [(n, round(c.time_ms), _color_hex(c), _loop_out(c))
           for c in _hot_cues(analysis) if 1 <= (n := _slot_num(c)) <= 3]
    memory = [(0, round(c.time_ms), "", _loop_out(c))
              for c in _memory_cues(analysis)]
    body = [
        sections.build_ppth(usb_file_path),
        sections.build_pvbr(),
        sections.build_pqtz(_beats(analysis), bpm),
    ]
    if analysis.waveform:
        body.append(sections.build_pwav(quantize.pwav_bytes(analysis.waveform)))
        body.append(sections.build_pwv2(quantize.pwv2_bytes(analysis.waveform)))
    body.append(sections.build_pcob_slot(1, hot))
    body.append(sections.build_pcob_slot(0, memory))
    payload = b"".join(body)
    return sections.build_file_header(28 + len(payload)) + payload


def _phrases(analysis: TrackAnalysis) -> list[tuple[int, int]] | None:
    """Phrase boundaries (beat_no, kind) from the cue heuristics: intro from
    the top, verse at the first kick, chorus at the drop, bridge at the
    breakdown, outro at the outro cue."""
    if analysis.grid is None or len(analysis.grid.beats) < 8:
        return None
    times = [b.time_ms for b in analysis.grid.beats]

    def beat_no(t_ms: float) -> int:
        import bisect

        return min(len(times), max(1, bisect.bisect_left(times, t_ms) + 1))

    kind_by_slot = {"A": sections.PHRASE_VERSE, "B": sections.PHRASE_CHORUS,
                    "C": sections.PHRASE_BRIDGE, "D": sections.PHRASE_OUTRO}
    entries = [(1, sections.PHRASE_INTRO)]
    for c in _hot_cues(analysis):
        kind = kind_by_slot.get(c.slot)
        if kind is None:
            continue
        bn = beat_no(c.time_ms)
        if bn > entries[-1][0]:
            entries.append((bn, kind))
    return entries if len(entries) >= 2 else None


def build_ext(analysis: TrackAnalysis, usb_file_path: str) -> bytes:
    bpm = analysis.grid.bpm if analysis.grid else 0.0
    ext_hot = [(n, round(c.time_ms), _color_hex(c), _loop_out(c))
               for c in _hot_cues(analysis) if (n := _slot_num(c)) >= 4]
    all_hot = [(n, round(c.time_ms), c.label, _color_hex(c), _loop_out(c))
               for c in _hot_cues(analysis) if (n := _slot_num(c)) >= 1]
    memory2 = [(0, round(c.time_ms), c.label, "", _loop_out(c))
               for c in _memory_cues(analysis)]
    body = [sections.build_ppth(usb_file_path)]
    if analysis.waveform:
        body.append(sections.build_pwv3(quantize.pwv3_bytes(analysis.waveform)))
    body.append(sections.build_pcob_slot(1, ext_hot))
    body.append(sections.build_pcob_slot(0, []))
    body.append(sections.build_pco2_slot(1, all_hot))
    body.append(sections.build_pco2_slot(0, memory2))
    body.append(sections.build_pqt2(_beats(analysis), bpm,
                                    values=_beat_amplitudes(analysis)))
    if analysis.waveform:
        body.append(sections.build_pwv5(quantize.pwv5_bytes(analysis.waveform)))
        body.append(sections.build_pwv4(quantize.pwv4_bytes(analysis.waveform)))
    phrases = _phrases(analysis)
    if phrases:
        body.append(sections.build_pssi(phrases, end_beat=len(analysis.grid.beats)))
    payload = b"".join(body)
    return sections.build_file_header(28 + len(payload)) + payload


def build_2ex(analysis: TrackAnalysis, usb_file_path: str) -> bytes | None:
    if not analysis.waveform:
        return None
    w = analysis.waveform
    pwv7 = quantize.pwv7_bytes(w)
    body = [
        sections.build_ppth(usb_file_path),
        sections.build_pwv7(pwv7, len(pwv7) // 3),
        sections.build_pwv6(quantize.pwv6_bytes(w)),
        sections.build_pwvc(),
    ]
    payload = b"".join(body)
    return sections.build_file_header(28 + len(payload)) + payload


def write_anlz(analysis: TrackAnalysis, usb_file_path: str,
               usb_root: Path) -> dict[str, Path]:
    """Write the analysis files for one track under usb_root/PIONEER/USBANLZ.

    usb_file_path is the USB-relative audio path (e.g.
    "/Contents/Artist/Album/Track.mp3") — it determines the hash folder and is
    embedded in every file's PPTH tag.
    """
    anlz_dir = usb_root / "PIONEER" / "USBANLZ" / anlz_folder_name(usb_file_path)
    anlz_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, data in (("ANLZ0000.DAT", build_dat(analysis, usb_file_path)),
                       ("ANLZ0000.EXT", build_ext(analysis, usb_file_path)),
                       ("ANLZ0000.2EX", build_2ex(analysis, usb_file_path))):
        if data is None:
            continue
        (anlz_dir / name).write_bytes(data)
        written[name] = anlz_dir / name
    return written


def anlz_usb_relative_dir(usb_file_path: str) -> str:
    return f"PIONEER/USBANLZ/{anlz_folder_name(usb_file_path)}"
