#!/usr/bin/env python3
"""
Interview Assistant — Оптимизированная версия.

Улучшения:
1. Кэширование ответов ИИ (дедупликация повторяющихся вопросов)
2. Streaming responses для уменьшения perceived latency
3. Асинхронные запросы к LMStudio
4. Дебаунс событий (не спамить одинаковыми запросами)
5. Кастомизируемые хоткеи через CLI: --hotkeys "name1=Ctrl+Shift+S,name2=Ctrl+Shift+A"
6. Предзагрузка скриншотов в фоне

Запуск:
  python main_optimized.py
  python main_optimized.py --hotkeys "toggle_fast_mode=Alt+S,pause_assistant=Alt+A"
"""

import sys
import os
import yaml
import logging
import argparse
from pathlib import Path
from typing import Optional

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "utils"))

from src.multimodal_client_optimized import MultimodalClientOptimized
from src.audio_capture import AudioCapture
from src.transcriber import RealtimeTranscriber
from src.screen_capture import ScreenCapture
from src.question_detector import QuestionDetector
from src.context_aggregator import ContextAggregator
from src.overlay_gui import OverlayWindow

from utils.hotkeys import HotkeyManager, HotkeyValidationError


logger = logging.getLogger(__name__)


class InterviewAssistantOptimized:
    """Оптимизированный Interview Assistant с кэшированием и streaming."""
    
    def __init__(self, config_path: str = "config.yaml", cli_hotkeys: Optional[str] = None):
        """
        Инициализирует приложение.
        
        Args:
            config_path: Путь к конфигурационному файлу
            cli_hotkeys: CLI формат хоткеев (опционально)
                Пример: "toggle_fast_mode=Alt+S,pause_assistant=Alt+A"
        """
        
        # Загружаем конфигурацию
        self.config = self._load_config(config_path)
        
        # Переопределяем хоткеи если переданы через CLI
        if cli_hotkeys:
            logger.info(f"Переопределение хоткеев через CLI: {cli_hotkeys}")
            self._apply_cli_hotkeys(cli_hotkeys)
        
        # Настраиваем логирование
        self._setup_logging()
        
        logger.info("=" * 60)
        logger.info("Interview Assistant (OPTIMIZED) запускается...")
        logger.info("=" * 60)
        
        # Инициализация компонентов
        self.llm_client: Optional[MultimodalClientOptimized] = None
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
        
        # Статистика для мониторинга
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "debounced_requests": 0
        }
        
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
    
    def _apply_cli_hotkeys(self, cli_hotkeys: str):
        """Применяет хоткеи из CLI аргументов."""
        try:
            pairs = cli_hotkeys.split(',')
            
            for pair in pairs:
                if '=' not in pair:
                    continue
                
                name, hotkey_str = pair.split('=', 1)
                name = name.strip()
                hotkey_str = hotkey_str.strip()
                
                # Обновляем конфиг
                if "hotkeys" not in self.config:
                    self.config["hotkeys"] = {}
                
                self.config["hotkeys"][name] = hotkey_str
                logger.info(f"CLI хоткей: {name} → {hotkey_str}")
                
        except Exception as e:
            logger.error(f"Ошибка применения CLI хоткеев: {e}")
    
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
        
        # 1. LMStudio Client (ОПТИМИЗИРОВАННЫЙ с кэшированием)
        lm_config = self.config.get("lmstudio", {})
        model_config = self.config.get("model", {})
        
        self.llm_client = MultimodalClientOptimized(
            host=lm_config.get("host", "localhost"),
            port=lm_config.get("port", 1234),
            cache_enabled=True,  # Включаем кэш
            cache_size=100,
            cache_ttl=model_config.get("cache_ttl", 300.0)  # 5 минут по умолчанию
        )
        
        if not self.llm_client.check_connection():
            logger.error("LMStudio не запущен!")
            raise RuntimeError("LMStudio недоступен")
        
        logger.info("✓ LMStudio Client подключён (с кэшированием)")
        
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
        
        self.audio_capture.on_audio_chunk = self.transcriber.add_audio_chunk
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
        
        self.screen_capture.on_screenshot = self._handle_screenshot
        logger.info("✓ Screen Capture инициализирован")
        
        # 7. Hotkey Manager (с поддержкой CLI аргументов)
        self.hotkey_manager = HotkeyManager()
        
        hotkeys_config = self.config.get("hotkeys", {})
        
        # Регистрируем хоткеи с описаниями
        self._register_hotkeys(hotkeys_config)
        
        logger.info("✓ Hotkey Manager инициализирован")
        
        # 8. Overlay GUI
        overlay_config = self.config.get("overlay", {})
        self.overlay = OverlayWindow(overlay_config)
        self.overlay.set_status("Готов к работе")
        
        logger.info("✓ Overlay GUI создан")
    
    def _register_hotkeys(self, hotkeys_config: dict):
        """Регистрирует все хоткеи из конфигурации."""
        
        # Тоггл быстрого режима
        toggle_key = hotkeys_config.get("toggle_fast_mode", "Ctrl+Shift+S")
        self.hotkey_manager.register(
            name="toggle_fast_mode",
            hotkey_str=toggle_key,
            callback=self._toggle_fast_mode,
            description="Переключить быстрый режим скриншотов"
        )
        
        # Пауза ассистента
        pause_key = hotkeys_config.get("pause_assistant", "Ctrl+Shift+A")
        self.hotkey_manager.register(
            name="pause_assistant",
            hotkey_str=pause_key,
            callback=self._toggle_pause,
            description="Пауза/возобновление ассистента"
        )
        
        # Принудительный запрос к ИИ
        force_key = hotkeys_config.get("force_request", "Ctrl+Shift+R")
        self.hotkey_manager.register(
            name="force_request",
            hotkey_str=force_key,
            callback=self._force_ai_request,
            description="Принудительный запрос к ИИ"
        )
        
        # Дополнительно: очистка кэша (опционально)
        if "clear_cache" in hotkeys_config:
            clear_key = hotkeys_config["clear_cache"]
            self.hotkey_manager.register(
                name="clear_cache",
                hotkey_str=clear_key,
                callback=self._clear_cache,
                description="Очистить кэш ИИ"
            )
    
    def _handle_transcription(self, text: str):
        """Обработчик новой транскрипции."""
        
        if self.is_paused:
            return
        
        self.context_aggregator.add_transcription(text, "interviewer")
        
        event = self.question_detector.process_transcription(text)
        
        if event:
            logger.info(f"Детектировано событие: {event.event_type} (conf={event.confidence:.2f})")
            
            if event.event_type == "code_request":
                self.screen_capture.set_fast_mode(True)
                self.overlay.show_notification("🚀 Быстрый режим скриншотов!")
                self.screen_capture.force_capture()
            
            # Запрашиваем ответ от ИИ (с кэшированием и дебаунсом)
            self._request_ai_response()
    
    def _handle_screenshot(self, filepath: str):
        """Обработчик нового скриншота."""
        
        if self.is_paused:
            return
        
        self.context_aggregator.add_screenshot(filepath)
        logger.debug(f"Скриншот добавлен: {filepath}")
    
    def _request_ai_response(self, skip_cache: bool = False):
        """Запрашивает ответ от ИИ с кэшированием."""
        
        if self.is_paused or not self.llm_client:
            return
        
        try:
            prompt, image_paths = self.context_aggregator.build_prompt()
            
            system_prompt_path = Path(__file__).parent / "prompts" / "interview_system.md"
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
            
            model_config = self.config.get("model", {})
            
            logger.info(f"Запрос к ИИ (изображений: {len(image_paths)}, cache_skip={skip_cache})...")
            
            # Используем оптимизированный клиент с кэшированием
            response = self.llm_client.chat(
                user_message=prompt,
                image_paths=image_paths if image_paths else None,
                system_prompt=system_prompt,
                max_tokens=model_config.get("max_tokens", 1024),
                temperature=model_config.get("temperature", 0.7),
                skip_cache=skip_cache
            )
            
            # Статистика
            self.stats["total_requests"] += 1
            
            if response is None:
                # Дебаунс сработал
                self.stats["debounced_requests"] = self.stats.get("debounced_requests", 0) + 1
                logger.debug("Запрос дебаунсен (дубликат)")
                return
            
            # Проверяем, был ли ответ из кэша
            cache_hits = getattr(self.llm_client, '_cache_hits', 0)
            if cache_hits > 0:
                self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
            
            self.overlay.update_text(response)
            self.overlay.set_status("Ответ получен", "#4CAF50")
            
            logger.info(f"Ответ от ИИ: {len(response)} символов")
            
        except Exception as e:
            logger.error(f"Ошибка при запросе к ИИ: {e}")
            self.overlay.show_notification("❌ Ошибка запроса к ИИ")
    
    def _force_ai_request(self):
        """Принудительный запрос (обход кэша)."""
        
        logger.info("Принудительный запрос к ИИ (skip_cache=True)...")
        self.overlay.show_notification("🔄 Запрос к ИИ...")
        
        if not self.screen_capture.is_paused:
            self.screen_capture.force_capture()
        
        # Пропускаем кэш для принудительного запроса
        self._request_ai_response(skip_cache=True)
    
    def _toggle_fast_mode(self):
        """Переключает быстрый режим скриншотов."""
        
        self.screen_capture.toggle_fast_mode()
        mode_str = "быстрый" if self.screen_capture.is_fast_mode else "нормальный"
        
        self.overlay.show_notification(f"📸 Режим: {mode_str}")
        logger.info(f"Режим скриншотов: {mode_str}")
    
    def _toggle_pause(self):
        """Переключает паузу."""
        
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
    
    def _clear_cache(self):
        """Очищает кэш ИИ."""
        
        if self.llm_client:
            self.llm_client.clear_cache()
            self.overlay.show_notification("🗑️ Кэш очищен")
            logger.info("Кэш ИИ очищен")
    
    def start(self):
        """Запускает приложение."""
        
        if self.is_running:
            logger.warning("Приложение уже запущено")
            return
        
        try:
            self.initialize_components()
            
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication(sys.argv)
            
            logger.info("Запуск компонентов...")
            
            self.audio_capture.start()
            self.transcriber.start()
            self.screen_capture.start()
            self.hotkey_manager.start()
            
            self.overlay.show()
            
            self.is_running = True
            
            logger.info("=" * 60)
            logger.info("✓ Interview Assistant (OPTIMIZED) запущен!")
            logger.info("=" * 60)
            
            # Показываем статистику хоткеев
            logger.info("Горячие клавиши:")
            for h in self.hotkey_manager.list_hotkeys():
                logger.info(f"  {h['hotkey']} → {h['description']}")
            
            if self.config.get("hotkeys", {}).get("clear_cache"):
                logger.info("  (Дополнительно: очистка кэша)")
            
            logger.info("=" * 60)
            logger.info("Оптимизации:")
            logger.info("  ✓ Кэширование ответов ИИ (TTL=300s)")
            logger.info("  ✓ Дебаунс дубликатов запросов")
            logger.info("  ✓ Асинхронные запросы к LMStudio")
            logger.info("=" * 60)
            
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
            if self.hotkey_manager:
                self.hotkey_manager.stop()
            
            if self.screen_capture:
                self.screen_capture.stop()
            
            if self.transcriber:
                self.transcriber.stop()
            
            if self.audio_capture:
                self.audio_capture.stop()
            
            # Останавливаем пул потоков LMStudio клиента
            if self.llm_client:
                self.llm_client.shutdown()
            
            if self.overlay:
                self.overlay.close()
            
            self.is_running = False
            
            # Выводим статистику
            logger.info("=" * 60)
            logger.info("Статистика сессии:")
            logger.info(f"  Всего запросов к ИИ: {self.stats.get('total_requests', 0)}")
            logger.info(f"  Дебаунсировано: {self.stats.get('debounced_requests', 0)}")
            logger.info("=" * 60)
            
            logger.info("✓ Interview Assistant остановлен")
            
        except Exception as e:
            logger.error(f"Ошибка при остановке: {e}")


def main():
    """Точка входа с поддержкой CLI аргументов."""
    
    parser = argparse.ArgumentParser(
        description="Interview Assistant — AI помощник для собеседований",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:

  Базовый запуск:
    python main_optimized.py
  
  С кастомными хоткеями через CLI:
    python main_optimized.py --hotkeys "toggle_fast_mode=Alt+S,pause_assistant=Alt+A"
  
  С указанием конфига:
    python main_optimized.py --config custom_config.yaml

Формат CLI хоткеев:
  name1=hotkey1,name2=hotkey2
  
  Примеры:
    toggle_fast_mode=Ctrl+Shift+S
    pause_assistant=Alt+A
    force_request=Ctrl+Shift+R,clear_cache=Ctrl+Shift+X

Доступные действия для хоткеев:
  - toggle_fast_mode: Переключить быстрый режим скриншотов
  - pause_assistant: Пауза/возобновление ассистента
  - force_request: Принудительный запрос к ИИ
  - clear_cache: Очистить кэш ИИ (опционально)
        """
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Путь к конфигурационному файлу (по умолчанию: config.yaml)"
    )
    
    parser.add_argument(
        "--hotkeys",
        type=str,
        default=None,
        help="CLI формат хоткеев: 'name1=hotkey1,name2=hotkey2'"
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Interview Assistant (OPTIMIZED)")
    print("=" * 60)
    print("\n⚠️  Перед запуском убедитесь, что:")
    print("  1. LMStudio запущен на localhost:1234")
    print("  2. Загружена мультимодальная модель (Qwen2.5-VL-7B-Instruct)")
    print("  3. Установлены зависимости: pip install -r requirements.txt")
    print("=" * 60)
    
    if args.hotkeys:
        print(f"\n🔑 Кастомные хоткеи: {args.hotkeys}")
    
    try:
        app = InterviewAssistantOptimized(
            config_path=args.config,
            cli_hotkeys=args.hotkeys
        )
        app.start()
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
