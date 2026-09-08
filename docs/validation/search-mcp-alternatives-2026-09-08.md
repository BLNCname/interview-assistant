# Бесплатные поисковые MCP: сравнение на 8 сентября 2026

Для настройки через `.env` первым кандидатом является официальный Tavily MCP. Для работы без регистрации подходит community-проект `nickclyde/duckduckgo-mcp-server`: он проходит критерии популярности и возраста. SearXNG подходит при готовности обслуживать собственный сервер.

Это исследование документации, исходников, метаданных GitHub и отзывов. Новые поисковые серверы не устанавливались, аккаунты не создавались, сравнительные замеры скорости и качества не проводились. Текущий провайдер приложения остаётся Exa. Рекомендация относится к условиям использования и сложности интеграции; она не доказывает превосходство качества поисковой выдачи.

## Метод отбора

Пользователь разрешил community/open-source решения с большим количеством звёзд и форков и возрастом больше полугода. Для воспроизводимого предварительного отбора приняты **не менее 1 000 звёзд, 100 форков, создание ранее 08.03.2026**. Численные пороги выбраны для этого исследования; пользователь не задавал конкретные значения.

Звёзды и форки характеризуют распространённость, но не подтверждают безопасность и работоспособность. Дополнительно проверены текущая поддержка, происхождение пакета, бесплатная квота, зависимости и способ запуска. Статистика MCP-обёртки отделена от статистики базового движка.

## Репозитории

Числа получены через GitHub REST API 08.09.2026. Дата создания относится к репозиторию, а не обязательно к появлению MCP-функции. Метаданные каждого проекта доступны по ссылке «API».

| Репозиторий | Звёзды | Форки | Создан | Результат отбора |
|---|---:|---:|---|---|
| [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server) · [API](https://api.github.com/repos/nickclyde/duckduckgo-mcp-server) | 1 463 | 189 | 22.02.2025 | Проходит |
| [tavily-ai/tavily-mcp](https://github.com/tavily-ai/tavily-mcp) · [API](https://api.github.com/repos/tavily-ai/tavily-mcp) | 2 374 | 292 | 27.01.2025 | Проходит |
| [firecrawl/firecrawl-mcp-server](https://github.com/firecrawl/firecrawl-mcp-server) · [API](https://api.github.com/repos/firecrawl/firecrawl-mcp-server) | 7 413 | 876 | 06.12.2024 | Проходит |
| [ihor-sokoliuk/mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) · [API](https://api.github.com/repos/ihor-sokoliuk/mcp-searxng) | 1 207 | 157 | 23.12.2024 | Проходит |
| [mrkrsl/web-search-mcp](https://github.com/mrkrsl/web-search-mcp) · [API](https://api.github.com/repos/mrkrsl/web-search-mcp) | 1 137 | 163 | 27.06.2025 | Проходит; последний push 08.08.2025 |
| [searxng/searxng](https://github.com/searxng/searxng) · [API](https://api.github.com/repos/searxng/searxng) | 36 637 | 3 343 | 12.04.2021 | Проходит; это базовый движок |
| [deedy5/ddgs](https://github.com/deedy5/ddgs) · [API](https://api.github.com/repos/deedy5/ddgs) | 2 940 | 282 | 25.04.2021 | Проходит; MCP-часть моложе самого репозитория |
| [pskill9/web-search](https://github.com/pskill9/web-search) · [API](https://api.github.com/repos/pskill9/web-search) | 468 | 77 | 30.12.2024 | Не проходит пороги популярности |
| [jae-jae/g-search-mcp](https://github.com/jae-jae/g-search-mcp) · [API](https://api.github.com/repos/jae-jae/g-search-mcp) | 273 | 37 | 26.03.2025 | Не проходит пороги популярности |
| [engram-ae/noapi-google-search-mcp](https://github.com/engram-ae/noapi-google-search-mcp) · [API](https://api.github.com/repos/engram-ae/noapi-google-search-mcp) | 125 | 35 | 10.02.2026 | Не проходит пороги популярности |
| [Kindly](https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server) · [API](https://api.github.com/repos/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server) | 384 | 30 | 02.01.2026 | Не проходит пороги популярности |
| [parallel-web/search-mcp](https://github.com/parallel-web/search-mcp) · [API](https://api.github.com/repos/parallel-web/search-mcp) | 12 | 1 | 03.09.2026 | Этот репозиторий не проходит; содержит документацию/конфигурацию сервиса |

## Tavily: официальный сервис с опубликованной квотой

Бесплатный Researcher предоставляет **1 000 кредитов каждый месяц**, регистрация и API-ключ нужны, карта не нужна. Квота обновляется первого числа. На бесплатном тарифе после её исчерпания запросы останавливаются; платное продолжение требует выбора соответствующего тарифа. [Тарифы и FAQ](https://www.tavily.com/pricing).

Обычный поиск `basic` расходует один кредит, `advanced` — два. Поэтому бюджет соответствует 1 000 обычных либо 500 расширенных поисков, если не тратить его на другие операции. [Учёт кредитов](https://docs.tavily.com/documentation/api-credits). Development key имеет ограничение 100 запросов в минуту. [Лимиты](https://docs.tavily.com/documentation/rate-limits).

Официальный удалённый сервер `https://mcp.tavily.com/mcp` принимает Bearer API-ключ. Это позволяет подключение непосредственно из Python-клиента приложения без установки Node/Docker у преподавателя. [MCP README](https://github.com/tavily-ai/tavily-mcp). При интеграции следует явно закрепить `search_depth=basic`, проверить фактический расход и не включать автоматический выбор расширенного поиска. [Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search).

Есть студенческая программа: **4 000 кредитов в месяц на четыре месяца** после подтверждения статуса. Это временная льгота, а не постоянная стандартная квота. [Условия для студентов](https://help.tavily.com/articles/6606514713-student-account).

## DuckDuckGo: подходящий готовый community MCP

Кандидат на возврат — NickClyde, выпустивший v0.7.0 04.09.2026. Поиск обращается к HTML-выдаче DuckDuckGo без ключа и регистрации. У MCP нет месячной платной квоты; ограничение 30 поисков/минуту установлено самим сервером и не является гарантией от DuckDuckGo. README описывает возможные HTTP 202/403 и опциональный backend `curl_cffi`. По умолчанию используется stdio. [Документация проекта](https://github.com/nickclyde/duckduckgo-mcp-server).

Это продукт сообщества, не официальный сервер компании DuckDuckGo. NickClyde не использует прежние `ddgs` и `primp`: текущий пакет зависит от BeautifulSoup, HTTPX/HTTPCore и MCP SDK; `curl_cffi` подключается дополнительно. [Метаданные пакета](https://pypi.org/pypi/duckduckgo-mcp-server/0.7.0/json).

Происхождение wheel подтверждено PyPI Trusted Publishing из GitHub-репозитория NickClyde, workflow `python-publish.yml`. SHA256 wheel: `2f676339f888971f33c6ebcc9952a307de066dc57fd8a6df23c7f3eb1ce74ba7`. [Аттестация публикации](https://pypi.org/integrity/duckduckgo-mcp-server/0.7.0/duckduckgo_mcp_server-0.7.0-py3-none-any.whl/provenance). Это проверка происхождения, не полный аудит безопасности.

Версия 0.7.0 требует `mcp>=2.1.1,<3`, тогда как приложение использует SDK 1.30.0. Поэтому внедрение требует отдельного окружения/процесса с закреплёнными зависимостями либо проверенной миграции SDK. Нельзя просто добавить пакет в текущую сборку и считать совместимость подтверждённой. [Описание релиза](https://github.com/nickclyde/duckduckgo-mcp-server/releases/tag/v0.7.0).

DDGS также проходит численные критерии и теперь имеет собственную MCP-функцию, но популярность всего многолетнего репозитория не доказывает зрелость именно этой функции. Он сохраняет зависимости `primp`/`lxml`, а автоматический выбор backend может обращаться к нескольким поисковикам. [DDGS](https://github.com/deedy5/ddgs).

## SearXNG: собственный сервер

Проходят критерии и базовый SearXNG, и community-обёртка `ihor-sokoliuk/mcp-searxng`. Обёртке нужны Node.js 22+ и адрес `SEARXNG_URL`; stdio поддерживается. [MCP README](https://github.com/ihor-sokoliuk/mcp-searxng).

У самостоятельно размещённого ПО нет коммерческой месячной квоты. Остаются затраты ресурсов сервера и ограничения внешних поисковых систем. Публичные экземпляры могут отключать JSON API, возвращая 403. [Search API](https://docs.searxng.org/dev/search_api.html). Официальный способ размещения включает контейнер; для требования «установил приложение и заполнил .env» это дополнительная инфраструктура. [Установка](https://docs.searxng.org/admin/installation-docker.html).

## Firecrawl: второй официальный вариант

Текущий Free Plan даёт **1 000 кредитов ежемесячно без карты**, что соответствует 500 обычным поискам до 10 результатов. Лимит поиска — 10 запросов в минуту. Загрузка страниц и дополнительные операции расходуют тот же бюджет. Старые статьи про 500 разовых кредитов не отражают нынешние условия. [Тарифы](https://www.firecrawl.dev/pricing).

Официальный MCP доступен удалённо. Есть и доступ без ключа, но численные суточные квоты этого режима не опубликованы: ограничения применяются к запросам и кредитам на IP. Поэтому анонимный Firecrawl нельзя объявить более щедрым, чем Exa. [MCP](https://github.com/firecrawl/firecrawl-mcp-server), [лимиты без ключа](https://docs.firecrawl.dev/rate-limits#keyless-no-api-key).

## Остальные варианты

`mrkrsl/web-search-mcp` проходит численные пороги, но последний push датирован августом 2025 года. Он использует браузеры Chromium/Firefox и цепочку Bing → Brave → DuckDuckGo. Для установщика и параллельной работы с STT/LLM это дополнительная зависимость и расход ресурсов, которые пока не измерялись. [README](https://github.com/mrkrsl/web-search-mcp).

Официальный **Ollama Web Search** доступен с бесплатным аккаунтом и API-ключом, имеет опубликованный Python MCP-пример. Точная бесплатная квота в проверенных страницах не указана. [Анонс](https://ollama.com/blog/web-search), [документация](https://docs.ollama.com/capabilities/web-search).

Официальный **Parallel Search MCP** тоже доступен без ключа с ограничениями. Проверенная документация не даёт численной анонимной квоты; новый GitHub-репозиторий содержит конфигурацию сервиса, а не собственный поисковый движок. [Документация](https://docs.parallel.ai/integrations/mcp/search-mcp). Старый возраст компании или основного продукта нельзя засчитать новому репозиторию.

## Что говорят пользователи

Отзывы используются как сообщения об опыте конкретных людей, а не как сравнительный тест или обещание доступности.

- [Reddit, 17.10.2025: используемые MCP](https://www.reddit.com/r/mcp/comments/1o8td2b/which_mcps_are_you_using_and_why/) — DuckDuckGo рекомендуют как бесплатный повседневный инструмент.
- [Reddit, 09.06.2026: TinySearch](https://www.reddit.com/r/LocalLLaMA/comments/1u106rc/still_a_very_lightweight_open_websearch_tool_for/) — автор перешёл с DDG на SearXNG из-за CAPTCHA; другой участник сообщает об успешной работе DDG. Приведённые 10–15 секунд относятся ко всему TinySearch с обработкой страниц, не к одному поиску.
- [Reddit, начало сентября 2026: альтернативы SearXNG](https://www.reddit.com/r/LocalLLM/comments/1w1fxzv/search_alternatives_to_searxng_without_api_keys/) — есть жалобы на блокировки при умеренной нагрузке и противоположный опыт успешного прямого DDGS.
- [Hacker News: SearXNG](https://news.ycombinator.com/item?id=48779454) — обсуждаются многолетняя успешная эксплуатация, нестабильность отдельных движков и ограничения публичных экземпляров.
- [Reddit, май 2026: поиск для локальных моделей](https://www.reddit.com/r/LocalLLM/comments/1tj39rv/web_search_for_local_models/) — пользователи описывают SearXNG с LM Studio и Ollama Web Search.
- [Обзор ChatForest](https://chatforest.com/reviews/duckduckgo-mcp-server/) прочитан, но не использован как доказательство качества: автор указывает, что материал создан ИИ без запуска сервера; сведения о зависимостях не совпадают с текущим upstream.

## Решение для проекта

**Основной кандидат для простой настройки — Tavily.** Причины: официальный удалённый MCP, опубликованная возобновляемая квота, отсутствие карты, настройка через ключ. Это не утверждение, что он быстрее или точнее конкурентов.

**Кандидат без регистрации — NickClyde DuckDuckGo MCP.** Условие популярности и возраста выполнено; для назначения основным провайдером нужна проверка актуального релиза на этой сети и в готовой сборке. Прежняя собственная MCP-обёртка приложения такой внешней истории не имеет.

**SearXNG — выбор для самостоятельного размещения**, если университету нужна управляемая инфраструктура. Для единичной проверки приложения это самый сложный из трёх вариантов по установке.

Перед сменой провайдера сравнить одинаковые RU/EN-запросы: полноту результата, ссылки на первоисточники, время до ответа, ошибки и восстановление после них. Нагрузку ограничить реальным сценарием интервью; не пытаться измерять квоты исчерпанием или обходить ограничения сервиса.
