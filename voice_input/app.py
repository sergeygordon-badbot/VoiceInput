from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QKeySequenceEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .actions import parse_action_command
from .backup import export_personalization, import_personalization
from .audio import (
    AudioClip,
    AudioRecorder,
    AudioQuality,
    analyze_audio,
    has_recordable_signal,
    list_input_devices,
)
from .config import (
    AI_TARGET_OPTIONS,
    DECODING_BEAM_SIZES,
    DECODING_OPTIONS,
    INSERTION_OPTIONS,
    LANGUAGE_OPTIONS,
    MODEL_OPTIONS,
    OUTPUT_MODE_OPTIONS,
    AppConfig,
    load_config,
    save_config,
)
from .engine import (
    WhisperEngine,
    is_reliable_preview_text,
    merge_incremental_transcript,
    normalize_transcript,
)
from .diagnostics import collect_diagnostics
from .history import append_history, clear_history, load_history
from .hotkeys import HOTKEY_OPTIONS, hotkey_label, parse_hotkey
from .personalization import (
    combine_custom_terms,
    expand_snippet,
    match_app_profile,
    parse_app_profiles,
    parse_snippets,
)
from .prompting import ProcessedText, process_transcript
from .quick_actions import QUICK_ACTION_OPTIONS, apply_quick_action
from .updater import (
    UpdateInfo,
    check_for_update,
    configured_repository,
    download_update,
    launch_update_installer,
)
from .windows import (
    GlobalHotkey,
    autostart_command,
    consume_show_settings_event,
    foreground_window,
    insert_text,
    play_feedback,
    send_enter,
    send_undo,
    set_autostart,
    window_process_name,
)


BG = "#F3F1EB"
CARD = "#FFFFFF"
CARD_LIGHT = "#F8F7F3"
BORDER = "#DDDAD2"
TEXT = "#171816"
MUTED = "#696A63"
ACCENT = "#171816"
ACID = "#C7FF36"
MINT = "#71E5BD"
RECORD = "#E5484D"
SUCCESS = "#10A37F"


def _reverse_map(mapping: dict[str, str]) -> dict[str, str]:
    return {label: key for key, label in mapping.items()}


class ScrollSafeComboBox(QComboBox):
    """A combo box that does not steal page scrolling when its popup is closed."""

    def wheelEvent(self, event: Any) -> None:
        if self.view().isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class EqualWidthTabBar(QTabBar):
    """A full-width navigation bar with evenly sized sections."""

    def tabSizeHint(self, index: int) -> Any:
        size = super().tabSizeHint(index)
        if self.count() > 0 and self.width() > 0:
            size.setWidth(max(1, self.width() // self.count()))
        return size

    def minimumTabSizeHint(self, index: int) -> Any:
        size = super().minimumTabSizeHint(index)
        size.setWidth(1)
        return size

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.updateGeometry()


class VoiceLevelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(300)
        self.setFixedHeight(38)
        self._levels = [0.04] * 28
        self._smoothed = 0.0

    def reset(self) -> None:
        self._levels = [0.04] * 28
        self._smoothed = 0.0
        self.update()

    def set_level(self, level: float) -> None:
        target = max(0.0, min(1.0, level))
        if target >= self._smoothed:
            self._smoothed = target * 0.72 + self._smoothed * 0.28
        else:
            self._smoothed = max(target, self._smoothed * 0.84)
        self._levels.pop(0)
        self._levels.append(max(0.04, self._smoothed))
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        painter.setPen(QColor(BORDER))
        painter.drawLine(0, height // 2, width, height // 2)
        painter.setPen(Qt.PenStyle.NoPen)
        gap = 3
        bar_width = max(2, (width - gap * (len(self._levels) - 1)) // len(self._levels))
        total_width = bar_width * len(self._levels) + gap * (len(self._levels) - 1)
        start_x = (width - total_width) // 2
        for index, level in enumerate(self._levels):
            bar_height = max(3, int((height - 4) * level))
            x = start_x + index * (bar_width + gap)
            y = (height - bar_height) // 2
            color = QColor(RECORD if level > 0.14 else "#9CA3AF")
            color.setAlpha(245 if level > 0.14 else 145)
            painter.setBrush(color)
            painter.drawRoundedRect(x, y, bar_width, bar_height, 2, 2)
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, hide_callback: Any) -> None:
        super().__init__()
        self._hide_callback = hide_callback
        self.allow_close = False
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        self._hide_callback()


class WindowTitleBar(QFrame):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self._window = window
        self._drag_offset = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(54)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._window.move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class VoiceInputApp:
    def __init__(
        self,
        application: QApplication,
        start_minimized: bool = False,
        show_settings_event: int = 0,
    ) -> None:
        self.application = application
        self.config = load_config()
        try:
            save_config(self.config)
        except OSError:
            pass
        self.start_minimized = start_minimized or self.config.start_minimized
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.recorder = AudioRecorder()
        self.engine = WhisperEngine()
        self.hotkey = GlobalHotkey()
        self.cancel_hotkey = GlobalHotkey()
        self._registered_hotkey: str | None = None
        self.state = "loading"
        self._closing = False
        self._model_generation = 0
        self._recording_session = 0
        self._preview_stop: threading.Event | None = None
        self._latest_preview_text = ""
        self._recording_started_at = 0.0
        self._recording_warning = ""
        self._recording_target_window = 0
        self._last_insertion_window = 0
        self._clipboard_restore_generation = 0
        self._microphone_test_running = False
        self._microphone_test_recorder: AudioRecorder | None = None
        self._active_output_mode = self.config.output_mode
        self._active_ai_target = self.config.ai_target
        self._active_project_context = self.config.project_context
        self._active_custom_terms = self.config.custom_terms
        self._active_language = self.config.language
        self._active_snippets = self.config.snippets
        self._active_application_name = ""
        self._active_custom_instruction = self.config.custom_instruction
        self._update_repository = configured_repository()
        self._update_in_progress = False
        self._settings_dirty = False
        self._show_settings_event = show_settings_event
        self.devices: list[dict[str, Any]] = []

        self.window = MainWindow(self.hide_window)
        self._build_window()
        self._build_overlay()
        self._refresh_devices()
        self._populate_from_config()
        self._connect_settings_dirty_signals()
        self._sync_autostart()
        self.mode_combo.currentIndexChanged.connect(self._on_output_mode_changed)
        self.target_combo.currentIndexChanged.connect(self._on_ai_target_changed)
        self._start_tray()
        self._register_hotkey()
        self._load_model(self.config.model)

        self.timer = QTimer()
        self.timer.timeout.connect(self._process_events)
        self.timer.start(50)
        if self._update_repository:
            QTimer.singleShot(5000, lambda: self.check_for_updates(manual=False))

        if self.start_minimized:
            self.window.hide()
        else:
            self.window.show()

    def _build_window(self) -> None:
        self.window.setWindowTitle("Речка")
        self.window.resize(760, 760)
        self.window.setMinimumSize(660, 650)
        self.window.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 9.5pt;
            }}
            QFrame#windowTitleBar {{
                background: {CARD};
                border: 0;
                border-bottom: 1px solid {BORDER};
            }}
            QFrame#windowTitleBar QLabel {{
                background: transparent;
                border: 0;
            }}
            QTabWidget::pane {{
                border: 0;
                background: transparent;
            }}
            QTabWidget#mainTabs::pane {{
                border-top: 1px solid {BORDER};
                background: transparent;
            }}
            QTabBar#mainTabBar {{
                background: {CARD};
                border: 0;
            }}
            QTabBar#mainTabBar::tab {{
                background: {CARD};
                color: {MUTED};
                min-width: 0;
                padding: 11px 18px 9px;
                margin: 0;
                border: 0;
                border-bottom: 3px solid transparent;
                border-radius: 0;
                font-weight: 600;
            }}
            QTabBar#mainTabBar::tab:hover {{
                color: {TEXT};
                background: {CARD_LIGHT};
            }}
            QTabBar#mainTabBar::tab:selected {{
                color: {TEXT};
                background: {CARD_LIGHT};
                border-bottom: 3px solid {ACID};
                font-weight: 700;
            }}
            QTabBar#resultTabBar::tab {{
                background: transparent;
                color: {MUTED};
                min-width: 0;
                padding: 6px 9px;
                margin-right: 10px;
                border: 0;
                border-bottom: 2px solid transparent;
                border-radius: 0;
                font-weight: 600;
            }}
            QTabBar#resultTabBar::tab:selected {{
                color: {TEXT};
                border-bottom-color: {TEXT};
                font-weight: 700;
            }}
            QComboBox, QLineEdit, QKeySequenceEdit, QTextEdit {{
                background: {CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 8px 10px;
                min-height: 20px;
                selection-background-color: #D1D5DB;
                selection-color: {TEXT};
            }}
            QComboBox:focus, QLineEdit:focus, QKeySequenceEdit:focus,
            QTextEdit:focus {{
                border: 1px solid {TEXT};
            }}
            QComboBox::drop-down {{
                border: 0;
                width: 28px;
            }}
            QComboBox QAbstractItemView {{
                background: {CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                selection-background-color: {CARD_LIGHT};
                selection-color: {TEXT};
                outline: 0;
            }}
            QCheckBox {{
                color: {TEXT};
                spacing: 7px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
            }}
            QProgressBar {{
                background: {CARD_LIGHT};
                border: 0;
                border-radius: 2px;
                height: 4px;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: 2px;
            }}
            QScrollArea {{
                background: transparent;
                border: 0;
            }}
            QScrollBar:vertical {{
                width: 7px;
                margin: 2px 0;
                background: transparent;
            }}
            QScrollBar::handle:vertical {{
                min-height: 32px;
                border-radius: 3px;
                background: #C8C6BE;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        )

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        title_bar = WindowTitleBar(self.window)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(18, 0, 10, 0)
        title_layout.setSpacing(10)

        icon = self._make_icon(ACCENT)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(30, 30))
        icon_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        title_layout.addWidget(icon_label)

        brand = QLabel("Речка")
        brand.setStyleSheet(
            f"color: {TEXT}; font-size: 12pt; font-weight: 700; border: 0;"
        )
        brand.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        title_layout.addWidget(brand)

        local_badge = QLabel("ЛОКАЛЬНО")
        local_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        local_badge.setFixedHeight(24)
        local_badge.setStyleSheet(
            f"color: {TEXT}; background: #E8FFC2; border-radius: 8px; "
            "padding: 0 8px; font-size: 7pt; font-weight: 700;"
        )
        local_badge.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        title_layout.addWidget(local_badge)
        title_layout.addStretch()

        minimize = QPushButton("—")
        minimize.setObjectName("windowControl")
        minimize.setFixedSize(36, 32)
        minimize.setToolTip("Свернуть")
        minimize.clicked.connect(self.window.showMinimized)
        close = QPushButton("×")
        close.setObjectName("windowControl")
        close.setFixedSize(36, 32)
        close.setToolTip("Скрыть в область уведомлений")
        close.clicked.connect(self.hide_window)
        for button in (minimize, close):
            button.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent;
                    color: {MUTED};
                    border: 0;
                    border-radius: 8px;
                    font-size: 14pt;
                }}
                QPushButton:hover {{
                    background: {CARD_LIGHT};
                    color: {TEXT};
                }}
                """
            )
            title_layout.addWidget(button)
        root_layout.addWidget(title_bar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.setTabBar(EqualWidthTabBar())
        self.tabs.tabBar().setObjectName("mainTabBar")
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.addTab(self._build_dictation_tab(), "Диктовка")
        self.tabs.addTab(self._build_settings_tab(), "Настройки")
        self.tabs.addTab(self._build_history_tab(), "История")
        self.tabs.setDocumentMode(True)
        self.tabs.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self.tabs, 1)
        self.window.setCentralWidget(central)

        self.window.setWindowIcon(icon)
        self.application.setWindowIcon(icon)

    def _build_dictation_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 13, 20)
        layout.setSpacing(12)

        heading = QLabel("Голос в текст")
        heading.setStyleSheet(
            f"color: {TEXT}; font-size: 18pt; font-weight: 750; "
            "letter-spacing: -0.5px;"
        )
        layout.addWidget(heading)
        subtitle = QLabel(
            "Выберите режим, нажмите горячую клавишу и говорите свободно."
        )
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        layout.addWidget(subtitle)

        mode_card = QFrame()
        mode_card.setStyleSheet(
            f"QFrame {{ background: {CARD}; border: 1px solid {BORDER}; "
            "border-radius: 14px; }}"
            "QLabel { border: 0; background: transparent; }"
        )
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(16, 14, 16, 14)
        mode_layout.setSpacing(9)

        mode_row = QHBoxLayout()
        mode_label = QLabel("Режим")
        mode_label.setStyleSheet(f"color: {TEXT}; font-weight: 650;")
        mode_label.setFixedWidth(110)
        self.mode_combo = ScrollSafeComboBox()
        self.mode_combo.addItems(list(OUTPUT_MODE_OPTIONS.values()))
        self.mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mode_combo.setMinimumContentsLength(20)
        self.mode_combo.setMinimumWidth(0)
        self.mode_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.mode_combo.currentTextChanged.connect(self.mode_combo.setToolTip)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_combo, 1)
        mode_layout.addLayout(mode_row)

        target_row = QHBoxLayout()
        self.target_label = QLabel("AI-система")
        self.target_label.setStyleSheet(f"color: {TEXT}; font-weight: 650;")
        self.target_label.setFixedWidth(110)
        self.target_combo = ScrollSafeComboBox()
        self.target_combo.addItems(list(AI_TARGET_OPTIONS.values()))
        self.target_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.target_combo.setMinimumContentsLength(20)
        self.target_combo.setMinimumWidth(0)
        self.target_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.target_combo.currentTextChanged.connect(
            self.target_combo.setToolTip
        )
        target_row.addWidget(self.target_label)
        target_row.addWidget(self.target_combo, 1)
        mode_layout.addLayout(target_row)

        self.mode_description = QLabel()
        self.mode_description.setWordWrap(True)
        self.mode_description.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        mode_layout.addWidget(self.mode_description)
        layout.addWidget(mode_card)

        record_card = QFrame()
        self.record_card = record_card
        record_card.setStyleSheet(
            f"QFrame {{ background: {TEXT}; border: 0; border-radius: 16px; }}"
            "QLabel { border: 0; background: transparent; }"
        )
        record_layout = QVBoxLayout(record_card)
        record_layout.setContentsMargins(20, 14, 20, 14)
        record_layout.setSpacing(8)
        status_row = QHBoxLayout()
        status_row.addStretch()
        self.record_status_dot = QLabel("●")
        self.record_status_dot.setStyleSheet(
            "color: #8B8D87; font-size: 8pt;"
        )
        status_row.addWidget(self.record_status_dot)
        self.status_label = QLabel("Подготовка…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "color: #D8D8D2; font-size: 9pt; font-weight: 600;"
        )
        status_row.addWidget(self.status_label)
        self.main_record_elapsed = QLabel("00:00")
        self.main_record_elapsed.setStyleSheet(
            "color: white; font-size: 9pt; font-weight: 700;"
        )
        self.main_record_elapsed.hide()
        status_row.addWidget(self.main_record_elapsed)
        status_row.addStretch()
        record_layout.addLayout(status_row)

        progress_row = QHBoxLayout()
        progress_row.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(240)
        progress_row.addWidget(self.progress)
        progress_row.addStretch()
        record_layout.addLayout(progress_row)

        main_level_row = QHBoxLayout()
        main_level_row.addStretch()
        self.main_voice_level = VoiceLevelWidget()
        self.main_voice_level.setMaximumWidth(360)
        self.main_voice_level.setFixedHeight(24)
        self.main_voice_level.hide()
        main_level_row.addWidget(self.main_voice_level)
        main_level_row.addStretch()
        record_layout.addLayout(main_level_row)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.record_button = QPushButton("Загрузка модели")
        self.record_button.setEnabled(False)
        self.record_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_button.setMinimumWidth(220)
        self.record_button.clicked.connect(self.toggle_recording)
        self._style_primary_button(self.record_button, ACID)
        button_row.addWidget(self.record_button)
        button_row.addStretch()
        record_layout.addLayout(button_row)

        self.hotkey_hint = QLabel()
        self.hotkey_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hotkey_hint.setStyleSheet("color: #A8AAA3; font-size: 8.5pt;")
        record_layout.addWidget(self.hotkey_hint)
        layout.addWidget(record_card)

        result_card = QFrame()
        self.result_card = result_card
        result_card.setStyleSheet(
            f"QFrame {{ background: {CARD}; border: 1px solid {BORDER}; "
            "border-radius: 14px; }}"
            "QLabel { border: 0; background: transparent; }"
        )
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(16, 13, 16, 15)
        result_layout.setSpacing(9)
        result_header = QHBoxLayout()
        last_label = QLabel("Последний результат")
        last_label.setStyleSheet(f"color: {TEXT}; font-weight: 650;")
        result_header.addWidget(last_label)
        result_header.addStretch()
        copy_result = QPushButton("Копировать")
        clear_result = QPushButton("Очистить")
        self.copy_result_button = copy_result
        self.clear_result_button = clear_result
        for button in (copy_result, clear_result):
            self._style_secondary_button(button)
            result_header.addWidget(button)
        copy_result.clicked.connect(self._copy_last_result)
        clear_result.clicked.connect(self._clear_last_result)
        result_layout.addLayout(result_header)

        self.result_tabs = QTabWidget()
        self.result_tabs.setObjectName("resultTabs")
        self.result_tabs.tabBar().setObjectName("resultTabBar")
        self.result_tabs.tabBar().setExpanding(False)
        self.result_tabs.tabBar().setDrawBase(False)
        self.last_text = QTextEdit()
        self.last_text.setReadOnly(False)
        self.last_text.setPlaceholderText(
            "Здесь появится распознанный текст."
        )
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setPlaceholderText(
            "Здесь появится исходная расшифровка Whisper."
        )
        for editor in (self.last_text, self.raw_text):
            editor.setMinimumHeight(0)
            editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            editor.document().contentsChanged.connect(
                self._schedule_result_editor_resize
            )
            editor.document().documentLayout().documentSizeChanged.connect(
                lambda _size: self._schedule_result_editor_resize()
            )
        self.result_tabs.addTab(self.last_text, "Готовый текст")
        self.result_tabs.addTab(self.raw_text, "Исходная расшифровка")
        self.result_tabs.currentChanged.connect(self._on_result_tab_changed)
        result_layout.addWidget(self.result_tabs)

        result_actions = QVBoxLayout()
        result_actions.setSpacing(7)
        recording_actions = QHBoxLayout()
        self.repeat_button = QPushButton("Повторить")
        self.repeat_button.setToolTip("Начать новую запись в том же режиме")
        self.undo_button = QPushButton("Отменить вставку")
        self.undo_button.setToolTip("Отменить последнюю вставку в исходном окне")
        self.undo_button.setEnabled(False)
        for button in (self.repeat_button, self.undo_button):
            self._style_secondary_button(button)
            recording_actions.addWidget(button)
        recording_actions.addStretch()
        result_actions.addLayout(recording_actions)
        self.repeat_button.clicked.connect(self._repeat_recording)
        self.undo_button.clicked.connect(self._undo_last_insertion)
        quick_actions = QHBoxLayout()
        self.quick_action_combo = ScrollSafeComboBox()
        self.quick_action_combo.addItems(list(QUICK_ACTION_OPTIONS.values()))
        self.quick_action_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.quick_action_button = QPushButton("Применить")
        self.quick_action_button.setToolTip(
            "Преобразовать только показанный результат"
        )
        self._style_secondary_button(self.quick_action_button)
        self.quick_action_button.clicked.connect(self._apply_quick_action)
        quick_actions.addWidget(self.quick_action_combo, 1)
        quick_actions.addWidget(self.quick_action_button)
        result_actions.addLayout(quick_actions)
        result_layout.addLayout(result_actions)
        self._set_result_available(False)
        layout.addWidget(result_card)
        layout.addStretch()
        scroll.setWidget(content)
        QTimer.singleShot(0, self._resize_result_editor)
        return scroll

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 18, 13, 20)
        outer.setSpacing(12)

        heading = QLabel("Настройки")
        heading.setStyleSheet(
            f"color: {TEXT}; font-size: 18pt; font-weight: 750; "
            "letter-spacing: -0.5px;"
        )
        outer.addWidget(heading)
        subtitle = QLabel(
            "Горячая клавиша, микрофон, скорость и обработка текста."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        outer.addWidget(subtitle)

        self.onboarding_hint = QLabel(
            "Первый запуск: выберите микрофон, нажмите «Тест микрофона», "
            "затем сохраните настройки. После этого можно диктовать из любого окна."
        )
        self.onboarding_hint.setWordWrap(True)
        self.onboarding_hint.setStyleSheet(
            f"background: {ACID}; color: {TEXT}; border-radius: 10px; "
            "padding: 10px 12px; font-weight: 600;"
        )
        outer.addWidget(self.onboarding_hint)

        def make_section(title: str) -> tuple[QFrame, QVBoxLayout]:
            frame = QFrame()
            frame.setStyleSheet(
                f"QFrame {{ background: {CARD}; border: 1px solid {BORDER}; "
                "border-radius: 14px; }}"
                "QLabel, QCheckBox { border: 0; background: transparent; }"
            )
            section_layout = QVBoxLayout(frame)
            section_layout.setContentsMargins(14, 12, 14, 13)
            section_layout.setSpacing(8)
            label = QLabel(title)
            label.setStyleSheet(
                f"color: {TEXT}; font-size: 10pt; font-weight: 700;"
            )
            section_layout.addWidget(label)
            return frame, section_layout

        def make_form() -> QFormLayout:
            form = QFormLayout()
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(9)
            form.setLabelAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
            return form

        def make_settings_combo(items: list[str] | None = None) -> QComboBox:
            combo = ScrollSafeComboBox()
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumWidth(0)
            combo.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            if items:
                combo.addItems(items)
            return combo

        controls, controls_layout = make_section("Управление")
        controls_form = make_form()
        self.hotkey_combo = make_settings_combo(
            [*HOTKEY_OPTIONS.values(), "Своя комбинация…"]
        )
        controls_form.addRow("Горячая клавиша", self.hotkey_combo)

        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setMaximumSequenceLength(1)
        self.hotkey_edit.setClearButtonEnabled(True)
        self.hotkey_edit.setToolTip(
            "Нажмите одну комбинацию. Например Ctrl+Alt+R или Ctrl+Shift+F9."
        )
        controls_form.addRow("Своя комбинация", self.hotkey_edit)
        self.hotkey_edit_label = controls_form.labelForField(self.hotkey_edit)
        self.hotkey_combo.currentIndexChanged.connect(
            self._update_custom_hotkey_visibility
        )

        self.insertion_combo = make_settings_combo(
            list(INSERTION_OPTIONS.values())
        )
        controls_form.addRow("Вставка текста", self.insertion_combo)
        controls_layout.addLayout(controls_form)
        controls_hint = QLabel(
            "Повторный запуск ярлыка откроет это окно, даже если «Речка» "
            "уже работает в области уведомлений."
        )
        controls_hint.setWordWrap(True)
        controls_hint.setStyleSheet(f"color: {MUTED}; font-size: 8pt;")
        controls_layout.addWidget(controls_hint)
        outer.addWidget(controls)

        recognition, recognition_layout = make_section("Распознавание")
        recognition_form = make_form()
        self.model_combo = make_settings_combo(list(MODEL_OPTIONS.values()))
        recognition_form.addRow("Модель", self.model_combo)

        self.decoding_combo = make_settings_combo(
            list(DECODING_OPTIONS.values())
        )
        recognition_form.addRow("Скорость", self.decoding_combo)

        self.language_combo = make_settings_combo(list(LANGUAGE_OPTIONS.values()))
        recognition_form.addRow("Язык", self.language_combo)

        self.device_combo = make_settings_combo()
        self.device_combo.currentTextChanged.connect(
            self.device_combo.setToolTip
        )
        recognition_form.addRow("Микрофон", self.device_combo)

        self.custom_terms_edit = QLineEdit()
        self.custom_terms_edit.setPlaceholderText(
            "Например: Codex, PostgreSQL, название компании"
        )
        self.custom_terms_edit.setToolTip(
            "Можно указать варианты распознавания: PostgreSQL = постгрес | пост грес"
        )
        recognition_form.addRow("Слова и названия", self.custom_terms_edit)
        recognition_layout.addLayout(recognition_form)
        microphone_actions = QHBoxLayout()
        refresh = QPushButton("Обновить")
        self._style_secondary_button(refresh)
        refresh.setToolTip("Обновить список доступных микрофонов")
        refresh.clicked.connect(self._refresh_devices)
        microphone_actions.addWidget(refresh)
        self.microphone_test_button = QPushButton("Тест микрофона")
        self._style_secondary_button(self.microphone_test_button)
        self.microphone_test_button.clicked.connect(self._test_microphone)
        microphone_actions.addWidget(self.microphone_test_button)
        self.diagnostics_button = QPushButton("Диагностика")
        self._style_secondary_button(self.diagnostics_button)
        self.diagnostics_button.setToolTip(
            "Копирует технический JSON без аудио, расшифровки и словаря"
        )
        self.diagnostics_button.clicked.connect(self._copy_diagnostics)
        microphone_actions.addWidget(self.diagnostics_button)
        microphone_actions.addStretch()
        recognition_layout.addLayout(microphone_actions)
        self.microphone_test_status = QLabel(
            "Проверка запишет только 3 секунды и сразу удалит аудио."
        )
        self.microphone_test_status.setWordWrap(True)
        self.microphone_test_status.setStyleSheet(
            f"color: {MUTED}; font-size: 8pt;"
        )
        recognition_layout.addWidget(self.microphone_test_status)
        outer.addWidget(recognition)

        processing, processing_layout = make_section("Обработка текста")
        processing_form = make_form()
        self.project_context_edit = QLineEdit()
        self.project_context_edit.setPlaceholderText(
            "Например: Windows-приложение, Python, важна приватность"
        )
        processing_form.addRow("Контекст проекта", self.project_context_edit)

        self.ollama_model_edit = QLineEdit()
        self.ollama_model_edit.setPlaceholderText("qwen3:4b")
        processing_form.addRow("AI-модель Ollama", self.ollama_model_edit)
        self.custom_instruction_edit = QTextEdit()
        self.custom_instruction_edit.setPlaceholderText(
            "Например: преврати текст в краткий отчёт с разделами «Итог» и «Следующие шаги»"
        )
        self.custom_instruction_edit.setFixedHeight(72)
        processing_form.addRow("Инструкция своего режима", self.custom_instruction_edit)
        processing_layout.addLayout(processing_form)

        self.append_space_check = QCheckBox("Добавлять пробел после вставки")
        self.commands_check = QCheckBox(
            "Понимать «новая строка», «поставь точку»"
        )
        self.use_local_ai_check = QCheckBox(
            "Улучшать текст через Ollama (медленнее)"
        )
        self.use_local_ai_check.setToolTip(
            "Локальная Ollama может улучшить обработку текста, но увеличивает задержку."
        )
        self.sound_feedback_check = QCheckBox("Мягкий звук начала и остановки")
        for check in (
            self.append_space_check,
            self.commands_check,
            self.use_local_ai_check,
            self.sound_feedback_check,
        ):
            processing_layout.addWidget(check)
        outer.addWidget(processing)

        personalization, personalization_layout = make_section("Персонализация")
        personalization_hint = QLabel(
            "Сниппеты срабатывают только при полном совпадении произнесённой фразы. "
            "Профили выбираются по имени .exe активного приложения."
        )
        personalization_hint.setWordWrap(True)
        personalization_hint.setStyleSheet(f"color: {MUTED}; font-size: 8pt;")
        personalization_layout.addWidget(personalization_hint)

        self.snippets_edit = QTextEdit()
        self.snippets_edit.setPlaceholderText(
            "подпись => С уважением,\\nИван\nмой адрес => Москва, улица…"
        )
        self.snippets_edit.setFixedHeight(86)
        personalization_layout.addWidget(QLabel("Сниппеты: фраза => текст"))
        personalization_layout.addWidget(self.snippets_edit)

        self.app_profiles_edit = QTextEdit()
        self.app_profiles_edit.setPlaceholderText(
            "code.exe | verbatim | Codex, GitHub\ntelegram*.exe | communication |"
        )
        self.app_profiles_edit.setFixedHeight(86)
        profiles_label = QLabel("Профили приложений: app.exe | режим | слова")
        profiles_label.setWordWrap(True)
        personalization_layout.addWidget(profiles_label)
        personalization_layout.addWidget(self.app_profiles_edit)

        self.history_enabled_check = QCheckBox(
            "Локальная история (без аудио)"
        )
        self.history_enabled_check.setToolTip(
            "Выключено по умолчанию. Хранится до 100 записей; аудио не сохраняется."
        )
        personalization_layout.addWidget(self.history_enabled_check)
        personalization_actions = QHBoxLayout()
        export_button = QPushButton("Экспорт")
        import_button = QPushButton("Импорт")
        export_button.setToolTip("Экспорт словаря, сниппетов и профилей в JSON")
        import_button.setToolTip("Импорт словаря, сниппетов и профилей из JSON")
        for button in (export_button, import_button):
            self._style_secondary_button(button)
            personalization_actions.addWidget(button)
        personalization_actions.addStretch()
        export_button.clicked.connect(self._export_personalization)
        import_button.clicked.connect(self._import_personalization)
        personalization_layout.addLayout(personalization_actions)
        outer.addWidget(personalization)

        system, system_layout = make_section("Запуск и обновления")
        self.start_minimized_check = QCheckBox("Запускать свёрнутой")
        self.autostart_check = QCheckBox("Запускать вместе с Windows")
        system_layout.addWidget(self.start_minimized_check)
        system_layout.addWidget(self.autostart_check)
        self.update_button = QPushButton("Проверить обновления")
        self._style_secondary_button(self.update_button)
        self.update_button.clicked.connect(
            lambda: self.check_for_updates(manual=True)
        )
        system_layout.addWidget(
            self.update_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        self.update_status = QLabel("Обновления устанавливаются поверх текущей версии.")
        self.update_status.setWordWrap(True)
        self.update_status.setStyleSheet(f"color: {MUTED}; font-size: 8pt;")
        system_layout.addWidget(self.update_status)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.hide()
        system_layout.addWidget(self.update_progress)
        outer.addWidget(system)

        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("settingsFooter")
        footer.setStyleSheet(
            f"QFrame#settingsFooter {{ background: {CARD}; "
            f"border-top: 1px solid {BORDER}; }}"
            "QLabel { border: 0; background: transparent; }"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 9, 20, 9)
        footer_layout.setSpacing(10)
        self.settings_save_status = QLabel(f"Версия {__version__}")
        self.settings_save_status.setStyleSheet(
            f"color: {MUTED}; font-size: 8pt;"
        )
        footer_layout.addWidget(self.settings_save_status)
        footer_layout.addStretch()
        self.settings_save_button = QPushButton("Сохранить")
        self._style_primary_button(
            self.settings_save_button,
            ACCENT,
            compact=True,
        )
        self.settings_save_button.clicked.connect(self.save_settings)
        footer_layout.addWidget(self.settings_save_button)
        tab_layout.addWidget(footer)
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(10)
        heading = QLabel("Локальная история")
        heading.setStyleSheet(
            f"color: {TEXT}; font-size: 18pt; font-weight: 750;"
        )
        layout.addWidget(heading)
        self.history_status = QLabel()
        self.history_status.setWordWrap(True)
        self.history_status.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        layout.addWidget(self.history_status)
        self.history_view = QTextEdit()
        self.history_view.setReadOnly(True)
        layout.addWidget(self.history_view, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("Обновить")
        copy = QPushButton("Копировать всё")
        clear = QPushButton("Удалить историю")
        for button in (refresh, copy, clear):
            self._style_secondary_button(button)
            actions.addWidget(button)
        actions.addStretch()
        refresh.clicked.connect(self._refresh_history)
        copy.clicked.connect(self._copy_history)
        clear.clicked.connect(self._clear_history)
        layout.addLayout(actions)
        return tab

    def _set_result_available(self, available: bool) -> None:
        if not hasattr(self, "result_tabs"):
            return
        self.copy_result_button.setEnabled(available)
        self.clear_result_button.setEnabled(available)
        self.repeat_button.setEnabled(available)
        self.quick_action_combo.setEnabled(available)
        self.quick_action_button.setEnabled(available)
        self.last_text.setReadOnly(not available)
        self.result_tabs.setTabVisible(1, available)
        if not available:
            self.result_tabs.setCurrentIndex(0)
        self._schedule_result_editor_resize()

    def _on_result_tab_changed(self, _index: int) -> None:
        editor = self.result_tabs.currentWidget()
        self.copy_result_button.setToolTip(
            "Копировать показанную исходную расшифровку"
            if editor is self.raw_text
            else "Копировать показанный готовый текст"
        )
        self._schedule_result_editor_resize()

    def _set_main_recording_feedback(self, active: bool) -> None:
        self.main_record_elapsed.setVisible(active)
        self.main_voice_level.setVisible(active)
        if active:
            self.main_record_elapsed.setText("00:00")
            self.main_voice_level.reset()
            self.record_card.setStyleSheet(
                f"QFrame {{ background: #211719; border: 2px solid {RECORD}; "
                "border-radius: 16px; }}"
                "QLabel { border: 0; background: transparent; }"
            )
            return
        self.main_voice_level.reset()
        self.record_card.setStyleSheet(
            f"QFrame {{ background: {TEXT}; border: 0; border-radius: 16px; }}"
            "QLabel { border: 0; background: transparent; }"
        )

    def _connect_settings_dirty_signals(self) -> None:
        for combo in (
            self.hotkey_combo,
            self.insertion_combo,
            self.model_combo,
            self.decoding_combo,
            self.language_combo,
            self.device_combo,
        ):
            combo.currentIndexChanged.connect(self._mark_settings_dirty)
        self.hotkey_edit.keySequenceChanged.connect(self._mark_settings_dirty)
        for edit in (
            self.custom_terms_edit,
            self.project_context_edit,
            self.ollama_model_edit,
        ):
            edit.textChanged.connect(self._mark_settings_dirty)
        for editor in (
            self.custom_instruction_edit,
            self.snippets_edit,
            self.app_profiles_edit,
        ):
            editor.textChanged.connect(self._mark_settings_dirty)
        for check in (
            self.append_space_check,
            self.commands_check,
            self.use_local_ai_check,
            self.sound_feedback_check,
            self.history_enabled_check,
            self.start_minimized_check,
            self.autostart_check,
        ):
            check.toggled.connect(self._mark_settings_dirty)

    def _mark_settings_dirty(self, *_args: Any) -> None:
        self._settings_dirty = True
        self.settings_save_status.setText("Есть несохранённые изменения")
        self.settings_save_status.setStyleSheet(
            f"color: {ACCENT}; font-size: 8pt; font-weight: 600;"
        )

    def _mark_settings_saved(self) -> None:
        self._settings_dirty = False
        self.settings_save_status.setText(f"Сохранено · версия {__version__}")
        self.settings_save_status.setStyleSheet(
            f"color: {SUCCESS}; font-size: 8pt; font-weight: 600;"
        )

    def _schedule_result_editor_resize(self, *_args: Any) -> None:
        QTimer.singleShot(0, self._resize_result_editor)

    def _resize_result_editor(self) -> None:
        if not hasattr(self, "result_tabs"):
            return
        editor = self.result_tabs.currentWidget()
        if not isinstance(editor, QTextEdit):
            return

        document_height = float(
            editor.document().documentLayout().documentSize().height()
        )
        desired_height = int(document_height + 24)
        editor_height = max(112, min(360, desired_height))
        editor.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if desired_height > 360
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        tab_height = max(30, self.result_tabs.tabBar().sizeHint().height())
        target_tabs_height = editor_height + tab_height + 8
        if self.result_tabs.height() != target_tabs_height:
            self.result_tabs.setFixedHeight(target_tabs_height)
            self.result_tabs.updateGeometry()
        if hasattr(self, "result_card"):
            card_height = self.result_card.layout().sizeHint().height()
            if self.result_card.height() != card_height:
                self.result_card.setFixedHeight(card_height)
                self.result_card.updateGeometry()

    def _style_primary_button(
        self,
        button: QPushButton,
        color: str,
        compact: bool = False,
    ) -> None:
        vertical = 7 if compact else 12
        horizontal = 18 if compact else 28
        if color == RECORD:
            hover = "#C93C41"
            text_color = "white"
        elif color == ACID:
            hover = "#B7EE31"
            text_color = TEXT
        else:
            hover = "#343541"
            text_color = "white"
        button.setStyleSheet(
            f"""
            QPushButton {{
                background: {color};
                color: {text_color};
                border: 1px solid {color};
                border-radius: 10px;
                padding: {vertical}px {horizontal}px;
                font-weight: 650;
            }}
            QPushButton:hover {{ background: {hover}; border-color: {hover}; }}
            QPushButton:pressed {{ padding-top: {vertical + 1}px; }}
            QPushButton:focus {{ border: 2px solid {TEXT}; }}
            QPushButton:disabled {{
                background: {CARD_LIGHT};
                border-color: {BORDER};
                color: #9CA3AF;
            }}
            """
        )

    def _style_secondary_button(self, button: QPushButton) -> None:
        button.setStyleSheet(
            f"""
            QPushButton {{
                background: {CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 7px 12px;
            }}
            QPushButton:hover {{ background: {CARD_LIGHT}; }}
            QPushButton:pressed {{ background: #E7E5DE; }}
            QPushButton:focus {{ border: 2px solid {TEXT}; }}
            QPushButton:disabled {{
                background: {CARD_LIGHT};
                color: #9CA3AF;
            }}
            """
        )

    def _build_overlay(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.overlay = QFrame(None, flags)
        self.overlay.setObjectName("voiceOverlay")
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.overlay.setFixedWidth(520)
        self.overlay.setStyleSheet(
            f"""
            QFrame#voiceOverlay {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(self.overlay)
        layout.setContentsMargins(15, 10, 15, 12)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(7)
        self.overlay_dot = QLabel("●")
        self.overlay_dot.setStyleSheet(f"color: {RECORD}; font-size: 10pt; border: 0;")
        self.overlay_state_text = QLabel("Слушаю…")
        self.overlay_state_text.setStyleSheet(
            f"color: {TEXT}; font-weight: 500; border: 0;"
        )
        self.overlay_elapsed = QLabel("00:00")
        self.overlay_elapsed.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; border: 0;"
        )
        header.addWidget(self.overlay_dot)
        header.addWidget(self.overlay_state_text)
        header.addStretch()
        header.addWidget(self.overlay_elapsed)
        layout.addLayout(header)

        self.overlay_audio_state = QLabel("Микрофон подключён · начинайте говорить")
        self.overlay_audio_state.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; border: 0;"
        )
        layout.addWidget(self.overlay_audio_state)

        self.voice_level = VoiceLevelWidget()
        layout.addWidget(self.voice_level)

        self.overlay_preview = QLabel()
        self.overlay_preview.setObjectName("livePreview")
        self.overlay_preview.setWordWrap(False)
        self.overlay_preview.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.overlay_preview.setFixedHeight(36)
        self.overlay_preview.setStyleSheet(
            f"color: {TEXT}; font-size: 9.5pt; border: 0; "
            "background: transparent; padding: 0;"
        )
        self.overlay_preview.hide()
        layout.addWidget(self.overlay_preview)

        self.overlay_hint = QLabel(
            f"{hotkey_label(self.config.hotkey)} — закончить · Esc — отменить"
        )
        self.overlay_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_hint.setStyleSheet(
            f"color: {MUTED}; font-size: 8pt; border: 0;"
        )
        layout.addWidget(self.overlay_hint)
        self.overlay.hide()

    def _position_overlay(self) -> None:
        self.overlay.adjustSize()
        screen = self.application.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.overlay.setFixedWidth(max(360, min(520, area.width() - 32)))
            x = area.center().x() - self.overlay.width() // 2
            y = area.bottom() - self.overlay.height() - 36
            self.overlay.move(x, y)

    def _show_overlay(
        self,
        text: str,
        color: str,
    ) -> None:
        self.overlay_state_text.setText(text)
        self.overlay_dot.setStyleSheet(f"color: {color}; font-size: 10pt; border: 0;")
        self._position_overlay()
        self.overlay.show()
        self.overlay.raise_()

    def _set_overlay_preview(self, text: str) -> None:
        clean = " ".join(text.split())
        if not clean:
            self.overlay_preview.clear()
            self.overlay_preview.hide()
            return

        words = clean.split()
        available_width = max(280, self.overlay.width() - 30)
        metrics = self.overlay_preview.fontMetrics()
        lines: list[str] = []
        cursor = len(words)
        for _line_number in range(2):
            if cursor <= 0:
                break
            start = cursor - 1
            while start > 0:
                candidate = " ".join(words[start - 1 : cursor])
                if metrics.horizontalAdvance(candidate) > available_width:
                    break
                start -= 1
            lines.append(" ".join(words[start:cursor]))
            cursor = start
        lines.reverse()

        if cursor > 0 and lines:
            first_words = lines[0].split()
            while (
                len(first_words) > 1
                and metrics.horizontalAdvance(f"… {' '.join(first_words)}")
                > available_width
            ):
                first_words.pop(0)
            lines[0] = f"… {' '.join(first_words)}"

        self.overlay_preview.setText("\n".join(lines))
        self.overlay_preview.show()

    def _make_icon(self, color: str) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C7FF36"))
        painter.drawRoundedRect(6, 6, 54, 54, 14, 14)
        painter.setBrush(QColor("#F3F1EB"))
        painter.drawRoundedRect(3, 3, 54, 54, 14, 14)
        painter.setBrush(QColor("#171816"))
        painter.drawRoundedRect(11, 11, 40, 40, 11, 11)

        for index, (x, y, width, height, radius) in enumerate(
            (
                (17, 24, 3, 11, 2),
                (23, 20, 3, 19, 2),
                (29, 16, 3, 27, 2),
                (35, 20, 3, 19, 2),
                (41, 24, 3, 11, 2),
            )
        ):
            painter.setBrush(QColor("#C7FF36" if index % 2 == 0 else "#71E5BD"))
            painter.drawRoundedRect(x, y, width, height, radius, radius)

        painter.setBrush(QColor("#F3F1EB"))
        painter.drawRoundedRect(17, 43, 29, 2, 1, 1)
        painter.drawRoundedRect(25, 47, 21, 2, 1, 1)
        painter.drawEllipse(43, 43, 16, 16)
        painter.setBrush(QColor(color))
        painter.drawEllipse(47, 47, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _start_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._make_icon(ACCENT), self.window)
        self.tray.setToolTip("Речка")
        menu = QMenu()
        toggle_action = QAction("Начать / остановить", menu)
        toggle_action.triggered.connect(lambda: self.events.put(("toggle", None)))
        show_action = QAction("Открыть настройки", menu)
        show_action.triggered.connect(
            lambda: self.events.put(("settings", None))
        )
        exit_action = QAction("Выход", menu)
        exit_action.triggered.connect(lambda: self.events.put(("exit", None)))
        menu.addAction(toggle_action)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def _set_tray_color(self, color: str) -> None:
        self.tray.setIcon(self._make_icon(color))

    def _refresh_devices(self) -> None:
        try:
            self.devices = list_input_devices()
        except Exception as exc:
            QMessageBox.critical(
                self.window,
                "Микрофон",
                f"Не удалось получить список устройств:\n{exc}",
            )
            return

        selected_device = self.config.device_index
        self.device_combo.clear()
        self.device_combo.addItem("Системный микрофон по умолчанию", None)
        for device in self.devices:
            suffix = " — по умолчанию" if device["is_default"] else ""
            label = f'{device["index"]}: {device["name"]}{suffix}'
            self.device_combo.addItem(label, int(device["index"]))

        index = self.device_combo.findData(selected_device)
        self.device_combo.setCurrentIndex(index if index >= 0 else 0)

    def _test_microphone(self) -> None:
        if self._microphone_test_running:
            return
        if self.state in {"recording", "transcribing"}:
            self._set_status(
                "Проверка микрофона недоступна во время диктовки",
                MUTED,
            )
            return

        self._microphone_test_running = True
        self.microphone_test_button.setEnabled(False)
        self.microphone_test_button.setText("Говорите…")
        self.microphone_test_status.setText(
            "Говорите обычным голосом в течение трёх секунд."
        )
        self.microphone_test_status.setStyleSheet(
            f"color: {ACCENT}; font-size: 8pt; font-weight: 600;"
        )
        device_index = self.device_combo.currentData()
        recorder = AudioRecorder()
        self._microphone_test_recorder = recorder

        def worker() -> None:
            try:
                sample_rate = recorder.start(device_index)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    health_error = recorder.health_error
                    if health_error:
                        raise RuntimeError(health_error)
                    time.sleep(0.05)
                clip = recorder.stop()
                quality = analyze_audio(clip.samples)
                self.events.put(
                    (
                        "microphone_test_result",
                        (quality, clip.duration_seconds, sample_rate),
                    )
                )
            except Exception as exc:
                recorder.abort()
                self.events.put(("microphone_test_error", str(exc)))

        threading.Thread(
            target=worker,
            name="microphone-test",
            daemon=True,
        ).start()

    def _finish_microphone_test(self) -> None:
        self._microphone_test_running = False
        self._microphone_test_recorder = None
        self.microphone_test_button.setEnabled(True)
        self.microphone_test_button.setText("Тест микрофона")

    def _handle_microphone_test_result(
        self,
        payload: tuple[AudioQuality, float, int],
    ) -> None:
        quality, duration, sample_rate = payload
        self._finish_microphone_test()
        if duration < 2.5 or (
            quality.rms < 1e-5 and quality.peak < 3e-5
        ):
            text = (
                "Сигнал не обнаружен. Проверьте выбранный микрофон и доступ "
                "к нему в параметрах Windows."
            )
            color = RECORD
        elif quality.clipped_fraction >= 0.01:
            text = (
                "Микрофон работает, но звук перегружен. Уменьшите усиление "
                "или отодвиньтесь от микрофона."
            )
            color = RECORD
        elif quality.dbfs < -42.0:
            text = (
                f"Микрофон работает, но сигнал тихий ({quality.dbfs:.0f} dBFS). "
                "Говорите ближе или увеличьте уровень в Windows."
            )
            color = ACCENT
        else:
            text = (
                f"Микрофон работает хорошо · {quality.dbfs:.0f} dBFS · "
                f"{sample_rate} Гц"
            )
            color = SUCCESS
        self.microphone_test_status.setText(text)
        self.microphone_test_status.setStyleSheet(
            f"color: {color}; font-size: 8pt; font-weight: 600;"
        )

    def _handle_microphone_test_error(self, text: str) -> None:
        self._finish_microphone_test()
        self.microphone_test_status.setText(
            f"Проверка не выполнена: {text}"
        )
        self.microphone_test_status.setStyleSheet(
            f"color: {RECORD}; font-size: 8pt; font-weight: 600;"
        )

    def _copy_diagnostics(self) -> None:
        self.diagnostics_button.setEnabled(False)
        self.diagnostics_button.setText("Собираю…")
        selected_device = self.device_combo.currentData()
        model_name = self.engine.model_name or self.config.model

        def worker() -> None:
            try:
                payload = collect_diagnostics(
                    selected_device_index=selected_device,
                    model_name=model_name,
                )
            except Exception as exc:
                self.events.put(("diagnostics_error", str(exc)))
                return
            self.events.put(("diagnostics_ready", payload))

        threading.Thread(
            target=worker,
            name="diagnostics",
            daemon=True,
        ).start()

    def _reset_diagnostics_button(self) -> None:
        self.diagnostics_button.setEnabled(True)
        self.diagnostics_button.setText("Диагностика")

    def _populate_from_config(self) -> None:
        self.model_combo.setCurrentText(MODEL_OPTIONS[self.config.model])
        self.decoding_combo.setCurrentText(
            DECODING_OPTIONS[self.config.decoding_mode]
        )
        self.mode_combo.setCurrentText(OUTPUT_MODE_OPTIONS[self.config.output_mode])
        self.target_combo.setCurrentText(AI_TARGET_OPTIONS[self.config.ai_target])
        self.language_combo.setCurrentText(LANGUAGE_OPTIONS[self.config.language])
        self.insertion_combo.setCurrentText(INSERTION_OPTIONS[self.config.insertion_mode])
        if self.config.hotkey in HOTKEY_OPTIONS:
            self.hotkey_combo.setCurrentText(HOTKEY_OPTIONS[self.config.hotkey])
        else:
            self.hotkey_combo.setCurrentText("Своя комбинация…")
            self.hotkey_edit.setKeySequence(QKeySequence(self.config.hotkey))
        self._update_custom_hotkey_visibility()
        self.append_space_check.setChecked(self.config.append_space)
        self.commands_check.setChecked(self.config.punctuation_commands)
        self.use_local_ai_check.setChecked(self.config.use_local_ai)
        self.sound_feedback_check.setChecked(self.config.sound_feedback)
        self.start_minimized_check.setChecked(self.config.start_minimized)
        self.autostart_check.setChecked(self.config.autostart)
        self.custom_terms_edit.setText(self.config.custom_terms)
        self.snippets_edit.setPlainText(self.config.snippets)
        self.app_profiles_edit.setPlainText(self.config.app_profiles)
        self.project_context_edit.setText(self.config.project_context)
        self.custom_instruction_edit.setPlainText(self.config.custom_instruction)
        self.history_enabled_check.setChecked(self.config.history_enabled)
        self.ollama_model_edit.setText(self.config.ollama_model)
        self._update_mode_description()
        self.onboarding_hint.setVisible(not self.config.onboarding_complete)
        self._refresh_history()
        self.hotkey_hint.setText(
            f"{hotkey_label(self.config.hotkey)} — начать/остановить · Esc — отменить"
        )
        self.overlay_hint.setText(
            f"{hotkey_label(self.config.hotkey)} — закончить · Esc — отменить"
        )

    def _sync_autostart(self, *, silent: bool = True) -> None:
        if os.environ.get("VOICE_INPUT_DATA_DIR"):
            return
        try:
            main_script = Path(__file__).resolve().parents[1] / "main.py"
            set_autostart(
                self.config.autostart,
                autostart_command(main_script),
            )
        except OSError:
            if not silent:
                raise

    def _update_custom_hotkey_visibility(self, _index: int = -1) -> None:
        visible = self.hotkey_combo.currentText() == "Своя комбинация…"
        self.hotkey_edit.setVisible(visible)
        if self.hotkey_edit_label is not None:
            self.hotkey_edit_label.setVisible(visible)

    def _selected_hotkey(self) -> str:
        reverse_hotkeys = _reverse_map(HOTKEY_OPTIONS)
        selected = reverse_hotkeys.get(self.hotkey_combo.currentText())
        if selected:
            return selected
        portable = self.hotkey_edit.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText
        )
        if not portable:
            raise ValueError("Нажмите пользовательскую комбинацию клавиш.")
        return parse_hotkey(portable).canonical

    def _update_mode_description(self) -> None:
        is_ai_prompt = self.config.output_mode == "ai_prompt"
        self.target_label.setVisible(is_ai_prompt)
        self.target_combo.setVisible(is_ai_prompt)
        self.target_combo.setEnabled(is_ai_prompt and self.state != "recording")
        if is_ai_prompt:
            text = (
                "Поток мыслей будет превращён в понятную задачу для нейросети. "
                "Выберите ChatGPT, Claude, Gemini или универсальный формат."
            )
        elif self.config.output_mode == "verbatim":
            text = (
                "Whisper вставит распознанный текст без AI-редактирования и "
                "удаления повторов. Голосовые команды пунктуации сохраняются."
            )
        elif self.config.output_mode == "custom":
            text = (
                "Расшифровка будет обработана по вашей инструкции через локальную "
                "Ollama. Если она недоступна, Речка сохранит близкий к оригиналу текст."
            )
        else:
            text = (
                "Текст останется близким к вашей речи: исправятся пунктуация, "
                "явные речевые повторы и случайные оговорки."
            )
        self.mode_description.setText(text)

    def _on_output_mode_changed(self, _index: int = -1) -> None:
        reverse_modes = _reverse_map(OUTPUT_MODE_OPTIONS)
        self.config.output_mode = reverse_modes.get(
            self.mode_combo.currentText(),
            "communication",
        )
        self._update_mode_description()
        save_config(self.config)

    def _on_ai_target_changed(self, _index: int = -1) -> None:
        reverse_targets = _reverse_map(AI_TARGET_OPTIONS)
        self.config.ai_target = reverse_targets.get(
            self.target_combo.currentText(),
            "universal",
        )
        save_config(self.config)

    @staticmethod
    def _mode_short_name(mode: str) -> str:
        return {
            "ai_prompt": "AI-промпт",
            "verbatim": "Дословно",
            "custom": "Свой режим",
        }.get(mode, "Общение")

    def _register_hotkey(
        self,
        value: str | None = None,
        *,
        interactive: bool = False,
    ) -> bool:
        hotkey_value = value or self.config.hotkey
        candidate = GlobalHotkey()
        try:
            candidate.start(
                hotkey_value,
                lambda: self.events.put(("toggle", None)),
            )
        except Exception as exc:
            candidate.stop()
            text = f"Не удалось включить {hotkey_label(hotkey_value)}:\n{exc}"
            if interactive:
                QMessageBox.warning(self.window, "Горячая клавиша", text)
            else:
                self.events.put(("hotkey_error", text))
            return False

        previous = self.hotkey
        self.hotkey = candidate
        self._registered_hotkey = hotkey_value
        previous.stop()
        return True

    def _start_cancel_hotkey(self) -> None:
        self.cancel_hotkey.stop()
        try:
            self.cancel_hotkey.start(
                "Escape",
                lambda: self.events.put(("cancel_recording", None)),
                allow_unmodified=True,
            )
        except Exception:
            # Recording remains usable through the main hotkey and button even
            # if another application temporarily owns global Escape.
            self.cancel_hotkey.stop()

    def _stop_cancel_hotkey(self) -> None:
        self.cancel_hotkey.stop()

    def _load_model(self, model_name: str) -> None:
        self._model_generation += 1
        generation = self._model_generation
        self.state = "loading"
        self._set_main_recording_feedback(False)
        self._set_status("Подготовка Whisper…", MUTED)
        self.record_button.setText("Загрузка модели")
        self.record_button.setEnabled(False)
        self._style_primary_button(self.record_button, ACID)
        self.progress.show()

        def worker() -> None:
            try:
                self.engine.load(
                    model_name,
                    status=lambda text: self.events.put(
                        ("model_status", (generation, text))
                    ),
                )
                self.events.put(("model_ready", generation))
            except Exception as exc:
                self.events.put(("model_error", (generation, str(exc))))

        threading.Thread(target=worker, name="model-loader", daemon=True).start()

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status_label.setText(text)
        display_color = "#D8D8D2" if color in {MUTED, ACCENT} else color
        self.status_label.setStyleSheet(
            f"color: {display_color}; font-weight: 600;"
        )
        dot_color = (
            color
            if color in {SUCCESS, RECORD}
            else ("#16A3A5" if color == ACCENT else "#8B8D87")
        )
        self.record_status_dot.setStyleSheet(
            f"color: {dot_color}; font-size: 8pt;"
        )

    def toggle_recording(self) -> None:
        if self._microphone_test_running:
            self._set_status("Сначала завершите проверку микрофона", MUTED)
            return
        if self.state == "ready":
            self._start_recording()
        elif self.state == "recording":
            self._stop_recording()
        elif self.state == "loading":
            self._set_status("Подождите: модель ещё загружается", MUTED)
        elif self.state == "transcribing":
            self._set_status("Распознавание уже выполняется", MUTED)

    def _start_recording(self) -> None:
        self._recording_target_window = foreground_window()
        application_name = window_process_name(self._recording_target_window)
        try:
            profile = match_app_profile(application_name, self.config.app_profiles)
        except ValueError:
            profile = None
        if self.config.sound_feedback:
            play_feedback("start")
        try:
            sample_rate = self.recorder.start(self.config.device_index)
        except Exception as exc:
            self._handle_error(f"Не удалось начать запись: {exc}")
            return

        self._recording_session += 1
        session = self._recording_session
        self._active_output_mode = (
            profile.output_mode if profile is not None else self.config.output_mode
        )
        self._active_ai_target = self.config.ai_target
        self._active_project_context = self.config.project_context
        self._active_custom_terms = combine_custom_terms(
            self.config.custom_terms,
            profile.custom_terms if profile is not None else "",
        )
        self._active_language = self.config.language
        self._active_snippets = self.config.snippets
        self._active_application_name = application_name
        self._active_custom_instruction = self.config.custom_instruction
        self._preview_stop = threading.Event()
        self._latest_preview_text = ""
        self._recording_started_at = time.monotonic()
        self._recording_warning = ""
        self.state = "recording"
        self._set_main_recording_feedback(True)
        mode_name = self._mode_short_name(self._active_output_mode)
        self._set_status(
            f"{mode_name}: идёт запись"
            + (f" · профиль {profile.name}" if profile is not None else ""),
            RECORD,
        )
        self.record_button.setText("Остановить запись")
        self._style_primary_button(self.record_button, RECORD)
        self.mode_combo.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.voice_level.reset()
        self._set_overlay_preview("")
        self.overlay_elapsed.setText("00:00")
        self.overlay_audio_state.setText("Микрофон подключён · начинайте говорить")
        self.overlay_audio_state.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; border: 0;"
        )
        self._show_overlay(
            f"{mode_name} · идёт запись",
            RECORD,
        )
        self._set_tray_color(RECORD)
        self._start_cancel_hotkey()
        threading.Thread(
            target=self._live_preview_worker,
            args=(session, self._preview_stop),
            name="live-preview",
            daemon=True,
        ).start()

    def _stop_recording(self) -> None:
        self._stop_cancel_hotkey()
        if self._preview_stop is not None:
            self._preview_stop.set()
        try:
            clip = self.recorder.stop()
        except Exception as exc:
            self._handle_error(f"Ошибка завершения записи: {exc}")
            return
        self._set_main_recording_feedback(False)

        if clip.status_messages:
            self._recording_warning = (
                "Во время записи микрофон сообщил о пропуске аудиоданных."
            )

        if self.config.sound_feedback:
            play_feedback("stop")
        if clip.duration_seconds < 0.35 or not has_recordable_signal(clip.samples):
            self.state = "ready"
            self.overlay.hide()
            self._set_status("Речь не обнаружена — попробуйте ещё раз", MUTED)
            self.record_button.setText("Начать запись")
            self._style_primary_button(self.record_button, ACID)
            self.mode_combo.setEnabled(True)
            self._update_mode_description()
            self._set_tray_color(ACCENT)
            return

        if clip.clipped_fraction >= 0.01:
            self._recording_warning = (
                "Микрофон перегружен: отодвиньтесь от него или уменьшите усиление."
            )

        self.state = "transcribing"
        mode = self._active_output_mode
        session = self._recording_session
        self._set_status("Перерабатываю всю записанную фразу…", ACCENT)
        self.record_button.setText("Распознавание…")
        self.record_button.setEnabled(False)
        self._style_primary_button(self.record_button, ACID)
        self._set_overlay_preview("")
        self.overlay_audio_state.setText("Запись завершена · обрабатываю результат")
        self._show_overlay(
            "Финальная расшифровка началась…",
            ACCENT,
        )
        self.voice_level.set_level(0.0)
        self._set_tray_color(ACCENT)
        threading.Thread(
            target=self._transcribe_worker,
            args=(clip, session, mode),
            name="transcriber",
            daemon=True,
        ).start()

    def _cancel_recording(self) -> None:
        if self.state != "recording":
            return
        self._stop_cancel_hotkey()
        if self._preview_stop is not None:
            self._preview_stop.set()
        self._recording_session += 1
        self.recorder.abort()
        self.overlay.hide()
        self.state = "ready"
        self._set_main_recording_feedback(False)
        self.record_button.setText("Начать запись")
        self.record_button.setEnabled(True)
        self._style_primary_button(self.record_button, ACID)
        self.mode_combo.setEnabled(True)
        self._update_mode_description()
        self.voice_level.reset()
        self._set_tray_color(ACCENT)
        self._set_status("Запись отменена — ничего не распознано и не вставлено", MUTED)
        if self.config.sound_feedback:
            play_feedback("stop")

    def _live_preview_worker(
        self,
        session: int,
        stop_event: threading.Event,
    ) -> None:
        committed_samples = 0
        stable_text = ""
        last_preview_total = 0
        minimum_first_samples = round(3.0 * 16_000)
        minimum_new_samples = round(2.8 * 16_000)
        overlap_samples = round(1.2 * 16_000)
        maximum_window_samples = round(12.0 * 16_000)
        stability_margin_seconds = 0.9

        if stop_event.wait(0.5):
            return
        while not stop_event.is_set():
            total_samples = self.recorder.sample_count
            enough_audio = total_samples >= minimum_first_samples
            enough_new_audio = (
                total_samples - last_preview_total >= minimum_new_samples
            )
            if enough_audio and enough_new_audio:
                start_sample = max(0, committed_samples - overlap_samples)
                start_sample = max(
                    start_sample,
                    total_samples - maximum_window_samples,
                )
                clip = self.recorder.snapshot(start_sample=start_sample)
                last_preview_total = total_samples
                if not has_recordable_signal(clip.samples):
                    if stop_event.wait(0.25):
                        return
                    continue
                try:
                    segments = self.engine.transcribe_segments(
                        clip.samples,
                        language=self._active_language,
                        beam_size=1,
                        custom_terms=self._active_custom_terms,
                        preview=True,
                    )
                except Exception:
                    return
                if stop_event.is_set():
                    return

                current_text = normalize_transcript(
                    " ".join(segment.text for segment in segments),
                    punctuation_commands=self.config.punctuation_commands,
                )
                if is_reliable_preview_text(current_text):
                    visible_text = merge_incremental_transcript(
                        stable_text,
                        current_text,
                    )
                    self.events.put(("preview", (session, visible_text)))

                stable_cutoff = max(
                    0.0,
                    clip.duration_seconds - stability_margin_seconds,
                )
                stable_segments = [
                    segment
                    for segment in segments
                    if segment.end <= stable_cutoff
                ]
                if stable_segments:
                    stable_part = normalize_transcript(
                        " ".join(segment.text for segment in stable_segments),
                        punctuation_commands=self.config.punctuation_commands,
                    )
                    if is_reliable_preview_text(stable_part):
                        stable_text = merge_incremental_transcript(
                            stable_text,
                            stable_part,
                        )
                        committed_samples = min(
                            total_samples,
                            clip.start_sample
                            + round(stable_segments[-1].end * 16_000),
                        )
            if stop_event.wait(0.3):
                return

    def _transcribe_worker(
        self,
        clip: AudioClip,
        session: int,
        output_mode: str,
    ) -> None:
        try:
            text = self.engine.transcribe(
                clip.samples,
                language=self._active_language,
                beam_size=DECODING_BEAM_SIZES[self.config.decoding_mode],
                custom_terms=self._active_custom_terms,
                punctuation_commands=self.config.punctuation_commands,
            )
            if not text:
                self.events.put(
                    ("transcript", (session, text, ProcessedText(text="")))
                )
                return

            snippet = expand_snippet(text, self._active_snippets)
            if snippet is not None:
                self.events.put(
                    (
                        "transcript",
                        (
                            session,
                            text,
                            ProcessedText(
                                text=snippet,
                                note="Применён персональный сниппет",
                            ),
                        ),
                    )
                )
                return

            stage_text = (
                "Формирую понятный промпт для AI…"
                if output_mode == "ai_prompt"
                else (
                    "Готовлю дословный текст…"
                    if output_mode == "verbatim"
                    else (
                        "Применяю пользовательскую инструкцию…"
                        if output_mode == "custom"
                        else "Аккуратно уточняю формулировку…"
                    )
                )
            )
            self.events.put(
                (
                    "processing_stage",
                    (session, stage_text),
                )
            )
            processed = process_transcript(
                text,
                output_mode,
                use_local_ai=self.config.use_local_ai,
                ollama_model=self.config.ollama_model,
                ai_target=self._active_ai_target,
                project_context=self._active_project_context,
                custom_instruction=self._active_custom_instruction,
            )
            self.events.put(("transcript", (session, text, processed)))
        except Exception as exc:
            self.events.put(("error", f"Ошибка распознавания: {exc}"))

    def _handle_transcript(
        self,
        payload: tuple[int, str, ProcessedText],
    ) -> None:
        session, raw_text, processed = payload
        if session != self._recording_session:
            return
        text = processed.text
        self.overlay.hide()
        self.state = "ready"
        self.record_button.setText("Начать запись")
        self.record_button.setEnabled(True)
        self._style_primary_button(self.record_button, ACID)
        self.mode_combo.setEnabled(True)
        self._update_mode_description()
        self._set_tray_color(ACCENT)

        if not raw_text or not text:
            self._set_status("Whisper не нашёл речи", MUTED)
            return

        self.last_text.setPlainText(text)
        self.raw_text.setPlainText(raw_text)
        self._set_result_available(True)
        self.result_tabs.setCurrentIndex(0)

        action = parse_action_command(raw_text)
        if action and self._execute_action_command(action):
            return

        if self.config.history_enabled:
            try:
                append_history(
                    text=text,
                    raw_text=raw_text,
                    mode=self._active_output_mode,
                    application=self._active_application_name,
                )
                self._refresh_history()
            except OSError:
                pass

        insertion_text = text
        if self.config.append_space and not insertion_text.endswith((" ", "\n", "\t")):
            insertion_text += " "

        inserted = True
        clipboard_snapshot = (
            self._clone_clipboard_data()
            if self.config.insertion_mode == "paste"
            else None
        )
        try:
            insert_text(insertion_text, self.config.insertion_mode)
        except Exception as exc:
            inserted = False
            self._handle_error(f"Текст распознан, но не вставлен: {exc}")
        finally:
            if clipboard_snapshot is not None:
                self._restore_clipboard_later(clipboard_snapshot)

        if inserted:
            can_undo = (
                self.config.insertion_mode == "paste"
                and bool(self._recording_target_window)
            )
            self._last_insertion_window = (
                self._recording_target_window if can_undo else 0
            )
            self.undo_button.setEnabled(can_undo)
            insertion_status = (
                "Готово — текст скопирован"
                if self.config.insertion_mode == "clipboard"
                else "Готово — текст вставлен"
            )
            status = (
                f"{insertion_status}. {processed.note}"
                if processed.note
                else insertion_status
            )
            if self._recording_warning:
                status = f"{status}. {self._recording_warning}"
            self._set_status(status, SUCCESS)

    def _execute_action_command(self, action: str) -> bool:
        try:
            if action == "undo":
                send_undo(self._recording_target_window)
                self._last_insertion_window = 0
                self.undo_button.setEnabled(False)
                self._set_status("Последнее действие в окне диктовки отменено", SUCCESS)
                return True
            if action == "enter":
                send_enter(self._recording_target_window)
                self._set_status("Enter отправлен в окно диктовки", SUCCESS)
                return True
            if action == "repeat":
                self._set_status("Начинаю повторную запись…", MUTED)
                QTimer.singleShot(180, self._repeat_recording)
                return True
        except Exception as exc:
            self._set_status(f"Не удалось выполнить голосовую команду: {exc}", RECORD)
            return True
        return False

    def _clone_clipboard_data(self) -> QMimeData:
        source = self.application.clipboard().mimeData()
        snapshot = QMimeData()
        if source is not None:
            for mime_type in source.formats():
                snapshot.setData(mime_type, source.data(mime_type))
        return snapshot

    def _restore_clipboard_later(self, snapshot: QMimeData) -> None:
        self._clipboard_restore_generation += 1
        generation = self._clipboard_restore_generation

        def restore() -> None:
            if self._closing or generation != self._clipboard_restore_generation:
                return
            self.application.clipboard().setMimeData(snapshot)

        QTimer.singleShot(180, restore)

    def _repeat_recording(self) -> None:
        if self.state != "ready":
            self._set_status("Повторная запись доступна после завершения обработки", MUTED)
            return
        self._start_recording()

    def _undo_last_insertion(self) -> None:
        if not self._last_insertion_window:
            self.undo_button.setEnabled(False)
            self._set_status("Нет вставки, которую можно безопасно отменить", MUTED)
            return
        try:
            send_undo(self._last_insertion_window)
        except Exception as exc:
            self._set_status(f"Не удалось отменить вставку: {exc}", RECORD)
            return
        self._last_insertion_window = 0
        self.undo_button.setEnabled(False)
        self._set_status("Последняя вставка отменена", SUCCESS)

    def _handle_error(self, text: str) -> None:
        self._stop_cancel_hotkey()
        if self._preview_stop is not None:
            self._preview_stop.set()
        self.recorder.abort()
        self._set_main_recording_feedback(False)
        self.overlay.hide()
        self.state = "ready" if self.engine.model_name else "error"
        self.record_button.setText(
            "Начать запись" if self.state == "ready" else "Ошибка"
        )
        self.record_button.setEnabled(self.state == "ready")
        self.mode_combo.setEnabled(self.state == "ready")
        self._update_mode_description()
        self._style_primary_button(
            self.record_button,
            ACID if self.state == "ready" else RECORD,
        )
        self._set_status(text, RECORD)
        self._set_tray_color(RECORD)
        if self.config.sound_feedback:
            play_feedback("error")

    def save_settings(self) -> None:
        reverse_models = _reverse_map(MODEL_OPTIONS)
        reverse_decoding = _reverse_map(DECODING_OPTIONS)
        reverse_modes = _reverse_map(OUTPUT_MODE_OPTIONS)
        reverse_targets = _reverse_map(AI_TARGET_OPTIONS)
        reverse_languages = _reverse_map(LANGUAGE_OPTIONS)
        reverse_insertions = _reverse_map(INSERTION_OPTIONS)

        old_model = self.config.model
        old_hotkey = self.config.hotkey
        try:
            parse_snippets(self.snippets_edit.toPlainText())
            parse_app_profiles(self.app_profiles_edit.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(
                self.window,
                "Персонализация",
                str(exc),
            )
            return
        try:
            selected_hotkey = self._selected_hotkey()
        except ValueError as exc:
            QMessageBox.warning(
                self.window,
                "Горячая клавиша",
                str(exc),
            )
            return
        if (
            selected_hotkey != old_hotkey
            or self._registered_hotkey != selected_hotkey
        ) and not self._register_hotkey(
            selected_hotkey,
            interactive=True,
        ):
            return

        self.config = AppConfig(
            model=reverse_models.get(self.model_combo.currentText(), "base"),
            decoding_mode=reverse_decoding.get(
                self.decoding_combo.currentText(),
                "fast",
            ),
            output_mode=reverse_modes.get(
                self.mode_combo.currentText(),
                "communication",
            ),
            ai_target=reverse_targets.get(
                self.target_combo.currentText(),
                "universal",
            ),
            language=reverse_languages.get(self.language_combo.currentText(), "ru"),
            device_index=self.device_combo.currentData(),
            insertion_mode=reverse_insertions.get(
                self.insertion_combo.currentText(),
                "paste",
            ),
            hotkey=selected_hotkey,
            append_space=self.append_space_check.isChecked(),
            punctuation_commands=self.commands_check.isChecked(),
            start_minimized=self.start_minimized_check.isChecked(),
            autostart=self.autostart_check.isChecked(),
            sound_feedback=self.sound_feedback_check.isChecked(),
            custom_terms=self.custom_terms_edit.text().strip(),
            snippets=self.snippets_edit.toPlainText().strip(),
            app_profiles=self.app_profiles_edit.toPlainText().strip(),
            project_context=self.project_context_edit.text().strip(),
            custom_instruction=self.custom_instruction_edit.toPlainText().strip(),
            history_enabled=self.history_enabled_check.isChecked(),
            use_local_ai=self.use_local_ai_check.isChecked(),
            ollama_model=self.ollama_model_edit.text().strip() or "qwen3:4b",
            beam_size=DECODING_BEAM_SIZES[
                reverse_decoding.get(self.decoding_combo.currentText(), "fast")
            ],
            onboarding_complete=True,
            settings_revision=3,
        )
        save_config(self.config)
        self._mark_settings_saved()

        try:
            self._sync_autostart(silent=False)
        except OSError as exc:
            QMessageBox.warning(
                self.window,
                "Автозапуск",
                f"Настройки сохранены, но автозапуск изменить не удалось:\n{exc}",
            )

        self.hotkey_hint.setText(
            f"{hotkey_label(self.config.hotkey)} — начать/остановить · Esc — отменить"
        )
        self.overlay_hint.setText(
            f"{hotkey_label(self.config.hotkey)} — закончить · Esc — отменить"
        )
        self._update_mode_description()
        self.onboarding_hint.hide()
        self._refresh_history()
        if self.config.model != old_model:
            self._load_model(self.config.model)
        else:
            self._set_status("Настройки сохранены", SUCCESS)

    def check_for_updates(self, manual: bool = True) -> None:
        if self._update_in_progress or self._closing:
            return
        if not self._update_repository:
            if manual:
                QMessageBox.information(
                    self.window,
                    "Обновления",
                    "Канал обновлений будет подключён при публикации первого "
                    "релиза.",
                )
            return

        self._update_in_progress = True
        self.update_button.setEnabled(False)
        self.update_button.setText("Проверка…")
        self.update_status.setText("Проверяю новую версию…")
        self.update_progress.hide()

        def worker() -> None:
            try:
                update = check_for_update(self._update_repository, __version__)
            except Exception as exc:
                self.events.put(("update_error", (manual, str(exc))))
                return
            self.events.put(("update_result", (manual, update)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_update_result(
        self,
        manual: bool,
        update: UpdateInfo | None,
    ) -> None:
        self._update_in_progress = False
        self.update_button.setEnabled(True)
        self.update_button.setText("Проверить обновления")

        if update is None:
            self.update_status.setText(
                f"Установлена актуальная версия {__version__}."
            )
            if manual:
                QMessageBox.information(
                    self.window,
                    "Обновления",
                    f"Установлена актуальная версия {__version__}.",
                )
            return

        notes = update.notes.strip()
        if len(notes) > 700:
            notes = notes[:697].rstrip() + "…"
        details = f"\n\nЧто изменилось:\n{notes}" if notes else ""
        size_mb = update.asset.size / (1024 * 1024)
        answer = QMessageBox.question(
            self.window,
            "Доступно обновление",
            f"Доступна версия {update.version} ({size_mb:.0f} МБ)."
            f"{details}\n\nСкачать и установить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.update_status.setText(
                f"Версия {update.version} доступна — можно установить позже."
            )
            return
        self._download_update(update)

    def _download_update(self, update: UpdateInfo) -> None:
        self._update_in_progress = True
        self.update_button.setEnabled(False)
        self.update_button.setText("Загрузка 0%")
        self.update_status.setText(
            f"Скачиваю «Речку» {update.version} и проверяю файл…"
        )
        self.update_progress.setValue(0)
        self.update_progress.show()

        def progress(downloaded: int, total: int) -> None:
            percent = min(100, round(downloaded * 100 / max(total, 1)))
            self.events.put(("update_progress", percent))

        def worker() -> None:
            try:
                path = download_update(update, progress=progress)
            except Exception as exc:
                self.events.put(("update_download_error", str(exc)))
                return
            self.events.put(("update_downloaded", path))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_update_button(self) -> None:
        self._update_in_progress = False
        self.update_button.setEnabled(True)
        self.update_button.setText("Проверить обновления")

    def _install_downloaded_update(self, path: Path) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("Установка…")
        self.update_progress.setValue(100)
        self.update_status.setText(
            "Файл проверен. Закрываю приложение и устанавливаю обновление…"
        )
        try:
            launch_update_installer(path)
        except Exception as exc:
            QMessageBox.warning(
                self.window,
                "Обновление",
                f"Не удалось запустить установщик:\n{exc}",
            )
            self._reset_update_button()
            self.update_status.setText("Автоматическая установка не запустилась.")
            return
        QTimer.singleShot(80, self.exit_app)

    def _copy_last_result(self) -> None:
        editor = self.result_tabs.currentWidget()
        text = (
            editor.toPlainText().strip()
            if isinstance(editor, QTextEdit)
            else ""
        )
        if text:
            self.application.clipboard().setText(text)
            self._set_status(
                (
                    "Исходная расшифровка скопирована"
                    if editor is self.raw_text
                    else "Готовый текст скопирован"
                ),
                SUCCESS,
            )

    def _apply_quick_action(self) -> None:
        text = self.last_text.toPlainText().strip()
        if not text:
            self._set_status("Сначала выполните диктовку", MUTED)
            return
        reverse_actions = _reverse_map(QUICK_ACTION_OPTIONS)
        action = reverse_actions.get(self.quick_action_combo.currentText(), "message")
        try:
            transformed = apply_quick_action(text, action)
        except ValueError as exc:
            self._set_status(str(exc), RECORD)
            return
        self.last_text.setPlainText(transformed)
        self.result_tabs.setCurrentIndex(0)
        self._set_status(
            "Результат преобразован; уже вставленный текст не изменён",
            SUCCESS,
        )

    def _export_personalization(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self.window,
            "Экспорт персонализации Речки",
            str(Path.home() / "rechka-personalization.json"),
            "JSON (*.json)",
        )
        if not filename:
            return
        try:
            export_personalization(Path(filename), self.config)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self.window,
                "Экспорт персонализации",
                f"Не удалось сохранить файл:\n{exc}",
            )
            return
        self._set_status("Персонализация экспортирована", SUCCESS)

    def _import_personalization(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self.window,
            "Импорт персонализации Речки",
            str(Path.home()),
            "JSON (*.json)",
        )
        if not filename:
            return
        try:
            data = import_personalization(Path(filename))
            parse_snippets(str(data.get("snippets", "")))
            parse_app_profiles(str(data.get("app_profiles", "")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(
                self.window,
                "Импорт персонализации",
                f"Не удалось прочитать файл:\n{exc}",
            )
            return

        if "custom_terms" in data:
            self.custom_terms_edit.setText(str(data["custom_terms"]))
        if "snippets" in data:
            self.snippets_edit.setPlainText(str(data["snippets"]))
        if "app_profiles" in data:
            self.app_profiles_edit.setPlainText(str(data["app_profiles"]))
        if "project_context" in data:
            self.project_context_edit.setText(str(data["project_context"]))
        if "custom_instruction" in data:
            self.custom_instruction_edit.setPlainText(
                str(data["custom_instruction"])
            )
        if "history_enabled" in data:
            self.history_enabled_check.setChecked(bool(data["history_enabled"]))
        self.save_settings()
        self._set_status("Персонализация импортирована и сохранена", SUCCESS)

    def _refresh_history(self) -> None:
        if not self.config.history_enabled:
            self.history_status.setText(
                "История выключена. Речка не сохраняет распознанный текст. "
                "Включить её можно в разделе «Персонализация»."
            )
            self.history_view.setPlainText("История не ведётся.")
            return
        entries = load_history(limit=50)
        self.history_status.setText(
            f"Сохранено локально: {len(entries)} из последних 50 записей. Аудио не хранится."
        )
        if not entries:
            self.history_view.setPlainText("История пока пуста.")
            return
        blocks: list[str] = []
        for entry in reversed(entries):
            created = entry.created_at[:16].replace("T", " ")
            application = f" · {entry.application}" if entry.application else ""
            block = f"[{created}] {entry.mode}{application}\n{entry.text}"
            if entry.raw_text and entry.raw_text != entry.text:
                block += f"\nИсходная расшифровка: {entry.raw_text}"
            blocks.append(block)
        self.history_view.setPlainText("\n\n".join(blocks))

    def _copy_history(self) -> None:
        text = self.history_view.toPlainText().strip()
        if text and text not in {"История не ведётся.", "История пока пуста."}:
            self.application.clipboard().setText(text)
            self._set_status("История скопирована", SUCCESS)

    def _clear_history(self) -> None:
        if not load_history(limit=1):
            self._refresh_history()
            return
        answer = QMessageBox.question(
            self.window,
            "Удалить историю",
            "Удалить всю локальную историю распознанного текста?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_history()
        except OSError as exc:
            QMessageBox.warning(
                self.window,
                "Удалить историю",
                f"Не удалось удалить историю:\n{exc}",
            )
            return
        self._refresh_history()
        self._set_status("Локальная история удалена", SUCCESS)

    def _clear_last_result(self) -> None:
        self.last_text.clear()
        self.raw_text.clear()
        self._set_result_available(False)

    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_settings(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.show_window()

    def hide_window(self) -> None:
        self.window.hide()

    def exit_app(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.timer.stop()
        if self._preview_stop is not None:
            self._preview_stop.set()
        self.recorder.abort()
        if self._microphone_test_recorder is not None:
            self._microphone_test_recorder.abort()
        self.hotkey.stop()
        self.cancel_hotkey.stop()
        self.overlay.hide()
        self.tray.hide()
        self.window.allow_close = True
        self.window.close()
        self.application.quit()

    def _process_events(self) -> None:
        if self._closing:
            return
        if (
            self._show_settings_event
            and consume_show_settings_event(self._show_settings_event)
        ):
            self.show_settings()
        if self.state == "recording":
            health_error = self.recorder.health_error
            if health_error:
                self._handle_error(
                    f"Запись остановлена: {health_error}. "
                    "Проверьте подключение и выберите микрофон заново."
                )
                return
            level = self.recorder.current_level
            self.voice_level.set_level(level)
            self.main_voice_level.set_level(level)
            elapsed = max(0.0, time.monotonic() - self._recording_started_at)
            minutes, seconds = divmod(int(elapsed), 60)
            elapsed_text = f"{minutes:02d}:{seconds:02d}"
            self.overlay_elapsed.setText(elapsed_text)
            self.main_record_elapsed.setText(elapsed_text)
            if level >= 0.16:
                self.overlay_audio_state.setText(
                    "Голос слышу · звук записывается"
                )
                self.overlay_audio_state.setStyleSheet(
                    f"color: {SUCCESS}; font-size: 8.5pt; "
                    "font-weight: 600; border: 0;"
                )
            elif elapsed >= 1.5:
                self.overlay_audio_state.setText(
                    "Сейчас тихо · скажите что-нибудь или проверьте микрофон"
                )
                self.overlay_audio_state.setStyleSheet(
                    f"color: {MUTED}; font-size: 8.5pt; border: 0;"
                )
            dot_alpha = 255 if int(elapsed * 2) % 2 == 0 else 120
            self.overlay_dot.setStyleSheet(
                f"color: rgba(229, 72, 77, {dot_alpha}); "
                "font-size: 10pt; border: 0;"
            )
            self.record_status_dot.setStyleSheet(
                f"color: rgba(229, 72, 77, {dot_alpha}); "
                "font-size: 8pt; border: 0;"
            )
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "toggle":
                self.toggle_recording()
            elif event == "cancel_recording":
                self._cancel_recording()
            elif event == "show":
                self.show_window()
            elif event == "settings":
                self.show_settings()
            elif event == "exit":
                self.exit_app()
                return
            elif event == "model_status":
                generation, text = payload
                if generation == self._model_generation:
                    self._set_status(text, MUTED)
            elif event == "model_ready":
                if payload == self._model_generation:
                    self.state = "ready"
                    self.progress.hide()
                    self.record_button.setText("Начать запись")
                    self.record_button.setEnabled(True)
                    self._style_primary_button(self.record_button, ACID)
                    self._set_status("Готово к диктовке", SUCCESS)
                    self._set_tray_color(ACCENT)
                    if not self.config.onboarding_complete and not self.start_minimized:
                        self.tabs.setCurrentIndex(1)
                        self.show_window()
            elif event == "model_error":
                generation, text = payload
                if generation == self._model_generation:
                    self.progress.hide()
                    self._handle_error(f"Не удалось загрузить модель: {text}")
            elif event == "transcript":
                self._handle_transcript(payload)
            elif event == "preview":
                session, text = payload
                if session == self._recording_session and self.state == "recording":
                    self._latest_preview_text = text
                    self._set_overlay_preview(text)
                    self._position_overlay()
            elif event == "microphone_test_result":
                self._handle_microphone_test_result(payload)
            elif event == "microphone_test_error":
                self._handle_microphone_test_error(payload)
            elif event == "diagnostics_ready":
                self._reset_diagnostics_button()
                self.application.clipboard().setText(
                    json.dumps(payload, ensure_ascii=False, indent=2)
                )
                self._set_status(
                    "Диагностика скопирована — аудио и текст в неё не входят",
                    SUCCESS,
                )
            elif event == "diagnostics_error":
                self._reset_diagnostics_button()
                self._set_status(
                    f"Не удалось собрать диагностику: {payload}",
                    RECORD,
                )
            elif event == "hotkey_error":
                self._set_status(payload, RECORD)
            elif event == "processing_stage":
                session, text = payload
                if session == self._recording_session and self.state == "transcribing":
                    self._set_status(text, ACCENT)
                    self._show_overlay(text, ACCENT)
            elif event == "error":
                self._handle_error(payload)
            elif event == "update_result":
                manual, update = payload
                self._handle_update_result(manual, update)
            elif event == "update_error":
                manual, text = payload
                self._reset_update_button()
                self.update_status.setText("Не удалось проверить обновления.")
                if manual:
                    QMessageBox.warning(self.window, "Обновления", text)
            elif event == "update_progress":
                self.update_button.setText(f"Загрузка {payload}%")
                self.update_progress.setValue(payload)
            elif event == "update_download_error":
                self._reset_update_button()
                self.update_progress.hide()
                self.update_status.setText("Не удалось скачать обновление.")
                QMessageBox.warning(
                    self.window,
                    "Обновление",
                    f"Не удалось скачать обновление:\n{payload}",
                )
            elif event == "update_downloaded":
                self._install_downloaded_update(payload)
