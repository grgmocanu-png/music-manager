# Music Manager

Aplicație desktop pentru gestionarea muzicii de mix: descărcare din playlisturi Spotify prin Soulseek, curățenie (dubluri, nume, taguri), conversie și bibliotecă cu filtre pentru DJ.

## Descărcare (Windows)

Ultima versiune e mereu aici: **[Releases → latest](../../releases/latest)**

- **MusicManager-Setup.exe** — installer obișnuit (recomandat). La prima pornire, Windows poate afișa „Windows protected your PC”: apasă *More info* → *Run anyway* (aplicația nu e semnată digital).
- **MusicManager-portabil.zip** — fără instalare: dezarhivezi și pornești `MusicManager.exe`.

## Ce face

**Fișiere**
- **Download** — playlisturi Spotify salvate ca butoane, cu data ultimului update. Verifică ce lipsește din muzica ta și descarcă de pe Soulseek în folderul ales (ex. `_unsorted`), cu nume și taguri curate din Spotify.
- **Curățenie** — dubluri (mutate în `_Dubluri`, nu șterse), redenumire după un model ales, cu anulare.
- **Conversie** — FLAC/WAV → MP3 320, WAV, AIFF, FLAC, păstrând tagurile și coperta.

**Bibliotecă**
- **Piese** — toată muzica din folderele alese, cu căutare.
- **Playlisturi** — filtre după gen, an, BPM, tonalitate (Camelot, mixaj armonic), ordonare pentru mix, salvare M3U, copiere pe stick.

## Componente incluse

- [Sockseek](https://github.com/fiso64/sockseek) (fostul sldl) — descărcarea de pe Soulseek, program separat, licență AGPL-3.0.
- [FFmpeg](https://ffmpeg.org) prin imageio-ffmpeg — conversia audio.
- Qt / PySide6, mutagen.
