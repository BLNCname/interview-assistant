# Interview Assistant

Interview Assistant — Windows-приложение для согласованных учебных интервью и лабораторных демонстраций. Оно захватывает системный звук и микрофон, распознаёт русскую и английскую речь локальным `faster-whisper`, обнаруживает вопросы, при необходимости добавляет временный снимок экрана и стримит ответ из LM Studio в полупрозрачную ленту Liquid Ribbon.

Проект не предназначен для скрытого использования. Перед записью звука, захватом экрана, сетевым поиском или записью встречи необходимо получить согласие всех участников и соблюдать правила площадки.

## Что реализовано

- Два независимых аудиоисточника: WASAPI loopback для собеседующего и обычный микрофон для кандидата.
- Потоковое RU/EN-распознавание на основном ПК через `faster-whisper`/CTranslate2 CUDA.
- Автоматическое обнаружение вопросов и ручной запрос по hotkey.
- Event-driven снимки: автоматический захват только для задач, где экран нужен по смыслу, и ручной резервный hotkey.
- Отбрасывание почти чёрных, защищённых и дублирующихся кадров; защищённый кадр не отправляется модели.
- Нативный streaming endpoint LM Studio `POST /api/v1/chat`, `store: false`, текстовые и мультимодальные запросы.
- Выбор моделей из `GET /v1/models`. Одну мультимодальную модель можно выбрать для текста и изображений: приложение использует один загруженный instance, а не две копии.
- Однократное восстановление выгруженной модели и повтор последнего актуального запроса с сокращённым recovery-контекстом.
- Ограниченный MCP retrieval: Context7 только для документации и локальный DuckDuckGo MCP только для поиска.
- Полупрозрачная верхняя Liquid Ribbon, настройка opacity/height, сворачивание, перенос и сохранение геометрии.
- Полный readiness gate и идемпотентное завершение audio/STT/capture/network/hotkey ресурсов.
- Windows onedir-пакет `dist\InterviewAssistant\InterviewAssistant.exe`.

## Архитектура развёртывания

Все функции приложения выполняются на основном Windows-ПК:

```text
Teams + microphone + display
          │
          ▼
InterviewAssistant.exe (основной ПК)
  WASAPI → CUDA STT → detector → context → Liquid Ribbon
                           │
                           ▼
                 LM Studio API на 127.0.0.1
                           │
              LM Link → Strix Halo (только inference)
                           │
                 Context7 / DuckDuckGo MCP
```

Strix Halo не запускает UI, аудиозахват, STT или screenshot worker. Он используется как inference-устройство через LM Link. Клиент намеренно принимает только loopback-адрес LM Studio; сетевой маршрут к inference-хосту остаётся ответственностью LM Studio/LM Link.

## Требования

### Основной ПК

- Windows 11 x64 с включённым Desktop Window Manager.
- Python 3.11 или 3.12 для запуска из исходников и сборки.
- NVIDIA GPU и драйвер/CUDA runtime, совместимые с установленным CTranslate2, для рабочей потоковой STT-сессии.
- Доступные и разные устройства: WASAPI loopback системного звука и микрофон.
- Разрешение Windows на доступ к микрофону.

CPU подходит для тестов, сборки и `--diagnostics --no-gui`, но текущий production-профиль STT создаёт `WhisperEngine(device="cuda", compute_type="float16")`. Интерактивная сессия без NVIDIA CUDA readiness gate не проходит.

### Inference

- [LM Studio 0.4+](https://lmstudio.ai/docs/developer/rest) и CLI `lms` на основном ПК.
- LM Link, связанный со Strix Halo; статус должен читаться командой `lms link status --json`.
- Одна или две подходящие модели, видимые локальному серверу LM Studio. Для screenshot-вопросов vision slot должен указывать на мультимодальную модель.

LM Studio не документирует в REST-ответе гарантированный физический placement конкретного instance. Readiness показывает наличие имени preferred device в LM Link status, но это предупреждение, а не доказательство маршрутизации. Проверяйте фактическое устройство в LM Studio и на Strix Halo.

## Установка из исходников

Установите [`uv`](https://docs.astral.sh/uv/getting-started/installation/), затем в
PowerShell из корня репозитория выполните frozen-синхронизацию:

```powershell
uv lock --check
uv sync --extra dev --frozen
uv run --extra dev --frozen pytest -q
```

`pyproject.toml` задаёт диапазоны зависимостей, а проверенный в репозитории `uv.lock`
фиксирует воспроизводимое разрешение вместе с dev extra. Поведение frozen sync описано в
[официальной документации uv](https://docs.astral.sh/uv/concepts/projects/sync/).
`requirements.txt` оставлен только как compatibility shim (`-e .`): это не lock-файл и
не канонический путь для воспроизводимой установки.

Здесь воспроизводимость означает одинаковое locked-разрешение зависимостей для выбранного
поддерживаемого Python 3.11 или 3.12. Она не обещает побайтово одинаковый EXE между разными
версиями Python, Windows SDK, PyInstaller или другой сборочной toolchain.

## LM Studio и LM Link

1. Установите LM Studio 0.4+ и включите Local Server на основном ПК.
2. Свяжите Strix Halo по официальной инструкции [LM Link](https://lmstudio.ai/docs/developer/core/lmlink).
3. Проверьте `lms link status --json`.
4. Оставьте endpoint приложения на `127.0.0.1:1234` (или другом локальном порту LM Studio).
5. Загрузите нужную модель на Strix Halo и убедитесь, что она видна в `GET http://127.0.0.1:1234/v1/models`.
6. В Settings нажмите Run checks, выберите устройства и модели, затем снова запустите проверки.

### Аутентификация

LM Studio поддерживает API tokens; см. [официальную документацию](https://lmstudio.ai/docs/developer/core/authentication). Введите token в Settings. Приложение сохраняет его в Windows Credential Manager под service `InterviewAssistant`, а не в YAML. Поле `lmstudio.api_token` в конфигурации намеренно отвергается.

HTTP-клиент не доверяет `HTTP_PROXY`/`HTTPS_PROXY`, поэтому loopback token, transcript и image data URL не уходят в системный proxy. Не публикуйте LM Studio API в LAN напрямую: приложение рассчитано на локальный endpoint и LM Link.

### Одна модель для двух ролей

Settings получает ключи моделей через `GET /v1/models`. Можно выбрать один и тот же key в полях Text model и Vision model. Registry дедуплицирует ключи и повторно использует тот же LM Studio instance ID. Отдельные модели создаются только для разных ключей.

## Конфигурация

После первого запуска приложение использует:

```text
%LOCALAPPDATA%\InterviewAssistant\InterviewAssistant\config.yaml
```

Пример без секретов:

```yaml
audio:
  system_device_id: null
  microphone_device_id: null
  sample_rate: 16000
  language: auto
  stt_model: large-v3-turbo
lmstudio:
  host: 127.0.0.1
  port: 1234
  text_model: ""
  vision_model: ""
  preferred_device_name: "Strix Halo"
capture:
  mode: event
  persistent_screenshots: false
  black_frame_threshold: 0.92
search:
  mode: auto
  provider: duckduckgo
  timeout_seconds: 5.0
overlay:
  opacity: 0.88
  max_height: 360
```

Обычно YAML не нужно редактировать вручную: выбор устройств, языка, моделей, режима поиска, overlay и горячих клавиш доступен в Settings. Повреждённый файл не перезаписывается автоматически.

## MCP: актуальная документация и поиск

LM Studio владеет MCP-процессами и вызывает их в рамках native chat API; приложение передаёт только allowlisted integration IDs/tools. Полный transcript и screenshot в поисковый запрос не включаются. Текущий вопрос очищается от имён, e-mail, URL с чувствительными query-параметрами и transcript metadata.

Разрешены только:

- `mcp/context7`: `resolve-library-id`, `query-docs`;
- `mcp/duckduckgo`: `search`.

Нет shell, filesystem-write, form submission, произвольного browser automation или произвольного page fetch.

### Установка бесплатного DuckDuckGo MCP

В репозитории закреплён [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server) `v0.5.0`/commit `8992977d65a086995c82826ceead42e890aa17c1` и hash-locked зависимости:

```powershell
py -3.11 -m venv tools\duckduckgo-mcp\.venv
.\tools\duckduckgo-mcp\.venv\Scripts\python -m pip install --require-hashes -r tools\duckduckgo-mcp\build-requirements.lock
.\tools\duckduckgo-mcp\.venv\Scripts\python -m pip install --no-build-isolation --require-hashes -r tools\duckduckgo-mcp\requirements.lock
```

Создайте bounded `mcp.json`, передав существующую директорию конфигурации LM Studio и абсолютный путь к executable:

```powershell
.\.venv\Scripts\python scripts\configure_mcp.py `
  --config-dir "C:\absolute\existing\lmstudio-config-dir" `
  --duckduckgo-executable "$PWD\tools\duckduckgo-mcp\.venv\Scripts\duckduckgo-mcp-server.exe"
```

Helper делает резервную копию только распознанной безопасной конфигурации и отказывается объединять неизвестные серверы или secret-like поля.

[Context7](https://github.com/upstash/context7) подключается к `https://mcp.context7.com/mcp`. Это удалённый сервис, а не локальный компонент; его владельцы рекомендуют API key для больших лимитов. Текущий bounded template использует endpoint без ключа и не записывает secrets в `mcp.json`. DuckDuckGo MCP запускается локально, но поисковые запросы всё равно уходят в интернет к внешнему поисковому сервису. В LM Studio разрешите вызовы только серверов из проверенного `mcp.json`; см. [LM Studio MCP](https://lmstudio.ai/docs/developer/core/mcp).

## Запуск

Из исходников:

```powershell
.\.venv\Scripts\python main.py
```

Из onedir-пакета:

```powershell
.\dist\InterviewAssistant\InterviewAssistant.exe
```

На старте Settings запускает readiness для DWM, двух audio devices, CUDA/STT, LM Studio auth, LM Link, model discovery/load/dedup, MCP, hotkeys, capture affinity, event capture и streaming TTFT. Start становится доступен только после обязательных проверок. Некоторые интеграционные проверки имеют статус warning, если их нельзя доказать без реального tool request или аппаратного теста.

### Hotkeys

Назначения меняются в Settings → `Горячие клавиши`: отредактируйте одну
комбинацию для каждого действия, затем сохраните форму. Settings проверяет
формат, обязательный модификатор и дубликаты, применяет корректный набор без
перезапуска и позволяет вернуть все семь значений кнопкой
`Восстановить стандартные`. Таблица ниже — видимые значения по умолчанию, а не
команды для запоминания: после настройки ориентируйтесь на Settings.

| Комбинация | Действие |
|---|---|
| `Ctrl+Shift+Space` | Отправить последний финальный вопрос вручную |
| `Ctrl+Shift+S` | Подготовить временный снимок для следующего запроса |
| `Ctrl+Shift+P` | Пауза/возобновление аудиозахвата |
| `Ctrl+Shift+O` | Показать/скрыть Ribbon локально |
| `Ctrl+Shift+I` | Перейти в режим изменения положения или размера Ribbon |
| `Ctrl+Shift+W` | Принудительно включить web search для следующего запроса |
| `Ctrl+Shift+C` | Очистить ответ и transcript history в памяти |

Ribbon запускается в пассивном click-through режиме: клики, колесо и hover
проходят к приложению под ним. `Ctrl+Shift+I` переключает в режим настройки,
где доступны перетаскивание, изменение размера, прокрутка и выделение текста;
повторное нажатие возвращает пассивный режим. Переключение видимости не
останавливает запись, распознавание или обработку запросов.

Горячая клавиша снимка показывает состояния `Снимок создаётся`, `Снимок готов
для следующего запроса` и, после успешного включения в следующий запрос,
`Снимок добавлен в запрос`. Непригодный или защищённый кадр не прикрепляется и
показывает краткую ошибку без пути к файлу. Снимок одноразовый: новый заменяет
старый, а отправленный не используется повторно.

Автоматическое обнаружение вопроса работает для финальных фраз с обоих
источников — системного звука `Interviewer` и микрофона `You`. Роли сохраняются
в контексте, а одинаковая фраза, пойманная обоими источниками, создаёт только
один запрос. Уточнение с микрофона используется вместе с последним запросом
интервьюера, чтобы подготовить ответ кандидата по текущей теме.

Кнопки Ribbon также открывают Settings и выполняют явный Quit. Закрытие Settings во время активной сессии не завершает приложение.

## Диагностика и сборка

### CPU/no-GUI smoke

Режим не создаёт `QApplication`, не открывает audio devices, не загружает модель и не делает сетевые запросы. Он проверяет замороженные imports, metadata, config schema и доступные CTranslate2 backends. Отсутствие CUDA — warning, а не ошибка:

```powershell
.\.venv\Scripts\python main.py --diagnostics --no-gui --diagnostics-output .\build\source-diagnostics.json
```

Для `console=False` executable всегда указывайте файл отчёта:

```powershell
.\dist\InterviewAssistant\InterviewAssistant.exe --diagnostics --no-gui --diagnostics-output .\build\packaged-diagnostics.json
```

JSON пишется атомарно и не содержит путей конфигурации, model keys, tokens или transcript.

### CUDA smoke

CUDA verifier загружает `audio.stt_model` из указанного config с `device="cuda"`, прогоняет bundled 2.0-second synthetic PCM WAV, форсирует чтение всех segments и выводит JSON с model-load time, transcription time и real-time factor. Успешный отчёт отмечает источник модели как `configured` или `bundled`, но не раскрывает её идентификатор или локальный путь:

```powershell
.\.venv\Scripts\python scripts\verify_cuda.py --config "$env:LOCALAPPDATA\InterviewAssistant\InterviewAssistant\config.yaml"
```

Он возвращает ненулевой код при отсутствии CUDA, модели, fixture или inference. Синтетический fixture проверяет только целостность CUDA/runtime и не измеряет точность RU/EN. Реальные языковые метрики требуют согласованного корпуса на RTX-машине. При запуске из исходников без локального bundle `faster-whisper` сохраняет обычное поведение alias: использует доступный cache либо скачивает модель согласно собственным правилам.

### Воспроизводимая onedir-сборка

```powershell
.\scripts\build.ps1
```

Скрипт сначала требует `uv`, отдельно выполняет `uv lock --check`, а затем `uv sync --extra dev --frozen`; отсутствующий или устаревший `uv.lock` останавливает сборку даже с `-SkipTests`. Затем он запускает тесты, задаёт `SOURCE_DATE_EPOCH` из текущего Git commit, отключает недетерминированный hash seed, собирает `console=False`/`onedir` через PyInstaller и запускает packaged no-GUI diagnostics с гарантированно отсутствующим тестовым config. На AMD development machine этого достаточно:

```powershell
.\scripts\build.ps1 -SkipTests
```

Сборка без `-SttModelPath` остаётся лёгкой: она не скачивает веса и не добавляет Whisper weights в `dist`. Для offline STT release передайте каталог заранее полученной pinned CTranslate2-модели:

```powershell
.\scripts\build.ps1 -SttModelPath "D:\models\faster-whisper-large-v3-turbo"
```

Скрипт проверяет каталог по `packaging/stt_model_manifest.json` до запуска PyInstaller: обязательны точные имена, размеры и SHA-256 всех шести файлов. В пакет копируются только перечисленные manifest-файлы под `models/stt/large-v3-turbo`; локальная `.cache`/Hugging Face metadata не копируется. Одна многоязычная модель `large-v3-turbo` обслуживает RU/EN. Зафиксированы repository `dropbox-dash/faster-whisper-large-v3-turbo` и revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`, включая `README.md` с model card и уведомлением лицензии `MIT`.

На RTX 5070 Ti offline release дополнительно проверьте CUDA на уже упакованной модели:

```powershell
.\scripts\build.ps1 -SttModelPath "D:\models\faster-whisper-large-v3-turbo" -VerifyCuda -ConfigPath "$env:LOCALAPPDATA\InterviewAssistant\InterviewAssistant\config.yaml"
```

Результат: `dist\InterviewAssistant\InterviewAssistant.exe`. Обычный пакет включает Python/PyQt6/native runtime/VAD assets и synthetic fixture, но не включает Whisper/LLM weights. Offline-вариант дополнительно включает только проверенный STT bundle. Оба варианта исключают MCP virtual environment и пользовательские secrets.

## Приватность и ограничения безопасности

- Процесс имеет обычное имя `InterviewAssistant.exe`, видим в Task Manager и не маскируется под системный процесс.
- Ribbon использует Windows `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` как best-effort. Это не security boundary и не обещание отсутствия окна в Teams, OBS, записи или снимке экрана.
- `Qt.Tool` влияет на локальное поведение окна, но сам по себе ничего не исключает из screen sharing.
- Приложение не отключает и не подавляет уведомления третьих программ о screenshot/recording. Сам факт захвата может быть обнаружен программой, Teams, ОС или политикой организации.
- DRM/защищённые окна не взламываются. Почти чёрный кадр помечается как protected, удаляется из visual request, а audio/manual text path продолжает работу.
- Event screenshots хранятся во временной директории, максимум три файла, и удаляются при штатном shutdown. `persistent_screenshots: true` не включает постоянное хранение и даёт readiness warning.
- Transcript history находится в памяти процесса и очищается hotkey/при завершении. Это не предотвращает данные в памяти ОС, crash dump, LM Studio logs или Teams recording.
- LM Studio/LM Link передаёт prompt и image на выбранное inference-устройство. MCP передаёт очищенный текущий вопрос внешним Context7/DuckDuckGo; полностью offline privacy при включённом search невозможна.
- Ответ модели может быть неверным или устаревшим. Проверяйте технические и особенно security-critical утверждения.

## Teams: обязательная full-screen приёмка

Capture exclusion зависит от версии Windows, DWM, GPU driver и механизма захвата Teams. Перед демонстрацией нельзя делать заявление о невидимости без отдельного теста:

1. Получите согласие преподавателя/второго участника и при необходимости согласие на запись.
2. Подключите второе устройство как независимого наблюдателя.
3. В Teams поделитесь всем primary display, а не отдельным окном.
4. Откройте, переместите, измените размер и сверните/разверните Ribbon; запустите streaming ответа и event screenshot.
5. Проверьте live view второго участника и запись встречи.
6. Зафиксируйте версии Windows, Teams и GPU driver, а также результат readiness `display_affinity`.
7. Если Ribbon виден хотя бы в одном канале, приёмка провалена: не скрывайте результат и не используйте этот режим как доказательство capture exclusion.

## Финальная аппаратная валидация

Пошаговый протокол находится в [scripts/teams_acceptance.md](scripts/teams_acceptance.md), а заполняемый журнал — в [docs/validation/acceptance-template.md](docs/validation/acceptance-template.md). Отчёт принимает только timing/metadata/booleans: transcript, prompt, answer, audio, pixels и credentials запрещены схемой.

Сначала безопасно проверьте сам recorder на любой машине:

```powershell
.\.venv\Scripts\python scripts\benchmark_session.py --mode self-test --report docs\validation\self-test.jsonl
```

Этот режим может подтвердить только `harness_self_test: PASS`; synthetic data не является аппаратным доказательством, поэтому его итог всегда `overall_acceptance: NOT_RUN`.

После согласованного RTX/Strix Halo/Teams прогона преобразуйте наблюдаемые события:

```powershell
.\.venv\Scripts\python scripts\benchmark_session.py --mode event-input --events-input C:\secure-evidence\session-events.jsonl --report docs\validation\latest.jsonl --session-id rtx-teams-YYYYMMDD-NN
```

`overall_acceptance: PASS` допустим только при полном observed evidence для RU/EN STT, LM Link recovery, event capture, Teams full-screen, protected-content fallback и shutdown. Незапущенная проверка остаётся `NOT_RUN`, а наблюдаемое несоответствие — `FAIL`; self-test не повышает этот статус.

## Разработка

```powershell
.\.venv\Scripts\python -m pytest -q --cov=interview_assistant --cov-report=term-missing
.\.venv\Scripts\python -m ruff check interview_assistant tests scripts main.py
.\.venv\Scripts\python -m mypy interview_assistant main.py
.\.venv\Scripts\python -m compileall -q interview_assistant scripts main.py
```

Дизайн и пошаговый implementation plan находятся в `docs/superpowers/specs/2026-07-12-interview-assistant-design.md` и `docs/superpowers/plans/2026-07-12-interview-assistant-implementation.md`.
