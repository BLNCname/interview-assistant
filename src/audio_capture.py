"""
Захват аудио с WASAPI loopback для записи системного звука (голос собеседника).
Работает на Windows через PyAudio + WASAPI exclusive mode.
"""

import pyaudio
import numpy as np
from typing import Optional, Callable
import threading
import logging
import queue

logger = logging.getLogger(__name__)


class AudioCapture:
    """Захват системного звука через WASAPI loopback."""
    
    def __init__(
        self, 
        device_index: Optional[int] = None,
        sample_rate: int = 16000,
        buffer_duration: float = 3.0
    ):
        self.sample_rate = sample_rate
        self.buffer_duration = buffer_duration
        self.device_index = device_index
        
        self.pa = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None
        self.is_running = False
        
        # Буфер для аудио данных
        self.audio_buffer = queue.Queue(maxsize=int(buffer_duration * sample_rate / 1024))
        
        # Callback для новых фрагментов аудио
        self.on_audio_chunk: Optional[Callable[[bytes], None]] = None
        
        logger.info(f"AudioCapture инициализирован: sample_rate={sample_rate}, buffer={buffer_duration}s")
    
    def list_devices(self):
        """Выводит список доступных устройств ввода."""
        print("\n=== Доступные устройства ввода ===")
        for i in range(self.pa.get_device_count()):
            info = self.pa.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"[{i}] {info['name']} (channels={info['maxInputChannels']})")
        print("=" * 40)
    
    def get_loopback_device(self) -> Optional[int]:
        """
        Находит устройство WASAPI loopback для записи системного звука.
        Возвращает индекс устройства или None если не найдено.
        """
        for i in range(self.pa.get_device_count()):
            try:
                info = self.pa.get_device_info_by_index(i)
                name = info['name'].lower()
                
                # Ищем WASAPI loopback устройства
                if 'loopback' in name or 'stereo mix' in name or 'what u hear' in name:
                    logger.info(f"Найдено устройство loopback: {info['name']}")
                    return i
            except Exception as e:
                continue
        
        # Если не найдено специализированное устройство, пробуем использовать дефолтное
        if self.device_index is None:
            default = self.pa.get_default_input_device_info()
            logger.warning(f"Устройство loopback не найдено. Используем дефолтное: {default['name']}")
            return default['index']
        
        return self.device_index
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback для потока PyAudio."""
        if status:
            logger.warning(f"Статус аудио потока: {status}")
        
        try:
            self.audio_buffer.put(in_data, block=False)
            
            if self.on_audio_chunk:
                self.on_audio_chunk(in_data)
        except queue.Full:
            # Буфер переполнен - пропускаем фрагмент
            pass
        
        return (None, pyaudio.paContinue)
    
    def start(self):
        """Запускает захват аудио."""
        if self.is_running:
            logger.warning("AudioCapture уже запущен")
            return
        
        device_index = self.get_loopback_device()
        
        if device_index is None:
            raise RuntimeError("Не найдено устройство для записи аудио")
        
        try:
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
                stream_callback=self._audio_callback
            )
            
            self.stream.start_stream()
            self.is_running = True
            
            logger.info(f"AudioCapture запущен: device={device_index}, rate={self.sample_rate}")
            
        except Exception as e:
            logger.error(f"Ошибка запуска AudioCapture: {e}")
            raise
    
    def stop(self):
        """Останавливает захват аудио."""
        if not self.is_running:
            return
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        
        self.is_running = False
        logger.info("AudioCapture остановлен")
    
    def get_audio_buffer(self) -> bytes:
        """
        Возвращает накопленный буфер аудио данных.
        Очищает буфер после чтения.
        """
        chunks = []
        while not self.audio_buffer.empty():
            try:
                chunks.append(self.audio_buffer.get_nowait())
            except queue.Empty:
                break
        
        return b''.join(chunks)
    
    def get_recent_audio(self, duration: Optional[float] = None) -> bytes:
        """
        Возвращает аудио за последние N секунд.
        
        Args:
            duration: Длительность в секундах (по умолчанию buffer_duration)
            
        Returns:
            Байты аудио данных
        """
        if duration is None:
            duration = self.buffer_duration
        
        frames_to_keep = int(duration * self.sample_rate / 1024)
        
        chunks = []
        while not self.audio_buffer.empty():
            try:
                chunks.append(self.audio_buffer.get_nowait())
            except queue.Empty:
                break
        
        # Оставляем только последние N фрагментов
        if len(chunks) > frames_to_keep:
            chunks = chunks[-frames_to_keep:]
        
        return b''.join(chunks)
    
    def is_running_check(self) -> bool:
        """Проверяет, запущен ли захват аудио."""
        return self.is_running and (self.stream is not None and self.stream.is_active())
    
    def __del__(self):
        """Очистка ресурсов при удалении объекта."""
        try:
            self.stop()
            self.pa.terminate()
        except:
            pass


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    capture = AudioCapture(sample_rate=16000, buffer_duration=3.0)
    
    # Показать устройства
    capture.list_devices()
    
    def on_chunk(data):
        print(f"Получен фрагмент аудио: {len(data)} байт")
    
    capture.on_audio_chunk = on_chunk
    
    try:
        capture.start()
        print("Нажмите Ctrl+C для остановки...")
        
        while True:
            import time
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        capture.stop()
