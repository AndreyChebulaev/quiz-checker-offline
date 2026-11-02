# Дымовые тесты (временные)

Прогоняют основные сценарии на **копии** проекта (рабочая `users.db` не меняется).
Вместо ML-модели подключается заглушка из `stubs/` — проверяется веб-логика, а не качество оценки.
На этапе 6 будут заменены на `pytest`.

```bash
PYTHONHASHSEED=0 PYTHONPATH=tests/smoke/stubs python tests/smoke/smoke.py .
```

Скрипты: `smoke.py` — основной сценарий, `smoke_parallel.py` — два студента одновременно,
`smoke_security.py` — права и пароли, `smoke_editor.py` — редактор тестов, `smoke_review.py` — ручная проверка и порог теста.
