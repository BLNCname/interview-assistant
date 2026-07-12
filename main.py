#!/usr/bin/env python3
"""
Interview Assistant — AI помощник для прохождения собеседований.
Мультимодальная система с аудио, скриншотами и ИИ подсказками в реальном времени.

Автор: Hermes Agent
Дата: 2024
"""

import sys
import os
import yaml
import logging
from pathlib import Path
from typing import Optional

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "utils"))

from src.multimodal_client import MultimodalClient
from src.audio_capture import AudioCapture
from src.transcriber import RealtimeTranscriber
from src.screen_capture import ScreenCapture
from src.question_detector import QuestionDetector
from src.context_aggregator import ContextAggregator
from src.overlay_gui import OverlayWindow

from utils.hotkeys import HotkeyManager


class InterviewAssistant:
    """Главный класс приложения Interview Assistant."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Инициализирует приложение."""
        
        # Загружаем конфигурацию
        self.config = self._load_config(config_path)
        
        # Настраиваем логирование
        self._setup_logging()
        
        logger.info("=" * 60)
        logger.info("Interview Assistant запускается...")
        logger.info("=" * 60)
        
        # Инициализация компонентов
        self.llm_client: Optional[MultimodalClient] = None
        self.audio_capture: Optional[AudioCapture] = None
        self.transcriber: Optional[RealtimeTranscriber] = None
        self.screen_capture: Optional[ScreenCapture] = None
        self.question_detector: Optional[QuestionDetector] = None
        self.context_aggregator: Optional[ContextAggregator] = None
        self.hotkey_manager: Optional[HotkeyManager] = None
        self.overlay: Optional[OverlayWindow] = None
        
        # Состояние приложения
        self.is_running = False
        self.is_paused = False
        
        logger.info("Все компоненты инициализированы")
    
    def _load_config(self, config_path: str) -> dict:
        """Загружает конфигурацию из YAML файла."""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            logger.info(f"Конфигурация загружена: {config_path}")
            return config
            
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            raise
    
    def _setup_logging(self):
        """Настраивает логирование."""
        log_config = self.config.get("logging", {})
        log_level = getattr(logging, log_config.get("level", "INFO"))
        log_file = log_config.get("log_file", "interview_assistant.log")
        
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout)
            ]
        )
    
    def initialize_components(self):
        """Инициализирует все компоненты приложения."""
        
        # 1. LMStudio Client (мультимодальный)
        lm_config = self.config.get("lmstudio", {})
        self.llm_client = MultimodalClient(
            host=lm_config.get("host", "localhost"),
            port=lm_config.get("port", 1234)
        )
        
        # Проверяем подключение к LMStudio
        if not self.llm_client.check_connection():
            logger.error("LMStudio не запущен! Запустите LMStudio и загрузите модель.")
            raise RuntimeError("LMStudio недоступен")
        
        logger.info("✓ LMStudio Client подключён")
        
        # 2. Audio Capture
        audio_config = self.config.get("audio", {})
        self.audio_capture = AudioCapture(
            device_index=audio_config.get("device_index"),
            sample_rate=audio_config.get("sample_rate", 16000),
            buffer_duration=audio_config.get("buffer_duration", 3.0)
        )
        logger.info("✓ Audio Capture инициализирован")
        
        # 3. Realtime Transcriber
        transcribe_config = self.config.get("audio", {})
        self.transcriber = RealtimeTranscriber(
            model_size=transcribe_config.get("transcription_model", "distil-large-v3"),
            device="auto",
            compute_type="default"
        )
        
        # Связываем аудио захват с транскрибером
        self.audio_capture.on_audio_chunk = self.transcriber.add_audio_chunk
        
        # Связываем транскрипцию с обработкой
        self.transcriber.on_transcription = self._handle_transcription
        
        logger.info("✓ Realtime Transcriber инициализирован")
        
        # 4. Question Detector
        context_config = self.config.get("context", {})
        self.question_detector = QuestionDetector(context_config)
        logger.info("✓ Question Detector инициализирован")
        
        # 5. Context Aggregator
        screen_config = self.config.get("screen", {})
        agg_config = {
            "conversation_window": context_config.get("conversation_window", 30),
            "max_buffer_size": screen_config.get("max_buffer_size", 5)
        }
        self.context_aggregator = ContextAggregator(agg_config)
        self.context_aggregator.set_question_detector(self.question_detector)
        logger.info("✓ Context Aggregator инициализирован")
        
        # 6. Screen Capture
        self.screen_capture = ScreenCapture(
            normal_interval=screen_config.get("normal_interval", 20.0),
            fast_interval=screen_config.get("fast_interval", 5.0),
            quality=screen_config.get("quality", 80),
            output_dir="screenshots" if screen_config.get("save_screenshots", False) else None
        )
        
        # Связываем скриншоты с агрегатором
        self.screen_capture.on_screenshot = self._handle_screenshot
        
        logger.info("✓ Screen Capture инициализирован")
        
        # 7. Hotkey Manager
        self.hotkey_manager = HotkeyManager()
        
        hotkeys_config = self.config.get("hotkeys", {})
        
        # Регистрируем горячие клавиши
        self.hotkey_manager.register(
            hotkeys_config.get("toggle_fast_mode", "Ctrl+Shift+S"),
            self._toggle_fast_mode
        )
        
        self.hotkey_manager.register(
            hotkeys_config.get("pause_assistant", "Ctrl+Shift+A"),
            self._toggle_pause
        )
        
        self.hotkey_manager.register(
            hotkeys_config.get("force_request", "Ctrl+Shift+R"),
            self._force_ai_request
        )
        
        logger.info("✓ Hotkey Manager инициализирован")
        
        # 8. Overlay GUI (PyQt6)
        overlay_config = self.config.get("overlay", {})
        self.overlay = OverlayWindow(overlay_config)
        self.overlay.set_status("Готов к работе")
        
        logger.info("✓ Overlay GUI создан")
    
    def _handle_transcription(self, text: str):
        """Обработчик новой транскрипции."""
        
        if self.is_paused:
            return
        
        # Добавляем в контекст
        self.context_aggregator.add_transcription(text, "interviewer")
        
        # Проверяем на вопросы/запросы на код
        event = self.question_detector.process_transcription(text)
        
        if event:
            logger.info(f"Детектировано событие: {event.event_type} (conf={event.confidence:.2f})")
            
            # Если это запрос на код — включаем быстрый режим скриншотов
            if event.event_type == "code_request":
                self.screen_capture.set_fast_mode(True)
                self.overlay.show_notification("🚀 Быстрый режим скриншотов активирован!")
                
                # Сразу делаем принудительный скриншот
                self.screen_capture.force_capture()
            
            # Запрашиваем ответ от ИИ
            self._request_ai_response()
    
    def _handle_screenshot(self, filepath: str):
        """Обработчик нового скриншота."""
        
        if self.is_paused:
            return
        
        # Добавляем в контекст
        self.context_aggregator.add_screenshot(filepath)
        logger.debug(f"Скриншот добавлен в контекст: {filepath}")
    
    def _request_ai_response(self):
        """Запрашивает ответ от ИИ на основе текущего контекста."""
        
        if self.is_paused or not self.llm_client:
            return
        
        try:
            # Строим промпт
            prompt, image_paths = self.context_aggregator.build_prompt()
            
            # Загружаем system prompt из файла
            system_prompt_path = Path(__file__).parent / "prompts" / "interview_system.md"
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
            
            model_config = self.config.get("model", {})
            
            # Отправляем запрос к ИИ
            logger.info(f"Отправка запроса к ИИ (изображений: {len(image_paths)})...")
            
            response = self.llm_client.chat(
                user_message=prompt,
                image_paths=image_paths if image_paths else None,
                system_prompt=system_prompt,
                max_tokens=model_config.get("max_tokens", 1024),
                temperature=model_config.get("temperature", 0.7)
            )
            
            # Отображаем ответ в overlay
            self.overlay.update_text(response)
            self.overlay.set_status("Ответ получен", "#4CAF50")
            
            logger.info(f"Ответ от ИИ получен: {len(response)} символов")
            
        except Exception as e:
            logger.error(f"Ошибка при запросе к ИИ: {e}")
            self.overlay.show_notification("❌ Ошибка запроса к ИИ")
    
    def _force_ai_request(self):
        """Принудительный запрос ответа от ИИ."""
        
        logger.info("Принудительный запрос к ИИ...")
        self.overlay.show_notification("🔄 Запрос к ИИ...")
        
        # Делаем скриншот если есть активный режим
        if not self.screen_capture.is_paused:
            self.screen_capture.force_capture()
        
        # Запрашиваем ответ
        self._request_ai_response()
    
    def _toggle_fast_mode(self):
        """Переключает быстрый режим скриншотов."""
        
        self.screen_capture.toggle_fast_mode()
        mode_str = "быстрый" if self.screen_capture.is_fast_mode else "нормальный"
        
        self.overlay.show_notification(f"📸 Режим: {mode_str}")
        logger.info(f"Переключён режим скриншотов: {mode_str}")
    
    def _toggle_pause(self):
        """Переключает паузу всего ассистента."""
        
        self.is_paused = not self.is_paused
        
        if self.is_paused:
            self.audio_capture.pause()
            self.screen_capture.pause()
            self.overlay.set_status("Приостановлен", "#FF5722")
            self.overlay.show_notification("⏸️ Приостановлено")
            logger.info("Ассистент приостановлен")
        else:
            self.audio_capture.resume()
            self.screen_capture.resume()
            self.overlay.set_status("Активен", "#4CAF50")
            self.overlay.show_notification("▶️ Возобновлён")
            logger.info("Ассистент возобновлён")
    
    def start(self):
        """Запускает приложение."""
        
        if self.is_running:
            logger.warning("Приложение уже запущено")
            return
        
        try:
            # Инициализация компонентов
            self.initialize_components()
            
            # Запуск PyQt6 GUI
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication(sys.argv)
            
            # Запуск компонентов
            logger.info("Запуск аудио захвата...")
            self.audio_capture.start()
            
            logger.info("Запуск транскрипции...")
            self.transcriber.start()
            
            logger.info("Запуск захвата скриншотов...")
            self.screen_capture.start()
            
            logger.info("Запуск менеджера горячих клавиш...")
            self.hotkey_manager.start()
            
            # Показываем overlay окно
            self.overlay.show()
            
            self.is_running = True
            
            logger.info("=" * 60)
            logger.info("✓ Interview Assistant запущен!")
            logger.info("=" * 60)
            logger.info("Горячие клавиши:")
            logger.info(f"  - {self.config['hotkeys']['toggle_fast_mode']}: Быстрый режим скриншотов")
            logger.info(f"  - {self.config['hotkeys']['pause_assistant']}: Пауза/возобновление")
            logger.info(f"  - {self.config['hotkeys']['force_request']}: Запрос к ИИ")
            logger.info("=" * 60)
            
            # Главный цикл PyQt6
            sys.exit(app.exec())
            
        except KeyboardInterrupt:
            logger.info("Прервано пользователем")
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
        finally:
            self.stop()
    
    def stop(self):
        """Останавливает приложение."""
        
        if not self.is_running:
            return
        
        logger.info("Остановка Interview Assistant...")
        
        try:
            # Останавливаем компоненты в обратном порядке
            if self.hotkey_manager:
                self.hotkey_manager.stop()
            
            if self.screen_capture:
                self.screen_capture.stop()
            
            if self.transcriber:
                self.transcriber.stop()
            
            if self.audio_capture:
                self.audio_capture.stop()
            
            # Закрываем GUI
            if self.overlay:
                self.overlay.close()
            
            self.is_running = False
            
            logger.info("✓ Interview Assistant остановлен")
            
        except Exception as e:
            logger.error(f"Ошибка при остановке: {e}")


def main():
    """Точка входа в приложение."""
    
    # Проверяем наличие LMStudio
    print("=" * 60)
    print("Interview Assistant — AI помощник для собеседований")
    print("=" * 60)
    print("\n⚠️  Перед запуском убедитесь, что:")
    print("  1. LMStudio запущен и работает на localhost:1234")
    print("  2. Загружена мультимодальная модель (Qwen2.5-VL-7B-Instruct)")
    print("  3. Установлены зависимости: pip install -r requirements.txt")
    print("=" * 60)
    
    # Создаём и запускаем приложение
    try:
        app = InterviewAssistant()
        app.start()
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
