from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voice_input.windows import (
    KEYEVENTF_KEYUP,
    VK_CONTROL,
    GlobalHotkey,
    _keyboard_input,
    _send_inputs,
    insert_text,
)

VK_SHIFT = 0x10
VK_F9 = 0x78


def main() -> int:
    application = QApplication([])
    window = QWidget()
    window.setWindowTitle("Rechka Windows smoke test")
    layout = QVBoxLayout(window)
    field = QLineEdit()
    layout.addWidget(field)
    window.resize(420, 90)
    window.show()
    window.raise_()
    window.activateWindow()
    field.setFocus()

    hotkey_triggered = threading.Event()
    hotkey = GlobalHotkey()
    hotkey.start("Ctrl+Shift+F9", hotkey_triggered.set)

    def type_text() -> None:
        window.raise_()
        window.activateWindow()
        field.setFocus()
        insert_text("Привет, Windows!", "type")

    def press_hotkey() -> None:
        _send_inputs(
            [
                _keyboard_input(VK_CONTROL),
                _keyboard_input(VK_SHIFT),
                _keyboard_input(VK_F9),
                _keyboard_input(VK_F9, flags=KEYEVENTF_KEYUP),
                _keyboard_input(VK_SHIFT, flags=KEYEVENTF_KEYUP),
                _keyboard_input(VK_CONTROL, flags=KEYEVENTF_KEYUP),
            ]
        )

    def finish() -> None:
        payload = {
            "typed_text": field.text(),
            "typing_ok": field.text() == "Привет, Windows!",
            "hotkey_ok": hotkey_triggered.is_set(),
        }
        print(json.dumps(payload, ensure_ascii=False))
        hotkey.stop()
        application.quit()

    QTimer.singleShot(400, type_text)
    QTimer.singleShot(850, press_hotkey)
    QTimer.singleShot(1400, finish)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
