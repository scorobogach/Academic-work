# Документация

| Файл | О чём |
|---|---|
| [architecture.md](architecture.md) | Слои, поток данных, почему выбраны такие решения |
| [storage-decision.md](storage-decision.md) | Почему SQLite, когда Postgres, как мигрировать |
| [journal-format.md](journal-format.md) | Формат записи, хеш-цепочка, verify, манифест, ретеншн |
| [session-protocol.md](session-protocol.md) | Старт/работа/финал сессии, правила контекста, анти-паттерны |
| [research-workflow.md](research-workflow.md) | Стадии, артефакты, связка с ARS, чек-лист перед сдачей |
| [gate-reference.md](gate-reference.md) | Все гейты: команды, пороги, блокирующие условия, canary-тест |
| [integrity-policy.md](integrity-policy.md) | Правила достоверности: что запрещено и что делать при ошибке |
| [ai-use-policy.md](ai-use-policy.md) | Что можно, что нельзя, лог использования ИИ, шаблоны disclosure |
| [memory-architecture.md](memory-architecture.md) | Журнал vs реестры vs память; почему саммари не заменяет журнал |
| [data-and-reproducibility.md](data-and-reproducibility.md) | Сырые данные, манифест, окружение, `make reproduce` |
| [respond-to-reviewers.md](respond-to-reviewers.md) | Ответ рецензенту как процесс с трассировкой |
| [privacy-and-legal.md](privacy-and-legal.md) | Персональные данные, удаление из append-only, облачные сервисы |
| [tool-selection.md](tool-selection.md) | Сравнение GitHub-проектов с проверенными данными |
| [en/overview.md](en/overview.md) | Краткая версия на английском |

Порядок чтения для первого раза:
`architecture.md` → `session-protocol.md` → `gate-reference.md` → `integrity-policy.md`.
