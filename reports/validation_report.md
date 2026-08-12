# Validation Report

### Overall Assessment: Ready to share

### Methodology Review
Вопрос — объяснить падение session-level search-to-book conversion после synthetic rollout. Окна имеют одинаковую длину; знаменатель — search-сессии, а не события. Сценарий и данные явно помечены как синтетические. Наблюдательная temporal связь отделена от causal claim.

### Issues Found
1. [Severity: Medium] Before/after не является экспериментом. В отчёте это ограничение видно рядом с локальным выводом; для causal решения предложен randomised holdback.
2. [Severity: Low] Версия приложения появляется в post-period, поэтому version effect не полностью отделим от времени и rollout selection.

### Calculation Spot-Checks
- Pandas vs DuckDB conversion: Verified — max absolute difference 0.0e+00.
- Contribution reconciliation: Verified — residual 1.4e-17.
- Event grain / freshness / joins: Verified — 8 of 8 checks passed.
- Leakage: Verified — session-attribute input has no outcome field; booking is inferred only from raw event rollup.

### Visualization Review
Проверены три PNG: дневной тренд с датой релиза, contribution decomposition и локальный срез с 95% Wilson CI. У осевых сравнительных bar-chart есть нулевая линия/честная шкала, а допущения не спрятаны в заголовках.

### Suggested Improvements
1. В production добавить availability, pricing, latency и crash telemetry, затем провести pre-registered holdback.

### Required Caveats for Stakeholders
- Synthetic scenario иллюстрирует метод, а не состояние реального бизнеса.
- Статистическая разница в сегменте не доказывает эффект релиза без дизайна причинной идентификации.
