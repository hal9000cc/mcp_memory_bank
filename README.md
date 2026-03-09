# MCP Memory Bank

Сервер долговременной памяти для AI-ассистентов, реализованный как MCP-сервер (Model Context Protocol).  
Позволяет сохранять и восстанавливать контекст между сессиями.

## Установка

```bash
pip install mcp-memory-bank
```

После установки команда `mcp-memory-bank` становится доступна в PATH.

## Настройка в Cline

Откройте настройки Cline → **MCP Servers** → добавьте сервер вручную:

```json
{
  "mcpServers": {
    "memory-bank": {
      "disabled": false,
      "timeout": 20,
      "type": "stdio",
      "command": "mcp-memory-bank",
      "args": [
        "--dir",
        "/absolute/path/to/your/project/.memory_bank"
      ]
    }
  }
}
```

> **Совет:** укажите путь к папке `.memory_bank` внутри вашего проекта.  
> Если аргумент `--dir` не передан, сервер создаст `.memory_bank/` в текущей рабочей директории.

Добавьте `.memory_bank/` в `.gitignore` проекта — память специфична для разработчика.

### Подключение RULES.md

Скопируйте файл [`RULES.md`](RULES.md) из этого репозитория в настройки инструкций Cline  
(Settings → Custom Instructions) или добавьте в `.clinerules` вашего проекта — это научит ассистента работать с памятью.

---

## Как это работает

### Хранилище

Данные хранятся в указанной папке:

```
.memory_bank/
├── documents/
│   ├── context.md          ← Markdown + YAML frontmatter
│   ├── activeTask.md
│   └── architecture.md
└── index.db                ← SQLite индекс для быстрого поиска
```

Каждый документ — это человекочитаемый Markdown-файл с метаданными в YAML frontmatter:

```markdown
---
tags:
  - decision
  - architecture
core: false
lastModified: '2026-03-09T01:00:00Z'
---
# Архитектурные решения

## Выбор СУБД
...
```

SQLite-индекс пересобирается автоматически при каждом запуске сервера — файлы остаются источником истины.

---

## Протокол

### Концепция

- Вся информация хранится в виде **документов** (Markdown-текст + метаданные)
- Каждый документ имеет: имя (`name`), контент (`content`), теги (`tags`), флаг `core` и дату изменения
- Флаг `core=true` означает, что документ загружается **автоматически** при каждом старте сессии
- Документы без `core` загружаются **по запросу** — для экономии контекста

---

### Инструменты (tools)

#### `memory_bank_read_context()`

Основной инструмент для начала сессии. Возвращает:
- Метаданные **всех** документов (без контента)
- Полный контент **только core-документов** (`core=true`)

**Параметры:** нет

**Возвращает:**
```json
{
  "documents": [
    {
      "name": "context.md",
      "tags": ["context", "global"],
      "core": true,
      "lastModified": "2026-03-09T01:00:00Z",
      "content": "# Проект: ...\n\n..."
    },
    {
      "name": "activeTask.md",
      "tags": ["task", "active"],
      "core": true,
      "lastModified": "2026-03-09T01:15:00Z",
      "content": "# Текущая задача: ...\n\n..."
    },
    {
      "name": "architecture.md",
      "tags": ["decision", "architecture"],
      "core": false,
      "lastModified": "2026-03-08T18:00:00Z",
      "content": null
    }
  ]
}
```

---

#### `memory_bank_read_documents(names)`

Читает один или несколько документов по имени.

**Параметры:**

| Параметр | Тип | Обязательный | Описание |
|---|---|---|---|
| `names` | `list[string]` | ✅ | Список имён документов |

**Возвращает:**
```json
{
  "documents": [
    {
      "name": "architecture.md",
      "tags": ["decision", "architecture"],
      "core": false,
      "lastModified": "2026-03-08T18:00:00Z",
      "content": "# Архитектурные решения\n\n## Выбор СУБД\n\nПринято решение использовать PostgreSQL..."
    }
  ]
}
```

Если документ не найден — возвращается `null` для соответствующего элемента.

---

#### `memory_bank_write_document(name, content, tags, core)`

Создаёт новый документ или полностью перезаписывает существующий.

**Параметры:**

| Параметр | Тип | Обязательный | Описание |
|---|---|---|---|
| `name` | `string` | ✅ | Имя документа (например, `activeTask.md`) |
| `content` | `string` | ✅ | Содержимое в формате Markdown |
| `tags` | `list[string]` | ✅ | Теги для поиска (минимум 2) |
| `core` | `boolean` | ❌ | Загружать при старте сессии (default: `false`) |

**Возвращает:**
```json
{
  "success": true,
  "name": "activeTask.md",
  "lastModified": "2026-03-09T01:20:00Z"
}
```

---

#### `memory_bank_search_by_tags(tags)`

Поиск документов по тегам. Возвращает документы, у которых есть **все** указанные теги.  
Контент **не возвращается** — только метаданные. Для чтения контента используй `read_documents`.

**Параметры:**

| Параметр | Тип | Обязательный | Описание |
|---|---|---|---|
| `tags` | `list[string]` | ✅ | Список тегов (документ должен содержать все) |

**Возвращает:**
```json
{
  "documents": [
    {
      "name": "architecture.md",
      "tags": ["decision", "architecture", "database"],
      "core": false,
      "lastModified": "2026-03-08T18:00:00Z"
    }
  ]
}
```

---

### Рекомендуемые документы

| Имя | Назначение | Теги | core |
|---|---|---|---|
| `context.md` | Цель проекта, технологии, ограничения | `context`, `global` | `true` |
| `projectStructure.md` | Структура директорий, модули, связи между компонентами | `structure`, `global` | `true` |
| `activeTask.md` | Текущая задача, последние действия, следующие шаги, блокировки | `task`, `active` | `true` |
| `progress.md` | Журнал выполненных задач и этапов | `progress`, `log` | `false` |
| `architecture.md` | Ключевые архитектурные и технические решения | `decision`, `architecture` | `false` |

Имена документов — рекомендация. Допустимы любые осмысленные имена в формате `camelCase.md` или `kebab-case.md`.

---

## Аргументы командной строки

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--dir` | путь | `.memory_bank/` в cwd | Путь к директории хранилища документов |
| `--log-level` | строка | `INFO` | Уровень логирования: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | путь | stderr | Файл для записи логов. Если не указан — логи выводятся в stderr |
| `--response-delay` | число (мс) | `0` | Задержка перед отправкой ответа на каждый вызов инструмента |

---

### `--response-delay`: обход бага с extended thinking

В Cline 3.71.0 при использовании моделей с extended thinking (например, Claude Opus с опцией «Extended thinking») возникает race condition: Cline не успевает передать thinking blocks обратно в API Claude до получения ответа от MCP-сервера. Это приводит к зависанию после надписи «Thinking...».

**Решение:** добавьте задержку 100 мс — этого достаточно для устранения проблемы:

```json
{
  "mcpServers": {
    "memory-bank": {
      "disabled": false,
      "timeout": 20,
      "type": "stdio",
      "command": "mcp-memory-bank",
      "args": [
        "--dir", "/absolute/path/to/your/project/.memory_bank",
        "--response-delay", "100"
      ]
    }
  }
}
```

> **Примечание:** аргумент `--response-delay` актуален только при использовании extended thinking. При обычной работе задержку можно не указывать.
