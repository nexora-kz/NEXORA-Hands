# NEXORA Hands — CHECKPOINT 2026-09-19

Статус: PASS

## Архитектура

- GitHub — исходный код, документация и доставка runtime.
- Windows-ПК — локальное выполнение команд.
- Supabase — транспорт, heartbeat, результаты и серверная логика.
- MCP NEXORA Hands — управление из ChatGPT.
- Пользовательский запуск сохранён: launch.html → CMD → Python → connected.
- Node.js для обычной работы Hands не требуется.

## Выполнено

- Локальные улучшения runtime синхронизированы с GitHub.
- shell timeout завершает дерево процессов только конкретной задачи.
- timeout передаётся MCP → command → channel → executor; после execution timeout предусмотрен транспортный grace 30 секунд.
- hands_health использует реальный executor_online.
- Добавлен rolling-log data/logs/channel_events.jsonl.
- Добавлен cleanup_preview без удаления файлов.
- Launcher закреплён на immutable runtime commit и проверяет SHA-256 скачанных файлов.
- README переведён на русский и очищен от конкретных имён/ID компьютеров.
- Deployed Hands Edge Function sources и Hands SQL migrations сохранены в репозитории.
- Friendly worker names вынесены из публичного кода в конфигурацию Supabase.
- Текущий публичный working tree проверен на персональные идентификаторы: совпадений нет.

## Live-проверки

- restart через существующий watchdog: PASS.
- reconnect: PASS.
- health: ready=true.
- shell: PASS.
- batch 5/5: PASS.
- long task: PASS.
- async submit: PASS.
- cancel: PASS.
- retry: PASS.
- prune с безопасным порогом: PASS, удалено 0.
- cleanup_preview: PASS, удаление не выполнялось.
- timeout process-tree test: PASS, дочерний процесс после timeout не остаётся.
- Control Center list_workers: PASS.
- установленный hands.py совпадает с репозиторием.
- установленный supabase_channel.py совпадает с репозиторием.
- публичный raw runtime по закреплённому commit проходит SHA-256.

## Git

Runtime commit: b51ba88367c225cdad090b0c88be688ff73cb3f2
Launcher/server finalize: 012fbb9ad7816c86427dfd201f31a5f91b44dbd0

## Supabase

- nexora-hands-mcp: ACTIVE, обновлён.
- nexora-hands-control-v2: ACTIVE, обновлён.
- nexora-hands-transport: рабочий production deploy оставлен без изменения; основной текущий Hands channel использует REST/RPC transport и от этой Edge Function не зависит.

## Отложено сознательно

- Авторизация Control Center — без изменений по принятому решению.
- DPAPI/изменение хранения worker_token — отложено.
- Автоматическое удаление старых outbox/results/processes — не включено; доступен только preview.
- История Git не переписывалась. Очистка относится к текущему публичному tree; старые commit-объекты сохраняются до отдельного решения о переписывании истории.
