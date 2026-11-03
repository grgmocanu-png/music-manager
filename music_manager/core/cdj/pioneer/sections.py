"""ANLZ section (tag) builders — big-endian binary blobs, one per fourcc.

Ported from DjManager's anlzWriter.js (hardware-verified field layouts,
cross-checked against the Deep Symmetry djl-analysis docs and crate-digger's
Kaitai spec). All builders return bytes; assembly into .DAT/.EXT/.2EX files
happens in writer.py.

Field-layout provenance comments are kept from the source: they encode which
constants were confirmed by hex-diffing native Rekordbox exports.
"""

from __future__ import annotations

import struct

# ── Path hashing ─────────────────────────────────────────────────────────────
# CDJs locate analysis files at PIONEER/USBANLZ/P<part1>/<part2>/ANLZ0000.*,
# derived from the USB-relative audio path.


def anlz_folder_name(usb_file_path: str) -> str:
    path = usb_file_path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    h = 0
    for ch in path:
        h = (h * 0x34F5501D + ord(ch) * 0x93B6) & 0xFFFFFFFF
    part2 = h % 0x30D43
    part1 = ((((((((((part2 >> 2) & 0x4000) | (part2 & 0x2000)) >> 3)
                  | (part2 & 0x200)) >> 1)
                | (part2 & 0xC0)) >> 3)
              | (part2 & 0x4)) >> 1) | (part2 & 0x1)
    return f"P{part1:03X}/{part2:08X}"


# ── Low-level helpers ────────────────────────────────────────────────────────


def _u32(v: int) -> bytes:
    return struct.pack(">I", v & 0xFFFFFFFF)


def _u16(v: int) -> bytes:
    return struct.pack(">H", v & 0xFFFF)


def _utf16be_z(text: str) -> bytes:
    return text.encode("utf-16-be") + b"\x00\x00"


def _section(fourcc: bytes, body: bytes, len_header: int) -> bytes:
    return fourcc + _u32(len_header) + _u32(len(body) + 12) + body


def _section_big_header(fourcc: bytes, specific_header: bytes, data: bytes) -> bytes:
    """24-byte header variant: 12 standard + 12 section-specific fields."""
    assert len(specific_header) == 12
    return fourcc + _u32(24) + _u32(24 + len(data)) + specific_header + data


# ── File header ──────────────────────────────────────────────────────────────


def build_file_header(total_size: int) -> bytes:
    # bytes 12-27: constants observed in real CDJ/Rekordbox ANLZ files
    return (b"PMAI" + _u32(0x1C) + _u32(total_size)
            + _u32(0x00000001) + _u32(0x00010000) + _u32(0x00010000) + _u32(0))


# ── PPTH: file path ──────────────────────────────────────────────────────────


def build_ppth(usb_file_path: str) -> bytes:
    encoded = _utf16be_z(usb_file_path)
    # len_path counts the 2-byte null terminator (confirmed from native output);
    # len_header=16: 12 common + 4 for the len_path field
    return _section(b"PPTH", _u32(len(encoded)) + encoded, 16)


# ── PQTZ / PQT2: beat grids ──────────────────────────────────────────────────
# Beat entry: beat_in_bar (u16), tempo in centi-BPM (u16), time in ms (u32).


def build_pqtz(beats: list[tuple[int, float]], bpm: float) -> bytes:
    """beats: list of (beat_in_bar 1..4, time_ms). len_header=24 confirmed.
    Tempo is stored PER BEAT (centi-BPM from the interval to the next beat),
    which is how variable grids work; a constant grid yields the same
    constant value throughout."""
    if not beats:
        body = _u32(0) + _u32(0x80000) + _u32(0)
        return _section(b"PQTZ", body, 24)
    fallback = round((bpm or 128.0) * 100) & 0xFFFF
    tempos = []
    for i in range(len(beats)):
        if i + 1 < len(beats) and beats[i + 1][1] > beats[i][1]:
            tempos.append(min(0xFFFF, round(6_000_000.0 / (beats[i + 1][1] - beats[i][1]))))
        else:
            tempos.append(tempos[-1] if tempos else fallback)
    parts = [_u32(0), _u32(0x80000), _u32(len(beats))]
    parts += [_u16(bib) + _u16(tempos[i]) + _u32(round(t))
              for i, (bib, t) in enumerate(beats)]
    return _section(b"PQTZ", b"".join(parts), 24)


def build_pqt2(beats: list[tuple[int, float]], bpm: float,
               values: list[int] | None = None) -> bytes:
    """Extended beatgrid (Rekordbox 6+). len_header=56; body is one u16 per
    beat = peak amplitude at that beat in thousandths (0-1000). Decoded from
    the golden click capture: constant-amplitude audio -> constant 954 =
    round(peak 0.954 * 1000). The grid itself comes from the anchors + PQTZ."""
    ec = len(beats)
    if values is None:
        values = [800] * ec  # neutral mid-high amplitude when unknown
    tempo = round((bpm or 128.0) * 100) & 0xFFFF
    hdr = bytearray(56)
    hdr[0:4] = b"PQT2"
    hdr[4:8] = _u32(56)
    hdr[8:12] = _u32(56 + ec * 2)
    hdr[16:20] = _u32(0x01000002)  # constant observed in all native files
    if ec:
        bib, t = beats[0]
        hdr[24:32] = _u16(bib) + _u16(tempo) + _u32(round(t))
        bib, t = beats[-1]
        hdr[32:40] = _u16(bib) + _u16(tempo) + _u32(round(t))
    hdr[40:44] = _u32(ec)
    body = b"".join(_u16(max(0, min(1000, v))) for v in values[:ec])
    body += _u16(800) * max(0, ec - len(values))
    return bytes(hdr) + body


# ── PVBR: seek index ─────────────────────────────────────────────────────────


def build_pvbr() -> bytes:
    """Native Rekordbox always writes PVBR between PPTH and PQTZ in the DAT —
    and writes it entirely zeroed (verified across 180 golden tracks incl.
    MP3s, Rekordbox 5-era and 7.2.18). Body: u32 + 401 u32 entries, all zero;
    len_tag 1620."""
    return _section(b"PVBR", bytes(4 + 401 * 4), 16)


# ── Cue colors ───────────────────────────────────────────────────────────────
# PCPT (hardware-read hot cue palette): codes 1-8 per slot.
# PCP2 uses a different ~64-step color wheel with explicit RGB.
# Keyed by lowercase hex color. ✓ = confirmed from native hex-diffs.

PIONEER_PALETTE = {
    "#ff6b35": 1,  # orange-red
    "#ff0000": 2,  # red
    "#ff9900": 3,  # orange ✓
    "#ffff00": 4,  # yellow
    "#00ff00": 5,  # green
    "#00b4d8": 6,  # cyan ✓
    "#0080ff": 7,  # blue
    "#cc00ff": 8,  # violet
}

PIONEER_PCP2_MAP = {
    "#ff6b35": (0x27, 0xFF, 0x46, 0x00),
    "#ff0000": (0x2A, 0xFF, 0x00, 0x00),  # ✓
    "#ff9900": (0x23, 0xFF, 0xA2, 0x00),
    "#ffff00": (0x1F, 0xF3, 0xF4, 0x00),
    "#00ff00": (0x16, 0x1A, 0xFF, 0x00),  # ✓
    "#00b4d8": (0x09, 0x00, 0xE0, 0xFF),  # ✓
    "#0080ff": (0x05, 0x00, 0x70, 0xFF),  # ✓
    "#cc00ff": (0x38, 0xB3, 0x00, 0xFF),  # ✓
}


# ── PCOB (DAT/EXT hardware cue lists) ────────────────────────────────────────
# Rekordbox 6+ sub-tagged format: each entry is a fixed 56-byte PCPT sub-tag.
# The older flat-entry format makes Rekordbox reject the whole ANLZ file.


def _pcpt_entry(hot_cue_num: int, time_ms: int, color_hex: str,
                loop_out_ms: int | None = None) -> bytes:
    buf = bytearray(56)
    buf[0:4] = b"PCPT"
    buf[4:8] = _u32(28)   # len_header
    buf[8:12] = _u32(56)  # len_tag
    buf[12:16] = _u32(hot_cue_num)  # 0=memory, 1=A, 2=B, ...
    # [16:20] status=0 (native Rekordbox writes 0)
    buf[20:24] = _u32(0x00010000)   # constant in all native files
    buf[24:26] = _u16(0xFFFF)       # order_first
    buf[26:28] = _u16(0xFFFF)       # order_last
    buf[28] = 2 if loop_out_ms is not None else 1  # type: 1=cue, 2=loop
    buf[30:32] = _u16(0x03E8)       # constant
    buf[32:36] = _u32(time_ms)
    buf[36:40] = _u32(loop_out_ms if loop_out_ms is not None else 0xFFFFFFFF)
    buf[40] = PIONEER_PALETTE.get(color_hex.lower(), 0) if color_hex else 0
    return bytes(buf)


def _empty_pcob(slot_type: int) -> bytes:
    return (b"PCOB" + _u32(24) + _u32(24) + _u32(slot_type) + _u32(0)
            + b"\xff\xff\xff\xff")


def build_pcob_slot(slot_type: int,
                    cues: list[tuple[int, int, str, int | None]]) -> bytes:
    """slot_type: 1=hot cues, 0=memory cues.
    cues: (hot_cue_num, time_ms, color_hex, loop_out_ms_or_None)."""
    if not cues:
        return _empty_pcob(slot_type)
    header = (b"PCOB" + _u32(24) + _u32(24 + len(cues) * 56) + _u32(slot_type)
              + _u16(0) + _u16(len(cues)) + b"\xff\xff\xff\xff")
    return header + b"".join(_pcpt_entry(n, t, c, lo) for n, t, c, lo in cues)


# ── PCO2 (EXT extended cue list with labels/colors) ──────────────────────────


def _pcp2_entry(hot_cue_num: int, time_ms: int, label: str, color_hex: str,
                loop_out_ms: int | None = None) -> bytes:
    label_bytes = _utf16be_z(label) if label else b""
    label_len = len(label_bytes)
    # Native Rekordbox writes body >= 88 whenever a label is present
    body_size = max(88, 28 + label_len + 44) if label else 72
    len_tag = 16 + body_size
    buf = bytearray(len_tag)
    buf[0:4] = b"PCP2"
    buf[4:8] = _u32(16)
    buf[8:12] = _u32(len_tag)
    buf[12:16] = _u32(hot_cue_num)
    buf[16] = 2 if loop_out_ms is not None else 1  # type: 1=cue, 2=loop
    buf[18:20] = _u16(0x03E8)       # constant
    buf[20:24] = _u32(time_ms)
    buf[24:28] = _u32(loop_out_ms if loop_out_ms is not None else 0xFFFFFFFF)
    buf[29] = 0x01                   # constant
    buf[40:44] = _u32(label_len)    # len_comment incl null terminator
    buf[44:44 + label_len] = label_bytes
    color_off = 44 + label_len
    if color_hex and (c := PIONEER_PCP2_MAP.get(color_hex.lower())):
        buf[color_off:color_off + 4] = bytes(c)
    return bytes(buf)


def _empty_pco2(slot_type: int) -> bytes:
    return b"PCO2" + _u32(20) + _u32(20) + _u32(slot_type) + _u32(0)


def build_pco2_slot(slot_type: int,
                    cues: list[tuple[int, int, str, str, int | None]]) -> bytes:
    """cues: (hot_cue_num, time_ms, label, color_hex, loop_out_ms_or_None)."""
    if not cues:
        return _empty_pco2(slot_type)
    entries = b"".join(_pcp2_entry(n, t, lbl, c, lo) for n, t, lbl, c, lo in cues)
    header = (b"PCO2" + _u32(20) + _u32(20 + len(entries)) + _u32(slot_type)
              + _u16(len(cues)) + _u16(0))
    return header + entries


# ── Waveform sections ────────────────────────────────────────────────────────
# Encodings are documented in quantize.py, which produces the raw byte arrays.

PWAV_COLS = 400
PWV2_COLS = 100
PWV4_COLS = 1200
PWV6_COLS = 1200


def build_pwav(data: bytes) -> bytes:
    body = _u32(len(data)) + _u32(0x00010000) + data
    return _section(b"PWAV", body, 20)


def build_pwv2(data: bytes) -> bytes:
    body = _u32(len(data)) + _u32(0x00010000) + data
    return _section(b"PWV2", body, 20)


def build_pwv3(data: bytes) -> bytes:
    hdr = _u32(1) + _u32(len(data)) + _u32(0x00960000)
    return _section_big_header(b"PWV3", hdr, data)


def build_pwv4(data: bytes) -> bytes:
    hdr = _u32(6) + _u32(len(data) // 6) + _u32(0)
    return _section_big_header(b"PWV4", hdr, data)


def build_pwv5(data: bytes) -> bytes:
    hdr = _u32(2) + _u32(len(data) // 2) + _u32(0x00960305)
    return _section_big_header(b"PWV5", hdr, data)


def build_pwv6(data: bytes) -> bytes:
    # len_header=20 (not 24) — confirmed from native .2EX
    return (b"PWV6" + _u32(20) + _u32(20 + len(data)) + _u32(3)
            + _u32(PWV6_COLS) + data)


def build_pwv7(data: bytes, num_cols: int) -> bytes:
    hdr = _u32(3) + _u32(num_cols) + _u32(0x00960000)
    return _section_big_header(b"PWV7", hdr, data)


def build_pwvc() -> bytes:
    # Calibration constants 100/104/197 observed in all native .2EX files;
    # len_header=14 is unusual but confirmed.
    return b"PWVC" + _u32(14) + _u32(20) + b"\x00\x00" + _u16(100) + _u16(104) + _u16(197)


# ── PSSI: song structure / phrase analysis (Rekordbox 6+, CDJ-3000 era) ──────
# Exported files carry the body XOR-masked; the mask is XOR_MASK[i % 19] +
# len_entries per byte, starting after the len_entries field (offset 18).

_PSSI_XOR = bytes.fromhex("CBE1EEFAE5EEADEEE9D2E9EBE1E9F3E8E9F4E1")

# phrase kinds for mood=2 ("mid"): 1=Intro, 2=Verse, 8=Bridge, 9=Chorus, 10=Outro
PHRASE_INTRO, PHRASE_VERSE, PHRASE_BRIDGE, PHRASE_CHORUS, PHRASE_OUTRO = 1, 2, 8, 9, 10


def build_pssi(entries: list[tuple[int, int]], end_beat: int,
               mood: int = 2, garble: bool = True) -> bytes:
    """entries: (beat_number 1-based, phrase kind), ascending. Matches the
    golden Rekordbox 7 layout: len_header=32, 24-byte entries (index, beat,
    kind, rest zero)."""
    body = bytearray()
    body += _u32(24)                 # len_entry_bytes
    body += _u16(len(entries))
    body += _u16(mood)
    body += bytes(6)                 # u1
    body += _u16(end_beat)
    body += b"\x00\x01"              # u2 constant (golden)
    body += bytes(2)                 # bank + u3
    for index, (beat, kind) in enumerate(entries, start=1):
        e = bytearray(24)
        e[0:2] = _u16(index)
        e[2:4] = _u16(beat)
        e[4:6] = _u16(kind)
        body += e
    tag = bytearray(b"PSSI" + _u32(32) + _u32(12 + len(body)) + bytes(body))
    if garble:
        shift = len(entries)
        for i in range(18, len(tag)):
            tag[i] ^= (_PSSI_XOR[(i - 18) % 19] + shift) & 0xFF
    return bytes(tag)
