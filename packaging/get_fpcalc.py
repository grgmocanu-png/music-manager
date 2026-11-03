"""Pentru build: pune fpcalc (Chromaprint) în folderul fpcalc/ din rădăcina proiectului."""
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from music_manager.core import fingerprint as fpr  # noqa: E402


def get(url):
    h = {"User-Agent": "MusicManager-build"}
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok and "api.github.com" in url:
        h["Authorization"] = f"Bearer {tok}"
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120, context=ctx) as r:
        return r.read()


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = fpr.download_fpcalc(os.path.join(root, "fpcalc"), http_get=get)
    print("fpcalc:", p)
