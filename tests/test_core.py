from __future__ import annotations

import os
import ctypes
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from voice_input.audio import prepare_audio_for_whisper, resample_audio
from voice_input.config import (
    AI_TARGET_OPTIONS,
    DECODING_BEAM_SIZES,
    MODEL_OPTIONS,
    OUTPUT_MODE_OPTIONS,
    AppConfig,
    load_config,
    save_config,
)
from voice_input.engine import (
    choose_chunk_length,
    merge_incremental_transcript,
    normalize_transcript,
)
from voice_input.hotkeys import hotkey_label, parse_hotkey
from voice_input.prompting import build_prompt_fallback, polish_communication_text
from voice_input.updater import (
    UpdateError,
    parse_version,
    update_from_release_payload,
)
from voice_input.windows import INPUT, _feedback_wave, physical_core_count


class AudioTests(unittest.TestCase):
    def test_resample_length(self) -> None:
        source = np.linspace(-1, 1, 44_100, dtype=np.float32)
        result = resample_audio(source, 44_100, 16_000)
        self.assertEqual(result.dtype, np.float32)
        self.assertEqual(result.size, 16_000)

    def test_quiet_audio_is_normalized_without_clipping(self) -> None:
        source = np.full(16_000, 0.01, dtype=np.float32)
        source[::2] *= -1
        result = prepare_audio_for_whisper(source)
        result_rms = float(np.sqrt(np.mean(np.square(result), dtype=np.float64)))
        self.assertGreaterEqual(result_rms, 0.039)
        self.assertLessEqual(float(np.max(np.abs(result))), 0.98)


class TextTests(unittest.TestCase):
    def test_normalization_and_commands(self) -> None:
        text = normalize_transcript(
            "  привет   мир поставь точку новая строка как дела вопросительный знак "
        )
        self.assertEqual(text, "Привет мир.\nКак дела?")

    def test_chunk_length_has_safe_bounds(self) -> None:
        self.assertEqual(choose_chunk_length(2 * 16_000), 30)
        self.assertEqual(choose_chunk_length(10.4 * 16_000), 30)
        self.assertEqual(choose_chunk_length(45 * 16_000), 30)

    def test_incremental_preview_merges_overlapping_words(self) -> None:
        previous = "Мне нужно быстро сформулировать мысль"
        current = "сформулировать мысль и вставить текст"
        self.assertEqual(
            merge_incremental_transcript(previous, current),
            "Мне нужно быстро сформулировать мысль и вставить текст",
        )

    def test_communication_cleanup_is_conservative(self) -> None:
        text = polish_communication_text("Эм, я я хочу оставить этот смысл.")
        self.assertEqual(text, "Я хочу оставить этот смысл.")

    def test_prompt_fallback_preserves_source(self) -> None:
        source = "Нужно ускорить расшифровку и сохранить точность."
        prompt = build_prompt_fallback(
            source,
            target="claude",
            project_context="Локальное приложение Windows",
        )
        self.assertIn(source, prompt)
        self.assertIn("главную цель", prompt)
        self.assertIn("Локальное приложение Windows", prompt)
        self.assertIn("Claude", prompt)


class ConfigTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                expected = AppConfig(model="base", language="auto", custom_terms="Codex")
                save_config(expected)
                actual = load_config()
                self.assertEqual(actual, expected)
                self.assertTrue((Path(directory) / "settings.json").exists())
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_fast_mode_and_turbo_model_are_available(self) -> None:
        self.assertEqual(DECODING_BEAM_SIZES["fast"], 1)
        self.assertIn("turbo", MODEL_OPTIONS)
        self.assertIn("ai_prompt", OUTPUT_MODE_OPTIONS)
        self.assertIn("chatgpt", AI_TARGET_OPTIONS)

    def test_legacy_medium_config_migrates_to_fast_small(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                (Path(directory) / "settings.json").write_text(
                    json.dumps({"model": "medium", "beam_size": 3}),
                    encoding="utf-8",
                )
                actual = load_config()
                self.assertEqual(actual.model, "base")
                self.assertEqual(actual.decoding_mode, "fast")
                self.assertFalse(actual.sound_feedback)
                self.assertFalse(actual.use_local_ai)
                self.assertEqual(actual.hotkey, "Ctrl+Alt+Space")
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_slow_beta_settings_migrate_to_recommended_profile(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                (Path(directory) / "settings.json").write_text(
                    json.dumps(
                        {
                            "model": "turbo",
                            "decoding_mode": "fast",
                            "hotkey": "ctrl_alt_space",
                            "use_local_ai": True,
                        }
                    ),
                    encoding="utf-8",
                )
                actual = load_config()
                self.assertEqual(actual.model, "base")
                self.assertEqual(actual.hotkey, "Ctrl+Alt+Space")
                self.assertFalse(actual.use_local_ai)
                self.assertEqual(actual.settings_revision, 2)
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous


class UpdaterTests(unittest.TestCase):
    def test_version_parser(self) -> None:
        self.assertEqual(parse_version("v0.3.1"), (0, 3, 1))
        self.assertGreater(parse_version("0.4.0"), parse_version("0.3.9"))

    def test_release_asset_requires_github_digest(self) -> None:
        payload = {
            "tag_name": "v0.4.0",
            "html_url": "https://github.com/example/voiceinput/releases/tag/v0.4.0",
            "draft": False,
            "body": "Новый релиз",
            "assets": [
                {
                    "name": "VoiceInput-Setup-0.4.0.exe",
                    "browser_download_url": (
                        "https://github.com/example/voiceinput/releases/download/"
                        "v0.4.0/VoiceInput-Setup-0.4.0.exe"
                    ),
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
        update = update_from_release_payload(payload, "0.3.1")
        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.version, "0.4.0")
        self.assertEqual(update.asset.sha256, "a" * 64)
        self.assertIsNone(update_from_release_payload(payload, "0.4.0"))

        del payload["assets"][0]["digest"]
        with self.assertRaises(UpdateError):
            update_from_release_payload(payload, "0.3.1")


class WindowsInteropTests(unittest.TestCase):
    def test_custom_hotkey_parser(self) -> None:
        specification = parse_hotkey("Ctrl+Shift+F9")
        self.assertEqual(specification.canonical, "Ctrl+Shift+F9")
        self.assertEqual(hotkey_label("ctrl_alt_space"), "Ctrl + Alt + Пробел")
        with self.assertRaises(ValueError):
            parse_hotkey("R")

    def test_send_input_structure_size(self) -> None:
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)

    def test_physical_core_count_is_sensible(self) -> None:
        self.assertGreaterEqual(physical_core_count(), 1)
        self.assertLessEqual(physical_core_count(), os.cpu_count() or 1)

    def test_soft_feedback_is_a_wave_file(self) -> None:
        self.assertTrue(_feedback_wave("start").startswith(b"RIFF"))


if __name__ == "__main__":
    unittest.main()
