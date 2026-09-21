# Academic-work: локальная автоматизация научной работы.
# Все команды идемпотентны и работают без сети (кроме `make gate-bib-online`).

PYTHON ?= python3
CLI     := $(PYTHON) scripts/journal_cli.py
DB      ?= $(HOME)/.academic/journal.db
JOURNAL ?= $(HOME)/.academic/journal
PROJECT ?= default
MANUSCRIPT ?= manuscript/main.tex
BIB        ?= manuscript/refs.bib
VALUES     ?= analysis/results.json
REPORTS    := reports/gates
EXPORT_DIR ?= export
CANARY_SEED ?= 42
CANARY_MANIFEST ?= $(basename $(MANUSCRIPT)).canary.json
DISCLOSURE ?= ai-disclosure.md
SOURCE     ?= literature/source.pdf
QUOTE      ?= ""
LOCATOR    ?= ""

.PHONY: help init resume journal-verify gates gate-hygiene gate-style gate-bib gate-numbers \
        gate-bib-online gate-claims gate-quote export canary canary-check disclosure \
        reproduce test clean

help: ## показать справку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

init: ## создать БД и каталог журнала
	$(CLI) init --db $(DB) --journal-dir $(JOURNAL) --project $(PROJECT)

resume: ## контекст-пак для старта сессии
	$(CLI) resume --db $(DB) --project $(PROJECT)

journal-verify: ## проверить хеш-цепочку журнала
	$(CLI) verify --db $(DB) --journal-dir $(JOURNAL)

gates: gate-hygiene gate-style gate-numbers gate-bib ## все детерминированные гейты

gate-hygiene: ## G0: невидимые символы
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G0 --db $(DB) --project $(PROJECT) --file $(MANUSCRIPT) \
		--report $(REPORTS)/G0-hygiene.json --record

gate-style: ## G1: шаблонные обороты и «вода»
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G1 --db $(DB) --project $(PROJECT) --file $(MANUSCRIPT) \
		--report $(REPORTS)/G1-style.json --record

gate-bib: ## G3: библиография (офлайн)
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G3 --db $(DB) --project $(PROJECT) --tex $(MANUSCRIPT) --bib $(BIB) \
		--report $(REPORTS)/G3-bibliography.json --record

gate-bib-online: ## G3: библиография с проверкой DOI через Crossref
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G3 --db $(DB) --project $(PROJECT) --tex $(MANUSCRIPT) --bib $(BIB) --online \
		--report $(REPORTS)/G3-bibliography.json --record

gate-numbers: ## G5: трассировка чисел на results.json (текст + таблицы)
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G5 --db $(DB) --project $(PROJECT) --file $(MANUSCRIPT) --values $(VALUES) \
		--report $(REPORTS)/G5-numbers.json --record

gate-claims: ## G4: verbatim-сверка цитат реестра с файлами источников
	@mkdir -p $(REPORTS)
	$(CLI) run-gate G4 --db $(DB) --project $(PROJECT) \
		--report $(REPORTS)/G4-claims.json --record

gate-quote: ## G4: разовая проверка одной цитаты (QUOTE=текст SOURCE=файл)
	$(CLI) gate-quote --db $(DB) --project $(PROJECT) --source $(SOURCE) --quote "$(QUOTE)" \
		--locator "$(LOCATOR)" --record

export: ## экспорт реестров в CSV/Markdown
	$(CLI) export --db $(DB) --project $(PROJECT) --out $(EXPORT_DIR) --what all --format both

canary: ## подмешать ложные элементы в копию черновика
	$(CLI) canary --file $(MANUSCRIPT) --seed $(CANARY_SEED)

CANARY_FILE ?= $(basename $(MANUSCRIPT)).canary$(suffix $(MANUSCRIPT))

canary-run: ## полный цикл: подмешать канарки → прогнать гейты → посчитать recall
	$(CLI) canary --file $(MANUSCRIPT) --seed $(CANARY_SEED)
	@mkdir -p $(REPORTS)
	-$(CLI) run-gate G1 --db $(DB) --project $(PROJECT) --file $(CANARY_FILE) --report $(REPORTS)/canary-G1.json
	-$(CLI) run-gate G3 --db $(DB) --project $(PROJECT) --tex $(CANARY_FILE) --bib $(BIB) --report $(REPORTS)/canary-G3.json
	-$(CLI) run-gate G5 --db $(DB) --project $(PROJECT) --file $(CANARY_FILE) --values $(VALUES) --report $(REPORTS)/canary-G5.json
	$(CLI) canary-check --db $(DB) --project $(PROJECT) --manifest $(CANARY_MANIFEST) \
		--g1 $(REPORTS)/canary-G1.json --g3 $(REPORTS)/canary-G3.json \
		--g5 $(REPORTS)/canary-G5.json --record

canary-check: ## посчитать recall контура по отчётам гейтов
	$(CLI) canary-check --db $(DB) --project $(PROJECT) --manifest $(CANARY_MANIFEST) \
		--g1 $(REPORTS)/G1-style.json --g3 $(REPORTS)/G3-bibliography.json \
		--g5 $(REPORTS)/G5-numbers.json --record

disclosure: ## собрать раздел «Использование ИИ» из журнала
	$(CLI) disclosure --db $(DB) --journal-dir $(JOURNAL) --out $(DISCLOSURE)

fix-hygiene: ## удалить невидимые символы (с .bak)
	$(CLI) hygiene --file $(MANUSCRIPT) --fix

extract: ## извлечь ключи цитирования, секции и числа
	$(CLI) extract --tex $(MANUSCRIPT) --bib $(BIB)

reproduce: ## воспроизвести все таблицы и фигуры из сырых данных
	@test -f analysis/run_all.sh && bash analysis/run_all.sh || \
	  echo "нет analysis/run_all.sh — добавьте скрипт пересчёта (см. docs/data-and-reproducibility.md)"

test: ## юнит-тесты
	$(PYTHON) -m unittest discover -s tests -v

clean: ## удалить отчёты гейтов
	rm -rf $(REPORTS)
