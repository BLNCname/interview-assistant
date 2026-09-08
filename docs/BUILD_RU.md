# Запуск и сборка из исходников

Готовый установщик находится в [GitHub Releases](https://github.com/BLNCname/interview-assistant/releases/latest). Эта инструкция — для разработки на Windows 11 x64.

## Окружение

Установите Git, Python 3.11/3.12 x64 и [uv](https://docs.astral.sh/uv/getting-started/installation/). Для сетевого установщика используется [Inno Setup 6.7.2](https://jrsoftware.org/isinfo.php). Для полной сборки с моделью резервируйте не менее 20 ГБ диска; дополнительные сборки и кеши требуют дополнительного места. Репозиторий приватный: Git должен быть авторизован для доступа к нему.

```powershell
git clone https://github.com/BLNCname/interview-assistant.git
Set-Location interview-assistant
uv sync --extra dev --extra cuda --frozen
```

Точные версии находятся в `uv.lock`; `requirements.txt` — только совместимый указатель на проект, а не lock-файл. Зафиксированные зависимости не гарантируют побайтовое совпадение EXE при другом Windows/Python/PyInstaller. Исходники не содержат веса STT, ключи, `.venv` или готовые бинарники.

## STT-модель

Скачайте закреплённую версию и проверьте файлы:

```powershell
$ModelPath = Join-Path $PWD 'models\stt\large-v3-turbo'
.\.venv\Scripts\hf.exe download dropbox-dash/faster-whisper-large-v3-turbo `
  --revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf `
  --local-dir $ModelPath
.\.venv\Scripts\python.exe -m interview_assistant.stt.bundle `
  --validate-bundle $ModelPath --require-all-files
```

Контрольные суммы — в `packaging/stt_model_manifest.json`, лицензия — в model card. Для запуска из исходников задайте профиль в необязательном `.env`:

```dotenv
STT_MODEL=large-v3-turbo
STT_DEVICE=cuda
STT_COMPUTE_TYPE=int8_float16
```

Для имени `large-v3-turbo` приложение сначала ищет и проверяет локальный `models/stt/large-v3-turbo`. Если локальной модели нет, библиотека может скачать её через Hugging Face. Явный альтернативный путь лучше задавать абсолютным; относительный зависит от рабочей папки. Готовая установка использует локальный каталог, который подготовил и проверил Setup.

## Запуск и тесты

```powershell
.\scripts\start.ps1 -SelfTestOnly
.\scripts\start.ps1
```

Выберите модели, устройства и введите ключи в Settings по [README](../README.md). `start.ps1` использует корневой `config.yaml`; прямой `main.py` без `--config` — пользовательский AppData-каталог.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check interview_assistant tests scripts main.py
.\.venv\Scripts\python.exe -m mypy interview_assistant
```

Для воспроизведения WAV и замеров STT: `scripts/replay_interview.py --help`. Автотесты не заменяют проверку CUDA, звука и внешних сервисов на целевом ПК.

## Сборка

```powershell
.\scripts\build.ps1 -SttModelPath $ModelPath -VerifyCuda `
  -ConfigPath .\packaging\source_release_config.yaml
```

Результат: `dist\InterviewAssistant\InterviewAssistant.exe` и `_internal`. Переносить нужно всю папку. Сборка без `-SttModelPath` не содержит полной встроенной STT-модели.

После тестов и проверки приложения создайте новую опись этой сборки; историческая `packaging/dist_inventory.json` относится к прежнему артефакту:

```powershell
.\.venv\Scripts\python.exe scripts/generate_release_inventory.py `
  --dist dist/InterviewAssistant --output build/release-inventory.json `
  --application InterviewAssistant
```

Из этой полной сборки создайте сетевой установщик. Опись и `COLLECT-00.toc` должны относиться к тому же запуску PyInstaller, а `.venv` — к его окружению. Укажите свой путь Inno Setup и новый пустой каталог результата:

```powershell
.\scripts\build_online_installer.ps1 `
  -IsccPath "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" `
  -DistPath dist/InterviewAssistant `
  -InventoryPath build/release-inventory.json `
  -CollectTocPath build/pyinstaller/interview_assistant/COLLECT-00.toc `
  -OutputPath dist/online-installer-0.1.3 `
  -Version 0.1.3
```

`build_online_installer.ps1` проверяет исходный portable по описи и STT manifest, сопоставляет его файлы с закреплёнными wheel-архивами из `uv.lock`, получает и проверяет архивы, затем компилирует **один `Setup.exe`**. Код приложения и базовый Python включаются в него; публичные зависимости и модель загружаются во время установки. Не создавайте опись лишь ради обхода несовпадения неизвестных файлов.

Кеш издателя по умолчанию — `build/online-installer-cache`; другой каталог задаётся через `-CachePath`. Это отдельный кеш от пользовательского `%LOCALAPPDATA%\InterviewAssistant\InstallerCache`. В каталоге результата также остаются manifest, журналы и `SHA256SUMS.txt` для проверки издателем. В Assets релиза публикуется только `Setup.exe`, а его SHA-256 переносится в описание релиза.

Устройство загрузок, ограничения кеша и публичные источники описаны в [ONLINE_INSTALLER_RU.md](ONLINE_INSTALLER_RU.md). Старый `build_installer.ps1` сохранён для прежнего офлайн-формата и не является основным способом подготовки релиза 0.1.3.

Для проверки install → diagnostics → uninstall существует `scripts/smoke_installer.ps1`. Он использует отдельный каталог внутри `build` и отказывается работать при наличии установленного приложения с тем же AppId. Для сетевого выпуска дополнительно проверьте загрузку с пустым кешем, повторный запуск с кешем и отказ при повреждённом файле. Наличие собранного `Setup.exe` само по себе не подтверждает эти проверки.

Перед публикацией проверьте Git-историю, отсутствие `.env`/токенов, тесты, frozen diagnostics, модель, установку/удаление и SHA-256. Доказательства выпусков находятся в `docs/validation/`.
