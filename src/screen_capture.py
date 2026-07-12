"""
Захват скриншотов с подавлением системных уведомлений Windows.
Поддерживает нормальный (20с) и быстрый (5с) режимы.
"""

import mss
import mss.tools
from PIL import Image
import io
import threading
import time
import logging
from typing import Optional, Callable, List
from pathlib import Path
import win32gui
import win32con

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Захват скриншотов с подавлением уведомлений и поддержкой режимов."""
    
    def __init__(
        self,
        normal_interval: float = 20.0,
        fast_interval: float = 5.0,
        quality: int = 80,
        output_dir: Optional[str] = None
    ):
        """
        Инициализирует захват скриншотов.
        
        Args:
            normal_interval: Интервал в обычном режиме (сек)
            fast_interval: Интервал в быстром режиме (сек)
            quality: Качество JPEG (1-100)
            output_dir: Директория для сохранения скриншотов
        """
        self.normal_interval = normal_interval
        self.fast_interval = fast_interval
        self.quality = quality
        self.output_dir = Path(output_dir) if output_dir else None
        
        # Создаём директорию если нужна
        if self.output_dir and not self.output_dir.exists():
            self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.sct = mss.mss()
        
        # Текущий режим
        self.is_fast_mode = False
        self.current_interval = normal_interval
        
        # Флаги состояния
        self.is_running = False
        self.is_paused = False
        
        # Поток захвата
        self.capture_thread: Optional[threading.Thread] = None
        
        # Буфер последних скриншотов (пути к файлам)
        self.screenshot_buffer: List[str] = []
        self.max_buffer_size = 5
        
        # Callback для новых скриншотов
        self.on_screenshot: Optional[Callable[[str], None]] = None
        
        # Подавление уведомлений Windows
        self._original_notification_state = None
        
        logger.info(f"ScreenCapture инициализирован: normal={normal_interval}s, fast={fast_interval}s")
    
    def _suppress_notifications(self):
        """Подавляет системные уведомления Windows (временно)."""
        try:
            # Отключаем уведомления через реестр (только для текущей сессии)
            import winreg
            
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings",
                0,
                winreg.KEY_SET_VALUE
            )
            
            self._original_notification_state = True
            winreg.SetValueEx(key, "NOC_GLOBAL_SETTING_ALLOW_NOTIFICATION_SOUND", 0, 
                            winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
            
            logger.debug("Уведомления Windows подавлены")
        except Exception as e:
            logger.warning(f"Не удалось подавить уведомления: {e}")
    
    def _restore_notifications(self):
        """Восстанавливает системные уведомления Windows."""
        if self._original_notification_state is None:
            return
        
        try:
            import winreg
            
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings",
                0,
                winreg.KEY_SET_VALUE
            )
            
            winreg.SetValueEx(key, "NOC_GLOBAL_SETTING_ALLOW_NOTIFICATION_SOUND", 0,
                            winreg.REG_DWORD, int(self._original_notification_state))
            winreg.CloseKey(key)
            
            logger.debug("Уведомления Windows восстановлены")
        except Exception as e:
            logger.warning(f"Не удалось восстановить уведомления: {e}")
    
    def capture_full_screen(self) -> bytes:
        """
        Делает скриншот всего экрана.
        
        Returns:
            Байты изображения в формате JPEG
        """
        # Захват всего экрана
        monitor = self.sct.monitors[0]  # Все мониторы
        
        with self.sct.grab(monitor) as img:
            # Конвертируем в PIL Image
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            
            # Сжимаем в JPEG
            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=self.quality, optimize=True)
            return buffer.getvalue()
    
    def capture_region(self, left: int, top: int, width: int, height: int) -> bytes:
        """
        Делает скриншот указанной области.
        
        Args:
            left, top: Координаты левого верхнего угла
            width, height: Размеры области
            
        Returns:
            Байты изображения в формате JPEG
        """
        region = {
            "left": left,
            "top": top,
            "width": width,
            "height": height
        }
        
        with self.sct.grab(region) as img:
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            
            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=self.quality, optimize=True)
            return buffer.getvalue()
    
    def _save_screenshot(self, data: bytes) -> str:
        """
        Сохраняет скриншот в файл и возвращает путь.
        
        Args:
            data: Байты изображения
            
        Returns:
            Путь к сохранённому файлу
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        if self.output_dir:
            filename = f"screenshot_{timestamp}.jpg"
            filepath = str(self.output_dir / filename)
            
            with open(filepath, "wb") as f:
                f.write(data)
            
            logger.debug(f"Скриншот сохранён: {filepath}")
        else:
            # Сохраняем во временную директорию
            filepath = mss.tools.to_png(data, 0) if hasattr(mss.tools, 'to_png') else None
            
            # Если mss.tools не поддерживает JPEG, сохраняем вручную
            if not filepath:
                from pathlib import Path
                temp_dir = Path("/tmp")
                temp_dir.mkdir(exist_ok=True)
                filepath = str(temp_dir / f"screenshot_{timestamp}.jpg")
                
                with open(filepath, "wb") as f:
                    f.write(data)
        
        return filepath
    
    def _capture_loop(self):
        """Цикл захвата скриншотов по таймеру."""
        while self.is_running:
            try:
                if not self.is_paused:
                    # Делаем скриншот
                    start_time = time.time()
                    
                    data = self.capture_full_screen()
                    filepath = self._save_screenshot(data)
                    
                    # Добавляем в буфер
                    self.screenshot_buffer.append(filepath)
                    if len(self.screenshot_buffer) > self.max_buffer_size:
                        self.screenshot_buffer.pop(0)
                    
                    logger.debug(f"Скриншот захвачен: {filepath} ({len(data)} байт)")
                    
                    # Вызываем callback
                    if self.on_screenshot:
                        self.on_screenshot(filepath)
                    
                    # Считаем время и спим до следующего кадра
                    elapsed = time.time() - start_time
                    sleep_time = max(0, self.current_interval - elapsed)
                    
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                else:
                    time.sleep(0.1)  # Пауза
                    
            except Exception as e:
                logger.error(f"Ошибка в цикле захвата скриншотов: {e}")
                time.sleep(1)
    
    def start(self):
        """Запускает захват скриншотов."""
        if self.is_running:
            logger.warning("ScreenCapture уже запущен")
            return
        
        # Подавляем уведомления перед запуском
        self._suppress_notifications()
        
        self.is_running = True
        self.current_interval = self.normal_interval
        
        # Запускаем фоновый поток
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        
        logger.info("ScreenCapture запущен (нормальный режим)")
    
    def stop(self):
        """Останавливает захват скриншотов."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.capture_thread:
            self.capture_thread.join(timeout=5.0)
            self.capture_thread = None
        
        # Восстанавливаем уведомления
        self._restore_notifications()
        
        logger.info("ScreenCapture остановлен")
    
    def toggle_fast_mode(self):
        """Переключает между нормальным и быстрым режимом."""
        self.is_fast_mode = not self.is_fast_mode
        self.current_interval = self.fast_interval if self.is_fast_mode else self.normal_interval
        
        mode_str = "быстрый" if self.is_fast_mode else "нормальный"
        logger.info(f"Режим скриншотов: {mode_str} ({self.current_interval}s)")
    
    def set_fast_mode(self, enabled: bool):
        """Устанавливает режим скриншотов."""
        self.is_fast_mode = enabled
        self.current_interval = self.fast_interval if enabled else self.normal_interval
        
        mode_str = "быстрый" if enabled else "нормальный"
        logger.info(f"Режим скриншотов: {mode_str} ({self.current_interval}s)")
    
    def pause(self):
        """Приостанавливает захват."""
        self.is_paused = True
        logger.debug("ScreenCapture приостановлен")
    
    def resume(self):
        """Возобновляет захват."""
        self.is_paused = False
        logger.debug("ScreenCapture возобновлён")
    
    def force_capture(self) -> str:
        """
        Принудительно делает скриншот (независимо от таймера).
        
        Returns:
            Путь к сохранённому файлу
        """
        data = self.capture_full_screen()
        filepath = self._save_screenshot(data)
        
        # Добавляем в буфер
        self.screenshot_buffer.append(filepath)
        if len(self.screenshot_buffer) > self.max_buffer_size:
            self.screenshot_buffer.pop(0)
        
        logger.info(f"Принудительный скриншот: {filepath}")
        
        if self.on_screenshot:
            self.on_screenshot(filepath)
        
        return filepath
    
    def get_latest_screenshot(self) -> Optional[str]:
        """Возвращает путь к последнему скриншоту."""
        return self.screenshot_buffer[-1] if self.screenshot_buffer else None
    
    def get_all_screenshots(self) -> List[str]:
        """Возвращает все скриншоты из буфера."""
        return self.screenshot_buffer.copy()
    
    def clear_buffer(self):
        """Очищает буфер скриншотов."""
        self.screenshot_buffer.clear()
        logger.debug("Буфер скриншотов очищен")
    
    def __del__(self):
        """Очистка ресурсов при удалении объекта."""
        try:
            self.stop()
            if hasattr(self, 'sct'):
                self.sct.close()
        except:
            pass


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    capture = ScreenCapture(
        normal_interval=20.0,
        fast_interval=5.0,
        quality=80,
        output_dir="screenshots"
    )
    
    def on_screenshot(filepath):
        print(f"[SCREENSHOT] Сохранён: {filepath}")
    
    capture.on_screenshot = on_screenshot
    
    try:
        capture.start()
        
        print("Нажмите 'f' для переключения режима, 's' для принудительного скриншота, Ctrl+C для остановки")
        
        while True:
            import time
            time.sleep(0.1)
            
            # Простая обработка клавиш (для теста)
            # В реальности используется hotkey manager
            
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        capture.stop()
