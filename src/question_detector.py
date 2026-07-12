"""
Детекция вопросов и триггеров для кодинга из транскрипции диалога.
Использует NLP паттерны + LLM классификацию.
"""

import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class DetectedEvent:
    """Событие детекции (вопрос или триггер кодинга)."""
    event_type: str  # 'question' или 'code_request'
    text: str
    confidence: float
    timestamp: float
    keywords_found: List[str]


class QuestionDetector:
    """Детекция вопросов и запросов на код из транскрипции."""
    
    def __init__(self, config: Dict):
        """
        Инициализирует детектор.
        
        Args:
            config: Конфигурация с порогами и ключевыми словами
        """
        self.question_threshold = config.get("question_detection_threshold", 0.7)
        self.code_keywords = config.get("code_detection_keywords", [])
        
        # Паттерны для детекции вопросов (русский + английский)
        self.question_patterns = [
            # Русские вопросы
            r"как\s+(было|сделать|написать|реализовать|понять)",
            r"что\s+(вы\w*\s+о|думаете|можете|предлагаете)",
            r"объясните\s+",
            r"расскажите\s+",
            r"какой\s+(лучше|правильный|оптимальный)",
            r"почему\s+",
            r"зачем\s+",
            r"для\s+чего\s+",
            r"в чём\s+(разница|сложность|проблема)",
            
            # Английские вопросы
            r"how\s+(would|do|can|could)\s+you",
            r"what\s+(is|are|do|would|do you)",
            r"explain\s+",
            r"describe\s+",
            r"why\s+",
            r"can you\s+",
            r"could you\s+",
            r"would you\s+",
        ]
        
        # Паттерны для детекции запросов на код
        self.code_patterns = [
            r"напишите\s+код",
            r"реализуйте\s+(функцию|класс|алгоритм)",
            r"решите\s+задачу",
            r"напишите\s+(алгоритм|программу|скрипт)",
            r"создайте\s+(класс|функцию|метод)",
            r"опишите\s+решение",
            r"implement\s+",
            r"write\s+code",
            r"solve\s+this\s+problem",
            r"write\s+a\s+\w+\s+function",
            r"create\s+a\s+\w+\s+class",
        ]
        
        # Компилируем паттерны для производительности
        self.compiled_question_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.question_patterns
        ]
        self.compiled_code_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.code_patterns
        ]
        
        # История последних событий
        self.recent_events: List[DetectedEvent] = []
        self.max_history_seconds = config.get("conversation_window", 30)
        
        logger.info(f"QuestionDetector инициализирован: threshold={self.question_threshold}")
    
    def detect_from_text(self, text: str) -> Optional[DetectedEvent]:
        """
        Анализирует текст на наличие вопросов или запросов на код.
        
        Args:
            text: Текст транскрипции
            
        Returns:
            DetectedEvent если найдено событие, иначе None
        """
        if not text.strip():
            return None
        
        # Проверяем паттерны кода (приоритет)
        code_event = self._check_code_patterns(text)
        if code_event:
            return code_event
        
        # Проверяем паттерны вопросов
        question_event = self._check_question_patterns(text)
        if question_event and question_event.confidence >= self.question_threshold:
            return question_event
        
        return None
    
    def _check_code_patterns(self, text: str) -> Optional[DetectedEvent]:
        """Проверяет текст на паттерны запросов на код."""
        keywords_found = []
        
        # Проверяем регулярные выражения
        for pattern in self.compiled_code_patterns:
            if pattern.search(text):
                keywords_found.append(pattern.pattern)
        
        # Проверяем ключевые слова из конфига
        text_lower = text.lower()
        for keyword in self.code_keywords:
            if keyword.lower() in text_lower:
                keywords_found.append(keyword)
        
        if keywords_found:
            # Высокая уверенность для кодовых запросов (0.85-1.0)
            confidence = min(1.0, 0.7 + len(keywords_found) * 0.1)
            
            event = DetectedEvent(
                event_type="code_request",
                text=text.strip(),
                confidence=confidence,
                timestamp=time.time(),
                keywords_found=keywords_found[:5]  # Ограничиваем список
            )
            
            logger.info(f"Детекция кода: '{text[:50]}...' (conf={confidence:.2f})")
            return event
        
        return None
    
    def _check_question_patterns(self, text: str) -> Optional[DetectedEvent]:
        """Проверяет текст на паттерны вопросов."""
        keywords_found = []
        
        for pattern in self.compiled_question_patterns:
            if pattern.search(text):
                keywords_found.append(pattern.pattern)
        
        if keywords_found:
            # Уверенность зависит от количества совпадений
            confidence = min(1.0, 0.5 + len(keywords_found) * 0.15)
            
            event = DetectedEvent(
                event_type="question",
                text=text.strip(),
                confidence=confidence,
                timestamp=time.time(),
                keywords_found=keywords_found[:3]
            )
            
            logger.debug(f"Детекция вопроса: '{text[:50]}...' (conf={confidence:.2f})")
            return event
        
        return None
    
    def process_transcription(self, text: str) -> Optional[DetectedEvent]:
        """
        Обрабатывает новую транскрипцию и возвращает событие если найдено.
        
        Args:
            text: Новый текст транскрипции
            
        Returns:
            DetectedEvent или None
        """
        event = self.detect_from_text(text)
        
        if event:
            # Добавляем в историю
            self.recent_events.append(event)
            
            # Очищаем старые события
            cutoff_time = time.time() - self.max_history_seconds
            self.recent_events = [
                e for e in self.recent_events 
                if e.timestamp > cutoff_time
            ]
        
        return event
    
    def get_recent_events(self, seconds: Optional[float] = None) -> List[DetectedEvent]:
        """
        Возвращает последние события за N секунд.
        
        Args:
            seconds: Длительность в секундах
            
        Returns:
            Список событий
        """
        if seconds is None:
            return self.recent_events.copy()
        
        cutoff_time = time.time() - seconds
        return [
            e for e in self.recent_events 
            if e.timestamp > cutoff_time
        ]
    
    def should_trigger_fast_mode(self) -> bool:
        """
        Проверяет, нужно ли включить быстрый режим скриншотов.
        
        Returns:
            True если в последних событиях есть запрос на код
        """
        recent = self.get_recent_events(seconds=60)  # Последние 60 секунд
        
        # Ищем недавние code_request события
        for event in reversed(recent):  # С последнего к первому
            if event.event_type == "code_request":
                return True
            
            # Если прошёл больше минуты с кодового запроса - выключаем
            if time.time() - event.timestamp > 60:
                break
        
        return False
    
    def get_context_summary(self) -> str:
        """
        Возвращает краткую сводку последних событий для контекста ИИ.
        
        Returns:
            Текстовая сводка
        """
        recent = self.get_recent_events(seconds=30)
        
        if not recent:
            return "Нет активных вопросов или запросов."
        
        summary_parts = []
        
        for event in recent[-5:]:  # Последние 5 событий
            if event.event_type == "code_request":
                summary_parts.append(f"Запрос на код: {event.text[:100]}")
            else:
                summary_parts.append(f"Вопрос: {event.text[:100]}")
        
        return "\n".join(summary_parts)
    
    def clear_history(self):
        """Очищает историю событий."""
        self.recent_events.clear()
        logger.debug("История событий очищена")


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    config = {
        "question_detection_threshold": 0.7,
        "code_detection_keywords": [
            "напишите код",
            "реализуйте функцию",
            "решите задачу",
            "implement",
            "write code"
        ],
        "conversation_window": 30
    }
    
    detector = QuestionDetector(config)
    
    # Тестовые фразы
    test_texts = [
        "Как бы вы реализовали эту функцию?",
        "Напишите код для сортировки массива",
        "Что вы думаете об этом подходе?",
        "Создайте класс для обработки данных",
        "Объясните разницу между синхронным и асинхронным кодом"
    ]
    
    for text in test_texts:
        event = detector.process_transcription(text)
        if event:
            print(f"\n[DETECTED] Тип: {event.event_type}, Уверенность: {event.confidence:.2f}")
            print(f"Текст: {text}")
            print(f"Ключевые слова: {event.keywords_found}")
        else:
            print(f"[NO MATCH] {text}")
