"""
Монитор производительности для оптимизации задержек.
Измеряет время выполнения компонентов и предлагает улучшения.
"""

import time
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from collections import deque
import logging
import threading

logger = logging.getLogger(__name__)


@dataclass
class Metric:
    """Метрика производительности."""
    name: str
    values: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def add(self, value: float):
        self.values.append(value)
    
    @property
    def avg(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0
    
    @property
    def min(self) -> float:
        return min(self.values) if self.values else 0.0
    
    @property
    def max(self) -> float:
        return max(self.values) if self.values else 0.0


class PerformanceMonitor:
    """Монитор производительности с профилированием."""
    
    def __init__(self):
        self.metrics: Dict[str, Metric] = {}
        self.timers: Dict[str, float] = {}
        self.lock = threading.Lock()
        
        # Предопределённые метрики
        self._init_metrics()
    
    def _init_metrics(self):
        """Инициализирует стандартные метрики."""
        metric_names = [
            "transcription_latency",      # Задержка транскрипции
            "llm_inference_time",         # Время инференса ИИ
            "screenshot_capture_time",    # Время захвата скриншота
            "context_assembly_time",      # Время сборки контекста
            "total_response_time",        # Общее время ответа
            "audio_buffer_fill_time",     # Время заполнения буфера аудио
            "hotkey_response_time",       # Время реакции на хоткеи
        ]
        
        for name in metric_names:
            self.metrics[name] = Metric(name=name)
    
    def start_timer(self, name: str):
        """Запускает таймер для метрики."""
        with self.lock:
            self.timers[name] = time.perf_counter()
    
    def stop_timer(self, name: str) -> float:
        """Останавливает таймер и возвращёт время в секундах."""
        with self.lock:
            if name not in self.timers:
                return 0.0
            
            start = self.timers.pop(name)
            elapsed = time.perf_counter() - start
            
            # Сохраняем метрику
            metric_name = f"{name}_time" if not name.endswith("_time") else name
            if metric_name in self.metrics:
                self.metrics[metric_name].add(elapsed)
            
            return elapsed
    
    def record(self, name: str, value: float):
        """Записывает значение метрики."""
        with self.lock:
            if name in self.metrics:
                self.metrics[name].add(value)
    
    def get_stats(self, name: str) -> Optional[Dict]:
        """Получает статистику для метрики."""
        with self.lock:
            if name not in self.metrics:
                return None
            
            m = self.metrics[name]
            return {
                "avg": m.avg,
                "min": m.min,
                "max": m.max,
                "samples": len(m.values)
            }
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Получает статистику для всех метрик."""
        with self.lock:
            return {name: self.get_stats(name) for name in self.metrics}
    
    def print_report(self):
        """Выводит отчёт по производительности."""
        print("\n" + "=" * 60)
        print("📊 Performance Report")
        print("=" * 60)
        
        for name, metric in self.metrics.items():
            if len(metric.values) > 0:
                avg_ms = metric.avg * 1000
                min_ms = metric.min * 1000
                max_ms = metric.max * 1000
                print(f"{name:35s} avg={avg_ms:6.1f}ms  min={min_ms:6.1f}ms  max={max_ms:6.1f}ms")
        
        print("=" * 60)
    
    def get_optimization_suggestions(self) -> list:
        """Анализирует метрики и предлагает оптимизации."""
        suggestions = []
        
        # Проверка транскрипции
        stats = self.get_stats("transcription_latency")
        if stats and stats["avg"] > 3.0:
            suggestions.append(
                "⚠️  Транскрипция медленная (>3s). Рассмотрите использование модели 'tiny' или 'base' для скорости."
            )
        
        # Проверка ИИ инференса
        stats = self.get_stats("llm_inference_time")
        if stats and stats["avg"] > 5.0:
            suggestions.append(
                "⚠️  Инференс ИИ медленный (>5s). Попробуйте квантование Q3_K_S или уменьшите max_tokens."
            )
        
        # Проверка скриншотов
        stats = self.get_stats("screenshot_capture_time")
        if stats and stats["avg"] > 0.5:
            suggestions.append(
                "⚠️  Захват скриншотов медленный (>500ms). Уменьшите качество JPEG или размер экрана."
            )
        
        # Общее время ответа
        stats = self.get_stats("total_response_time")
        if stats and stats["avg"] > 8.0:
            suggestions.append(
                "⚠️  Общее время ответа >8s. Включите кэширование ответов или используйте smaller модель."
            )
        
        return suggestions
    
    def benchmark_context(self, name: str):
        """Контекстный менеджер для профилирования."""
        class BenchmarkContext:
            def __init__(self, monitor, name):
                self.monitor = monitor
                self.name = name
            
            def __enter__(self):
                self.monitor.start_timer(self.name)
                return self
            
            def __exit__(self, *args):
                self.monitor.stop_timer(self.name)
        
        return BenchmarkContext(self, name)


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    monitor = PerformanceMonitor()
    
    # Имитация работы
    with monitor.benchmark_context("transcription_latency"):
        time.sleep(2.5)  # Имитация транскрипции
    
    with monitor.benchmark_context("llm_inference_time"):
        time.sleep(4.0)  # Имитация инференса
    
    with monitor.benchmark_context("screenshot_capture_time"):
        time.sleep(0.3)  # Имитация скриншота
    
    # Повторяем несколько раз для статистики
    for _ in range(5):
        with monitor.benchmark_context("total_response_time"):
            time.sleep(7.0)
    
    # Отчёт
    monitor.print_report()
    
    print("\n💡 Optimization Suggestions:")
    for suggestion in monitor.get_optimization_suggestions():
        print(f"  {suggestion}")
