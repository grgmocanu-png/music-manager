"""Regula din Windows Firewall care permite Soulseek-ului să primească conexiuni.

Pe Soulseek, de multe ori celălalt utilizator se conectează la tine ca să-ți trimită
fișierul. Dacă Windows blochează programul de download, multe transferuri eșuează
(„toate încercările de descărcare au eșuat”).
"""
from __future__ import annotations

import subprocess
import sys

RULE_NAME = "Music Manager - Soulseek"
LISTEN_PORT = 49998


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                          creationflags=0x08000000 if sys.platform == "win32" else 0)


def rule_exists() -> bool | None:
    """True / False pe Windows; None pe alte sisteme."""
    if sys.platform != "win32":
        return None
    p = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_NAME}"])
    return p.returncode == 0 and RULE_NAME.lower() in p.stdout.decode("utf-8", "ignore").lower()


def add_rule(program: str) -> bool:
    """Cere drepturi de administrator (fereastra Windows „Da/Nu”) și adaugă regula."""
    if sys.platform != "win32":
        return False
    prog = program.replace("'", "''")
    inner = (f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Inbound -Program '{prog}' "
             f"-Action Allow -Profile Any | Out-Null; "
             f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Inbound -Protocol TCP "
             f"-LocalPort {LISTEN_PORT} -Action Allow -Profile Any | Out-Null")
    # pornim un PowerShell cu drepturi de administrator (Windows arată fereastra „Da / Nu”)
    encoded = __import__("base64").b64encode(inner.encode("utf-16-le")).decode()
    ps = ["powershell", "-NoProfile", "-Command",
          f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
          f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'"]
    p = _run(ps)
    return p.returncode == 0 and bool(rule_exists())
