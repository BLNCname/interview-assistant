# Запуск и сборка из исходников

Готовый установщик находится в [GitHub Releases](https://github.com/BLNCname/interview-assistant/releases/latest). Эта инструкция — для разработки на Windows 11 x64.

## Окружение

Установите Git, Python 3.11/3.12 x64 и [uv](https://docs.astral.sh/uv/getting-started/installation/). Для установщика нужен [Inno Setup 6](https://jrsoftware.org/isinfo.php). Для полной сборки с моделью резервируйте не менее 20 ГБ диска.

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

Контрольные суммы — в `packaging/stt_model_manifest.json`, лицензия — в model card. Для запуска из исходников укажите каталог в необязательном `.env`:

```dotenv
STT_MODEL=large-v3-turbo
STT_DEVICE=cuda
STT_COMPUTE_TYPE=int8_float16
```

Для имени `large-v3-turbo` приложение сначала ищет и проверяет локальный `models/stt/large-v3-turbo`. Если локальной модели нет, библиотека может скачать её через Hugging Face. Явный альтернативный путь лучше задавать абсолютным; относительный зависит от рабочей папки. Релиз использует проверенный встроенный каталог.

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

Сборка установщика с частями для GitHub (укажите свой путь Inno Setup):

```powershell
.\scripts\build_installer.ps1 `
  -IsccPath "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" `
  -Version 0.1.2 -DistPath dist/InterviewAssistant `
  -InventoryPath build/release-inventory.json `
  -OutputPath dist/installer -Compression normal -DiskSpanning
```

Скрипт проверяет состав и хеши дистрибутива и STT manifest. Не создавайте опись лишь ради обхода несовпадения неизвестных файлов. Без `-DiskSpanning` получается единый `.exe`, который может превышать лимит GitHub.

Проверка install → diagnostics → uninstall: `scripts/smoke_installer.ps1`. Она использует отдельный каталог внутри `build` и отказывается работать при наличии установленного приложения с тем же AppId. Все `.bin` должны оставаться рядом с проверяемым `.exe`.

Перед публикацией проверьте Git-историю, отсутствие `.env`/токенов, тесты, frozen diagnostics, модель, установку/удаление и SHA-256. Доказательства выпусков находятся в `docs/validation/`.
