#!/usr/bin/env python3
"""Render the reader-facing Russian RCA report from computed pipeline outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def pp(value: float, digits: int = 2) -> str:
    return f"{value * 100:+.{digits}f} п.п."


def p_value(value: float) -> str:
    return "< 0.001" if value < 0.001 else f"= {value:.3f}"


def render(metrics: dict, quality: dict) -> str:
    before = metrics["period_totals"]["before"]
    after = metrics["period_totals"]["after"]
    decomposition = metrics["decomposition"]
    local = metrics["local_release_slice"]
    top_mix_rows = "\n".join(
        "| {segment} | {mix} | {rate} | {before_share} → {after_share} |".format(
            segment=row["segment"],
            mix=pp(row["mix_effect"]),
            rate=pp(row["rate_effect"]),
            before_share=pct(row["before_share"]),
            after_share=pct(row["after_share"]),
        )
        for row in metrics["top_mix_contributors"]
    )
    local_quality_rows = "\n".join(
        "| {quality} | {before} | {after} | {difference} | {ci_low}; {ci_high} |".format(
            quality=row["session_quality"],
            before=pct(row["before_rate"]),
            after=pct(row["after_rate"]),
            difference=pp(row["difference"]),
            ci_low=pp(row["ci_low"]),
            ci_high=pp(row["ci_high"]),
        )
        for row in local["by_quality"]
    )
    check_rows = "\n".join(
        f"| {name} | {'пройдено' if payload['passed'] else 'не пройдено'} | {payload['rule']} |"
        for name, payload in quality["checks"].items()
    )
    expected_lift = 0.02
    baseline = local["after_rate"]
    z_alpha = 1.96
    z_beta = 0.84
    pooled = baseline + expected_lift / 2
    sample_per_arm = (
        (
            z_alpha * (2 * pooled * (1 - pooled)) ** 0.5
            + z_beta * (baseline * (1 - baseline) + (baseline + expected_lift) * (1 - baseline - expected_lift)) ** 0.5
        )
        ** 2
        / expected_lift**2
    )
    return f"""# Расследование падения search-to-book conversion

## Executive Summary

- **Верхнеуровневая метрика действительно снизилась:** с {pct(before['conversion'])} ({before['bookings']:,} бронирований / {before['search_sessions']:,} search-сессий) до {pct(after['conversion'])} ({after['bookings']:,} / {after['search_sessions']:,}), то есть на {pp(metrics['global_delta'])}.
- **Большая часть видимого падения — изменение состава трафика:** вклад mix равен {pp(decomposition['mix_effect'])}, или {pct(decomposition['mix_share_of_absolute_delta'])} абсолютного topline-изменения. После релиза резко выросла доля low-quality paid social; агрегат нельзя приписывать релизу без стандартизации.
- **Есть локальный тревожный сигнал:** в заранее выделенном срезе Android × Moscow × paid_search показатель для версии 5.14.0 составил {pct(local['after_rate'])} против {pct(local['before_rate'])} до релиза ({pp(local['difference'])}, 95% CI {pp(local['ci_low'])}…{pp(local['ci_high'])}; two-proportion test p {p_value(local['p_value'])}). Это приоритетная гипотеза инцидента, а не доказанный causal effect.
- **Данные пригодны для учебного RCA:** {len(quality['checks'])} проверок качества пройдено; SQL и pandas независимо воспроизводят conversion с максимальным расхождением {metrics['validation']['pandas_sql_conversion_max_abs_error']:.1e}.

> **Статус источника.** Все данные в кейсе синтетические, детерминированно сгенерированы из кода. Они не являются данными Делимобиля или любой другой компании.

## Что измеряем и почему это важно

**Search-to-book conversion** = число search-сессий с хотя бы одним `booking_created` / число search-сессий с `search_results_viewed`. Гранулярность — сессия, а не событие: это защищает знаменатель от повторных просмотров выдачи. Окна равной длины: `{metrics['window']['before']}` и `{metrics['window']['after']}`. Временная зона — {metrics['window']['timezone']}.

```mermaid
flowchart TD
    A["Search-to-book conversion"] --> B["Bookings — numerator"]
    A --> C["Search sessions — denominator"]
    B --> D["Availability / price / payment"]
    B --> E["Search result relevance / UX"]
    C --> F["Channel mix"]
    C --> G["Platform / city / app version"]
    C --> H["Session quality"]
    E --> I["Android 5.14.0 · Moscow · paid_search"]
```

## Сначала исключили проблему измерения

Проверка началась не с гипотезы о релизе: валидированы уникальность `event_id` и `session_id`, порядок search → booking, полнота дневных партиций, покрытие JOIN, отсутствие null в ключевых измерениях и freshness загрузки. Последняя загрузка отстаёт от synthetic as-of timestamp на {quality['checks']['freshness']['value']:.1f} часа.

| Проверка | Статус | Правило |
|---|---|---|
{check_rows}

Это снижает риск, что падение создано дублями, сдвигом event grain, потерей JOIN или неполной последней датой. Оно не заменяет проверку production-инструментации в реальном продукте.

## Временная привязка: падение начинается около rollout

![Дневная конверсия](figures/daily_conversion.png)

График показывает ежедневную session-weighted конверсию и дату начала rollout 20 июля. Временная близость делает релиз разумной гипотезой для проверки, но сама по себе **не доказывает причинность**: одновременно мог измениться канал, состав аудитории, спрос или доступность предложения.

## Mix shift объясняет большую часть topline-падения

Для взаимоисключающих ячеек `acquisition_channel × session_quality` выполнена decomposition:

`ΔCR = Σ(w_after − w_before) × CR_before + Σw_after × (CR_after − CR_before)`.

Первое слагаемое — composition/mix effect, второе — within-cell rate effect. Эффекты суммируются до общего изменения с ошибкой {metrics['decomposition']['reconciliation_error']:.1e}.

![Декомпозиция вклада](figures/mix_decomposition.png)

| Сегмент | Mix effect | Within-cell effect | Доля сессий: before → after |
|---|---:|---:|---:|
{top_mix_rows}

**Интерпретация.** Главный отрицательный вклад создаёт уход из высококонверсионных organic и paid-search ячеек. Paid social × low действительно вырос, но его небольшой положительный механический вклад не компенсирует потерю high-intent трафика. Поэтому агрегат нельзя читать как «релиз сломал всё»: это практическая ловушка эффекта Симпсона, когда взвешенная средняя меняет масштаб истории относительно сопоставимых сегментов.

## При этом локальный Android-срез требует немедленной проверки

![Локальный срез](figures/local_regression.png)

Срез фиксирован по platform, city и channel; дополнительно проверен внутри pre-treatment прокси качества сессии. Падение присутствует во всех трёх quality-бакетах, что делает простую смену quality mix внутри этого узкого среза недостаточным объяснением.

| Качество сессии | Before CR | After 5.14.0 CR | Δ | 95% CI для Δ |
|---|---:|---:|---:|---:|
{local_quality_rows}

Пострелизные старые Android-версии в том же geography/channel имеют CR {pct(local['after_old_conversion'])}; это полезный diagnostic comparator, но не контрольная группа: rollout не был случайным, а версии могут отличаться по пользователям и устройствам. Поэтому p-value описывает совместимость разницы со случайной вариацией при выбранной модели, а не causal effect версии 5.14.0.

## Приоритетный план действий

1. **P0 — снизить риск:** feature flag / rollback нового search-result card только для Android × Moscow × paid_search; параллельно проверить error-rate, latency, availability и путь от search до booking. Сохранить holdback, если он уже есть.
2. **P1 — подтвердить механизм:** разобрать session replay / технические логи и добавить диагностические события: impression card, tap, availability response, price change, payment start/success. Проверить, не меняется ли пропуск событий после 5.14.0.
3. **P2 — исправить и измерить:** после фикса запустить заранее зарегистрированный randomised holdback в затронутой популяции, если переключатель доступен серверно. Primary metric — search-to-book; guardrails — crash-free sessions, search latency, availability response rate и booking cancellation.

## Мониторинг и confirmatory experiment

Ежедневно мониторить общий CR **и** standardized CR на pre-release mix, долю channel × quality, CR по platform × city × app version, а также freshness/duplicates/coverage. Алерт: падение standardized CR более чем на 1 п.п. за 24 часа при не менее 1 000 search-сессий и при пройденных data-quality checks; отдельный алерт для локального среза — более 2 п.п. от rolling-7-day baseline.

Для эксперимента с ожидаемым улучшением {pct(expected_lift)} относительно текущего {pct(baseline)} ориентир — около **{sample_per_arm:,.0f} search-сессий на вариант** (двусторонний α=0.05, power≈80%, независимые сессии). До запуска нужно скорректировать расчёт на кластеризацию по пользователю, фактическую baseline-конверсию и допустимые множественные проверки. Решение о rollout — только по заранее заданному primary metric и guardrails.

## Ограничения и открытые вопросы

- Это портфельный synthetic scenario: числа не являются внешним фактом и не должны использоваться для оценок рынка или продукта.
- Before/after и наблюдательный comparator не отделяют эффект релиза от календарного спроса, предложения, маркетинга и self-selection в rollout.
- В данных нет реальных логов availability, цены, ошибок и отмен; они нужны, чтобы подтвердить механизм, а не только место аномалии.
- Локальный тест — диагностический. При поиске по многим срезам p-values требуют pre-registration либо корректировки на multiple testing.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    quality = json.loads(args.quality.read_text(encoding="utf-8"))
    args.output.write_text(render(metrics, quality), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
