# Уведомление об источниках и лицензиях

Этот репозиторий — **собственный код и документация** (лицензия MIT, см. [LICENSE](LICENSE)).
Ниже перечислены внешние проекты, на которые мы опираемся, и правила их использования.

## Важно о лицензиях

| Проект | Лицензия | Как используется здесь | Можно ли копировать к нам |
|---|---|---|---|
| [Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills) | **CC BY-NC 4.0** | Внешний плагин/зависимость; рекомендуется как основной research-пайплайн | **Нет.** Некоммерческая лицензия: файлы скиллов нельзя включать в этот репозиторий и перераспределять. Используйте как установленный плагин и ссылайтесь |
| [misbahsy/anti-ai-slop](https://github.com/misbahsy/anti-ai-slop) | MIT | Источник идей для гейтов G0/G1 (4 гейта, порог 90/100, exit code для CI) | Да, с сохранением копирайта; мы не копируем код, а реализуем совместимый формат правил (`config/style-rules.ru.json`) |
| [Gentleman-Programming/engram](https://github.com/Gentleman-Programming/engram) | MIT | Опциональная семантическая память поверх журнала | Да, с сохранением копирайта |
| [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory) | Apache-2.0 | Альтернатива Engram для проектной памяти | Да, с соблюдением Apache-2.0 |
| [color4-alt/CiteCheck](https://github.com/color4-alt/CiteCheck) | MIT | Проверка библиографии (внешний скилл/CLI) | Да |
| [Warnes-Innovations/citation-audit](https://github.com/Warnes-Innovations/citation-audit) | без лицензии | Образец схемы аудита (`.audit/`, `index.json`); **код не копируется** | Нет: у репозитория нет лицензии — используйте только как идею |
| [VivienP/scientific-claim-verification-engine](https://github.com/VivienP/scientific-claim-verification-engine) | NOASSERTION | Образец формата отчёта (вердикты `supported` / `partially_supported` / `not_addressed` / `unverifiable`, `provenance.jsonl`) | С осторожностью: лицензия не указана явно |
| [llm-as-a-verifier](https://github.com/llm-as-a-verifier/llm-as-a-verifier) | MIT | Опциональный оценщик вариантов текста/плана (не верификатор фактов) | Да |
| [strategyconnect/webcite-mcp-server](https://github.com/strategyconnect/webcite-mcp-server) | без лицензии, платный API | Идея детерминированного пересчёта чисел; в пайплайне не используется | Нет |
| [guillaumemeyer/watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover) | MIT | **Сознательно не используется** | См. ниже |

## Почему здесь нет инструментов удаления водяных знаков

`watermarks-remover` снимает SynthID/C2PA-маркеры происхождения контента. Для научной
и учебной работы это не решает задачу достоверности и создаёт риск нарушения правил
журнала или вуза: удаление provenance прямо противоречит требованиям раскрытия
использования ИИ. Вместо удаления маркеров этот проект делает обратное —
**сохраняет provenance**: журнал действий, лог использования ИИ по секциям,
манифест данных, disclosure-раздел (см. [docs/ai-use-policy.md](docs/ai-use-policy.md)).

## Сторонние данные

`examples/minimal/*` содержит вымышленные ссылки и числа, созданные для демонстрации
гейтов. DOI `10.1000/xyz123` и `10.2000/abc456` — примеры формата, а не реальные
публикации.
