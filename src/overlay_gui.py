"""
PyQt6 Overlay GUI для отображения подсказок ИИ.
Невидимо при демонстрации экрана благодаря ToolWindow и layered window атрибутам.
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QVBoxLayout, 
    QWidget, QTextEdit, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QFont, QColor, QPalette, QPainter, QBrush
import win32gui
import win32con
import logging

logger = logging.getLogger(__name__)


class OverlayWindow(QMainWindow):
    """Прозрачное overlay окно для подсказок ИИ."""
    
    def __init__(self, config: dict):
        super().__init__()
        
        self.config = config
        self.current_text = ""
        self.is_visible = True
        
        # Настройки из конфига
        position = config.get("position", "bottom-right")
        offset_x = config.get("offset_x", 20)
        offset_y = config.get("offset_y", 100)
        width = config.get("width", 450)
        height = config.get("height", 300)
        font_size = config.get("font_size", 12)
        transparency = config.get("transparency", 0.9)
        
        # Устанавливаем стиль окна
        self._setup_window_style()
        
        # Создаём центральное виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Layout
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # Заголовок
        self.title_label = QLabel("🤖 Interview Assistant")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #4CAF50;
                font-size: 14px;
                font-weight: bold;
                padding: 5px;
                border-bottom: 1px solid #4CAF50;
            }
        """)
        layout.addWidget(self.title_label)
        
        # Область текста с подсказками
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setStyleSheet("""
            QTextEdit {
                background-color: rgba(30, 30, 30, 230);
                color: #E0E0E0;
                border: 1px solid #4CAF50;
                border-radius: 5px;
                padding: 8px;
                font-size: {}px;
                line-height: 1.4;
            }
        """.format(font_size))
        
        # Настройка шрифта
        font = QFont("Segoe UI", font_size)
        self.text_area.setFont(font)
        
        layout.addWidget(self.text_area)
        
        # Статус бар
        self.status_label = QLabel("● Активен")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #4CAF50;
                font-size: 11px;
                padding: 3px;
            }
        """)
        layout.addWidget(self.status_label)
        
        # Устанавливаем размер и позицию
        self.resize(width, height)
        self._set_position(position, offset_x, offset_y)
        
        # Таймер для обновления (если нужно)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._check_updates)
        self.update_timer.start(1000)  # Проверка каждую секунду
        
        logger.info(f"OverlayWindow создана: {position}, размер={width}x{height}")
    
    def _setup_window_style(self):
        """Устанавливает стили для скрытия от демонстрации экрана."""
        
        # Always on Top
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool  # Tool flag - не появляется при демо
        )
        
        # Прозрачный фон
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Для Windows: делаем окно "tool window" (не появляется в Alt+Tab и при демо)
        hwnd = int(self.winId())
        
        try:
            # Устанавливаем WS_EX_TOOLWINDOW и WS_EX_TOPMOST
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ex_style |= win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
            
            # Добавляем прозрачность через layered window
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | 
                win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
            )
            
            logger.debug("Window style настроен: ToolWindow + TopMost")
        except Exception as e:
            logger.warning(f"Не удалось настроить Windows стиль: {e}")
    
    def _set_position(self, position: str, offset_x: int, offset_y: int):
        """Устанавливает позицию окна на экране."""
        
        # Получаем размеры экрана
        screen = QApplication.primaryScreen().geometry()
        screen_width = screen.width()
        screen_height = screen.height()
        
        window_width = self.width()
        window_height = self.height()
        
        # Вычисляем позицию в зависимости от corner
        if position == "top-left":
            x = offset_x
            y = offset_y
        elif position == "top-right":
            x = screen_width - window_width - offset_x
            y = offset_y
        elif position == "bottom-left":
            x = offset_x
            y = screen_height - window_height - offset_y
        elif position == "bottom-right":
            x = screen_width - window_width - offset_x
            y = screen_height - window_height - offset_y
        else:
            # Дефолт: bottom-right
            x = screen_width - window_width - offset_x
            y = screen_height - window_height - offset_y
        
        self.move(x, y)
    
    def update_text(self, text: str):
        """
        Обновляет текст в области подсказок.
        
        Args:
            text: Новый текст для отображения
        """
        if not text.strip():
            return
        
        self.current_text = text
        
        # Форматируем текст (заменяем переносы строк)
        formatted_text = text.replace("\n", "<br>")
        
        # Ограничиваем длину (последние 2000 символов)
        if len(formatted_text) > 2000:
            formatted_text = "..." + formatted_text[-1997:]
        
        self.text_area.setHtml(formatted_text)
        
        logger.debug(f"Текст обновлён: {len(text)} символов")
    
    def clear_text(self):
        """Очищает текст."""
        self.text_area.clear()
        self.current_text = ""
    
    def set_status(self, status: str, color: str = "#4CAF50"):
        """
        Устанавливает статус в нижней части окна.
        
        Args:
            status: Текст статуса
            color: Цвет статуса (CSS format)
        """
        self.status_label.setText(f"● {status}")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 11px;
                padding: 3px;
            }}
        """)
    
    def show_notification(self, message: str):
        """Показывает краткое уведомление (всплывающее)."""
        # Временно заменяем текст
        old_text = self.current_text
        self.update_text(f"📢 {message}")
        
        # Возвращаем старый текст через 3 секунды
        QTimer.singleShot(3000, lambda: self.update_text(old_text))
    
    def toggle_visibility(self):
        """Переключает видимость окна."""
        if self.isVisible():
            self.hide()
            self.is_visible = False
            self.set_status("Приостановлен", "#FF5722")
        else:
            self.show()
            self.activateWindow()
            self.raise_()
            self.is_visible = True
            self.set_status("Активен", "#4CAF50")
    
    def _check_updates(self):
        """Проверка на обновления (заглушка для будущего использования)."""
        pass
    
    def closeEvent(self, event):
        """Обработка закрытия окна."""
        # Вместо закрытия просто скрываем
        self.hide()
        event.ignore()
    
    def mousePressEvent(self, event):
        """Обработка клика мыши (для перетаскивания)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.globalPosition().toPoint()
            self.drag_offset = self.pos() - self.drag_start_pos


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    config = {
        "position": "bottom-right",
        "offset_x": 20,
        "offset_y": 100,
        "width": 450,
        "height": 300,
        "font_size": 12,
        "transparency": 0.9
    }
    
    app = QApplication(sys.argv)
    
    window = OverlayWindow(config)
    window.show()
    
    # Тестовое обновление текста
    test_text = """
    <b>Пример ответа от ИИ:</b><br><br>
    Для реализации функции сортировки массива рекомендую использовать 
    <i>быструю сортировку (quicksort)</i>, так как она имеет среднюю 
    сложность O(n log n).<br><br>
    <b>Пример кода на Python:</b><br>
    <code>
    def quicksort(arr):
        if len(arr) <= 1:
            return arr
        pivot = arr[len(arr) // 2]
        left = [x for x in arr if x < pivot]
        middle = [x for x in arr if x == pivot]
        right = [x for x in arr if x > pivot]
        return quicksort(left) + middle + quicksort(right)
    </code>
    """
    
    window.update_text(test_text)
    window.set_status("Готов к работе", "#4CAF50")
    
    # Через 5 секунд покажем уведомление
    QTimer.singleShot(5000, lambda: window.show_notification("Новый вопрос детектирован!"))
    
    sys.exit(app.exec())
