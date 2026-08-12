# Data dictionary

## Уровень данных и временные правила

- Часовой пояс событий: `UTC`.
- Окно анализа: 29 июня — 9 августа 2026; rollout начинается 20 июля 2026.
- `funnel_session_mart` имеет одну строку на session с `search_results_viewed`.
- Booking засчитывается, если в той же session после поиска есть `booking_created`.
- Все файлы генерируются локально, фиксированным seed; внешних данных нет.

## `events.csv`

| Поле | Тип | Описание | Правило качества |
|---|---|---|---|
| `event_id` | string | Уникальный идентификатор события | Уникален |
| `session_id` | string | Идентификатор search-сессии | Ссылается на `session_attributes` |
| `user_id` | string | Синтетический идентификатор пользователя | Не используется как outcome |
| `event_name` | string | `search_results_viewed` или `booking_created` | Допустимый enum |
| `event_ts` | timestamp | Время события | Booking позже search в той же сессии |
| `ingested_at` | timestamp | Время доступности события в витрине | Не позже 24 ч до synthetic as-of |

## `session_attributes.csv`

| Поле | Тип | Описание | Роль в RCA |
|---|---|---|---|
| `session_id` | string | Ключ сессии | Ключ JOIN с raw events |
| `user_id` | string | Синтетический user key | Возможная кластеризация в будущих тестах |
| `event_date` | date | Дата поиска | Определяет before/after |
| `session_started_at` | timestamp | Время начала search-сессии | Проверка порядка событий |
| `city` | string | Moscow / Saint Petersburg / Kazan | Сегмент RCA |
| `platform` | string | iOS / Android / Web | Сегмент и rollout |
| `app_version` | string | Версия на момент search | Диагностический прокси rollout |
| `acquisition_channel` | string | organic / paid_search / paid_social / referral | Компонент mix |
| `session_quality` | string | high / standard / low, известна до booking | Контроль composition |

`session_attributes.csv` намеренно не содержит `booked` или другого post-outcome поля: conversion строится только из raw event log.

## `release_calendar.csv`

| Поле | Тип | Описание |
|---|---|---|
| `release_id` | string | Идентификатор synthetic release |
| `platform` | string | Платформа релиза |
| `app_version` | string | Версия релиза |
| `released_at` | timestamp | Время публикации |
| `rollout_start` | timestamp | Время начала rollout |
| `release_note` | string | Краткое synthetic описание изменения |

## Производные поля и метрики

| Сущность | Формула / определение |
|---|---|
| `has_search` | В session есть `search_results_viewed` |
| `has_booking` | В session есть `booking_created` |
| `search-to-book conversion` | `SUM(has_booking) / COUNT(*)` в `funnel_session_mart` |
| `period` | `before`, если `event_date < 2026-07-20`; иначе `after` |
| `mix_effect` | `Σ(w_after − w_before) × CR_before` по `channel × quality` |
| `rate_effect` | `Σw_after × (CR_after − CR_before)` по тем же ячейкам |

