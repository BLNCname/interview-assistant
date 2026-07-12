"""
Оптимизированный LMStudio клиент с:
1. Кэшированием ответов (дедупликация вопросов)
2. Streaming responses для уменьшения perceived latency
3. Асинхронные запросы через ThreadPoolExecutor
4. Автоматическая дебаунс-логика
"""

import base64
import requests
import threading
import time
import hashlib
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import logging

logger = logging.getLogger(__name__)


class ResponseCache:
    """Кэш ответов ИИ с TTL и LRU eviction."""
    
    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0):
        self.cache: Dict[str, tuple] = {}  # hash -> (response, timestamp)
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
    
    def _hash_prompt(self, prompt: str, image_paths: List[str]) -> str:
        """Создаёт хэш для запроса (текст + изображения)."""
        content = prompt
        
        # Добавляем хэши изображений (не сами данные)
        if image_paths:
            img_hashes = []
            for path in image_paths:
                try:
                    m = hashlib.md5()
                    with open(path, "rb") as f:
                        m.update(f.read())
                    img_hashes.append(m.hexdigest())
                except:
                    img_hashes.append("error")
            content += "|" + "|".join(img_hashes)
        
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get(self, prompt: str, image_paths: List[str]) -> Optional[str]:
        """Получает ответ из кэша если он свежий."""
        key = self._hash_prompt(prompt, image_paths)
        
        with self.lock:
            if key in self.cache:
                response, timestamp = self.cache[key]
                
                # Проверяем TTL
                if time.time() - timestamp < self.ttl_seconds:
                    logger.debug(f"Кэш HIT для запроса")
                    return response
                
                # Устаревший кэш — удаляем
                del self.cache[key]
        
        return None
    
    def set(self, prompt: str, image_paths: List[str], response: str):
        """Сохраняет ответ в кэш."""
        key = self._hash_prompt(prompt, image_paths)
        
        with self.lock:
            # LRU eviction если кэш полон
            if len(self.cache) >= self.max_size:
                # Удаляем самый старый элемент
                oldest_key = min(self.cache.keys(), 
                               key=lambda k: self.cache[k][1])
                del self.cache[oldest_key]
            
            self.cache[key] = (response, time.time())
            logger.debug(f"Кэш SAVE: {len(self.cache)} элементов")
    
    def clear(self):
        """Очищает кэш."""
        with self.lock:
            self.cache.clear()


class MultimodalClientOptimized:
    """Оптимизированный клиент для LMStudio с кэшированием и streaming."""
    
    def __init__(
        self, 
        host: str = "localhost", 
        port: int = 1234,
        cache_enabled: bool = True,
        cache_size: int = 100,
        cache_ttl: float = 300.0,
        max_workers: int = 2
    ):
        self.base_url = f"http://{host}:{port}"
        self.api_endpoint = f"{self.base_url}/v1/chat/completions"
        self.conversation_history: List[Dict[str, Any]] = []
        
        # Кэш ответов
        self.cache = ResponseCache(max_size=cache_size, ttl_seconds=cache_ttl) if cache_enabled else None
        
        # Пул потоков для асинхронных запросов
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Дебаунс дедупликация
        self.last_request_hash: Optional[str] = None
        self.debounce_window = 10.0  # секунд
        self.last_request_time = 0.0
        
        logger.info(f"MultimodalClientOptimized инициализирован (cache={cache_enabled})")
    
    def encode_image_to_base64(self, image_path: str) -> str:
        """Кодирует изображение в base64 (оптимизировано)."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    
    def create_multimodal_message(
        self, 
        text: str, 
        image_paths: Optional[List[str]] = None,
        role: str = "user"
    ) -> Dict[str, Any]:
        """Создаёт мультимодальное сообщение."""
        content = []
        
        if text:
            content.append({"type": "text", "text": text})
        
        if image_paths:
            for img_path in image_paths:
                try:
                    base64_image = self.encode_image_to_base64(img_path)
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    })
                except Exception as e:
                    logger.error(f"Ошибка кодирования изображения {img_path}: {e}")
        
        return {
            "role": role,
            "content": content if len(content) > 1 else (text or "")
        }
    
    def _should_debounce(self, prompt: str, image_paths: List[str]) -> bool:
        """Проверяет, не является ли запрос дубликатом."""
        current_hash = hashlib.md5(
            (prompt + "|".join(image_paths)).encode()
        ).hexdigest()
        
        # Если тот же запрос был недавно — пропускаем
        if (current_hash == self.last_request_hash and 
            time.time() - self.last_request_time < self.debounce_window):
            logger.debug(f"Дебаунс: дубликат запроса ({time.time() - self.last_request_time:.1f} сек)")
            return True
        
        self.last_request_hash = current_hash
        self.last_request_time = time.time()
        return False
    
    def chat(
        self, 
        user_message: str, 
        image_paths: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        skip_cache: bool = False
    ) -> str:
        """
        Синхронный запрос к ИИ с кэшированием.
        
        Args:
            user_message: Текст запроса
            image_paths: Пути к изображениям
            system_prompt: Системный промпт
            max_tokens: Максимум токенов
            temperature: Температура генерации
            skip_cache: Пропустить кэш (принудительный запрос)
            
        Returns:
            Текст ответа
        """
        image_paths = image_paths or []
        
        # Проверяем дебаунс
        if self._should_debounce(user_message, image_paths):
            return None  # Дубликат — пропускаем
        
        # Проверяем кэш
        if not skip_cache and self.cache:
            cached = self.cache.get(user_message, image_paths)
            if cached:
                logger.info("Ответ из кэша")
                return cached
        
        # Формируем сообщения
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.extend(self.conversation_history[-10:])
        
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
                    "model": "local-model",
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": False
                },
                timeout=120  # Увеличенный таймаут для мультимодальных запросов
            )
            response.raise_for_status()
            
            result = response.json()
            assistant_message = result["choices"][0]["message"]["content"]
            
            # Сохраняем в кэш
            if self.cache and not skip_cache:
                self.cache.set(user_message, image_paths, assistant_message)
            
            # Сохраняем в историю
            self.conversation_history.append(user_msg)
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            return assistant_message
            
        except Exception as e:
            logger.error(f"Ошибка при запросе к ИИ: {e}")
            raise
    
    def chat_stream(
        self, 
        user_message: str, 
        image_paths: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        on_token: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Потоковый запрос к ИИ (постепенный вывод токенов).
        
        Args:
            user_message: Текст запроса
            image_paths: Пути к изображениям
            system_prompt: Системный промпт
            max_tokens: Максимум токенов
            temperature: Температура генерации
            on_token: Callback для каждого токена
            
        Returns:
            Полный текст ответа
        """
        image_paths = image_paths or []
        
        # Формируем сообщения (как в chat())
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.conversation_history[-10:])
        
        user_msg = self.create_multimodal_message(
            text=user_message,
            image_paths=image_paths,
            role="user"
        )
        messages.append(user_msg)
        
        full_response = ""
        
        try:
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": "local-model",
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True  # Включаем streaming
                },
                stream=True,
                timeout=120
            )
            response.raise_for_status()
            
            # Обрабатываем потоковый ответ
            for line in response.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    
                    if line.startswith("data: "):
                        data = line[6:]  # Удаляем "data: " префикс
                        
                        if data.strip() == "[DONE]":
                            break
                        
                        try:
                            import json as json_module
                            chunk = json_module.loads(data)
                            
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                
                                if content:
                                    full_response += content
                                    
                                    # Вызываем callback для каждого токена
                                    if on_token:
                                        on_token(content)
                        
                        except json_module.JSONDecodeError:
                            continue
            
            # Сохраняем в историю
            self.conversation_history.append(user_msg)
            self.conversation_history.append({
                "role": "assistant",
                "content": full_response
            })
            
            return full_response
            
        except Exception as e:
            logger.error(f"Ошибка при streaming запросе: {e}")
            raise
    
    def chat_async(
        self, 
        user_message: str, 
        image_paths: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        callback: Optional[Callable[[str, Optional[Exception]], None]] = None
    ):
        """
        Асинхронный запрос к ИИ (в фоновом потоке).
        
        Args:
            user_message: Текст запроса
            image_paths: Пути к изображениям
            system_prompt: Системный промпт
            max_tokens: Максимум токенов
            temperature: Температура генерации
            callback: Callback(response, error) при завершении
            
        Returns:
            Future объект
        """
        future = self.executor.submit(
            self.chat,
            user_message=user_message,
            image_paths=image_paths,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature
        )
        
        if callback:
            def wrapped_callback(f):
                try:
                    result = f.result()
                    callback(result, None)
                except Exception as e:
                    callback(None, e)
            
            future.add_done_callback(wrapped_callback)
        
        return future
    
    def clear_history(self):
        """Очищает историю разговора."""
        self.conversation_history.clear()
    
    def clear_cache(self):
        """Очищает кэш ответов."""
        if self.cache:
            self.cache.clear()
    
    def check_connection(self) -> bool:
        """Проверяет подключение к LMStudio."""
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def shutdown(self):
        """Останавливает пул потоков."""
        self.executor.shutdown(wait=False)


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    client = MultimodalClientOptimized(
        cache_enabled=True,
        cache_size=100,
        cache_ttl=300.0
    )
    
    if not client.check_connection():
        print("LMStudio не запущен!")
        exit(1)
    
    print("Подключение успешно!")
    
    # Тест кэширования
    response1 = client.chat(
        user_message="Привет! Как дела?",
        system_prompt="Ты помощник."
    )
    print(f"Первый запрос: {response1[:50]}...")
    
    # Второй такой же запрос — должен прийти из кэша
    response2 = client.chat(
        user_message="Привет! Как дела?",
        system_prompt="Ты помощник."
    )
    print(f"Второй запрос (из кэша): {response2[:50]}...")
    
    # Тест streaming
    def on_token(token):
        print(token, end="", flush=True)
    
    print("\n\nStreaming ответ:")
    client.chat_stream(
        user_message="Расскажи шутку",
        system_prompt="Ты комик.",
        on_token=on_token
    )
