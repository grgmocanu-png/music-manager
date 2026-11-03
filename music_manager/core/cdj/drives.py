"""Stickurile pe care avem voie să scriem și formatarea lor.

Reguli (toate trebuie să treacă):
  * apar doar stickuri și discuri externe (USB); discul calculatorului nu apare deloc;
  * sistemul de fișiere e FAT32 (citit de toate CDJ-urile) sau exFAT (doar playerele noi);
  * stickul e gol, e deja stick de DJ (are PIONEER/) sau a fost scris de Manager — altfel refuzăm
    (îți propunem formatarea), ca să nu amestecăm piesele cu alte fișiere ale tale;
  * ștergem doar fișiere scrise de Manager, și doar când bifezi asta.
Formatarea: FAT32, cu confirmare în care tastezi numele stickului. Pe Windows, FAT32 merge până la 32 GB
(limita Windows); peste, îți spunem ce să faci. Pe Mac, orice mărime.
"""
from __future__ import annotations

import os
import string
import subprocess
import sys
from dataclasses import dataclass

FAT32_WIN_LIMIT = 32 * 1024 ** 3 + 512 * 1024 ** 2     # „32 GB” de pe ambalaj ≈ 31,x GiB; lăsăm o marjă


@dataclass
class Drive:
    root: str            # „E:\\” sau „/Volumes/USB”
    label: str
    fs: str              # FAT32 / exFAT / NTFS / APFS …
    size: int
    free: int
    kind: str = "stick"  # stick / disc extern

    @property
    def title(self) -> str:
        gb = self.size / 1024 ** 3
        name = self.label or "fără nume"
        return f"{self.root.rstrip(os.sep) if sys.platform == 'win32' else self.root}  {name}  ·  {self.fs}  ·  " \
               f"{gb:.1f} GB ({self.free / 1024 ** 3:.1f} GB liberi)"

    @property
    def fat32(self) -> bool:
        return self.fs.upper() in ("FAT32", "FAT", "MSDOS", "VFAT")

    @property
    def exfat(self) -> bool:
        return self.fs.upper() == "EXFAT"


# --- lista ----------------------------------------------------------------------------------
def list_drives() -> list[Drive]:
    try:
        if sys.platform == "win32":
            return _win_drives()
        if sys.platform == "darwin":
            return _mac_drives()
    except Exception:  # noqa: BLE001
        return []
    return []


def _win_drives() -> list[Drive]:
    import ctypes
    k32 = ctypes.windll.kernel32
    system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
    out = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if root.upper() == system or not os.path.exists(root):
            continue
        t = k32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if t == 2:                                    # DRIVE_REMOVABLE: stick, card
            kind = "stick"
        elif t == 3 and _win_is_usb(letter):          # DRIVE_FIXED, dar pe USB: disc extern
            kind = "disc extern"
        else:
            continue
        name, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
        if not k32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, None, None, None, fs, 261):
            continue
        free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
        k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(root), ctypes.byref(free), ctypes.byref(total), None)
        out.append(Drive(root, name.value, fs.value, total.value, free.value, kind))
    return out


def _win_is_usb(letter: str) -> bool:
    """Discul e pe USB? (IOCTL_STORAGE_QUERY_PROPERTY → BusType 7 = USB)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)
    if h in (-1, 0, ctypes.c_void_p(-1).value):
        return False
    try:
        query = (ctypes.c_ubyte * 12)()        # PropertyId=0 (StorageDeviceProperty), QueryType=0
        buf = (ctypes.c_ubyte * 1024)()
        got = wintypes.DWORD()
        ok = k32.DeviceIoControl(h, 0x002D1400, query, 12, buf, 1024, ctypes.byref(got), None)
        if not ok or got.value < 32:
            return False
        bus_type = buf[28]                     # STORAGE_DEVICE_DESCRIPTOR.BusType
        return bus_type == 7
    finally:
        k32.CloseHandle(h)


def _mac_drives() -> list[Drive]:
    import plistlib
    out = []
    for name in sorted(os.listdir("/Volumes")):
        root = os.path.join("/Volumes", name)
        if not os.path.ismount(root) or os.path.realpath(root) == "/":
            continue
        r = subprocess.run(["diskutil", "info", "-plist", root], capture_output=True)
        if r.returncode != 0:
            continue
        info = plistlib.loads(r.stdout)
        external = (not info.get("Internal", True)) or info.get("RemovableMediaOrExternalDevice", False)
        if not external or info.get("SystemImage"):
            continue
        fs = (info.get("FilesystemType") or info.get("FilesystemName") or "").lower()
        fs = {"msdos": "FAT32", "exfat": "exFAT", "apfs": "APFS", "hfs": "HFS+"}.get(fs, fs.upper())
        try:
            st = os.statvfs(root)
            size, free = st.f_frsize * st.f_blocks, st.f_frsize * st.f_bavail
        except OSError:
            size, free = int(info.get("TotalSize", 0)), int(info.get("FreeSpace", 0))
        out.append(Drive(root, name, fs, size, free,
                         "stick" if info.get("RemovableMedia") or info.get("Removable") else "disc extern"))
    return out


# --- verificarea -------------------------------------------------------------------------------
def check(drive: Drive) -> tuple[bool, str]:
    """(se poate scrie?, de ce / ce să faci)."""
    if not (drive.fat32 or drive.exfat):
        return False, (f"Stickul e {drive.fs or 'neformatat'}. CDJ-urile citesc FAT32 (exFAT doar playerele noi). "
                       "Formatează-l ca stick de DJ.")
    try:
        entries = [e for e in os.listdir(drive.root)
                   if not e.startswith(".") and e not in ("System Volume Information", "$RECYCLE.BIN")]
    except OSError as e:
        return False, f"Nu pot citi stickul: {e.strerror or e}"
    from .stick import MANIFEST_REL
    if entries and "PIONEER" not in entries and not os.path.exists(os.path.join(drive.root, MANIFEST_REL)):
        return False, ("Stickul are alte fișiere și nu e stick de DJ. Ca să nu le amestec cu piesele, alege alt "
                       "stick sau formatează-l (se șterge tot de pe el).")
    note = ""
    if drive.exfat:
        note = " Atenție: exFAT merge doar pe playerele noi (CDJ-3000, XDJ-AZ); CDJ-350 / 2000NXS2 vor FAT32."
    return True, ("Stick gol, bun de scris." if not entries else "Stick de DJ, îl actualizez.") + note


# --- formatarea --------------------------------------------------------------------------------
def clean_label(label: str) -> str:
    keep = "".join(c for c in (label or "").upper() if c.isascii() and (c.isalnum() or c in "_-"))
    return keep[:11] or "DJ"


def can_format(drive: Drive) -> tuple[bool, str]:
    if sys.platform == "win32" and drive.size > FAT32_WIN_LIMIT:
        return False, ("Windows nu formatează FAT32 stickuri mai mari de 32 GB. Variante: un stick de cel mult "
                       "32 GB, formatarea pe un Mac (Utilitar disc → MS-DOS (FAT)), sau exFAT dacă mergi doar pe "
                       "playere noi (CDJ-3000).")
    if sys.platform not in ("win32", "darwin"):
        return False, "Formatarea merge doar pe Windows și Mac."
    return True, ""


def format_fat32(drive: Drive, label: str) -> None:
    """Formatează stickul FAT32 (șterge tot de pe el). Pe Windows cere drept de administrator (apare fereastra
    Windows de confirmare). Ridică RuntimeError dacă nu merge."""
    ok, why = can_format(drive)
    if not ok:
        raise RuntimeError(why)
    # verificăm încă o dată, chiar înainte, că e tot un stick / disc extern din listă (nu discul calculatorului)
    if not any(d.root == drive.root for d in list_drives()):
        raise RuntimeError("Stickul nu mai e în lista de stickuri (l-ai scos?).")
    lab = clean_label(label)
    if sys.platform == "win32":
        letter = drive.root[0]
        if letter.upper() not in string.ascii_uppercase:
            raise RuntimeError("literă de disc greșită")
        cmd = (f"Format-Volume -DriveLetter {letter} -FileSystem FAT32 -NewFileSystemLabel '{lab}' "
               "-Force -Confirm:$false -ErrorAction Stop | Out-Null")
        _win_run_elevated("powershell.exe", f'-NoProfile -NonInteractive -WindowStyle Hidden -Command "{cmd}"')
    else:
        r = subprocess.run(["diskutil", "eraseVolume", "MS-DOS FAT32", lab, drive.root], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "ignore").strip()[-300:])


def _win_run_elevated(exe: str, params: str, timeout_ms: int = 10 * 60 * 1000) -> None:
    """Rulează cu drept de administrator (fereastra Windows de confirmare) și așteaptă să termine."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040                  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = 0                           # fără fereastră de consolă
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError("Formatarea a fost anulată (n-ai dat voie în fereastra Windows).")
    k32 = ctypes.windll.kernel32
    if k32.WaitForSingleObject(info.hProcess, timeout_ms) != 0:
        k32.CloseHandle(info.hProcess)
        raise RuntimeError("Formatarea durează prea mult.")
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    k32.CloseHandle(info.hProcess)
    if code.value != 0:
        raise RuntimeError(f"Formatarea n-a mers (cod {code.value}). Închide programele care folosesc stickul și "
                           "încearcă din nou.")
