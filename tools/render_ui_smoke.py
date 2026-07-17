from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QWidget,
)

from voice_input import __version__  # noqa: E402
from voice_input.app import (  # noqa: E402
    ACID,
    RECORD,
    SUCCESS,
    ScrollSafeComboBox,
    VoiceInputApp,
)


class RenderApp(VoiceInputApp):
    """Side-effect-free application shell for deterministic UI screenshots."""

    def _start_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.window)

    def _register_hotkey(
        self,
        value: str | None = None,
        *,
        interactive: bool = False,
    ) -> bool:
        self._registered_hotkey = value or self.config.hotkey
        return True

    def _refresh_devices(self) -> None:
        self.devices = []
        self.device_combo.clear()
        self.device_combo.addItem("Системный микрофон по умолчанию", None)

    def _load_model(self, model_name: str) -> None:
        self._model_generation += 1
        self.state = "ready"
        self.progress.hide()
        self.record_button.setText("Начать запись")
        self.record_button.setEnabled(True)
        self._style_primary_button(self.record_button, ACID)
        self._set_status("Готово к диктовке", SUCCESS)
        if not self.config.onboarding_complete and not self.start_minimized:
            self.tabs.setCurrentIndex(1)


def render(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rechka-ui-smoke-") as data_dir:
        os.environ["VOICE_INPUT_DATA_DIR"] = data_dir
        qt = QApplication.instance() or QApplication([])
        app = RenderApp(qt)
        app.timer.stop()
        app.window.resize(760, 760)
        app.window.show()

        screenshots: dict[str, str] = {}
        main_tab_bar = app.tabs.tabBar()
        main_tab_geometry = {
            "bar_width": main_tab_bar.width(),
            "bar_x": main_tab_bar.mapTo(
                app.window, main_tab_bar.rect().topLeft()
            ).x(),
            "tabs": [
                {
                    "x": main_tab_bar.tabRect(index).x(),
                    "width": main_tab_bar.tabRect(index).width(),
                    "right": main_tab_bar.tabRect(index).right() + 1,
                }
                for index in range(main_tab_bar.count())
            ],
        }

        def capture(name: str) -> None:
            qt.processEvents()
            target = output_dir / f"ui-{__version__}-{name}.png"
            if not app.window.grab().save(str(target), "PNG"):
                raise RuntimeError(f"Не удалось сохранить {target}")
            screenshots[name] = str(target.resolve())

        def capture_overlay(name: str) -> dict[str, int]:
            qt.processEvents()
            target = output_dir / f"ui-{__version__}-{name}.png"
            if not app.overlay.grab().save(str(target), "PNG"):
                raise RuntimeError(f"Не удалось сохранить {target}")
            screenshots[name] = str(target.resolve())
            return {
                "width": app.overlay.width(),
                "height": app.overlay.height(),
            }

        capture("onboarding")
        app.tabs.setCurrentIndex(0)
        qt.processEvents()
        app._resize_result_editor()
        qt.processEvents()
        initial_result_height = app.result_tabs.height()
        capture("dictation")
        app.state = "recording"
        app._set_main_recording_feedback(True)
        app._set_status("Общение: идёт запись", RECORD)
        app.main_record_elapsed.setText("00:18")
        app.record_button.setText("Остановить запись")
        app._style_primary_button(app.record_button, RECORD)
        for level in (0.04, 0.08, 0.16, 0.28, 0.18, 0.34):
            app.main_voice_level.set_level(level)
        capture("recording-state")
        app.overlay_elapsed.setText("00:18")
        app.overlay_audio_state.setText("Голос слышу · звук записывается")
        app._show_overlay(
            "Общение · идёт запись",
            RECORD,
        )
        overlay_geometry = capture_overlay("recording-overlay")
        if not app.overlay_preview.isHidden():
            raise RuntimeError("Пустой живой черновик занимает место в плашке")
        app._set_overlay_preview(
            "Проверяю микрофон: этот аккуратный живой черновик появляется "
            "только после распознавания речи."
        )
        app._position_overlay()
        preview_overlay_geometry = capture_overlay("recording-overlay-preview")
        app.overlay.hide()
        app.state = "ready"
        app._set_main_recording_feedback(False)
        app._set_status("Готово к диктовке", SUCCESS)
        app.record_button.setText("Начать запись")
        app._style_primary_button(app.record_button, ACID)
        app.last_text.setPlainText(
            "\n".join(
                f"Строка {index}: результат должен увеличивать поле по содержимому."
                for index in range(1, 13)
            )
        )
        qt.processEvents()
        app._resize_result_editor()
        qt.processEvents()
        expanded_result_height = app.result_tabs.height()
        capture("dictation-expanded")
        app.last_text.setPlainText("\n".join("Очень длинный текст" for _ in range(80)))
        qt.processEvents()
        app._resize_result_editor()
        qt.processEvents()
        capped_result_height = app.result_tabs.height()
        app.last_text.clear()
        qt.processEvents()
        app._resize_result_editor()

        dictation_widget = app.tabs.currentWidget()
        dictation_scroll = (
            dictation_widget
            if isinstance(dictation_widget, QScrollArea)
            else dictation_widget.findChild(QScrollArea)
        )
        if dictation_scroll is None:
            raise RuntimeError("Не найдена прокрутка диктовки")

        app.tabs.setCurrentIndex(2)
        capture("history")
        app.tabs.setCurrentIndex(1)
        settings_widget = app.tabs.currentWidget()
        scroll = (
            settings_widget
            if isinstance(settings_widget, QScrollArea)
            else settings_widget.findChild(QScrollArea)
        )
        if scroll is None:
            raise RuntimeError("Не найдена прокрутка настроек")
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("personalization")

        def button_geometry(text: str) -> dict[str, int]:
            button = next(
                item
                for item in app.window.findChildren(QPushButton)
                if item.text() == text
            )
            top_left = button.mapTo(app.window, button.rect().topLeft())
            return {
                "x": top_left.x(),
                "y": top_left.y(),
                "width": button.width(),
                "height": button.height(),
                "right": top_left.x() + button.width(),
            }

        result_actions = {
            "repeat": button_geometry("Повторить"),
            "undo": button_geometry("Отменить вставку"),
            "quick_action": button_geometry("Применить"),
        }

        class WheelProbe:
            ignored = False

            def ignore(self) -> None:
                self.ignored = True

        wheel_probe = WheelProbe()
        app.device_combo.wheelEvent(wheel_probe)
        settings_geometry = {
            "viewport_width": scroll.viewport().width(),
            "content_width": scroll.widget().width(),
            "horizontal_overflow": scroll.horizontalScrollBar().maximum(),
            "save_button": button_geometry("Сохранить"),
            "widest_children": sorted(
                (
                    {
                        "class": item.metaObject().className(),
                        "minimum_hint": item.minimumSizeHint().width(),
                        "size_hint": item.sizeHint().width(),
                        "text": (
                            item.text()[:80]
                            if hasattr(item, "text")
                            and isinstance(item.text(), str)
                            else ""
                        ),
                        "child_texts": [
                            child.text()[:60]
                            for child in item.children()
                            if isinstance(child, (QLabel, QCheckBox, QPushButton))
                            and child.text()
                        ],
                    }
                    for item in scroll.widget().findChildren(QWidget)
                ),
                key=lambda item: item["minimum_hint"],
                reverse=True,
            )[:12],
        }

        app.window.resize(app.window.minimumWidth(), app.window.minimumHeight())
        qt.processEvents()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("personalization-minimum")
        settings_geometry["minimum_size"] = {
            "window_width": app.window.width(),
            "viewport_width": scroll.viewport().width(),
            "content_width": scroll.widget().width(),
            "horizontal_overflow": scroll.horizontalScrollBar().maximum(),
            "save_button": button_geometry("Сохранить"),
        }
        if settings_geometry["horizontal_overflow"] != 0:
            raise RuntimeError("Настройки переполняются по горизонтали при 760 px")
        if settings_geometry["minimum_size"]["horizontal_overflow"] != 0:
            raise RuntimeError("Настройки переполняются на минимальной ширине")
        if any(item["right"] > 760 for item in result_actions.values()):
            raise RuntimeError(
                "Действия результата выходят за границы окна: "
                f"{result_actions}"
            )
        if dictation_scroll.horizontalScrollBar().maximum() != 0:
            widest = sorted(
                (
                    (
                        item.metaObject().className(),
                        item.minimumSizeHint().width(),
                        item.sizeHint().width(),
                    )
                    for item in dictation_scroll.widget().findChildren(QWidget)
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:8]
            raise RuntimeError(
                "Диктовка переполняется по горизонтали: "
                f"overflow={dictation_scroll.horizontalScrollBar().maximum()}, "
                f"widest={widest}"
            )
        if not wheel_probe.ignored:
            raise RuntimeError("Колесо мыши меняет закрытый список микрофонов")
        main_tab_widths = [
            item["width"] for item in main_tab_geometry["tabs"]
        ]
        if max(main_tab_widths) - min(main_tab_widths) > 1:
            raise RuntimeError("Главные вкладки имеют разную ширину")
        if (
            main_tab_geometry["bar_x"] != 0
            or main_tab_geometry["tabs"][0]["x"] != 0
            or main_tab_geometry["tabs"][-1]["right"]
            < main_tab_geometry["bar_width"] - 1
        ):
            raise RuntimeError("Главные вкладки не заполняют навигационную полосу")
        if not initial_result_height < expanded_result_height <= capped_result_height:
            raise RuntimeError("Поле результата не растёт вместе с текстом")
        if capped_result_height > 410:
            raise RuntimeError("Поле результата превысило безопасную высоту")
        if app.config.hotkey != "Ctrl+Space":
            raise RuntimeError("Горячая клавиша по умолчанию должна быть Ctrl+Space")
        if app.config.model != "base" or app.config.decoding_mode != "balanced":
            raise RuntimeError("Профиль по умолчанию должен быть Base + Баланс")
        if "Гастроконсьерж" in app.custom_terms_edit.placeholderText():
            raise RuntimeError("В подсказке словаря осталось название другого проекта")
        if overlay_geometry["height"] > 150:
            raise RuntimeError("Плашка записи осталась слишком высокой")
        if (
            preview_overlay_geometry["height"] <= overlay_geometry["height"]
            or preview_overlay_geometry["height"] > 200
        ):
            raise RuntimeError(
                "Живой черновик имеет неправильную высоту: "
                f"{preview_overlay_geometry}"
            )
        if not isinstance(app.overlay_preview, QLabel):
            raise RuntimeError("Живой черновик должен быть лёгкой текстовой строкой")

        result = {
            "window": {
                "render_width": 760,
                "render_height": 760,
                "minimum_width": app.window.minimumWidth(),
                "minimum_height": app.window.minimumHeight(),
            },
            "tabs": [app.tabs.tabText(index) for index in range(app.tabs.count())],
            "main_navigation": main_tab_geometry,
            "screenshots": screenshots,
            "history_enabled_by_default": app.config.history_enabled,
            "onboarding_visible": not app.config.onboarding_complete,
            "settings_geometry": settings_geometry,
            "result_actions": result_actions,
            "dictation_geometry": {
                "horizontal_overflow": (
                    dictation_scroll.horizontalScrollBar().maximum()
                ),
                "initial_result_height": initial_result_height,
                "expanded_result_height": expanded_result_height,
                "capped_result_height": capped_result_height,
            },
            "recording_overlay": overlay_geometry,
            "recording_overlay_with_preview": preview_overlay_geometry,
            "default_hotkey": app.config.hotkey,
            "default_recognition_profile": {
                "model": app.config.model,
                "decoding_mode": app.config.decoding_mode,
            },
            "recording_hotkey_hint": app.overlay_hint.text(),
            "custom_terms_placeholder": app.custom_terms_edit.placeholderText(),
            "microphone_wheel_safe": (
                isinstance(app.device_combo, ScrollSafeComboBox)
                and wheel_probe.ignored
            ),
        }
        app.exit_app()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Снять UI-smoke Речки без записи")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = render(args.output_dir.resolve())
    report = args.output_dir.resolve() / f"ui-{__version__}-report.json"
    report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
