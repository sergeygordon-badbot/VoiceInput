from __future__ import annotations

import os
import ctypes
import io
import json
import tempfile
import time
import unittest
import wave
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from voice_input.actions import parse_action_command
from voice_input.backup import export_personalization, import_personalization
from voice_input.audio import (
    AudioRecorder,
    PREVIEW_BUFFER_SECONDS,
    analyze_audio,
    has_recordable_signal,
    prepare_audio_for_whisper,
    resample_audio,
)
from voice_input.benchmarking import aggregate_scores, score_transcript
from voice_input.benchmark_corpus import (
    append_manifest_case,
    load_manifest_payload,
    validate_case_id,
    write_mono_wav,
)
from voice_input.config import (
    AI_TARGET_OPTIONS,
    DECODING_BEAM_SIZES,
    MODEL_OPTIONS,
    OUTPUT_MODE_OPTIONS,
    RECOGNITION_MODE_OPTIONS,
    AppConfig,
    data_dir,
    load_config,
    save_config,
)
from voice_input.engine import (
    DEFAULT_VAD_PROFILE,
    VAD_PROFILES,
    WhisperEngine,
    apply_custom_terms,
    choose_chunk_length,
    detect_speech_regions,
    is_reliable_preview_text,
    merge_incremental_transcript,
    normalize_transcript,
    parse_custom_terms,
)
from voice_input.diagnostics import _package_versions, collect_diagnostics
from voice_input.hardware import InferenceProfile, assess_computer, detect_inference_profile
from voice_input.history import append_history, clear_history, load_history
from voice_input.hotkeys import hotkey_label, parse_hotkey
from voice_input.prompting import (
    build_prompt_fallback,
    improve_communication_punctuation,
    polish_communication_text,
    process_transcript,
)
from voice_input.quick_actions import apply_quick_action
from voice_input.recognition import (
    HuggingFaceSpaceProvider,
    RecognitionMetrics,
    RecognitionProviderError,
    encode_mono_wav,
    parse_gradio_sse,
    timed_provider_transcription,
)
from voice_input.personalization import (
    combine_custom_terms,
    expand_snippet,
    match_app_profile,
    parse_app_profiles,
    parse_snippets,
)
from voice_input.updater import (
    UpdateError,
    check_for_update,
    launch_update_installer,
    parse_version,
    update_from_release_payload,
)
from voice_input.windows import (
    INPUT,
    _feedback_wave,
    close_handle,
    consume_show_settings_event,
    create_show_settings_event,
    physical_core_count,
    signal_show_settings_event,
)


class AudioTests(unittest.TestCase):
    def test_resample_length(self) -> None:
        source = np.linspace(-1, 1, 44_100, dtype=np.float32)
        result = resample_audio(source, 44_100, 16_000)
        self.assertEqual(result.dtype, np.float32)
        self.assertEqual(result.size, 16_000)

    def test_resample_filters_frequencies_above_nyquist(self) -> None:
        sample_rate = 48_000
        timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
        source = np.sin(2 * np.pi * 12_000 * timeline).astype(np.float32)
        result = resample_audio(source, sample_rate, 16_000)
        central = result[500:-500]
        rms = float(np.sqrt(np.mean(np.square(central), dtype=np.float64)))
        self.assertLess(rms, 0.01)

    def test_quiet_signal_is_not_rejected_as_silence(self) -> None:
        quiet = np.full(16_000, 2e-5, dtype=np.float32)
        quiet[::2] *= -1
        self.assertTrue(has_recordable_signal(quiet))
        self.assertFalse(has_recordable_signal(np.zeros(16_000, dtype=np.float32)))

    def test_audio_quality_reports_clipping(self) -> None:
        clipped = np.ones(1_000, dtype=np.float32)
        quality = analyze_audio(clipped)
        self.assertAlmostEqual(quality.peak, 1.0)
        self.assertAlmostEqual(quality.clipped_fraction, 1.0)

    def test_quiet_audio_is_normalized_without_clipping(self) -> None:
        source = np.full(16_000, 0.01, dtype=np.float32)
        source[::2] *= -1
        result = prepare_audio_for_whisper(source)
        result_rms = float(np.sqrt(np.mean(np.square(result), dtype=np.float64)))
        self.assertGreaterEqual(result_rms, 0.039)
        self.assertLessEqual(float(np.max(np.abs(result))), 0.98)

    def test_very_quiet_audio_receives_bounded_gain(self) -> None:
        source = np.full(16_000, 5e-5, dtype=np.float32)
        source[::2] *= -1
        result = prepare_audio_for_whisper(source)
        source_rms = float(np.sqrt(np.mean(np.square(source))))
        result_rms = float(np.sqrt(np.mean(np.square(result))))
        self.assertGreater(result_rms, source_rms * 7.9)
        self.assertLessEqual(float(np.max(np.abs(result))), 0.98)

    def test_preview_snapshot_copies_only_requested_tail(self) -> None:
        recorder = AudioRecorder()
        with recorder._lock:
            recorder._sample_rate = 16_000
            recorder._chunks = [
                np.arange(0, 16_000, dtype=np.float32),
                np.arange(16_000, 32_000, dtype=np.float32),
            ]
            recorder._total_frames = 32_000

        clip = recorder.snapshot(start_sample=24_000)

        self.assertEqual(clip.start_sample, 24_000)
        self.assertEqual(clip.total_samples, 32_000)
        self.assertEqual(clip.samples.size, 8_000)
        self.assertEqual(float(clip.samples[0]), 24_000.0)

    def test_preview_buffer_stays_bounded_during_long_recording(self) -> None:
        recorder = AudioRecorder()
        recorder._sample_rate = 16_000
        for second in range(60):
            chunk = np.full(16_000, second, dtype=np.float32)
            with recorder._lock:
                recorder._append_preview_chunk_locked(chunk)
                recorder._total_frames += chunk.size

        self.assertEqual(
            recorder._buffered_frames,
            PREVIEW_BUFFER_SECONDS * 16_000,
        )
        self.assertEqual(recorder._buffer_start_frame, 40 * 16_000)
        clip = recorder.snapshot(start_sample=55 * 16_000)
        self.assertEqual(clip.samples.size, 5 * 16_000)
        self.assertEqual(float(clip.samples[0]), 55.0)

    def test_spool_preserves_complete_recording_and_is_deleted(self) -> None:
        class FakeStream:
            active = True

            def stop(self) -> None:
                raise OSError("device disconnected")

            def close(self) -> None:
                return None

        recorder = AudioRecorder()
        recorder._sample_rate = 48_000
        recorder._start_spool()
        source = np.sin(
            2 * np.pi * 440 * np.arange(3 * 48_000, dtype=np.float32) / 48_000
        ).astype(np.float32)
        spool_path = recorder._spool_path
        self.assertIsNotNone(recorder._spool_queue)
        recorder._spool_queue.put(source)
        recorder._total_frames = source.size
        recorder._stream = FakeStream()

        clip = recorder.stop()

        self.assertEqual(clip.samples.size, 3 * 16_000)
        self.assertTrue(
            any("device disconnected" in item for item in clip.status_messages)
        )
        self.assertFalse(spool_path.exists())

    @patch("voice_input.audio.sd.InputStream", side_effect=OSError("device busy"))
    @patch("voice_input.audio.sd.check_input_settings")
    @patch(
        "voice_input.audio.sd.query_devices",
        return_value={"default_samplerate": 48_000},
    )
    def test_failed_start_removes_temporary_spool(
        self,
        _query: object,
        _check: object,
        _stream: object,
    ) -> None:
        recorder = AudioRecorder()
        with self.assertRaisesRegex(OSError, "device busy"):
            recorder.start(0)
        self.assertIsNone(recorder._spool_path)
        self.assertIsNone(recorder._spool_thread)

    def test_inactive_microphone_stream_is_reported(self) -> None:
        class FakeStream:
            active = False

        recorder = AudioRecorder()
        recorder._stream = FakeStream()
        recorder._started_at = time.monotonic() - 1.0
        self.assertIn("Микрофон отключён", recorder.health_error)
        recorder.abort()


class TextTests(unittest.TestCase):
    def test_action_commands_require_the_whole_transcript(self) -> None:
        self.assertEqual(parse_action_command("Отмени последнее."), "undo")
        self.assertEqual(parse_action_command("Нажми Enter!"), "enter")
        self.assertEqual(parse_action_command("Запиши заново"), "repeat")
        self.assertIsNone(
            parse_action_command("Объясни, как отменить последнее действие")
        )

    def test_verbatim_mode_does_not_polish_the_transcript(self) -> None:
        source = "Эм, я я хочу оставить это дословно."
        processed = process_transcript(
            source,
            "verbatim",
            use_local_ai=True,
        )
        self.assertEqual(processed.text, source)
        self.assertFalse(processed.used_local_ai)

    def test_custom_mode_has_safe_local_fallback(self) -> None:
        processed = process_transcript(
            "Эм, подготовь подготовь отчёт.",
            "custom",
            use_local_ai=False,
            custom_instruction="Сделай краткий отчёт",
        )
        self.assertEqual(processed.text, "Подготовь отчёт.")
        self.assertIn("требует", processed.note)

    def test_quick_actions_transform_without_changing_source(self) -> None:
        source = "Первый пункт. Второй пункт!"
        self.assertEqual(
            apply_quick_action(source, "list"),
            "• Первый пункт.\n• Второй пункт!",
        )
        self.assertEqual(
            apply_quick_action(source, "task"),
            "- [ ] Первый пункт.\n- [ ] Второй пункт!",
        )
        self.assertIn("Здравствуйте!", apply_quick_action(source, "email"))

    def test_normalization_and_commands(self) -> None:
        text = normalize_transcript(
            "  привет   мир поставь точку новая строка как дела вопросительный знак "
        )
        self.assertEqual(text, "Привет мир.\nКак дела?")

    def test_spoken_question_command_does_not_leave_extra_period(self) -> None:
        text = normalize_transcript("как дела поставь вопросительный знак.")
        self.assertEqual(text, "Как дела?")

    def test_spoken_question_command_tolerates_recognition_inflection(self) -> None:
        text = normalize_transcript("поставь в вопросительных знак.")
        self.assertEqual(text, "?")

    def test_command_linking_conjunction_is_removed(self) -> None:
        text = normalize_transcript(
            "подготовь сообщение и поставь вопросительный знак."
        )
        self.assertEqual(text, "Подготовь сообщение?")

    def test_spoken_dash_and_hyphen_commands_are_distinct(self) -> None:
        self.assertEqual(
            normalize_transcript("главное поставь тире сохранить смысл"),
            "Главное — сохранить смысл",
        )
        self.assertEqual(
            normalize_transcript("северо поставь дефис западный"),
            "Северо-западный",
        )

    def test_spoken_semicolon_command_is_not_partly_consumed(self) -> None:
        self.assertEqual(
            normalize_transcript("первое поставь точку с запятой второе"),
            "Первое; второе",
        )

    def test_custom_terms_preserve_casing_and_explicit_aliases(self) -> None:
        glossary = "Codex, GitHub; PostgreSQL = постгрес | пост грес"
        text = apply_custom_terms(
            "открой codex и пост грес, но не githubчик",
            glossary,
        )
        self.assertEqual(text, "открой Codex и PostgreSQL, но не githubчик")
        entries = parse_custom_terms(glossary)
        self.assertEqual([entry.canonical for entry in entries], ["Codex", "GitHub", "PostgreSQL"])

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

    def test_live_preview_rejects_repetitive_hallucination(self) -> None:
        hallucination = " ".join(["я не знаю что я не знаю"] * 6)
        self.assertFalse(is_reliable_preview_text(hallucination))
        self.assertTrue(
            is_reliable_preview_text(
                "Проверяю запись микрофона и диктую нормальное предложение"
            )
        )

    def test_final_transcription_keeps_segments_after_thirty_seconds(self) -> None:
        class Segment:
            def __init__(self, start: float, end: float, text: str) -> None:
                self.start = start
                self.end = end
                self.text = text

        class FakeModel:
            def __init__(self) -> None:
                self.options: dict[str, object] = {}

            def transcribe(self, samples: object, **options: object) -> object:
                self.options = options
                self.sample_count = len(samples)
                segments = [
                    Segment(0.0, 28.0, "первая часть"),
                    Segment(31.0, 52.0, "вторая часть"),
                ]
                if options.get("without_timestamps"):
                    segments = segments[:1]
                return iter(segments), object()

        engine = WhisperEngine(cpu_threads=1)
        fake_model = FakeModel()
        engine._model = fake_model

        with patch(
            "voice_input.engine.get_speech_timestamps",
            return_value=[{"start": 0, "end": 55 * 16_000}],
        ):
            result = engine.transcribe(
                np.zeros(55 * 16_000, dtype=np.float32),
                language="ru",
            )

        self.assertEqual(result, "Первая часть вторая часть")
        self.assertFalse(fake_model.options["without_timestamps"])
        self.assertFalse(fake_model.options["vad_filter"])
        self.assertTrue(fake_model.options["condition_on_previous_text"])
        self.assertEqual(fake_model.sample_count, 55 * 16_000)

    def test_vad_guard_skips_whisper_when_no_speech_is_found(self) -> None:
        class FakeModel:
            called = False

            def transcribe(self, _samples: object, **_options: object) -> object:
                self.called = True
                return iter(()), object()

        engine = WhisperEngine(cpu_threads=1)
        fake_model = FakeModel()
        engine._model = fake_model

        with patch("voice_input.engine.get_speech_timestamps", return_value=[]):
            result = engine.transcribe(np.zeros(5 * 16_000, dtype=np.float32))

        self.assertEqual(result, "")
        self.assertFalse(fake_model.called)

    def test_vad_regions_preserve_speech_around_a_pause(self) -> None:
        samples = np.ones(5 * 16_000, dtype=np.float32)
        with patch(
            "voice_input.engine.get_speech_timestamps",
            return_value=[
                {"start": 8_000, "end": 24_000},
                {"start": 40_000, "end": 72_000},
            ],
        ):
            regions = detect_speech_regions(samples, DEFAULT_VAD_PROFILE)

        self.assertEqual(regions, ((8_000, 24_000), (40_000, 72_000)))
        self.assertIn("strict", VAD_PROFILES)
        self.assertIsNone(VAD_PROFILES["off"])

    def test_final_vad_concatenates_regions_before_whisper(self) -> None:
        class FakeModel:
            sample_count = 0

            def transcribe(self, samples: object, **_options: object) -> object:
                self.sample_count = len(samples)
                return iter(()), object()

        engine = WhisperEngine(cpu_threads=1)
        fake_model = FakeModel()
        engine._model = fake_model
        with patch(
            "voice_input.engine.get_speech_timestamps",
            return_value=[
                {"start": 0, "end": 16_000},
                {"start": 32_000, "end": 64_000},
            ],
        ):
            engine.transcribe(np.ones(5 * 16_000, dtype=np.float32))

        self.assertEqual(fake_model.sample_count, 3 * 16_000)

    def test_unknown_vad_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Неизвестный VAD-профиль"):
            detect_speech_regions(np.ones(16_000, dtype=np.float32), "missing")

    def test_communication_cleanup_is_conservative(self) -> None:
        text = polish_communication_text("Эм, я я хочу оставить этот смысл.")
        self.assertEqual(text, "Я хочу оставить этот смысл.")

    def test_communication_mode_marks_obvious_direct_questions(self) -> None:
        self.assertEqual(
            polish_communication_text("где находится файл."),
            "Где находится файл?",
        )
        self.assertEqual(
            polish_communication_text("можешь проверить обновление"),
            "Можешь проверить обновление?",
        )
        self.assertEqual(
            polish_communication_text("ты можешь проверить обновление."),
            "Ты можешь проверить обновление?",
        )
        self.assertEqual(
            polish_communication_text("подскажите пожалуйста где установщик"),
            "Подскажите, пожалуйста, где установщик?",
        )

    def test_communication_mode_does_not_rewrite_indirect_question(self) -> None:
        self.assertEqual(
            polish_communication_text("я не знаю, где находится файл."),
            "Я не знаю, где находится файл.",
        )

    def test_communication_punctuation_uses_russian_dash(self) -> None:
        self.assertEqual(
            improve_communication_punctuation("Главное - сохранить смысл."),
            "Главное — сохранить смысл.",
        )
        self.assertEqual(
            improve_communication_punctuation("Как красиво."),
            "Как красиво.",
        )

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


class BenchmarkTests(unittest.TestCase):
    def test_scoring_ignores_case_and_punctuation(self) -> None:
        score = score_transcript("Привет, Ёлка!", "привет елка")
        self.assertEqual(score.word_edits, 0)
        self.assertEqual(score.char_edits, 0)

    def test_aggregate_wer_is_weighted_by_reference_length(self) -> None:
        scores = [
            score_transcript("один два три четыре", "один два три"),
            score_transcript("пять", "ошибка"),
        ]
        aggregate = aggregate_scores(scores)
        self.assertEqual(aggregate["word_edits"], 2)
        self.assertAlmostEqual(aggregate["wer"], 0.4)

    def test_silence_hallucinations_are_counted(self) -> None:
        scores = [
            score_transcript("", ""),
            score_transcript("", "выдуманный текст"),
        ]
        aggregate = aggregate_scores(scores)
        self.assertEqual(aggregate["silence_cases"], 2)
        self.assertEqual(aggregate["hallucinated_words"], 2)


class BenchmarkCorpusTests(unittest.TestCase):
    def test_wav_writer_creates_clipped_mono_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio" / "sample.wav"
            write_mono_wav(
                path,
                np.array([np.nan, -2.0, -0.5, 0.5, 2.0], dtype=np.float32),
            )

            with wave.open(str(path), "rb") as source:
                self.assertEqual(source.getnchannels(), 1)
                self.assertEqual(source.getsampwidth(), 2)
                self.assertEqual(source.getframerate(), 16_000)
                pcm = np.frombuffer(source.readframes(5), dtype="<i2")

        self.assertEqual(pcm.tolist(), [0, -32767, -16384, 16384, 32767])

    def test_manifest_append_is_atomic_and_duplicate_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            audio = root / "audio" / "quiet-01.wav"
            audio.parent.mkdir(parents=True)
            audio.touch()

            case = append_manifest_case(
                manifest,
                case_id="quiet-01",
                audio_path=audio,
                reference="Тихая тестовая фраза.",
                tags=("real", "quiet", "real"),
            )
            self.assertEqual(case["audio"], "audio/quiet-01.wav")
            self.assertEqual(case["tags"], ["real", "quiet"])
            with self.assertRaisesRegex(ValueError, "уже есть"):
                append_manifest_case(
                    manifest,
                    case_id="quiet-01",
                    audio_path=audio,
                    reference="Дубликат",
                )

            append_manifest_case(
                manifest,
                case_id="quiet-01",
                audio_path=audio,
                reference="Исправленная фраза.",
                overwrite=True,
            )
            payload = load_manifest_payload(manifest)

        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["reference"], "Исправленная фраза.")
        self.assertEqual(validate_case_id("names_ru-02"), "names_ru-02")


class PersonalizationTests(unittest.TestCase):
    def test_snippets_expand_only_full_spoken_phrase(self) -> None:
        value = "мой адрес => Москва, улица Тестовая, 1\nподпись => С уважением,\\nИван"
        snippets = parse_snippets(value)
        self.assertEqual(len(snippets), 2)
        self.assertEqual(
            expand_snippet("Мой адрес!", value),
            "Москва, улица Тестовая, 1",
        )
        self.assertEqual(expand_snippet("подпись", value), "С уважением,\nИван")
        self.assertIsNone(expand_snippet("добавь мой адрес в письмо", value))

    def test_app_profiles_match_process_and_combine_terms(self) -> None:
        value = "code.exe | verbatim | Codex, GitHub\ntelegram*.exe | communication |"
        profiles = parse_app_profiles(value)
        self.assertEqual(len(profiles), 2)
        profile = match_app_profile("Telegram.Desktop.exe", value)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.output_mode, "communication")
        code = match_app_profile("Code.exe", value)
        assert code is not None
        self.assertEqual(code.custom_terms, "Codex, GitHub")
        self.assertEqual(
            combine_custom_terms("Речка", code.custom_terms),
            "Речка; Codex, GitHub",
        )

    def test_history_is_bounded_and_contains_no_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            for index in range(4):
                append_history(
                    text=f"Текст {index}",
                    raw_text=f"Исходник {index}",
                    mode="verbatim",
                    application="notepad.exe",
                    path=path,
                    max_entries=3,
                )
            entries = load_history(path)
            serialized = path.read_text(encoding="utf-8")
            self.assertEqual([entry.text for entry in entries], ["Текст 1", "Текст 2", "Текст 3"])
            self.assertNotIn("audio", serialized.casefold())
            clear_history(path)
            self.assertFalse(path.exists())

    def test_personalization_export_round_trip(self) -> None:
        config = AppConfig(
            custom_terms="Codex",
            snippets="подпись => Иван",
            app_profiles="code.exe | verbatim | GitHub",
            project_context="Локальный проект",
            history_enabled=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rechka-personalization.json"
            export_personalization(path, config)
            imported = import_personalization(path)
        self.assertEqual(imported["custom_terms"], "Codex")
        self.assertEqual(imported["snippets"], "подпись => Иван")
        self.assertTrue(imported["history_enabled"])


class DiagnosticsTests(unittest.TestCase):
    @patch("voice_input.diagnostics.import_module")
    @patch("voice_input.diagnostics.version")
    def test_package_versions_fall_back_to_bundled_modules(
        self,
        metadata_version: object,
        import_module: object,
    ) -> None:
        metadata_version.side_effect = PackageNotFoundError()
        import_module.return_value.__version__ = "9.8.7"

        versions = _package_versions()

        self.assertTrue(versions)
        self.assertTrue(all(value == "9.8.7" for value in versions.values()))

    @patch(
        "voice_input.diagnostics.list_input_devices",
        return_value=[
            {
                "index": 2,
                "name": "Test microphone",
                "sample_rate": 48_000,
                "is_default": True,
            }
        ],
    )
    @patch(
        "voice_input.diagnostics._default_device_indices",
        return_value=[2, 3],
    )
    @patch("voice_input.diagnostics.detect_inference_profile")
    def test_report_contains_no_audio_or_transcript(
        self,
        profile: object,
        _default_devices: object,
        _devices: object,
    ) -> None:
        profile.return_value.to_dict.return_value = {
            "device": "cpu",
            "compute_type": "int8",
        }
        payload = collect_diagnostics(
            selected_device_index=2,
            model_name="base",
        )
        self.assertEqual(payload["model"], "base")
        self.assertEqual(payload["selected_device_index"], 2)
        self.assertNotIn("transcript", payload)
        self.assertNotIn("custom_terms", payload)
        self.assertFalse(payload["privacy"]["contains_audio"])
        self.assertFalse(payload["privacy"]["contains_transcript"])
        self.assertFalse(payload["privacy"]["contains_custom_terms"])


class HardwareTests(unittest.TestCase):
    @patch("voice_input.hardware.physical_core_count", return_value=8)
    @patch("voice_input.hardware.ctranslate2.get_cuda_device_count", return_value=1)
    @patch("voice_input.hardware.ctranslate2.get_supported_compute_types")
    def test_cuda_is_selected_when_float16_is_supported(
        self,
        supported: object,
        _cuda_count: object,
        _cores: object,
    ) -> None:
        supported.side_effect = lambda device: (
            {"float16", "int8_float16"} if device == "cuda" else {"int8", "float32"}
        )
        profile = detect_inference_profile()
        self.assertEqual(profile.device, "cuda")
        self.assertEqual(profile.compute_type, "float16")

    @patch("voice_input.hardware.physical_core_count", return_value=6)
    @patch("voice_input.hardware.ctranslate2.get_cuda_device_count", return_value=0)
    @patch(
        "voice_input.hardware.ctranslate2.get_supported_compute_types",
        return_value={"int8", "float32"},
    )
    def test_cpu_is_safe_fallback(
        self,
        _supported: object,
        _cuda_count: object,
        _cores: object,
    ) -> None:
        profile = detect_inference_profile()
        self.assertEqual(profile.device, "cpu")
        self.assertEqual(profile.compute_type, "int8")
        self.assertEqual(profile.physical_cores, 6)

    def test_weak_computer_gets_tiny_local_fallback(self) -> None:
        profile = InferenceProfile(
            device="cpu",
            compute_type="int8",
            device_index=0,
            cuda_device_count=0,
            cpu_compute_types=("int8", "float32"),
            cuda_compute_types=(),
            physical_cores=2,
        )
        assessment = assess_computer(
            profile,
            memory_bytes=4 * 1024**3,
            build_number=19_045,
            logical_cores=4,
        )
        self.assertEqual(assessment.recommended_model, "tiny")
        self.assertEqual(assessment.preferred_recognition, "cloud")
        self.assertEqual(assessment.expected_local_speed, "низкая")
        self.assertEqual(assessment.memory_gb, 4.0)

    def test_regular_cpu_gets_base_instead_of_medium(self) -> None:
        profile = InferenceProfile(
            device="cpu",
            compute_type="int8",
            device_index=0,
            cuda_device_count=0,
            cpu_compute_types=("int8", "float32"),
            cuda_compute_types=(),
            physical_cores=8,
        )
        assessment = assess_computer(
            profile,
            memory_bytes=16 * 1024**3,
            logical_cores=16,
        )
        self.assertEqual(assessment.recommended_model, "base")
        self.assertEqual(assessment.preferred_recognition, "local")


class RecognitionProviderTests(unittest.TestCase):
    def test_wav_encoding_is_mono_pcm16(self) -> None:
        payload = encode_mono_wav(
            np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32)
        )
        with wave.open(io.BytesIO(payload), "rb") as source:
            self.assertEqual(source.getnchannels(), 1)
            self.assertEqual(source.getsampwidth(), 2)
            self.assertEqual(source.getframerate(), 16_000)
            pcm = np.frombuffer(source.readframes(5), dtype="<i2")
        self.assertEqual(pcm.tolist(), [-32767, -16384, 0, 16384, 32767])

    def test_gradio_complete_event_is_parsed(self) -> None:
        payload = (
            'event: heartbeat\ndata: null\n\n'
            'event: complete\ndata: ["Проверка распознавания."]\n\n'
        )
        self.assertEqual(
            parse_gradio_sse(payload),
            ["Проверка распознавания."],
        )

    def test_hugging_face_protocol_uploads_and_reads_result(self) -> None:
        provider = HuggingFaceSpaceProvider(
            provider_id="test",
            label="Test Space",
            base_url="https://example.test",
            endpoint="/transcribe",
        )
        upload = Mock()
        upload.raise_for_status.return_value = None
        upload.json.return_value = ["/tmp/test.wav"]
        submitted = Mock()
        submitted.raise_for_status.return_value = None
        submitted.json.return_value = {"event_id": "event-1"}
        result = Mock()
        result.raise_for_status.return_value = None
        result.text = 'event: complete\ndata: ["Готовый текст"]\n\n'
        client = Mock()
        client.post.side_effect = [upload, submitted]
        client.get.return_value = result

        text = provider.transcribe(
            np.zeros(16_000, dtype=np.float32),
            client=client,
        )

        self.assertEqual(text, "Готовый текст")
        self.assertEqual(client.post.call_count, 2)
        request_payload = client.post.call_args_list[1].kwargs["json"]["data"]
        self.assertEqual(request_payload[1], "transcribe")
        self.assertEqual(request_payload[0]["meta"]["_type"], "gradio.FileData")

    def test_speed_label_explains_real_time_factor(self) -> None:
        fast = RecognitionMetrics("test", "Test", 10.0, 2.0)
        slow = RecognitionMetrics("test", "Test", 10.0, 15.0)
        self.assertEqual(fast.speed_label, "5.0× быстрее реального времени")
        self.assertEqual(slow.speed_label, "1.5× длительности записи")

    def test_cloud_request_has_hard_deadline(self) -> None:
        provider = Mock()
        provider.provider_id = "slow-test"
        provider.label = "Slow Test"
        provider.transcribe.side_effect = lambda *_args, **_kwargs: time.sleep(2)
        with self.assertRaisesRegex(RecognitionProviderError, "0.01 секунд"):
            timed_provider_transcription(
                provider,
                np.zeros(100, dtype=np.float32),
                deadline_seconds=0.01,
            )


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

    def test_legacy_data_directory_is_renamed_to_rechka(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "VoiceInput"
            legacy.mkdir()
            (legacy / "settings.json").write_text("{}", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": str(root),
                    "RECHKA_DATA_DIR": "",
                    "VOICE_INPUT_DATA_DIR": "",
                },
                clear=False,
            ):
                migrated = data_dir()

            self.assertEqual(migrated, root / "Rechka")
            self.assertTrue((migrated / "settings.json").is_file())
            self.assertFalse(legacy.exists())

    def test_fast_mode_and_turbo_model_are_available(self) -> None:
        self.assertEqual(DECODING_BEAM_SIZES["fast"], 1)
        self.assertIn("turbo", MODEL_OPTIONS)
        self.assertIn("ai_prompt", OUTPUT_MODE_OPTIONS)
        self.assertIn("verbatim", OUTPUT_MODE_OPTIONS)
        self.assertIn("chatgpt", AI_TARGET_OPTIONS)

    def test_legacy_medium_config_migrates_to_balanced_base(self) -> None:
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
                self.assertEqual(actual.decoding_mode, "balanced")
                self.assertEqual(actual.beam_size, 2)
                self.assertFalse(actual.sound_feedback)
                self.assertFalse(actual.use_local_ai)
                self.assertEqual(actual.hotkey, "Ctrl+Space")
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
                self.assertEqual(actual.decoding_mode, "balanced")
                self.assertEqual(actual.hotkey, "Ctrl+Space")
                self.assertFalse(actual.use_local_ai)
                self.assertEqual(actual.settings_revision, 7)
                self.assertTrue(actual.onboarding_complete)
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_current_custom_hotkey_is_not_overwritten(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                (Path(directory) / "settings.json").write_text(
                    json.dumps(
                        {
                            "hotkey": "Ctrl+Alt+Space",
                            "settings_revision": 5,
                            "onboarding_complete": True,
                        }
                    ),
                    encoding="utf-8",
                )

                actual = load_config()

                self.assertEqual(actual.hotkey, "Ctrl+Alt+Space")
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_current_fast_decoding_choice_is_not_overwritten(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                (Path(directory) / "settings.json").write_text(
                    json.dumps(
                        {
                            "decoding_mode": "fast",
                            "settings_revision": 5,
                            "onboarding_complete": True,
                        }
                    ),
                    encoding="utf-8",
                )

                actual = load_config()

                self.assertEqual(actual.decoding_mode, "fast")
                self.assertEqual(actual.beam_size, 1)
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_recognition_mode_and_local_profile_are_preserved(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                save_config(
                    AppConfig(
                        recognition_mode="local",
                        model="tiny",
                        decoding_mode="fast",
                    )
                )

                actual = load_config()

                self.assertEqual(actual.recognition_mode, "local")
                self.assertEqual(actual.model, "tiny")
                self.assertEqual(actual.decoding_mode, "fast")
                self.assertIn("cloud", RECOGNITION_MODE_OPTIONS)
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous

    def test_invalid_recognition_mode_falls_back_to_auto(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                (Path(directory) / "settings.json").write_text(
                    json.dumps(
                        {
                            "recognition_mode": "unknown",
                            "settings_revision": 7,
                        }
                    ),
                    encoding="utf-8",
                )

                actual = load_config()

                self.assertEqual(actual.recognition_mode, "auto")
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous


class BuildPackagingTests(unittest.TestCase):
    def test_release_downloader_prepares_every_bundled_model(self) -> None:
        from tools import download_bundled_model

        with (
            patch.object(
                download_bundled_model,
                "download_model_files",
            ) as download,
            patch("builtins.print"),
        ):
            result = download_bundled_model.main()

        self.assertEqual(result, 0)
        destinations = [
            call.kwargs["destination"].name
            for call in download.call_args_list
        ]
        self.assertEqual(
            destinations,
            ["faster-whisper-tiny", "faster-whisper-base"],
        )


class UpdaterTests(unittest.TestCase):
    def test_version_parser(self) -> None:
        self.assertEqual(parse_version("v0.3.1"), (0, 3, 1))
        self.assertGreater(parse_version("0.4.0"), parse_version("0.3.9"))

    def test_release_asset_requires_github_digest(self) -> None:
        payload = {
            "tag_name": "v0.4.0",
            "html_url": "https://github.com/example/rechka/releases/tag/v0.4.0",
            "draft": False,
            "body": "Новый релиз",
            "assets": [
                {
                    "name": "Rechka-Setup-0.4.0.exe",
                    "browser_download_url": (
                        "https://github.com/example/rechka/releases/download/"
                        "v0.4.0/Rechka-Setup-0.4.0.exe"
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

    @patch("voice_input.updater.httpx.get")
    def test_update_check_follows_repository_redirects(self, get: object) -> None:
        response = get.return_value
        response.json.return_value = {
            "tag_name": "v0.6.1",
            "html_url": "https://github.com/example/rechka/releases/tag/v0.6.1",
            "draft": False,
            "body": "",
            "assets": [
                {
                    "name": "Rechka-Setup-0.6.1.exe",
                    "browser_download_url": (
                        "https://github.com/example/rechka/releases/download/"
                        "v0.6.1/Rechka-Setup-0.6.1.exe"
                    ),
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }

        update = check_for_update("example/rechka", "0.6.0")

        self.assertIsNotNone(update)
        self.assertTrue(get.call_args.kwargs["follow_redirects"])
        headers = get.call_args.kwargs["headers"]
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["Pragma"], "no-cache")

    def test_update_accepts_rechka_installer_name(self) -> None:
        payload = {
            "tag_name": "v0.6.1",
            "html_url": "https://github.com/example/rechka/releases/tag/v0.6.1",
            "draft": False,
            "body": "",
            "assets": [
                {
                    "name": "Rechka-Setup-0.6.1.exe",
                    "browser_download_url": (
                        "https://github.com/example/rechka/releases/download/"
                        "v0.6.1/Rechka-Setup-0.6.1.exe"
                    ),
                    "size": 123,
                    "digest": "sha256:" + "b" * 64,
                },
            ],
        }

        update = update_from_release_payload(payload, "0.6.0")

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.asset.name, "Rechka-Setup-0.6.1.exe")
        self.assertEqual(update.asset.sha256, "b" * 64)

    def test_required_update_dialog_has_no_later_option(self) -> None:
        payload = {
            "tag_name": "v0.7.0",
            "html_url": "https://github.com/example/rechka/releases/tag/v0.7.0",
            "draft": False,
            "body": "Обязательное обновление",
            "assets": [
                {
                    "name": "Rechka-Setup-0.7.0.exe",
                    "browser_download_url": (
                        "https://github.com/example/rechka/releases/download/"
                        "v0.7.0/Rechka-Setup-0.7.0.exe"
                    ),
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
        update = update_from_release_payload(payload, "0.6.2")
        assert update is not None

        class FakeApp:
            _closing = False
            _update_prompt_open = False
            _update_in_progress = False
            _microphone_test_running = False
            state = "ready"
            window = object()
            update_status = Mock()
            show_window = Mock()
            _download_update = Mock()
            exit_app = Mock()

        from voice_input.app import VoiceInputApp

        fake = FakeApp()
        with patch("voice_input.app.QMessageBox") as message_box:
            dialog = message_box.return_value
            install_button = object()
            exit_button = object()
            dialog.addButton.side_effect = [install_button, exit_button]
            dialog.clickedButton.return_value = install_button

            VoiceInputApp._prompt_required_update(fake, update)

        labels = [call.args[0] for call in dialog.addButton.call_args_list]
        self.assertEqual(labels, ["Обновить сейчас", "Закрыть Речку"])
        fake.show_window.assert_called_once_with()
        fake._download_update.assert_called_once_with(update)
        fake.exit_app.assert_not_called()

    def test_update_installer_runs_silently_and_closes_the_app(self) -> None:
        previous = os.environ.get("VOICE_INPUT_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VOICE_INPUT_DATA_DIR"] = directory
                installer = (
                    Path(directory)
                    / "updates"
                    / "Rechka-Setup-9.9.9.exe"
                )
                installer.parent.mkdir(parents=True)
                installer.write_bytes(b"test installer")

                with patch("voice_input.updater.subprocess.Popen") as popen:
                    launch_update_installer(installer)

                arguments = popen.call_args.args[0]
                self.assertEqual(arguments[0], str(installer.resolve()))
                self.assertIn("/VERYSILENT", arguments)
                self.assertIn("/SUPPRESSMSGBOXES", arguments)
                self.assertIn("/CLOSEAPPLICATIONS", arguments)
                self.assertIn("/FORCECLOSEAPPLICATIONS", arguments)
                self.assertIn("/UPDATE=1", arguments)
        finally:
            if previous is None:
                os.environ.pop("VOICE_INPUT_DATA_DIR", None)
            else:
                os.environ["VOICE_INPUT_DATA_DIR"] = previous


class WindowsInteropTests(unittest.TestCase):
    def test_custom_hotkey_parser(self) -> None:
        specification = parse_hotkey("Ctrl+Shift+F9")
        self.assertEqual(specification.canonical, "Ctrl+Shift+F9")
        self.assertEqual(hotkey_label("ctrl_space"), "Ctrl + Пробел")
        self.assertEqual(hotkey_label("ctrl_alt_space"), "Ctrl + Alt + Пробел")
        with self.assertRaises(ValueError):
            parse_hotkey("R")
        escape = parse_hotkey("Esc", allow_unmodified=True)
        self.assertEqual(escape.canonical, "Escape")
        self.assertEqual(escape.virtual_key, 0x1B)

    def test_send_input_structure_size(self) -> None:
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)

    def test_physical_core_count_is_sensible(self) -> None:
        self.assertGreaterEqual(physical_core_count(), 1)
        self.assertLessEqual(physical_core_count(), os.cpu_count() or 1)

    def test_soft_feedback_is_a_wave_file(self) -> None:
        self.assertTrue(_feedback_wave("start").startswith(b"RIFF"))

    def test_second_launch_can_request_settings_window(self) -> None:
        handle = create_show_settings_event()
        try:
            self.assertTrue(signal_show_settings_event())
            self.assertTrue(consume_show_settings_event(handle))
            self.assertFalse(consume_show_settings_event(handle))
        finally:
            close_handle(handle)


if __name__ == "__main__":
    unittest.main()
