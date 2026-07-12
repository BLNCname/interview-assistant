"""
Агрегатор контекста для мультимодальных запросов к ИИ.
Собирает транскрипцию, скриншоты и историю диалога в единый промпт.
"""

import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import logging
import base64
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ContextSnapshot:
    """Снимок контекста для отправки в ИИ."""
    transcription_text: str
    screenshot_paths: List[str]
    detected_events: List[Dict]
    conversation_history: List[Dict]
    timestamp: float = field(default_factory=time.time)


class ContextAggregator:
    """Агрегация контекста из всех источников для ИИ."""
    
    def __init__(self, config: Dict):
        """
        Инициализирует агрегатор.
        
        Args:
            config: Конфигурация с настройками окон и буферов
        """
        self.conversation_window = config.get("conversation_window", 30)
        self.max_screenshots = config.get("max_buffer_size", 5)
        
        # История диалога (текстовая)
        self.conversation_history: List[Dict] = []
        
        # Буфер скриншотов (пути к файлам)
        self.screenshot_buffer: List[str] = []
        
        # Детектор событий (будет установлен позже)
        self.question_detector = None
        
        logger.info("ContextAggregator инициализирован")
    
    def set_question_detector(self, detector):
        """Устанавливает детектор вопросов для доступа к событиям."""
        self.question_detector = detector
    
    def add_transcription(self, text: str, speaker: str = "interviewer"):
        """
        Добавляет транскрипцию в историю диалога.
        
        Args:
            text: Текст транскрипции
            speaker: Кто говорил (interviewer/candidate)
        """
        if not text.strip():
            return
        
        entry = {
            "role": "user" if speaker == "interviewer" else "assistant",
            "content": text,
            "timestamp": time.time()
        }
        
        self.conversation_history.append(entry)
        
        # Очищаем старую историю
        cutoff_time = time.time() - (self.conversation_window * 2)
        self.conversation_history = [
            e for e in self.conversation_history 
            if e["timestamp"] > cutoff_time
        ]
    
    def add_screenshot(self, filepath: str):
        """
        Добавляет скриншот в буфер.
        
        Args:
            filepath: Путь к файлу скриншота
        """
        self.screenshot_buffer.append(filepath)
        
        if len(self.screenshot_buffer) > self.max_screenshots:
            self.screenshot_buffer.pop(0)
    
    def get_recent_transcription(self, seconds: Optional[float] = None) -> str:
        """
        Возвращает текст транскрипции за последние N секунд.
        
        Args:
            seconds: Длительность в секундах
            
        Returns:
            Объединённый текст
        """
        if seconds is None:
            seconds = self.conversation_window
        
        cutoff_time = time.time() - seconds
        
        recent_entries = [
            e for e in self.conversation_history 
            if e["timestamp"] > cutoff_time
        ]
        
        return "\n".join([e["content"] for e in recent_entries])
    
    def get_latest_screenshots(self, count: Optional[int] = None) -> List[str]:
        """
        Возвращает последние N скриншотов.
        
        Args:
            count: Количество (по умолчанию все)
            
        Returns:
            Список путей к файлам
        """
        if count is None:
            return self.screenshot_buffer.copy()
        
        return self.screenshot_buffer[-count:] if len(self.screenshot_buffer) >= count else self.screenshot_buffer.copy()
    
    def create_context_snapshot(self) -> ContextSnapshot:
        """
        Создаёт снимок текущего контекста.
        
        Returns:
            ContextSnapshot с актуальными данными
        """
        # Получаем последние события от детектора
        detected_events = []
        if self.question_detector:
            recent_events = self.question_detector.get_recent_events(seconds=self.conversation_window)
            detected_events = [
                {
                    "type": e.event_type,
                    "text": e.text,
                    "confidence": e.confidence,
                    "keywords": e.keywords_found
                }
                for e in recent_events[-5:]  # Последние 5 событий
            ]
        
        return ContextSnapshot(
            transcription_text=self.get_recent_transcription(),
            screenshot_paths=self.get_latest_screenshots(2),  # Последние 2 скриншота
            detected_events=detected_events,
            conversation_history=self.conversation_history[-10:]  # Последние 10 сообщений
        )
    
    def build_prompt(self, snapshot: Optional[ContextSnapshot] = None) -> Tuple[str, List[str]]:
        """
        Строит промпт для ИИ на основе текущего контекста.
        
        Args:
            snapshot: Опциональный снимок контекста (если None - создаёт новый)
            
        Returns:
            Кортеж (промпт, список путей к изображениям)
        """
        if snapshot is None:
            snapshot = self.create_context_snapshot()
        
        # Формируем текстовый промпт
        prompt_parts = []
        
        # 1. Контекст диалога
        if snapshot.transcription_text:
            prompt_parts.append("=== ТЕКУЩИЙ ДИАЛОГ ===")
            prompt_parts.append(snapshot.transcription_text)
            prompt_parts.append("")
        
        # 2. Детектированные события
        if snapshot.detected_events:
            prompt_parts.append("=== ДЕТЕКТИРОВАННЫЕ СОБЫТИЯ ===")
            for event in snapshot.detected_events:
                event_type = "Запрос на код" if event["type"] == "code_request" else "Вопрос"
                confidence = "высокая" if event["confidence"] > 0.8 else "средняя"
                prompt_parts.append(f"- {event_type} (уверенность: {confidence})")
                prompt_parts.append(f"  Текст: {event['text'][:150]}")
            prompt_parts.append("")
        
        # 3. Инструкция для ИИ
        prompt_parts.append("=== ЗАДАЧА ===")
        prompt_parts.append("""Ты помощник на техническом собеседовании. Тебе предоставлен:
1. Текст текущего диалога между интервьюером и кандидатом
2. Скриншот экрана (если есть) с кодом или задачей
3. Детектированные события (вопросы, запросы на код)

Твоя задача:
- Если есть вопрос от интервьюера - дай краткий, точный ответ
- Если есть запрос на код - предоставь решение с объяснением
- Если на скриншоте виден код - проанализируй его и предложи улучшения или исправления
- Отвечай на русском языке, если только не запрошен другой язык

Будь краток, но информативен. Избегай излишних деталей.""")
        prompt_parts.append("")
        
        # 4. Указание об изображениях
        if snapshot.screenshot_paths:
            prompt_parts.append(f"=== ДОСТУПНО {len(snapshot.screenshot_paths)} СКРИНШОТ(ОВ) ===")
            for i, path in enumerate(snapshot.screenshot_paths):
                prompt_parts.append(f"- Скриншот {i+1}: {Path(path).name}")
            prompt_parts.append("")
        
        full_prompt = "\n".join(prompt_parts)
        
        return full_prompt, snapshot.screenshot_paths
    
    def clear(self):
        """Очищает всю историю и буферы."""
        self.conversation_history.clear()
        self.screenshot_buffer.clear()
        logger.debug("ContextAggregator очищен")


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    config = {
        "conversation_window": 30,
        "max_buffer_size": 5
    }
    
    aggregator = ContextAggregator(config)
    
    # Имитация добавления данных
    aggregator.add_transcription("Как бы вы реализовали функцию сортировки?", "interviewer")
    aggregator.add_transcription("Я бы использовал quicksort...", "candidate")
    
    print(f"Транскрипция: {aggregator.get_recent_transcription()}")
    print(f"История: {len(aggregator.conversation_history)} записей")
    
    # Создаём снимок и строим промпт
    snapshot = aggregator.create_context_snapshot()
    prompt, images = aggregator.build_prompt(snapshot)
    
    print("\n=== ПРОМПТ ===")
    print(prompt[:500])
    print(f"\nИзображений: {len(images)}")
