"""Testăm integrarea cu Sockseek folosind un „Sockseek fals” care scrie aceleași evenimente."""
import json
import os
import stat
import sys
import textwrap

import pytest

from music_manager.core.soulseek import (
    DownloadItem, EventParser, build_command, pretty_title, run_download,
)
from music_manager.core.spotify import SpotifyTrack

FAKE = textwrap.dedent('''\
    #!{python}
    import csv, json, os, sys
    args = sys.argv[1:]
    csv_path = args[0]
    out = args[args.index("-o") + 1]
    conf = open(args[args.index("-c") + 1]).read()
    assert "username = user" in conf and "password = secret" in conf
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    sub = os.path.join(out, "lista")
    os.makedirs(sub, exist_ok=True)
    def ev(t, **d):
        print(json.dumps({{"type": t, "data": d}}), flush=True)
    print("Logging in...", flush=True)
    for i, r in enumerate(rows):
        ev("search_start", artist=r["Artist"], title=r["Title"])
        if i == 1:
            ev("track_state", artist=r["Artist"], title=r["Title"], terminalOutcome="Failed",
               failureReason="NoSuitableFileFound")
            continue
        ev("download_start", artist=r["Artist"], title=r["Title"], username="bob",
           filename="@@x\\\\Music\\\\" + r["Title"] + ".mp3")
        ev("download_progress", jobId="j", percent=50.0)
        p = os.path.join(sub, r["Artist"] + " - " + r["Title"] + ".mp3")
        open(p, "wb").write(b"fake")
        ev("track_state", artist=r["Artist"], title=r["Title"], terminalOutcome="Succeeded",
           downloadPath=p, bitRate=320, extension="mp3")
''')


@pytest.mark.skipif(sys.platform == "win32", reason="scriptul fals folosește shebang")
def test_run_download_with_fake_sockseek(tmp_path):
    exe = tmp_path / "sockseek"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    items = [
        DownloadItem(SpotifyTrack("1", "Strobe - Radio Edit", ["deadmau5"], duration=210)),
        DownloadItem(SpotifyTrack("2", "Nope", ["Nobody"], duration=100)),
        DownloadItem(SpotifyTrack("3", "Glue", ["Bicep"], duration=269, album="Bicep", year="2017")),
    ]
    out = tmp_path / "_unsorted"
    events = []
    summary = run_download(items, str(out), "user", "secret", exe=str(exe),
                           work_dir=str(tmp_path / "work"), on_event=events.append)
    assert [i for i, _ in summary.failed] == [1]
    done = dict(summary.done)
    assert sorted(done) == [0, 2]
    assert os.path.basename(done[0]) == "deadmau5 - Strobe (Radio Edit).mp3"
    assert os.path.dirname(done[0]) == str(out)
    assert sorted(os.listdir(out)) == ["Bicep - Glue.mp3", "deadmau5 - Strobe (Radio Edit).mp3"]
    assert not os.path.exists(tmp_path / "work" / "sockseek.conf")     # parola ștearsă
    kinds = [e.kind for e in events]
    assert "searching" in kinds and "downloading" in kinds and "progress" in kinds


def test_build_command_quality():
    c = build_command("ss", "l.csv", "out", "c.conf", "mp3_320_strict")
    assert c[c.index("--min-bitrate") + 1] == "315"
    c = build_command("ss", "l.csv", "out", "c.conf", "flac_first")
    assert c[c.index("--pref-format") + 1] == "flac"
    assert "--progress-json" in c and "--no-skip-existing" in c


def test_event_parser():
    items = [DownloadItem(SpotifyTrack("1", "Song", ["Artist"]))]
    p = EventParser(items)
    ev = p.parse(json.dumps({"type": "track_state", "data": {
        "artist": "artist", "title": "song", "terminalOutcome": "Failed", "failureReason": "NoResults"}}))
    assert ev.kind == "failed" and ev.index == 0 and "negăsită" in ev.text
    assert p.parse("some log line").kind == "log"


def test_pretty_title():
    assert pretty_title("Strobe - Radio Edit") == "Strobe (Radio Edit)"
    assert pretty_title("Glue") == "Glue"
