# Проверки текущей сборки

Дата: 24 октября 2026. Исполнение — Linux, CPython 3.12.14.

- Установка из `requirements.lock` с проверкой хешей и editable-установка проекта завершились успешно.
- `pytest -q`: **61 passed**, нет failed/skipped/xfail. Есть одно предупреждение зависимости
  Starlette о будущем переходе TestClient с httpx на httpx2; тесты с ним не отключались.
- `ruff check .` и `ruff format --check .`: успешно.
- `python -m skin_radar scan --demo`: три синтетические котировки, один кандидат,
  прибыль 191,12 ₽ и ROI 15,17%, без автоматической отправки Telegram.
- Реальный ASGI-сервер поднят через `python -m skin_radar run --demo`;
  `/health` возвращает `status=ok, mode=demo`.
- Chromium/Playwright: страница загружается, метка DEMO присутствует, детализация
  и две ссылки открываются, сохранение фильтра прибыли 500 ₽ скрывает кандидата,
  возврат к 150 ₽ возвращает его. Проверены сортировка и ширина 390 px;
  горизонтального выхода страницы за viewport и JavaScript page errors нет.
- Скриншоты: [desktop](screenshots/dashboard-demo-desktop.png),
  [mobile](screenshots/dashboard-demo-mobile.png). Все показанные котировки синтетические.
- Семь команд Telegram обработаны через aiogram Dispatcher с mock-транспортом.
  Посторонний chat ID отклоняется. **Реальная Telegram-доставка не проверена:** токена нет.
- Live-команда попыталась прочитать два API, не подменила ошибки demo-данными,
  показала недоступные источники и завершилась с кодом 2. Подробности — в API_REPORT.md.

Windows CI сконфигурирован в `.github/workflows/tests.yml`; локальная сессия не исполнялась
на Windows. Публикация конфигурации облака и запуск в новой машине не проверялись.
