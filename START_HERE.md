# Начало работы

Для установки и настройки через интерфейс откройте [README](README.md).

- [Скачать актуальный установщик](https://github.com/BLNCname/interview-assistant/releases/latest)
- [Как работает сетевой Setup.exe](docs/ONLINE_INSTALLER_RU.md)
- [Требования к видеокарте и памяти](docs/HARDWARE_RU.md)
- [Запуск и сборка из исходников](docs/BUILD_RU.md)

В **0.1.7** скачайте `Setup.exe` или `InterviewAssistant-0.1.7-win64-portable.zip` на [странице релиза](https://github.com/BLNCname/interview-assistant/releases/latest). Распакуйте переносную версию целиком. SHA-256 обоих файлов опубликован в `SHA256SUMS.txt`.

Установщик, а для переносной версии — первый запуск, автоматически подбирает GPU, скачивает модель Base с Hugging Face и проверяет распознавание. На NVIDIA используется локальная JIT-компиляция CUDA, на Intel — OpenVINO, на AMD — DirectML. На компьютере без подходящего графического адаптера доступен CPU. Подготовку можно повторить в **Settings → Audio**.

Там же выберите системный звук и микрофон. Ключи нужных сервисов вводятся в Settings. Подробная настройка описана в [README](README.md).
