"""Testăm integrarea cu Sockseek folosind un „Sockseek fals” care scrie aceleași evenimente."""
import json
import os
import stat
import sys
import textwrap

import pytest

from music_manager.core.soulseek import (
    STOP_AFTER_CURRENT, STOP_NOW, DownloadItem, EventParser, build_command, pretty_title,
    run_download,
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
    assert [f[0] for f in summary.failed] == [1]
    assert summary.failed[0][2] == "NoSuitableFileFound"
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


SLOW = textwrap.dedent('''\
    #!{python}
    import csv, json, os, sys, time
    args = sys.argv[1:]
    out = args[args.index("-o") + 1]
    rows = list(csv.DictReader(open(args[0], encoding="utf-8")))
    def ev(t, **d):
        print(json.dumps({{"type": t, "data": d}}), flush=True)
    for r in rows:
        ev("search_start", artist=r["Artist"], title=r["Title"])
        ev("download_start", artist=r["Artist"], title=r["Title"], username="u", filename="x.mp3")
        p = os.path.join(out, r["Title"] + ".mp3")
        open(p + ".incomplete", "wb").write(b"half")
        time.sleep(1.0)
        os.replace(p + ".incomplete", p)
        ev("track_state", artist=r["Artist"], title=r["Title"], terminalOutcome="Succeeded", downloadPath=p)
''')


def _slow_exe(tmp_path):
    exe = tmp_path / "sockseek"
    exe.write_text(SLOW.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def _items(n):
    return [DownloadItem(SpotifyTrack(str(i), f"T{i}", ["A"], duration=100)) for i in range(n)]


@pytest.mark.skipif(sys.platform == "win32", reason="scriptul fals folosește shebang")
def test_stop_now_removes_partial_file(tmp_path):
    out = tmp_path / "o"
    events = []
    state = {"stop": None}

    def on_event(e):
        events.append(e)
        if e.kind == "downloading":
            state["stop"] = STOP_NOW

    s = run_download(_items(3), str(out), "u", "p", exe=_slow_exe(tmp_path),
                     work_dir=str(tmp_path / "w"), on_event=on_event, cancelled=lambda: state["stop"])
    assert s.cancelled and not s.done and s.stopped_now == [0]
    assert not [f for f in os.listdir(out) if f.endswith(".incomplete")]
    assert not [f for f in os.listdir(out) if f.endswith(".mp3")]


@pytest.mark.skipif(sys.platform == "win32", reason="scriptul fals folosește shebang")
def test_stop_after_current_finishes_file(tmp_path):
    out = tmp_path / "o"
    state = {"stop": None}

    def on_event(e):
        if e.kind == "downloading" and e.index == 0:
            state["stop"] = STOP_AFTER_CURRENT

    s = run_download(_items(3), str(out), "u", "p", exe=_slow_exe(tmp_path),
                     work_dir=str(tmp_path / "w"), on_event=on_event, cancelled=lambda: state["stop"])
    assert s.cancelled
    assert [i for i, _ in s.done] == [0]
    files = os.listdir(out)
    assert len([f for f in files if f.endswith(".mp3")]) == 1
    assert not [f for f in files if f.endswith(".incomplete")]


def test_parse_search_output():
    from music_manager.core.soulseek import parse_search_output
    out = 'Logging in...\n[{"User":{"Username":"bob","UploadSpeed":1.5,"HasFreeUploadSlot":true},' \
          '"File":{"Length":300,"Filename":"@@abc\\\\Music\\\\Artist - Song.mp3","Size":12000000,"Bitrate":320}}]\n'
    r = parse_search_output(out)
    assert len(r) == 1 and r[0].username == "bob" and r[0].bitrate == 320 and r[0].free_slot
    assert r[0].name == "Artist - Song.mp3" and r[0].ext == "mp3"
    assert r[0].link == "slsk://bob/@@abc/Music/Artist - Song.mp3"


STAGED_FAKE = textwrap.dedent('''\
    #!{python}
    import csv, json, os, sys
    args = sys.argv[1:]
    out = args[args.index("-o") + 1]
    rows = list(csv.DictReader(open(args[0], encoding="utf-8")))
    def ev(t, **d):
        print(json.dumps({{"type": t, "data": d}}), flush=True)
    for r in rows:
        ev("search_start", artist=r["Artist"], title=r["Title"])
        # pe „Soulseek” există doar fișierul fără artist: îl găsim doar căutând numai titlul
        if r["Artist"] or "kalbata" not in r["Title"]:
            ev("track_state", artist=r["Artist"], title=r["Title"], terminalOutcome="Failed",
               failureReason="NoResults")
            continue
        name = "Amsterdam Experience kalbata Edit.mp3"
        open(os.path.join(out, name), "wb").write(b"fake")
        # cale relativă, ca la unele versiuni Sockseek
        ev("track_state", artist=r["Artist"], title=r["Title"], terminalOutcome="Succeeded",
           downloadPath=name, bitRate=320, extension="mp3")
''')


@pytest.mark.skipif(sys.platform == "win32", reason="scriptul fals folosește shebang")
def test_staged_download_title_only_and_rename(tmp_path):
    from music_manager.core.soulseek import run_download_staged
    exe = tmp_path / "sockseek"
    exe.write_text(STAGED_FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    items = [DownloadItem(SpotifyTrack("1", "Amsterdam Experience - Kalbata Edit", ["Shimshon Miel"], duration=300)),
             DownloadItem(SpotifyTrack("2", "Nothing Here", ["Nobody"], duration=200))]
    out = tmp_path / "_unsorted"
    events = []
    s = run_download_staged(items, str(out), "user", "secret", exe=str(exe), work_dir=str(tmp_path / "w"),
                            on_event=events.append)
    done = dict(s.done)
    assert list(done) == [0]
    assert os.path.basename(done[0]) == "Shimshon Miel - Amsterdam Experience (Kalbata Edit).mp3"
    assert [f[0] for f in s.failed] == [1]
    # un singur „failed” final pentru piesa negăsită, nu câte unul la fiecare pas
    assert [e.index for e in events if e.kind == "failed"] == [1]


def test_build_command_lossless_fallback():
    c = build_command("ss", "l.csv", "out", "c.conf", "mp3_320_lossless")
    assert c[c.index("--format") + 1] == "mp3,flac,aiff,aif,wav"
    assert c[c.index("--pref-format") + 1] == "mp3" and c[c.index("--min-bitrate") + 1] == "315"
