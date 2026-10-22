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
    if os.environ.get("MM_SMOKE_TEST"):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
