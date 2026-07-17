from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voice_input.app import VoiceInputApp


def main() -> int:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "test-output/voice-input-ui.png")
    selected_tab = sys.argv[2] if len(sys.argv) > 2 else "dictation"
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "VOICE_INPUT_DATA_DIR",
        str(output.parent / "render-app-data"),
    )

    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    controller = VoiceInputApp(application, start_minimized=False)

    attempts = 0

    def capture() -> None:
        application.processEvents()
        target = (
            controller.overlay
            if selected_tab in {"overlay", "overlay-long"}
            else controller.window
        )
        if not target.grab().save(str(output)):
            print(f"Не удалось сохранить {output}", file=sys.stderr)
            controller.exit_app()
            return
        print(output.resolve())
        controller.exit_app()

    def capture_when_ready() -> None:
        nonlocal attempts
        attempts += 1
        if controller.state == "ready" or attempts >= 120:
            if selected_tab == "dictation":
                controller.mode_combo.setCurrentText(
                    "Общение — близко к оригиналу"
                )
            if selected_tab == "settings":
                controller.tabs.setCurrentIndex(1)
            if selected_tab == "ai":
                controller.mode_combo.setCurrentText(
                    "Промпт для AI — структурированная задача"
                )
                controller.target_combo.setCurrentText("ChatGPT")
            if selected_tab in {"overlay", "overlay-long"}:
                controller.window.hide()
                controller.voice_level.reset()
                for level in (0.1, 0.25, 0.55, 0.9, 0.45, 0.7, 0.2):
                    controller.voice_level.set_level(level)
                if selected_tab == "overlay-long":
                    controller._set_overlay_preview(
                        "Проверяю микрофон — живой черновик появляется здесь "
                        "во время разговора и занимает не больше двух строк."
                    )
                controller._show_overlay(
                    "Общение · идёт запись",
                    "#EF476F",
                )
            else:
                controller.window.show()
                controller.window.raise_()
            QTimer.singleShot(250, capture)
            return
        QTimer.singleShot(100, capture_when_ready)

    QTimer.singleShot(100, capture_when_ready)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
