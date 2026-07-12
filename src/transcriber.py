"""
Real-time транскрипция аудио с использованием faster-whisper.
Оптимизировано для скорости на GPU (ROCm/CUDA).
"""

import threading
import logging
from typing import Optional, Callable
from faster_whisper import WhisperModel
import numpy as np
import queue
import time

logger = logging.getLogger(__name__)


class RealtimeTranscriber:
    """Транскрипция аудио в реальном времени с буферизацией."""
    
    def __init__(
        self, 
        model_size: str = "distil-large-v3",
        device: str = "auto",
        compute_type: str = "default"
    ):
        """
        Инициализирует транскриптор.
        
        Args:
            model_size: Размер модели faster-whisper
                - tiny/base/small/medium/large-v3
                - distil-large-v3 (быстрее, хорошая точность)
            device: Устройство для инференса (auto/cpu/cuda)
            compute_type: Тип вычислений (default/int8_float16/fp16)
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        
        self.model: Optional[WhisperModel] = None
        self.is_running = False
        
        # Буфер для аудио фрагментов
        self.audio_queue = queue.Queue()
        
        # Callback для новых транскрипций
        self.on_transcription: Optional[Callable[[str], None]] = None
        
        # История транскрипций (последние N секунд)
        self.transcription_history: list = []
        self.max_history_seconds = 60
        
        # Поток обработки
        self.process_thread: Optional[threading.Thread] = None
        
        logger.info(f"RealtimeTranscriber инициализирован: model={model_size}, device={device}")
    
    def load_model(self):
        """Загружает модель faster-whisper."""
        if self.model is not None:
            return
        
        try:
            logger.info(f"Загрузка модели {self.model_size}...")
            
            self.model = WhisperModel(
                model_size_or_path=self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=2  # Многопоточность для декодирования
            )
            
            logger.info(f"Модель {self.model_size} загружена успешно")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки модели: {e}")
            raise
    
    def _audio_to_numpy(self, audio_data: bytes) -> np.ndarray:
        """Конвертирует байты аудио в numpy массив."""
        import struct
        
        # Преобразуем Int16 байты в float32 массив
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio_array
    
    def _process_loop(self):
        """Цикл обработки аудио в фоновом потоке."""
        buffer_chunks = []
        buffer_duration = 0.0
        target_duration = 3.0  # Транскрибируем каждые 3 секунды
        
        while self.is_running:
            try:
                # Ждём новый фрагмент аудио
                audio_data = self.audio_queue.get(timeout=1.0)
                
                buffer_chunks.append(audio_data)
                buffer_duration += len(audio_data) / (16000 * 2)  # 16kHz, Int16
                
                # Когда накопилось достаточно - транскрибируем
                if buffer_duration >= target_duration:
                    full_audio = b''.join(buffer_chunks)
                    audio_array = self._audio_to_numpy(full_audio)
                    
                    # Транскрипция
                    segments, info = self.model.transcribe(
                        audio_array,
                        language="ru",  # Приоритет русскому языку
                        beam_size=5,
                        word_timestamps=False,
                        vad_filter=True  # Фильтрация тишины
                    )
                    
                    text = "".join([segment.text for segment in segments]).strip()
                    
                    if text:
                        timestamp = time.time()
                        transcription = {
                            "text": text,
                            "timestamp": timestamp,
                            "duration": buffer_duration
                        }
                        
                        # Добавляем в историю
                        self.transcription_history.append(transcription)
                        
                        # Очищаем старую историю (> max_history_seconds)
                        cutoff_time = timestamp - self.max_history_seconds
                        self.transcription_history = [
                            t for t in self.transcription_history 
                            if t["timestamp"] > cutoff_time
                        ]
                        
                        logger.debug(f"Транскрипция: {text}")
                        
                        # Вызываем callback
                        if self.on_transcription:
                            self.on_transcription(text)
                    
                    # Сбрасываем буфер
                    buffer_chunks = []
                    buffer_duration = 0.0
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Ошибка в цикле транскрипции: {e}")
                continue
    
    def start(self):
        """Запускает транскрипцию."""
        if self.is_running:
            logger.warning("Transcriber уже запущен")
            return
        
        # Загружаем модель
        self.load_model()
        
        self.is_running = True
        
        # Запускаем фоновый поток обработки
        self.process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self.process_thread.start()
        
        logger.info("RealtimeTranscriber запущен")
    
    def stop(self):
        """Останавливает транскрипцию."""
        self.is_running = False
        
        if self.process_thread:
            self.process_thread.join(timeout=5.0)
            self.process_thread = None
        
        logger.info("RealtimeTranscriber остановлен")
    
    def add_audio_chunk(self, audio_data: bytes):
        """
        Добавляет фрагмент аудио в очередь для транскрипции.
        
        Args:
            audio_data: Байты аудио (Int16, 16kHz)
        """
        if not self.is_running:
            return
        
        try:
            self.audio_queue.put(audio_data, block=False)
        except queue.Full:
            # Очередь переполнена - пропускаем фрагмент
            logger.debug("Очередь аудио переполнена")
    
    def get_recent_transcriptions(self, seconds: Optional[float] = None) -> list:
        """
        Возвращает последние транскрипции за N секунд.
        
        Args:
            seconds: Длительность в секундах (по умолчанию вся история)
            
        Returns:
            Список словарей с текстом и временными метками
        """
        if seconds is None:
            return self.transcription_history.copy()
        
        cutoff_time = time.time() - seconds
        return [
            t for t in self.transcription_history 
            if t["timestamp"] > cutoff_time
        ]
    
    def get_full_text(self, seconds: Optional[float] = None) -> str:
        """
        Возвращает весь текст транскрипций за последние N секунд.
        
        Args:
            seconds: Длительность в секундах
            
        Returns:
            Объединённый текст всех транскрипций
        """
        transcriptions = self.get_recent_transcriptions(seconds)
        return " ".join([t["text"] for t in transcriptions])


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    transcriber = RealtimeTranscriber(
        model_size="distil-large-v3",
        device="auto",  # Автоматически выбирает GPU если доступен
        compute_type="default"
    )
    
    def on_text(text):
        print(f"[TRANSCRIPT] {text}")
    
    transcriber.on_transcription = on_text
    
    try:
        transcriber.start()
        
        # Тестовый цикл (в реальности сюда приходит аудио из AudioCapture)
        print("Нажмите Ctrl+C для остановки...")
        
        while True:
            import time
            time.sleep(1)
            
            # Пример добавления аудио (заглушка)
            # transcriber.add_audio_chunk(audio_data)
            
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        transcriber.stop()
