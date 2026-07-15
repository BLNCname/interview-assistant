# Архив: прежний документ оптимизаций удалён

Старый `OPTIMIZATIONS.md` описывал удалённый pre-alpha код, неподтверждённые latency-цифры и небезопасные обещания о screen capture. Эти сведения больше не соответствуют проекту и намеренно не сохранены как руководство.

Актуальные требования, архитектура, воспроизводимая сборка, CUDA smoke и честные ограничения описаны в [README.md](README.md). Детальные решения находятся в [design spec](docs/superpowers/specs/2026-07-12-interview-assistant-design.md), а последовательность реализации — в [implementation plan](docs/superpowers/plans/2026-07-12-interview-assistant-implementation.md).

Любые performance-утверждения должны опираться на датированный benchmark с указанием Windows, GPU, driver, CTranslate2/LM Studio/model version и p50/p95. Результаты AMD development machine нельзя выдавать за CUDA-результаты RTX 5070 Ti.
