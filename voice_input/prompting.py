from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

TARGET_NAMES = {
    "universal": "любой современной AI-системы",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
}


@dataclass(slots=True)
class ProcessedText:
    text: str
    used_local_ai: bool = False
    note: str = ""


def polish_communication_text(text: str) -> str:
    """Apply only conservative edits that do not rewrite the user's meaning."""
    polished = text.strip()
    polished = re.sub(r"\b(?:э-э+|эм+|мм+)\b[,.]?\s*", "", polished, flags=re.IGNORECASE)

    duplicate_word = re.compile(
        r"\b([A-Za-zА-Яа-яЁё0-9][\w-]*)\s+\1\b",
        flags=re.IGNORECASE,
    )
    while True:
        updated = duplicate_word.sub(r"\1", polished)
        if updated == polished:
            break
        polished = updated

    polished = re.sub(r"[ \t]+", " ", polished)
    polished = re.sub(r" +([,.;:!?])", r"\1", polished)
    for index, character in enumerate(polished):
        if character.isalpha():
            polished = (
                polished[:index]
                + character.upper()
                + polished[index + 1 :]
            )
            break
    return polished.strip()


def build_prompt_fallback(
    transcript: str,
    target: str = "universal",
    project_context: str = "",
) -> str:
    """Create a useful prompt without requiring any external AI service."""
    source = transcript.strip()
    target_name = TARGET_NAMES.get(target, TARGET_NAMES["universal"])
    context = project_context.strip()
    context_section = (
        f"\n\nКонтекст проекта:\n{context}"
        if context
        else ""
    )
    prompt = (
        "Помоги мне выполнить задачу на основе моего исходного потока мыслей.\n\n"
        f"Целевая система: {target_name}."
        f"{context_section}\n\n"
        f"Исходная формулировка:\n---\n{source}\n---\n\n"
        "Как обработать запрос:\n"
        "1. Определи мою главную цель и не меняй её смысл.\n"
        "2. Учти упомянутый контекст, требования и ограничения.\n"
        "3. Убери повторы и расположи мысли в логичном порядке.\n"
        "4. Если информации достаточно — сразу выполни задачу.\n"
        "5. Если без уточнения возможен существенно неверный результат — "
        "задай не более трёх коротких вопросов.\n\n"
        "Ожидаемый ответ: конкретный, практичный и без выдуманных фактов."
    )
    if target == "claude":
        prompt += (
            "\n\nДля разделения сложного контекста и задачи можешь использовать "
            "XML-теги, если это улучшит точность."
        )
    elif target == "gemini":
        prompt += (
            "\n\nСначала учти весь контекст, затем выполни задачу; соблюдай "
            "явно указанный формат результата."
        )
    return prompt


def _clean_model_output(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def polish_communication_with_ollama(text: str, model: str) -> str:
    system_prompt = (
        "Ты аккуратный редактор русской устной речи. Преврати расшифровку в "
        "естественный текст для общения. Сохрани исходный смысл, тон, факты, "
        "названия и степень уверенности. Исправь пунктуацию, явные оговорки, "
        "слова-паразиты, случайные повторы и самокоррекции говорящего. Если "
        "человек сначала сказал один вариант, а затем явно заменил или исправил "
        "его, оставь только итоговый вариант. Не сокращай идеи, не улучшай их "
        "содержание и не превращай текст в промпт. Верни только готовый текст. "
        "/no_think"
    )
    response = httpx.post(
        OLLAMA_CHAT_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text.strip()},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.0,
                "num_ctx": 3072,
                "num_predict": 500,
            },
        },
        timeout=httpx.Timeout(connect=0.7, read=180.0, write=10.0, pool=1.0),
    )
    response.raise_for_status()
    result = _clean_model_output(str(response.json().get("message", {}).get("content", "")))
    if not result:
        raise RuntimeError("локальная модель вернула пустой ответ")
    return result


def build_prompt_with_ollama(
    transcript: str,
    model: str,
    target: str = "universal",
    project_context: str = "",
) -> str:
    target_name = TARGET_NAMES.get(target, TARGET_NAMES["universal"])
    target_guidance = {
        "universal": (
            "Используй ясные Markdown-разделы и формулировки, понятные разным моделям."
        ),
        "chatgpt": (
            "Поставь инструкции в начале, отдели контекст явным разделителем и "
            "точно задай желаемый формат ответа."
        ),
        "claude": (
            "Для сложных частей используй понятные XML-теги, явно сформулируй "
            "критерии успеха и формат ответа."
        ),
        "gemini": (
            "Используй точные инструкции и стабильную структуру; отдели контекст "
            "от задачи и явно укажи ограничения и формат ответа."
        ),
    }.get(target, "")
    system_prompt = (
        "Ты редактор запросов для AI-систем. Преобразуй поток мыслей пользователя "
        "в один ясный рабочий промпт. Не отвечай на сам запрос и не добавляй факты. "
        "Сохрани цель, контекст, названия, требования, ограничения и желаемый "
        "результат. Удали речевые повторы и оговорки. Используй только уместные "
        "разделы из списка: Цель, Контекст, Задача, Требования, Ограничения, "
        "Ожидаемый результат. Верни только готовый промпт без предисловия. "
        f"Оптимизируй его для {target_name}. {target_guidance} /no_think"
    )
    user_content = transcript.strip()
    if project_context.strip():
        user_content = (
            f"Постоянный контекст проекта:\n{project_context.strip()}\n\n"
            f"Текущий поток мыслей:\n{user_content}"
        )
    response = httpx.post(
        OLLAMA_CHAT_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.1,
                "num_ctx": 3072,
                "num_predict": 700,
            },
        },
        timeout=httpx.Timeout(connect=0.7, read=180.0, write=10.0, pool=1.0),
    )
    response.raise_for_status()
    payload = response.json()
    result = _clean_model_output(str(payload.get("message", {}).get("content", "")))
    if not result:
        raise RuntimeError("локальная модель вернула пустой ответ")
    return result


def process_custom_with_ollama(
    transcript: str,
    model: str,
    instruction: str,
) -> str:
    response = httpx.post(
        OLLAMA_CHAT_URL,
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Обработай расшифровку строго по пользовательской инструкции. "
                        "Не добавляй факты и верни только готовый текст. /no_think\n\n"
                        f"Инструкция: {instruction.strip()}"
                    ),
                },
                {"role": "user", "content": transcript.strip()},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.0,
                "num_ctx": 3072,
                "num_predict": 700,
            },
        },
        timeout=httpx.Timeout(connect=0.7, read=180.0, write=10.0, pool=1.0),
    )
    response.raise_for_status()
    result = _clean_model_output(
        str(response.json().get("message", {}).get("content", ""))
    )
    if not result:
        raise RuntimeError("локальная модель вернула пустой ответ")
    return result


def process_transcript(
    transcript: str,
    mode: str,
    use_local_ai: bool = True,
    ollama_model: str = "qwen3:4b",
    ai_target: str = "universal",
    project_context: str = "",
    custom_instruction: str = "",
) -> ProcessedText:
    if mode == "verbatim":
        return ProcessedText(text=transcript.strip())
    if mode == "custom":
        instruction = custom_instruction.strip()
        if use_local_ai and instruction:
            try:
                result = process_custom_with_ollama(
                    transcript,
                    ollama_model.strip() or "qwen3:4b",
                    instruction,
                )
                return ProcessedText(
                    text=result,
                    used_local_ai=True,
                    note="Применена пользовательская локальная инструкция",
                )
            except (httpx.HTTPError, OSError, RuntimeError, ValueError):
                pass
        return ProcessedText(
            text=polish_communication_text(transcript),
            note=(
                "Свой режим требует включённую Ollama и инструкцию; "
                "использована безопасная базовая обработка"
            ),
        )
    if mode != "ai_prompt":
        if use_local_ai:
            try:
                polished = polish_communication_with_ollama(
                    transcript,
                    ollama_model.strip() or "qwen3:4b",
                )
                return ProcessedText(
                    text=polished,
                    used_local_ai=True,
                    note="Формулировка аккуратно исправлена локальной AI",
                )
            except (httpx.HTTPError, OSError, RuntimeError, ValueError):
                pass
        return ProcessedText(text=polish_communication_text(transcript))

    model = ollama_model.strip() or "qwen3:4b"
    if use_local_ai:
        try:
            prompt = build_prompt_with_ollama(
                transcript,
                model,
                target=ai_target,
                project_context=project_context,
            )
            return ProcessedText(
                text=prompt,
                used_local_ai=True,
                note=(
                    f"Промпт для {TARGET_NAMES.get(ai_target, 'AI')} "
                    f"сформирован локальной моделью {model}"
                ),
            )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            pass

    return ProcessedText(
        text=build_prompt_fallback(
            transcript,
            target=ai_target,
            project_context=project_context,
        ),
        note="Ollama недоступна — использован локальный шаблон промпта",
    )
