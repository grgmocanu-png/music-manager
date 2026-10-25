"""Pornirea aplicației."""
from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
    from PySide6.QtCore import QCoreApplication, QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME

    QCoreApplication.setOrganizationName("MusicManager")
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    from PySide6.QtCore import QSettings
    from .ui.theme import apply_theme
    apply_theme(app, QSettings().value("ui/theme", "dark") or "dark")
    from PySide6.QtGui import QIcon
    for base in (getattr(sys, "_MEIPASS", ""), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging")):
        icon = os.path.join(base, "icon.png")
        if base and os.path.exists(icon):
            app.setWindowIcon(QIcon(icon))
            break

    log_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "erori.log")

    def excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        box = QMessageBox(QMessageBox.Critical, "A apărut o eroare",
                          f"Ceva n-a mers bine: {exc}\n\nDetaliile au fost salvate în:\n{log_path}\n"
                          "Trimite-i-le lui Claude ca să repare problema.")
        box.setDetailedText(text)
        box.exec()

    sys.excepthook = excepthook

    from .ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    if os.environ.get("MM_DIAG_ANALYZE"):
        from . import diag
        return diag.run(app, win, os.environ["MM_DIAG_ANALYZE"], os.environ.get("MM_DIAG_OUT", "diag.json"))
    if os.environ.get("MM_SMOKE_TEST"):
        # verificăm că bibliotecile încărcate „la nevoie” există și în aplicația împachetată
        import mutagen  # noqa: F401
        import yt_dlp
        from .core.convert import ffmpeg_path
        yt_dlp.YoutubeDL({"quiet": True})
        if not ffmpeg_path():
            return 3
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
