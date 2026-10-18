# Проверка результатов

### Результаты автоматических проверок

### Методика
Вопрос — объяснить падение session-level search-to-book conversion после synthetic rollout. Окна имеют одинаковую длину; знаменатель — search-сессии, а не события. Сценарий и данные явно помечены как синтетические. Наблюдательная temporal связь отделена от causal claim.

### Ограничения дизайна
1. Before/after не является экспериментом. В отчёте это ограничение видно рядом с локальным выводом; для causal решения предложен randomised holdback.
2. Версия приложения появляется в post-period, поэтому version effect не полностью отделим от времени и rollout selection.

### Контрольные пересчёты
- Pandas vs DuckDB conversion: Verified — max absolute difference 0.0e+00.
- Contribution reconciliation: Verified — residual 1.4e-17.
- Headline fixture: Verified — global delta -3.49 pp; mix effect -3.59 pp.
- Event grain / freshness / joins: Verified — 8 of 8 checks passed.
- Leakage: Verified — session-attribute input has no outcome field; booking is inferred only from raw event rollup.

### Файлы графиков
Проверено наличие трёх непустых SVG-файлов: дневного тренда, разложения изменения конверсии и локального среза с 95% Wilson CI. Автоматическая проверка файлов не подтверждает читаемость графиков или корректность их визуального оформления.

### Что проверить дальше
1. В production добавить availability, pricing, latency и crash telemetry, затем провести pre-registered holdback.

### Ограничения интерпретации
- Synthetic scenario иллюстрирует метод, а не состояние реального бизнеса.
- Статистическая разница в сегменте не доказывает эффект релиза без дизайна причинной идентификации.
