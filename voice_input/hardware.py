from __future__ import annotations

from dataclasses import asdict, dataclass

import ctranslate2

from .windows import physical_core_count


@dataclass(frozen=True, slots=True)
class InferenceProfile:
    device: str
    compute_type: str
    device_index: int
    cuda_device_count: int
    cpu_compute_types: tuple[str, ...]
    cuda_compute_types: tuple[str, ...]
    cuda_error: str = ""
    physical_cores: int = 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _supported_compute_types(device: str) -> tuple[str, ...]:
    return tuple(sorted(ctranslate2.get_supported_compute_types(device)))


def detect_inference_profile(preference: str = "auto") -> InferenceProfile:
    if preference not in {"auto", "cpu", "cuda"}:
        raise ValueError(f"Неизвестный тип ускорителя: {preference}")

    physical_cores = max(1, physical_core_count())
    cpu_types = _supported_compute_types("cpu")
    cuda_count = 0
    if preference != "cpu":
        try:
            cuda_count = max(0, int(ctranslate2.get_cuda_device_count()))
        except Exception:
            cuda_count = 0

    cuda_types: tuple[str, ...] = ()
    cuda_error = ""
    if preference != "cpu" and cuda_count:
        try:
            cuda_types = _supported_compute_types("cuda")
        except Exception as exc:
            cuda_error = str(exc)
        else:
            for compute_type in ("float16", "int8_float16", "int8"):
                if compute_type in cuda_types:
                    return InferenceProfile(
                        device="cuda",
                        compute_type=compute_type,
                        device_index=0,
                        cuda_device_count=cuda_count,
                        cpu_compute_types=cpu_types,
                        cuda_compute_types=cuda_types,
                        physical_cores=physical_cores,
                    )

    if preference == "cuda" and not cuda_error:
        cuda_error = "Совместимая CUDA-видеокарта не найдена"
    cpu_compute_type = "int8" if "int8" in cpu_types else "float32"
    return InferenceProfile(
        device="cpu",
        compute_type=cpu_compute_type,
        device_index=0,
        cuda_device_count=cuda_count,
        cpu_compute_types=cpu_types,
        cuda_compute_types=cuda_types,
        cuda_error=cuda_error,
        physical_cores=physical_cores,
    )
