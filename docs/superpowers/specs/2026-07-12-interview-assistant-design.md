# Interview Assistant — Design Specification

Дата: 12 июля 2026 г.  
Статус: утверждён пользователем (`LGTM`) и используется как контракт implementation plan.

## 1. Цель

Создать stand-alone Windows-приложение для согласованного использования во время учебных технических интервью в Microsoft Teams. Приложение в реальном времени транскрибирует преподавателя и студента, определяет вопросы, собирает разрешённый визуальный и веб-контекст, получает streaming-ответ от локальной модели LM Studio и показывает его в полупрозрачном overlay.

Вся логика Interview Assistant работает на основном ПК с NVIDIA RTX 5070 Ti. AMD Strix Halo используется исключительно для inference LLM/VLM через LM Link.

## 2. Границы и ограничения

- Целевая ОС: Windows 11 x64.
- Целевая конференц-платформа для приёмки: Microsoft Teams Desktop.
- Целевой режим Teams: демонстрация всего экрана.
- Процесс имеет честное имя `InterviewAssistant.exe` и остаётся видимым в диспетчере задач.
- Overlay скрывается из taskbar и Alt+Tab штатными оконными флагами.
- Overlay получает `WDA_EXCLUDEFROMCAPTURE`; это best-effort исключение из поддерживаемых Windows capture API, а не абсолютная гарантия невидимости.
- Приложение не подавляет уведомления, защитные механизмы или DLP-события сторонних программ.
- Приложение не обходит окна, защищённые от screen capture.
- Приложение не получает shell, filesystem-write, form submission или произвольный browser automation через MCP.
- Интернет-поиск является явно настраиваемым внешним каналом; полный transcript, screenshots и данные резюме поисковому провайдеру не передаются.

## 3. Развёртывание

### Основной ПК: RTX 5070 Ti

На основном ПК работают:

- Microsoft Teams;
- `InterviewAssistant.exe`;
- захват системного аудио и микрофона;
- VAD и streaming STT;
- определение вопросов;
- управление контекстом;
- event-driven screen capture;
- MCP-процессы и web tool routing;
- локальный LM Studio API server на `localhost:1234`;
- Liquid Ribbon overlay;
- настройки, hotkeys, diagnostics и временное хранилище.

### Inference-машина: AMD Strix Halo

На Strix Halo работают:

- LM Studio/llmster;
- выбранные LLM/VLM;
- inference запросов, направленных через LM Link.

Никакой компонент бизнес-логики Interview Assistant на Strix Halo не развёртывается.

### Сетевой путь

```text
InterviewAssistant.exe
        ↓ HTTP localhost
LM Studio API :1234 на основном ПК
        ↓ LM Link, end-to-end encrypted
LM Studio/llmster на Strix Halo
        ↓
LLM/VLM inference
```

Приложение не хранит прямой IP Strix Halo. Выбор Strix Halo выполняется механизмом preferred device LM Link.

## 4. Архитектура приложения

```text
System Audio ─┐
              ├→ Audio Pipeline → Streaming STT → Transcript Store
Microphone ───┘                              ↓
                                      Question Detector
                                             ↓
Event Screenshot ─→ Frame Validator ─→ Context Manager
                                             ↓
Context7 / Web Search ← Tool Router ← Request Orchestrator
                                             ↓
                               LM Studio Model Manager
                                             ↓
                                 Streaming LLM Client
                                             ↓
                                    Qt Signal Bus
                                             ↓
                                  Liquid Ribbon Overlay
```

Каждая подсистема имеет ограниченную очередь и отменяет устаревшую работу. GUI изменяется только в главном Qt-потоке через signals/slots.

## 5. Аудио и streaming STT

### Источники

- `system`: речь преподавателя из Teams/system output;
- `microphone`: речь студента.

Источники не смешиваются. Каждый имеет собственные ring buffer, VAD state, timestamp и transcript channel.

### Роли

- system audio записывается как `Interviewer` и может запускать автоматический ответ;
- microphone записывается как `You` и используется только для контекста;
- microphone не запускает автоматическую генерацию.

### Модель и backend

- backend: faster-whisper/CTranslate2;
- production device: CUDA на RTX 5070 Ti;
- модель по умолчанию: multilingual `large-v3-turbo`;
- development fallback: CPU с уменьшенной STT-моделью или replay записанного PCM без изменения бизнес-логики.

`distil-large-v3` не используется по умолчанию, поскольку он ориентирован на английское распознавание.

### Streaming-алгоритм

- вход downmix/resample в mono PCM 16 kHz;
- VAD определяет voiced segments;
- partial inference запускается каждые 400–700 мс;
- rolling window использует overlap 600–1000 мс;
- stable-prefix публикует только подтверждённую часть последовательных гипотез;
- финальная реплика создаётся после 500–700 мс тишины;
- partial decoding использует greedy/`beam_size=1`;
- final decoding начинает с `beam_size=3`, если измеренная задержка соответствует бюджету;
- system queue имеет приоритет над microphone queue;
- `condition_on_previous_text=False` используется для partial-проходов, чтобы уменьшить повторяющиеся циклы;
- финал получает только ограниченный подтверждённый предыдущий контекст.

### Языки

Настройки: `Auto`, `Русский`, `English`.

В `Auto`:

- язык определяется после 1,5–2 секунд voiced audio;
- язык фиксируется до конца реплики;
- переключение требует настроенного confidence threshold;
- при низкой уверенности сохраняется предыдущий язык данного источника;
- технические английские термины внутри русской реплики не переключают язык всей реплики;
- multilingual decoding допускает code-switching.

### Целевые показатели

- первый partial: не позднее 1 секунды после начала речи;
- обновление partial: каждые 400–700 мс;
- final transcript: не позднее 800 мс после паузы;
- отсутствие смешивания system и microphone transcripts;
- корректное переключение RU/EN между репликами.

## 6. Определение вопросов

Question Detector не ограничивается текущими regex. Он объединяет:

- punctuation/interrogative признаки;
- RU/EN question patterns;
- code/system-design/behavioral keywords;
- завершение реплики по VAD;
- cooldown и semantic deduplication;
- ручной force-request hotkey.

Типы запроса:

- `theory`;
- `coding`;
- `system_design`;
- `behavioral`;
- `screen_analysis`;
- `manual`.

Автоматический запрос запускается только по final system-audio utterance. Новая более свежая реплика может отменить ещё не начавшуюся устаревшую генерацию.

## 7. Контекст

### Session Context

Нормальный запрос может содержать:

- короткий system prompt;
- тип задачи;
- текущий final-вопрос;
- ограниченное окно подтверждённых реплик `Interviewer` и `You`;
- краткое резюме/вакансию/выбранный стек;
- один релевантный screenshot;
- результаты разрешённого MCP search;
- предыдущий ответ только если он нужен для продолжения.

Контекст имеет жёсткий token budget и очищается по времени и релевантности.

### Recovery Context

После reload модели отправляется минимальный самодостаточный пакет:

1. сокращённый system prompt;
2. тип задачи;
3. последний актуальный вопрос преподавателя;
4. последнее релевантное уточнение студента;
5. один относящийся к вопросу screenshot.

Старые ответы и нерелевантная история не отправляются. Целевой размер Recovery Context: 1–2 тысячи входных токенов до учёта изображения.

## 8. LM Studio API и выбор моделей

### API

Основной inference endpoint: `POST /api/v1/chat`, поскольку он поддерживает streaming, изображения и MCP integrations.

Используются также:

- `GET /v1/models` для OpenAI-compatible discovery;
- `GET /api/v1/models` для расширенных metadata и loaded instances;
- `POST /api/v1/models/load` для явной загрузки/recovery;
- `POST /api/v1/models/unload` только при явном пользовательском действии или корректном завершении session-owned instance.

### Выбор моделей

Настройки содержат два логических selector-а:

- text model;
- vision model.

Один model key разрешено выбирать в обеих ролях. Selector не создаёт instance.

### Model Registry

`ModelRegistry` индексируется стабильным model key. Он:

- строит множество уникальных выбранных ключей;
- запрещает параллельный duplicate load через single-flight lock;
- хранит состояния `unloaded`, `loading`, `ready`, `recovering`, `failed`;
- связывает обе логические роли с одним instance при одинаковом key;
- не выгружает instance, пока хотя бы одна роль на него ссылается;
- не выполняет автоматический fallback на RTX.

### Session start

При начале сессии приложение:

1. обновляет model list;
2. проверяет preferred LM Link device;
3. проверяет каждый уникальный model key;
4. загружает отсутствующие модели на preferred Strix Halo;
5. сохраняет `instance_id`;
6. выполняет короткий warm-up;
7. переводит readiness в `ready`.

### Recovery

Если модель исчезла или запрос сообщает unloaded instance:

1. незавершённый запрос отменяется;
2. очередь схлопывается до последнего актуального вопроса;
3. model key блокируется single-flight lock;
4. выполняется один load request;
5. после `ready` выполняется warm-up;
6. последний вопрос повторяется с Recovery Context;
7. применяется максимум три попытки с backoff;
8. после трёх ошибок состояние становится `LLM offline` до ручного retry.

## 9. MCP и актуальная информация

### API integration

LM Studio основного ПК оркестрирует MCP и удалённый inference. Strix Halo выполняет только model inference.

### Context7

- transport: remote HTTPS MCP;
- endpoint: `https://mcp.context7.com/mcp`;
- назначение: version-specific library documentation и code examples;
- разрешаются только инструменты resolution/search документации;
- API key, если используется, хранится вне репозитория.

### Web search

Первичная реализация:

- локальный `nickclyde/duckduckgo-mcp-server`;
- фиксированная версия/commit;
- отдельное virtual environment;
- lock-файл и hashes зависимостей;
- запуск по абсолютному пути;
- только инструменты search и fetch content.

Архитектурная замена:

- self-hosted SearXNG через общий `SearchProvider` contract;
- смена provider не меняет Request Orchestrator или overlay.

### Search modes

- `Off`: веб-инструменты не передаются модели;
- `Auto`: локальный classifier разрешает search только для актуальных фактов, версий, новостей и документации;
- `Forced`: один search для текущего вопроса запускается hotkey.

### Search policy

- для library/API вопросов сначала используется Context7;
- для общих актуальных фактов используется DuckDuckGo;
- максимум один search и один fetch на вопрос;
- общий timeout 5 секунд;
- максимум три search results;
- fetch ограничен одним наиболее релевантным документом;
- допустимы только HTTP/HTTPS public addresses;
- localhost, link-local, LAN и cloud metadata addresses запрещены;
- размер ответа ограничен 1 МБ;
- binary downloads запрещены;
- полный transcript, screenshot и персональные данные в search query не включаются;
- tool outputs считаются недоверенными данными;
- инструкции со страниц не исполняются;
- при timeout/rate limit модель отвечает без веба и overlay показывает ограничение;
- overlay показывает использованные URL и дату поиска.

### LM Studio security

- локальный API требует token authentication;
- token хранится в Windows Credential Manager;
- `Allow calling servers from mcp.json` включается только для известных MCP;
- per-request `allowed_tools` скрывает все лишние инструменты;
- shell, filesystem, browser automation, forms и write-tools отсутствуют.

## 10. Screen capture

### Режим

Используется event-driven capture:

- один автоматический кадр при `coding`, `system_design` или `screen_analysis`;
- ручной кадр по hotkey;
- повторный кадр только при существенном perceptual изменении;
- постоянная съёмка по таймеру отсутствует.

### Capture behavior

- capture выполняется через Windows capture API без эмуляции клавиши Print Screen;
- overlay имеет `WDA_EXCLUDEFROMCAPTURE` и не должен попадать в поддерживаемые capture paths;
- доступ к capture backend сериализуется;
- одинаковые кадры отбрасываются по perceptual hash;
- filename использует collision-safe identifier;
- временные файлы удаляются при eviction и завершении;
- screenshot не сохраняется постоянно без явного opt-in.

### Protected content

Frame Validator анализирует:

- среднюю яркость;
- variance;
- entropy;
- размер contiguous near-black region;
- изменение относительно предыдущего доступного кадра.

Если кадр считается защищённым/пустым:

- он не отправляется VLM;
- overlay показывает `Protected content`;
- запрос продолжает использовать audio transcript;
- пользователь может явно передать clipboard text или ручной input;
- обход screen-capture protection не выполняется.

### Сторонние уведомления

Приложение не эмулирует системный screenshot hotkey и не создаёт собственное уведомление Print Screen. Оно не гарантирует отсутствие и не подавляет защитные уведомления сторонних программ.

## 11. Liquid Ribbon UI

Выбран визуальный вариант B: верхняя полупрозрачная liquid-glass лента.

### Свойства

- always-on-top;
- matte/liquid-glass appearance;
- регулируемая прозрачность;
- drag и resize;
- автоматическая высота до заданного максимума;
- ручная прокрутка длинного ответа;
- сворачивание в тонкий status bar;
- горячая клавиша видимости;
- streaming rendering без полного repaint текста;
- отдельное обычное окно Settings.

### Состояния

- `Starting`;
- `Ready`;
- `Listening`;
- `Transcribing`;
- `Thinking`;
- `Searching`;
- `Generating`;
- `Recovering`;
- `Protected content`;
- `LLM offline`;
- `Paused`.

Overlay показывает модель, STT/Web status, TTFT/tokens-per-second в диагностическом режиме и источники веб-ответа.

### Windows visibility

- `QApplication` создаётся до любого QWidget;
- Qt `Tool`/frameless flags скрывают окно из taskbar/Alt+Tab;
- top-level HWND получает `WDA_EXCLUDEFROMCAPTURE`;
- результат `SetWindowDisplayAffinity` и `GetWindowDisplayAffinity` проверяется;
- ошибку affinity нельзя скрывать: readiness становится warning/failed;
- процесс остаётся видимым в Task Manager.

## 12. Concurrency и состояние

Высокоуровневая state machine:

```text
STARTING → READY → LISTENING → TRANSCRIBING
                      ↓
                  GENERATING
                  ↙    ↓     ↘
            SEARCHING READY RECOVERING
                              ↓
                         READY / OFFLINE
```

Правила:

- Qt widgets изменяются только главным потоком;
- workers публикуют typed events;
- очереди bounded;
- каждый вопрос имеет monotonic request id;
- token/event с устаревшим request id игнорируется;
- новый forced request может отменить старую generation;
- capture и model load используют single-flight locks;
- shutdown идемпотентен и очищает частично запущенные компоненты;
- временные файлы и MCP subprocess завершаются гарантированно.

## 13. Настройки

Настраиваются:

- system audio device;
- microphone device;
- STT model/device/compute type;
- язык `Auto/RU/EN`;
- LM Studio host/port/API token reference;
- text model;
- vision model;
- search mode;
- Context7 availability;
- DuckDuckGo/SearXNG provider;
- screenshot hotkey;
- force request hotkey;
- pause hotkey;
- overlay visibility hotkey;
- overlay transparency, width, height and position;
- diagnostics mode;
- persistent screenshot opt-in.

Конфигурация валидируется typed schema. Secrets не сохраняются в YAML.

## 14. Readiness check

До начала интервью проверяются:

1. Windows/DWM compatibility;
2. system audio и microphone;
3. CUDA и STT model load;
4. RU/EN test transcription fixtures;
5. LM Studio API authentication;
6. LM Link status;
7. preferred Strix Halo device;
8. text/vision model discovery;
9. отсутствие duplicate instances для одинакового key;
10. model load/warm-up;
11. Context7 connectivity;
12. DuckDuckGo MCP connectivity;
13. hotkeys;
14. display affinity;
15. event screenshot и overlay exclusion;
16. streaming TTFT.

Readiness имеет итог `ready`, `warning` или `failed`. Пользователь видит конкретный failed check.

## 15. Тестирование

### Unit

- config validation;
- model-key deduplication;
- lifecycle transitions;
- recovery queue collapse;
- context token budgeting;
- transcript source separation;
- stable-prefix reconciliation;
- language hysteresis;
- detector RU/EN cases;
- search policy and URL restrictions;
- perceptual screenshot deduplication;
- protected-frame detection;
- idempotent shutdown.

### Integration

- fake HTTP LM Studio contract server для детерминированных API tests;
- local real LM Studio smoke tests;
- LM Link model discovery/load/recovery;
- Context7 and DuckDuckGo tool calls;
- dual audio replay;
- cancellation of stale streaming request;
- one model selected twice produces one load call/instance;
- temporary files are deleted.

### Hardware/system

- RTX 5070 Ti CUDA STT benchmark;
- Strix Halo remote inference benchmark;
- model unload/reload during active session;
- Teams full-screen share observed from a second participant;
- Teams recording review;
- multiple monitors and display scaling;
- screen lock, sleep and reconnect;
- protected-content black frame;
- MCP timeout/rate limit;
- LM Link disconnect/reconnect.

## 16. Acceptance criteria

- Основной ПК выполняет всю application logic; Strix Halo выполняет только model inference.
- System и microphone transcripts не смешиваются.
- Русский и английский распознаются в одной сессии.
- Первый partial STT появляется не позднее 1 секунды на целевом ПК в benchmark profile.
- Одинаковая text/vision модель создаёт не более одного loaded instance.
- Выгруженная модель автоматически восстанавливается на preferred Strix Halo максимум за три попытки.
- После recovery повторяется только последний актуальный вопрос с Recovery Context.
- LLM tokens отображаются по мере получения.
- MCP web search не получает полный transcript/screenshots и не имеет write/browser/shell capabilities.
- Event screenshot не содержит overlay в проверенном Windows capture backend.
- Защищённый чёрный кадр обнаруживается и не отправляется VLM.
- Overlay отсутствует в проверяемой Teams full-screen трансляции на тестовой конфигурации либо readiness явно сообщает несовместимость.
- Overlay отсутствует в taskbar/Alt+Tab, но процесс виден как `InterviewAssistant.exe`.
- Shutdown освобождает audio, CUDA worker, LM client, capture, MCP subprocess и временные файлы.

## 17. Декомпозиция реализации

Спецификация охватывает несколько технически независимых подсистем. Реализация должна выполняться последовательными вертикальными этапами, каждый из которых заканчивается работающим и проверяемым состоянием:

1. **Core foundation:** единая точка входа, typed config, state machine, Qt signal bus, идемпотентный lifecycle и test harness.
2. **Dual-source STT:** два аудиоисточника, VAD, multilingual streaming, stable-prefix и transcript store.
3. **LM Studio/LM Link:** model discovery, два selector-а, instance deduplication, streaming client, load/recovery и token-budgeted context.
4. **Liquid Ribbon и capture:** новый overlay, Windows affinity, event-driven screenshot, frame validation и hotkeys.
5. **MCP retrieval:** переход на `/api/v1/chat`, Context7, локальный DuckDuckGo MCP, search policy и source rendering.
6. **System integration:** readiness, packaging, hardware benchmarks и Teams full-screen acceptance.

Каждый этап получает отдельный набор задач и review checkpoint в implementation plan. Этап не считается завершённым, пока его unit/integration tests не проходят и не сохранены измеримые результаты применимых latency checks.

## 18. Документация и источники API

- [Microsoft SetWindowDisplayAffinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)
- [LM Studio LM Link](https://lmstudio.ai/docs/developer/core/lmlink)
- [LM Studio model list](https://lmstudio.ai/docs/developer/openai-compat/models)
- [LM Studio native REST API](https://lmstudio.ai/docs/developer/rest)
- [LM Studio model load](https://lmstudio.ai/docs/developer/rest/load)
- [LM Studio MCP via API](https://lmstudio.ai/docs/developer/core/mcp)
- [Context7 remote MCP](https://context7.com/docs/resources/all-clients)
- [DuckDuckGo MCP](https://github.com/nickclyde/duckduckgo-mcp-server)
- [SearXNG](https://github.com/searxng/searxng)
- [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
