"""Citire și scriere de taguri audio, independent de format.

Formate suportate: MP3, FLAC, WAV, AIFF, M4A/AAC/ALAC, OGG, Opus.
Toate funcțiile lucrează cu un dicționar simplu de câmpuri (vezi FIELDS),
ca restul programului să nu depindă de detaliile fiecărui format.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".wave", ".aif", ".aiff", ".m4a", ".mp4",
    ".aac", ".alac", ".ogg", ".oga", ".opus", ".wma",
}
LOSSLESS_EXTENSIONS = {".flac", ".wav", ".wave", ".aif", ".aiff", ".alac"}

FIELDS = ("artist", "title", "album", "albumartist", "genre", "year",
          "track", "bpm", "key", "comment", "label")

# --- corespondența câmpurilor pe formate ---------------------------------
_ID3_FRAMES = {
    "artist": "TPE1", "title": "TIT2", "album": "TALB", "albumartist": "TPE2",
    "genre": "TCON", "year": "TDRC", "track": "TRCK", "bpm": "TBPM",
    "key": "TKEY", "label": "TPUB",
}
_VORBIS_KEYS = {
    "artist": ["artist"], "title": ["title"], "album": ["album"],
    "albumartist": ["albumartist", "album artist"], "genre": ["genre"],
    "year": ["date", "year"], "track": ["tracknumber"], "bpm": ["bpm", "tempo"],
    "key": ["initialkey", "key"], "comment": ["comment", "description"],
    "label": ["label", "organization", "publisher"],
}
_MP4_KEYS = {
    "artist": "\xa9ART", "title": "\xa9nam", "album": "\xa9alb",
    "albumartist": "aART", "genre": "\xa9gen", "year": "\xa9day",
    "comment": "\xa9cmt",
}
_MP4_KEY_FREEFORM = "----:com.apple.iTunes:initialkey"
_MP4_LABEL_FREEFORM = "----:com.apple.iTunes:LABEL"


@dataclass
class AudioInfo:
    duration: float = 0.0        # secunde
    bitrate: int = 0             # kbps
    sample_rate: int = 0
    bits_per_sample: int = 0
    channels: int = 0
    lossless: bool = False


@dataclass
class TagData:
    fields: dict = field(default_factory=dict)
    info: AudioInfo = field(default_factory=AudioInfo)
    cover: tuple | None = None   # (bytes, mime)
    has_cover: bool = False


def is_audio_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def _first(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip()


def _clean(fields: dict) -> dict:
    out = {k: "" for k in FIELDS}
    for k, v in fields.items():
        if k in out and v is not None:
            out[k] = str(v).strip()
    # an: păstrăm doar primele 4 cifre ("2019-05-01" -> "2019")
    y = out["year"]
    if len(y) >= 4 and y[:4].isdigit():
        out["year"] = y[:4]
    # piesa: "3/12" -> "3"
    out["track"] = out["track"].split("/")[0].strip()
    # bpm: "128.00" -> "128"
    b = out["bpm"].replace(",", ".")
    try:
        f = float(b)
        out["bpm"] = str(int(round(f))) if f > 0 else ""
    except ValueError:
        out["bpm"] = ""
    return out


def read_tags(path: str, want_cover: bool = False) -> TagData:
    """Citește tagurile și informațiile tehnice ale unui fișier.

    Nu aruncă excepții pentru fișiere fără taguri; aruncă doar dacă fișierul
    nu poate fi citit deloc.
    """
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Format necunoscut")

    ext = os.path.splitext(path)[1].lower()
    data = TagData()
    raw: dict = {}
    tags = audio.tags

    if tags is not None and isinstance(tags, ID3):
        for name, frame_id in _ID3_FRAMES.items():
            frame = tags.get(frame_id)
            if frame is not None and getattr(frame, "text", None):
                raw[name] = _first(frame.text)
        comms = tags.getall("COMM")
        if comms:
            raw["comment"] = _first(comms[0].text)
        pics = tags.getall("APIC")
        data.has_cover = bool(pics)
        if want_cover and pics:
            data.cover = (pics[0].data, pics[0].mime or "image/jpeg")
    elif isinstance(audio, MP4):
        t = tags or {}
        for name, key in _MP4_KEYS.items():
            if key in t:
                raw[name] = _first(t[key])
        if "trkn" in t and t["trkn"]:
            raw["track"] = str(t["trkn"][0][0])
        if "tmpo" in t and t["tmpo"]:
            raw["bpm"] = str(t["tmpo"][0])
        if _MP4_KEY_FREEFORM in t and t[_MP4_KEY_FREEFORM]:
            v = t[_MP4_KEY_FREEFORM][0]
            raw["key"] = v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)
        if _MP4_LABEL_FREEFORM in t and t[_MP4_LABEL_FREEFORM]:
            v = t[_MP4_LABEL_FREEFORM][0]
            raw["label"] = v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)
        data.has_cover = bool(t.get("covr"))
        if want_cover and "covr" in t and t["covr"]:
            c = t["covr"][0]
            mime = "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
            data.cover = (bytes(c), mime)
    elif tags is not None:
        # Vorbis comments (FLAC, OGG, Opus) și alte formate tip dicționar
        lower = {}
        try:
            for k, v in tags.items():
                lower.setdefault(str(k).lower(), v)
        except Exception:
            lower = {}
        for name, keys in _VORBIS_KEYS.items():
            for k in keys:
                if k in lower and _first(lower[k]):
                    raw[name] = _first(lower[k])
                    break
        pics = getattr(audio, "pictures", None)
        data.has_cover = bool(pics) or "metadata_block_picture" in lower
        if want_cover and pics:
            data.cover = (pics[0].data, pics[0].mime or "image/jpeg")

    data.fields = _clean(raw)

    info = audio.info
    ai = AudioInfo()
    ai.duration = float(getattr(info, "length", 0) or 0)
    ai.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    ai.bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
    ai.channels = int(getattr(info, "channels", 0) or 0)
    br = int(getattr(info, "bitrate", 0) or 0)
    if not br and ai.duration > 0:
        br = int(os.path.getsize(path) * 8 / ai.duration)
    ai.bitrate = round(br / 1000)
    codec = str(getattr(info, "codec", "") or "").lower()
    ai.lossless = ext in LOSSLESS_EXTENSIONS or codec == "alac"
    data.info = ai
    return data


def write_tags(path: str, fields: dict, cover: tuple | None = None, clear: tuple = ()) -> None:
    """Scrie tagurile date într-un fișier. Câmpurile goale sunt ignorate.

    MP3, WAV și AIFF primesc ID3v2.3 (cel mai compatibil cu Rekordbox,
    Serato, Traktor și playerele mai vechi).
    """
    import mutagen
    from mutagen.id3 import ID3, APIC, COMM, Frames
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    from mutagen.flac import FLAC, Picture

    fields = {k: v for k, v in _clean(fields).items() if v}
    clear = tuple(c for c in clear if c not in fields)
    ext = os.path.splitext(path)[1].lower()

    if ext == ".mp3" or ext in (".wav", ".wave", ".aif", ".aiff"):
        audio = mutagen.File(path)
        if audio is None:
            raise ValueError("Format necunoscut")
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        for name, frame_id in _ID3_FRAMES.items():
            if name in fields:
                tags.delall(frame_id)
                tags.add(Frames[frame_id](encoding=3, text=[fields[name]]))
        if "comment" in fields:
            tags.delall("COMM")
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[fields["comment"]]))
        if cover:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0]))
        for c in clear:
            if c == "comment":
                tags.delall("COMM")
            elif c in _ID3_FRAMES:
                tags.delall(_ID3_FRAMES[c])
        audio.save(v2_version=3)
        return

    if ext == ".flac":
        audio = FLAC(path)
        for name, keys in _VORBIS_KEYS.items():
            if name in fields:
                audio[keys[0]] = fields[name]
        for c in clear:
            for k in _VORBIS_KEYS.get(c, []):
                if k in audio:
                    del audio[k]
        if cover:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = cover[1]
            pic.data = cover[0]
            audio.add_picture(pic)
        audio.save()
        return

    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Format necunoscut")
    if isinstance(audio, MP4):
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        for name, key in _MP4_KEYS.items():
            if name in fields:
                t[key] = [fields[name]]
        if "track" in fields and fields["track"].isdigit():
            t["trkn"] = [(int(fields["track"]), 0)]
        if "bpm" in fields:
            t["tmpo"] = [int(fields["bpm"])]
        if "key" in fields:
            t[_MP4_KEY_FREEFORM] = [MP4FreeForm(fields["key"].encode("utf-8"))]
        if "label" in fields:
            t[_MP4_LABEL_FREEFORM] = [MP4FreeForm(fields["label"].encode("utf-8"))]
        for c in clear:
            key = _MP4_KEYS.get(c)
            if key and key in t:
                del t[key]
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
            t["covr"] = [MP4Cover(cover[0], imageformat=fmt)]
        audio.save()
        return

    # OGG / Opus și alte formate cu Vorbis comments
    if audio.tags is None:
        audio.add_tags()
    for name, keys in _VORBIS_KEYS.items():
        if name in fields:
            audio.tags[keys[0]] = [fields[name]]
    audio.save()
