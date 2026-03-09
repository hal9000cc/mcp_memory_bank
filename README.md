![PyPI - Version](https://img.shields.io/pypi/v/mcp-memory-bank)
![GitHub License](https://img.shields.io/github/license/hal9000cc/mcp_memory_bank)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/mcp-memory-bank)
# MCP Memory Bank

A long-term memory server for AI assistants, implemented as an MCP server (Model Context Protocol).  
Allows you to save and restore context between sessions. Requires Python 3.10+.

## Why do you need this?

### 💰 Token savings

The main cost when working with AI assistants (especially in development) comes from reading context. Every extra file read by the agent ends up in the context and is paid for on every request.

Memory Bank solves this problem by:

- At session start, the assistant only receives what matters most — documents with the `core` flag.
- Other knowledge is loaded on demand, only when actually needed.
- The agent stops aimlessly reading project files and immediately knows the architecture, current task, constraints, and status.

### 🧠 Long-term memory

A regular assistant forgets everything after the chat is closed. Memory Bank preserves decisions made, architectural details, agreements, and any other important context between sessions.

- No repeated explanations — the agent remembers why this particular database was chosen, what constraints were imposed, what architectural trade-offs were made.
- When resuming work (even after a week or a month), the assistant is immediately up to date with the last state of the project.
- All changes are synchronized automatically — the agent writes documents itself using the provided tools.

### ✅ Task management

Memory Bank helps organize work on a project:

- Break large tasks into stages.
- The agent stores the task list directly in the project memory.
- In the morning, ask the assistant: "What's our task for today?" — and it will answer by checking the current `activeTask.md` document.
- The agent tracks progress, records completed steps, and you always stay informed about the current state of tasks.

---

## Installation

```bash
pip install mcp-memory-bank
```

After installation, the `mcp-memory-bank` command becomes available in PATH. If Python is installed in a non-standard way, this may not happen. In that case, add the path to the executable manually.

The author uses Cline, so the configuration below is for Cline. Since the protocol is standard, mcp-memory-bank should work with other AI agents as well, but the configuration may differ slightly.

## Setup in Cline

Open Cline settings → **MCP Servers** → add the server manually.

Choose one of two storage modes:

---

### Mode 1: Global storage

All projects are stored in one place, isolated by `project_id`.  
Data is stored by default in the user's system directory (via [platformdirs](https://platformdirs.readthedocs.io/)):

| OS | Path |
|---|---|
| Linux | `~/.local/share/mcp-memory-bank/` |
| macOS | `~/Library/Application Support/mcp-memory-bank/` |
| Windows | `C:\Users\<user>\AppData\Local\mcp-memory-bank\mcp-memory-bank\` |

```json
{
  "mcpServers": {
    "memory-bank": {
      "disabled": false,
      "type": "stdio",
      "command": "mcp-memory-bank"
    }
  }
}
```

Configure once — works for all projects automatically.  
On each tool call, the agent passes `project_id` — and the data goes to the correct storage.

---

### Mode 2: Local storage in the project (for Cline)

Each project's data is stored directly in its directory (`.memory_bank/` inside the project).  
This is convenient in Cline, since the agent receives the project path via `Current Working Directory`.

```json
{
  "mcpServers": {
    "memory-bank": {
      "disabled": false,
      "type": "stdio",
      "command": "mcp-memory-bank",
      "args": ["--project-local"]
    }
  }
}
```

> **Important:** in `--project-local` mode, the `project_id` parameter is used as an absolute path to the project root.  
> Cline passes it automatically via `Current Working Directory`.

Add `.memory_bank/` to the project's `.gitignore` — memory is developer-specific.

---

### Connecting RULES.md

Copy the [`RULES.md`](RULES.md) file from this repository to the Cline instructions settings  
(Settings → Custom Instructions) or add it to your project's `.clinerules` — this will teach the assistant how to work with memory.

> **Important:** according to the rules, the agent should initialize the Memory Bank on the first request if the bank is empty. However, sometimes the agent immediately focuses on your task and skips this step. In that case, simply ask: "Initialize the Memory Bank".

---

## How it works

### Project identification

Each tool call contains a **`project_id`** parameter — a unique project identifier.  
The server uses it to isolate data between projects.

It is recommended to pass the **absolute path to the project root** (for Cline — this is `Current Working Directory`). When passing a path as `project_id`, you can use the `--project-local` argument and store bank documents in the project folder.

However, any meaningful string identifier is valid as `project_id`. In that case, only global storage is available and the `--project-local` argument cannot be used.

### Storage

**Global mode** — storage structure:
```
~/.local/share/mcp-memory-bank/
└── projects/
    ├── my_project_a1b2c3d4/    ← slug from project_id
    │   ├── documents/
    │   │   ├── context.md
    │   │   └── activeTask.md
    │   └── index.db
    └── another_project_e5f6a7b8/
        └── ...
```

**Local mode** (`--project-local`) — storage structure:
```
/path/to/your/project/
└── .memory_bank/
    ├── documents/
    │   ├── context.md
    │   └── activeTask.md
    └── index.db
```

Each document is a human-readable Markdown file with metadata in YAML frontmatter:

```markdown
---
tags:
  - decision
  - architecture
core: false
lastModified: '2026-03-09T01:00:00Z'
---
# Architectural Decisions

## Database Selection
...
```

The SQLite index is rebuilt automatically on every server start — files remain the source of truth.

---

## Protocol

### Concept

- All information is stored as **documents** (Markdown text + metadata)
- Each document has: a name (`name`), content (`content`), tags (`tags`), a `core` flag, and a modification date
- The `core=true` flag means the document is loaded **automatically** at every session start
- Documents without `core` are loaded **on demand** — to save context

---

### Tools

All tools accept the required **`project_id`** parameter.

#### `memory_bank_read_context(project_id)`

The main tool for starting a session. Returns:
- Metadata of **all** documents (without content)
- Full content of **core documents only** (`core=true`)

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | ✅ | Project identifier (recommended — absolute path to project root) |

**Returns:**
```json
{
  "documents": [
    {
      "name": "context.md",
      "tags": ["context", "global"],
      "core": true,
      "lastModified": "2026-03-09T01:00:00Z",
      "content": "# Project: ...\n\n..."
    },
    {
      "name": "activeTask.md",
      "tags": ["task", "active"],
      "core": true,
      "lastModified": "2026-03-09T01:15:00Z",
      "content": "# Current Task: ...\n\n..."
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

#### `memory_bank_read_documents(project_id, names)`

Reads one or more documents by name.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | ✅ | Project identifier |
| `names` | `list[string]` | ✅ | List of document names |

**Returns:**
```json
{
  "documents": [
    {
      "name": "architecture.md",
      "tags": ["decision", "architecture"],
      "core": false,
      "lastModified": "2026-03-08T18:00:00Z",
      "content": "# Architectural Decisions\n\n## Database Selection\n\nDecided to use PostgreSQL..."
    }
  ]
}
```

If a document is not found — `null` is returned for the corresponding element.

---

#### `memory_bank_write_document(project_id, name, content, tags, core)`

Creates a new document or completely overwrites an existing one.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | ✅ | Project identifier |
| `name` | `string` | ✅ | Document name (e.g., `activeTask.md`) |
| `content` | `string` | ✅ | Content in Markdown format |
| `tags` | `list[string]` | ✅ | Tags for search (minimum 2) |
| `core` | `boolean` | ❌ | Load at session start (default: `false`) |

**Returns:**
```json
{
  "success": true,
  "name": "activeTask.md",
  "lastModified": "2026-03-09T01:20:00Z"
}
```

---

#### `memory_bank_search_by_tags(project_id, tags)`

Searches documents by tags. Returns documents that have **all** of the specified tags.  
Content is **not returned** — only metadata. To read content, use `read_documents`.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | ✅ | Project identifier |
| `tags` | `list[string]` | ✅ | List of tags (document must contain all of them) |

**Returns:**
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

#### `memory_bank_append_content(project_id, name, content, tags, core)`

Appends text to the end of an existing document **without reading its content into the LLM context**.  
The server reads the file internally, appends the new content with a blank line separator, and saves it back.

This is the preferred way to update log-style documents (e.g., `progress.md`) — it avoids loading the full document into context on every update.

If the document does not exist, it will be created (tags are required in that case).

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | ✅ | Project identifier |
| `name` | `string` | ✅ | Document name (e.g., `progress.md`) |
| `content` | `string` | ✅ | Markdown text to append at the end of the document |
| `tags` | `list[string]` | ❌ | Required only when creating a new document (minimum 2). Ignored if document exists. |
| `core` | `boolean` | ❌ | Used only when creating a new document (default: `false`) |

**Returns:**
```json
{
  "success": true,
  "name": "progress.md",
  "lastModified": "2026-03-09T21:00:00Z",
  "contentLength": 1024
}
```

`contentLength` — total length of the document content after the append (in characters).

---

### Recommended documents

| Name | Purpose | Tags | core |
|---|---|---|---|
| `context.md` | Project goal, technologies, constraints | `context`, `global` | `true` |
| `projectStructure.md` | Directory structure, modules, component relationships | `structure`, `global` | `true` |
| `activeTask.md` | Current task, recent actions, next steps, blockers | `task`, `active` | `true` |
| `progress.md` | Log of completed tasks and phases | `progress`, `log` | `false` |
| `architecture.md` | Key architectural and technical decisions | `decision`, `architecture` | `false` |

Document names are recommendations. Any meaningful names in `camelCase.md` or `kebab-case.md` format are allowed.

---

## Command-line arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--dir` | path | system data directory | Root directory for global storage. Ignored with `--project-local` |
| `--project-local` | flag | disabled | Store data inside each project (`<project_id>/.memory_bank/`) |
| `--log-level` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | path | stderr | File for writing logs. If not specified — logs go to stderr |
| `--response-delay` | number (ms) | `0` | Delay before sending a response to each tool call |

---

### `--response-delay`: workaround for extended thinking bug

In Cline 3.71.0, when using models with extended thinking (e.g., Claude Opus with the "Extended thinking" option), a race condition occurs: Cline doesn't have time to pass thinking blocks back to the Claude API before receiving a response from the MCP server. This causes a hang after the "Thinking..." message.

**Fix:** add a 100 ms delay — this is enough to resolve the issue:

```json
{
  "mcpServers": {
    "memory-bank": {
      "disabled": false,
      "type": "stdio",
      "command": "mcp-memory-bank",
      "args": ["--project-local", "--response-delay", "100"]
    }
  }
}
```

> **Note:** the `--response-delay` argument is only relevant when using extended thinking. For regular usage, the delay can be omitted.
