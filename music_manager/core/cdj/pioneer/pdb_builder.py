"""Template-seeded export.pdb construction.

PdbEditor performs surgical edits the way Rekordbox does; starting from a
template pdb captured from a real Rekordbox export keeps every undocumented
header/page field bit-true. The committed interim template comes from the
rekordbox-pdb project's test data (a real export); the user's own golden
'empty' capture replaces it once available (see tests/fixtures/golden/).
"""

from __future__ import annotations

from pathlib import Path

from .model import TrackAnalysis
from ..rekordbox_pdb import Database
from ..rekordbox_pdb.edit import PdbEditor

_CAMELOT_TO_STANDARD = {
    "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm",
    "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "Dbm",
    "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb",
    "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
}


def key_name(camelot: str) -> str:
    """Rekordbox stores key rows as musical names (Am, F#, ...)."""
    return _CAMELOT_TO_STANDARD.get(camelot.upper(), camelot)


class PdbBuilder:
    def __init__(self, editor: PdbEditor):
        self._editor = editor

    @classmethod
    def from_template(cls, template_path: Path) -> PdbBuilder:
        return cls(PdbEditor.from_file(template_path))

    def get_or_create_artwork(self, artwork_path: str) -> int:
        """Artwork rows share the u32-id + string row shape with genres, but
        their string field is `path`, so the editor's name-keyed _get_or_create
        can't be reused directly."""
        from ..rekordbox_pdb import TableType

        ed = self._editor
        rows = ed.db.rows(TableType.ARTWORK)
        for row in rows:
            if row.path == artwork_path:
                return row.id
        new_id = max((r.id for r in rows), default=0) + 1
        row_bytes, alloc, shift_at = ed._build_named_row(new_id, artwork_path)
        ed._append_row(TableType.ARTWORK, row_bytes, alloc, index_shift_at=shift_at)
        return new_id

    def add_track(self, analysis: TrackAnalysis, usb_audio_path: str,
                  anlz_dat_path: str, artwork_id: int = 0) -> int:
        """anlz_dat_path: USB-relative ANLZ0000.DAT path with leading slash."""
        meta = analysis.meta
        return self._editor.add_track(
            title=meta.title,
            file_path=usb_audio_path,
            artist=meta.artist or None,
            album=meta.album or None,
            genre=meta.genre or None,
            key=key_name(analysis.key.camelot) if analysis.key else None,
            analyze_path=anlz_dat_path,
            tempo=round(analysis.grid.bpm * 100) if analysis.grid else 0,
            duration=round(meta.duration_ms / 1000),
            year=meta.year,
            bitrate=meta.bitrate,
            sample_rate=meta.sample_rate,
            file_size=meta.file_size,
            track_number=meta.track_no,
            artwork_id=artwork_id,
        )

    def create_playlist(self, name: str) -> int:
        return self._editor.create_playlist(name)

    def add_to_playlist(self, playlist_id: int, track_id: int) -> None:
        self._editor.add_to_playlist(playlist_id, track_id)

    def to_bytes(self) -> bytes:
        return self._editor.to_bytes()

    def save(self, path: Path) -> None:
        self._editor.save(path)

    @property
    def db(self) -> Database:
        return self._editor.db
