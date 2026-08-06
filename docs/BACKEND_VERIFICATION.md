# Что проверяется

Проверка проходит через весь source-neutral backend:

`local JSON fixture` → `normalization` → `persistence` → `ScrapeRun` →
`SUCCESS / PARTIAL / FAILED` → `reconciliation` → `SQLite`.

То есть тестовые вакансии читаются из локального файла, нормализуются,
сохраняются в базу, а результат запуска и изменения пропущенных вакансий
проверяются так же, как это потребуется будущему разрешённому источнику.

# Что такое fake source

Fake source — это локальные synthetic JSON-файлы, созданные только для
тестирования. Они не обращаются к LinkedIn или другим сайтам.

Позже fixture adapter можно заменить adapter'ом разрешённого production
source. Нормализация, persistence, статусы запусков, reconciliation и остальная
backend-логика при этом останутся прежними.

# Автоматическая проверка

Выполните из корня проекта в PowerShell:

```powershell
.venv\Scripts\python.exe manage.py check

.venv\Scripts\python.exe -m pytest tests/test_backend_integration.py -vv

.venv\Scripts\python.exe -m pytest `
    tests/test_fixture_pipeline.py `
    tests/test_recoverable_pipeline.py `
    tests/test_background.py `
    -vv

.venv\Scripts\python.exe -m pytest
```

Ожидаемый результат: все команды успешно завершаются, integration tests и
полный pytest проходят, сеть не используется. Общее количество тестов здесь не
зафиксировано, потому что оно будет расти вместе с проектом.

# Какие сценарии проверены

## Run 1

- Три вакансии создаются.

## Run 2

- Одна вакансия остаётся без изменений.
- Одна вакансия обновляется.
- Одна новая вакансия создаётся.
- Одна отсутствующая вакансия получает первый `miss`.

## Run 3

- Повторно отсутствующая вакансия становится `NOT_FOUND`.

## PARTIAL

- Корректные вакансии сохраняются.
- Повреждённая запись возвращает безопасную ошибку.
- Отсутствующие вакансии не получают `miss`.

## FAILED

- Запуск фиксируется как `FAILED`.
- Существующие вакансии не деактивируются.
- Запуск не остаётся в состоянии `RUNNING`.

## Background

- `submit` быстро возвращает handle.
- Обработка выполняется в worker thread.
- Второй активный запуск той же `Company` отклоняется.

## Company isolation

- Данные разных компаний не смешиваются.

# Что эта проверка не доказывает

- Получение актуальных вакансий из LinkedIn.
- Полноту production pagination.
- Разрешение на автоматический сбор данных.
- Работу с будущим production source.
- Работу пользовательского интерфейса.

# Итог Milestone 2

Source-neutral backend завершён и проверен на fixtures.

Production source integration остаётся **Blocked** до решения команды о
разрешённом источнике и автоматическом получении данных.
