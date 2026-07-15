# Архив: прежний отчёт о проекте заменён

Предыдущий отчёт анализировал legacy `src/`/`utils/` прототип, которого больше нет, и содержал неподтверждённые заявления о скрытии процесса, overlay и screenshot-механизмах. Использовать его для оценки текущего кода нельзя.

Каноническое описание текущего приложения, setup, packaging, privacy model и границы capture exclusion находится в [README.md](README.md). Архитектурные требования доступны в [design spec](docs/superpowers/specs/2026-07-12-interview-assistant-design.md), реализационный статус и критерии — в [implementation plan](docs/superpowers/plans/2026-07-12-interview-assistant-implementation.md).

Аппаратные утверждения считаются подтверждёнными только после выполнения CUDA/LM Link/Teams acceptance на целевых машинах. До этого WDA capture exclusion остаётся best-effort, а отсутствие CUDA на AMD development machine является ожидаемым ограничением среды, а не успешным GPU-тестом.
