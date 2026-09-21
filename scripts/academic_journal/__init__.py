"""Academic-work: журнал, реестры и quality-gates для научной работы с ИИ-ассистентами.

Пакет зависит только от стандартной библиотеки Python 3.9+ (sqlite3 с FTS5).
Никаких обязательных внешних пакетов: ядро должно работать офлайн и years later.
"""

__version__ = "0.1.0"

DEFAULT_DB = "sqlite:///~/.academic/journal.db"
DEFAULT_JOURNAL_DIR = "~/.academic/journal"
