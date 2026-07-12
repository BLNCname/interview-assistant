"""
LMStudio Client для мультимодальных запросов (текст + изображения).
Поддерживает Qwen2.5-VL и другие vision-language модели через llama.cpp backend.
"""

import base64
import requests
from typing import Optional, List, Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class MultimodalClient:
    """Клиент для LMStudio с поддержкой изображений в запросах."""
    
    def __init__(self, host: str = "localhost", port: int = 1234):
        self.base_url = f"http://{host}:{port}"
        self.api_endpoint = f"{self.base_url}/v1/chat/completions"
        self.conversation_history: List[Dict[str, Any]] = []
        
    def encode_image_to_base64(self, image_path: str) -> str:
        """Кодирует изображение в base64 строку."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    
    def create_multimodal_message(
        self, 
        text: str, 
        image_paths: Optional[List[str]] = None,
        role: str = "user"
    ) -> Dict[str, Any]:
        """
        Создаёт сообщение для мультимодальной модели.
        
        Args:
            text: Текстовый промпт
            image_paths: Список путей к изображениям (опционально)
            role: 'user', 'assistant', или 'system'
            
        Returns:
            Словарь сообщения в формате LMStudio
        """
        content = []
        
        # Добавляем текст
        if text:
            content.append({
                "type": "text",
                "text": text
            })
        
        # Добавляем изображения (если есть)
        if image_paths:
            for img_path in image_paths:
                try:
                    base64_image = self.encode_image_to_base64(img_path)
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    })
                except Exception as e:
                    logger.error(f"Ошибка кодирования изображения {img_path}: {e}")
        
        return {
            "role": role,
            "content": content if len(content) > 1 else (text or "")
        }
    
    def chat(
        self, 
        user_message: str, 
        image_paths: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> str:
        """
        Отправляет мультимодальный запрос в LMStudio.
        
        Args:
            user_message: Текст от пользователя
            image_paths: Список путей к изображениям (скриншоты)
            system_prompt: Системный промпт (опционально)
            max_tokens: Максимальное количество токенов ответа
            temperature: Температура генерации
            
        Returns:
            Текст ответа от модели
        """
        # Формируем сообщения
        messages = []
        
        # Добавляем системный промпт если есть
        if system_prompt:
            messages.append({
                "role": "system", 
                "content": system_prompt
            })
        
        # Добавляем историю разговора (последние 10 сообщений)
        messages.extend(self.conversation_history[-10:])
        
        # Добавляем текущее сообщение
        user_msg = self.create_multimodal_message(
            text=user_message,
            image_paths=image_paths,
            role="user"
        )
        messages.append(user_msg)
        
        try:
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": "local-model",  # LMStudio использует локальную модель по умолчанию
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": False
                },
                timeout=60  # Таймаут для мультимодальных запросов
            )
            response.raise_for_status()
            
            result = response.json()
            assistant_message = result["choices"][0]["message"]["content"]
            
            # Сохраняем в историю
            self.conversation_history.append(user_msg)
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            return assistant_message
            
        except requests.exceptions.ConnectionError:
            logger.error("LMStudio не запущен! Проверьте, что сервер работает на localhost:1234")
            raise
        except requests.exceptions.Timeout:
            logger.error("Таймаут запроса к LMStudio (возможно, модель загружает изображение)")
            raise
        except Exception as e:
            logger.error(f"Ошибка при запросе к LMStudio: {e}")
            raise
    
    def clear_history(self):
        """Очищает историю разговора."""
        self.conversation_history.clear()
    
    def check_connection(self) -> bool:
        """Проверяет подключение к LMStudio."""
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return response.status_code == 200
        except:
            return False


# Пример использования
if __name__ == "__main__":
    # Тестовый запуск
    client = MultimodalClient()
    
    if not client.check_connection():
        print("LMStudio не запущен! Запустите LMStudio и загрузите модель.")
        exit(1)
    
    print("Подключение к LMStudio успешно!")
    
    # Тест текстового запроса
    response = client.chat(
        user_message="Привет! Как дела?",
        system_prompt="Ты помощник на собеседовании. Отвечай кратко и по делу."
    )
    print(f"Ответ: {response}")
