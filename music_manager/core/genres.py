"""Familii de gen („grosier”) și gruparea stilurilor detaliate în ele.

Genul scris în tag = familia (ex. House). Stilul exact (ex. Deep House) merge în
Comentariu. Tabelul se poate edita din Setări; aici sunt valorile implicite.
"""
from __future__ import annotations

import json
import re
import unicodedata

DEFAULT_FAMILIES = [
    "House", "Techno", "Disco", "Electro", "Breaks", "Drum & Bass", "Garage & Bass", "Trance",
    "Ambient & Downtempo", "Hip Hop", "Funk & Soul", "Jazz", "Rock", "Pop", "Latin", "Afro",
    "World", "Experimental",
]

# stil (cum apare pe Discogs / Spotify / Beatport, scris oricum) -> familie
DEFAULT_STYLE_MAP = {
    # House
    "house": "House", "deep house": "House", "tech house": "House", "progressive house": "House",
    "acid house": "House", "afro house": "House", "chicago house": "House", "garage house": "House",
    "jackin house": "House", "microhouse": "House", "minimal house": "House", "lo-fi house": "House",
    "funky house": "House", "soulful house": "House", "vocal house": "House", "tribal house": "House",
    "hard house": "House", "electro house": "House", "italo house": "House", "hip-house": "House",
    "ghetto house": "House", "melodic house": "House", "organic house": "House", "french house": "House",
    "euro house": "House", "big room": "House", "future house": "House", "bass house": "House",
    # Techno
    "techno": "Techno", "minimal": "Techno", "minimal techno": "Techno", "dub techno": "Techno",
    "hard techno": "Techno", "acid": "Techno", "detroit techno": "Techno", "industrial techno": "Techno",
    "peak time techno": "Techno", "melodic techno": "Techno", "schranz": "Techno", "tech trance": "Techno",
    "ebm": "Techno", "industrial": "Techno", "hardcore": "Techno", "gabber": "Techno", "hard dance": "Techno",
    # Disco
    "disco": "Disco", "nu-disco": "Disco", "nu disco": "Disco", "italo-disco": "Disco", "italo disco": "Disco",
    "euro-disco": "Disco", "cosmic disco": "Disco", "disco polo": "Disco", "hi nrg": "Disco",
    "boogie": "Disco", "balearic": "Disco", "space disco": "Disco",
    # Electro
    "electro": "Electro", "electroclash": "Electro", "synth-pop": "Electro", "synthpop": "Electro",
    "new wave": "Electro", "darkwave": "Electro", "new beat": "Electro", "miami bass": "Electro",
    "indie dance": "Electro", "electropop": "Electro",
    # Breaks
    "breakbeat": "Breaks", "breaks": "Breaks", "big beat": "Breaks", "nu skool breaks": "Breaks",
    "progressive breaks": "Breaks", "florida breaks": "Breaks",
    # Drum & Bass
    "drum n bass": "Drum & Bass", "drum and bass": "Drum & Bass", "drum & bass": "Drum & Bass",
    "jungle": "Drum & Bass", "liquid funk": "Drum & Bass", "neurofunk": "Drum & Bass",
    "breakcore": "Drum & Bass", "footwork": "Drum & Bass", "juke": "Drum & Bass",
    # Garage & Bass
    "uk garage": "Garage & Bass", "speed garage": "Garage & Bass", "2-step": "Garage & Bass",
    "garage": "Garage & Bass", "dubstep": "Garage & Bass", "grime": "Garage & Bass", "uk funky": "Garage & Bass",
    "bassline": "Garage & Bass", "future garage": "Garage & Bass", "bass music": "Garage & Bass",
    "trap": "Garage & Bass", "wonky": "Garage & Bass",
    # Trance
    "trance": "Trance", "progressive trance": "Trance", "psy-trance": "Trance", "psytrance": "Trance",
    "goa trance": "Trance", "hard trance": "Trance", "uplifting trance": "Trance", "euro trance": "Trance",
    # Ambient & Downtempo
    "ambient": "Ambient & Downtempo", "downtempo": "Ambient & Downtempo", "trip hop": "Ambient & Downtempo",
    "chillout": "Ambient & Downtempo", "chillwave": "Ambient & Downtempo", "lounge": "Ambient & Downtempo",
    "dub": "Ambient & Downtempo", "drone": "Ambient & Downtempo", "new age": "Ambient & Downtempo",
    # Hip Hop
    "hip hop": "Hip Hop", "hip-hop": "Hip Hop", "rap": "Hip Hop", "boom bap": "Hip Hop",
    "instrumental hip hop": "Hip Hop", "conscious": "Hip Hop", "gangsta": "Hip Hop", "g-funk": "Hip Hop",
    "abstract": "Hip Hop", "r&b": "Funk & Soul", "rnb/swing": "Funk & Soul", "contemporary r&b": "Funk & Soul",
    # Funk & Soul
    "funk": "Funk & Soul", "soul": "Funk & Soul", "neo soul": "Funk & Soul", "jazz-funk": "Funk & Soul",
    "p.funk": "Funk & Soul", "rhythm & blues": "Funk & Soul", "northern soul": "Funk & Soul",
    "gospel": "Funk & Soul", "funk / soul": "Funk & Soul",
    # Jazz
    "jazz": "Jazz", "acid jazz": "Jazz", "nu jazz": "Jazz", "jazzdance": "Jazz", "fusion": "Jazz",
    "soul-jazz": "Jazz", "bossa nova": "Latin", "free jazz": "Jazz", "modal": "Jazz", "jazz-rock": "Jazz",
    # Rock
    "rock": "Rock", "indie rock": "Rock", "post-punk": "Rock", "punk": "Rock", "psychedelic rock": "Rock",
    "krautrock": "Rock", "alternative rock": "Rock", "shoegaze": "Rock", "garage rock": "Rock",
    "hard rock": "Rock", "prog rock": "Rock", "art rock": "Rock", "post rock": "Rock",
    # Pop
    "pop": "Pop", "dance-pop": "Pop", "europop": "Pop", "indie pop": "Pop", "electro pop": "Pop",
    "k-pop": "Pop", "city pop": "Pop", "dance": "Pop",
    # Latin
    "latin": "Latin", "salsa": "Latin", "cumbia": "Latin", "reggaeton": "Latin", "samba": "Latin",
    "baile funk": "Latin", "latin jazz": "Latin", "afro-cuban": "Latin", "merengue": "Latin", "bachata": "Latin",
    # Afro
    "afrobeat": "Afro", "afrobeats": "Afro", "amapiano": "Afro", "gqom": "Afro", "kwaito": "Afro",
    "highlife": "Afro", "afro-tech": "Afro", "afro tech": "Afro",
    # World
    "reggae": "World", "dancehall": "World", "folk": "World", "world": "World", "balkan": "World",
    "manele": "World", "flamenco": "World", "tropical": "World",
    # Experimental
    "experimental": "Experimental", "idm": "Experimental", "leftfield": "Experimental", "noise": "Experimental",
    "musique concrète": "Experimental", "glitch": "Experimental", "avantgarde": "Experimental",
    "electro-acoustic": "Experimental",
}

# genurile mari de pe Discogs, folosite când nu există stil
DISCOGS_GENRE_MAP = {
    "hip hop": "Hip Hop", "funk / soul": "Funk & Soul", "jazz": "Jazz", "rock": "Rock", "pop": "Pop",
    "latin": "Latin", "reggae": "World", "folk, world, & country": "World", "blues": "Funk & Soul",
    "classical": "Experimental", "non-music": "Experimental", "stage & screen": "Pop",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().strip()
    return re.sub(r"\s+", " ", s)


class GenreMapper:
    def __init__(self, families: list[str] | None = None, style_map: dict | None = None):
        self.families = families or list(DEFAULT_FAMILIES)
        self.style_map = {_norm(k): v for k, v in (style_map or DEFAULT_STYLE_MAP).items()}

    @classmethod
    def from_json(cls, families_json: str, map_json: str) -> "GenreMapper":
        try:
            fam = json.loads(families_json) if families_json else None
        except ValueError:
            fam = None
        try:
            mp = json.loads(map_json) if map_json else None
        except ValueError:
            mp = None
        return cls(fam, mp)

    def family_of(self, style: str) -> str:
        n = _norm(style)
        if not n:
            return ""
        # chiar familia (ex. „house”, „techno”)
        for f in self.families:
            if _norm(f) == n:
                return f
        fam = self.style_map.get(n)
        if fam:
            return fam
        # potrivire parțială: „Melodic Deep House” -> house
        for key in sorted(self.style_map, key=len, reverse=True):
            if re.search(r"\b" + re.escape(key) + r"\b", n):
                return self.style_map[key]
        return ""

    def classify(self, styles: list[str], genres: list[str] | None = None) -> str:
        """Familia pentru o listă de stiluri (primul stil recunoscut câștigă)."""
        votes: dict[str, int] = {}
        for i, s in enumerate(styles):
            f = self.family_of(s)
            if f:
                votes[f] = votes.get(f, 0) + (len(styles) - i)
        if votes:
            return max(votes.items(), key=lambda kv: kv[1])[0]
        for g in genres or []:
            f = DISCOGS_GENRE_MAP.get(_norm(g)) or self.family_of(g)
            if f:
                return f
        return ""

    def unknown_styles(self, styles) -> list[str]:
        return sorted({s for s in styles if s and not self.family_of(s)}, key=str.lower)
