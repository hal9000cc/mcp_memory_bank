# Memory Bank — Rules for AI Assistant

You must use the `memory-bank` MCP server to preserve context between sessions.  
This is your long-term memory. Do not ignore it.

---

## project_id — Required Parameter

**Every tool call requires a `project_id` parameter.**

Use the **absolute path to the project root directory** (Current Working Directory) as `project_id`.  
This ensures each project has its own isolated memory bank.

Example: if the Current Working Directory is `/home/user/projects/my_app`, pass `project_id="/home/user/projects/my_app"`.

---

## Available Tools

| Tool | Purpose |
|---|---|
| `memory_bank_read_context(project_id)` | Reads metadata of all documents + content of core documents |
| `memory_bank_read_documents(project_id, names)` | Reads full content of specific documents by name |
| `memory_bank_write_document(project_id, name, content, tags, core)` | Creates or fully overwrites a document |
| `memory_bank_append_content(project_id, name, content, tags, core)` | Appends text to the end of a document without reading it first |
| `memory_bank_delete_document(project_id, name)` | Deletes a document by name; returns an error if it does not exist |
| `memory_bank_search_by_tags(project_id, tags)` | Searches documents by tags, returns metadata only |

### Common Storage

Memory Bank also supports a **common shared storage** that is not tied to a specific project.

- Use `project_id=""` (empty string) to read or write documents in the common storage
- Common documents are **not** loaded automatically by `memory_bank_read_context(project_id=<project>)`
- `memory_bank_search_by_tags(project_id=<project>, tags=[...])` searches both:
  - the current project storage
  - the common shared storage
- Search results include:
  - `common: true` for documents from the shared storage
  - `common: false` for project-specific documents
  - `size` to help decide whether a document is worth loading in full

This is useful for reusable materials shared across multiple projects, for example:
- release checklists
- personal operational notes
- reusable prompts
- logs or reference modules if the user and agent decide that is appropriate

CRITICAL: Only place documents in shared storage at the user's request or with permission.

### When to use `append_content` vs `write_document`

- **`write_document`** — when you need to **restructure** or **rewrite** the entire document (e.g., updating `activeTask.md` with new task status)
- **`append_content`** — when you need to **add a new entry** to a growing log (e.g., adding a session summary to `progress.md`)

The key advantage of `append_content`: the server reads the file internally — the existing document content **never enters your context**. Use it for any log-style document that grows over time.

For common storage, the same tools are used — just pass `project_id=""`.

---

## Memory Bank in PLAN MODE

Memory Bank documents are **metadata**, not code or project files.  
Working with them primarily happens in **PLAN MODE**, so writing is allowed in **both modes**.

If you need to write a document in PLAN MODE, **do it immediately**.  
- **Do not ask the user to switch to ACT MODE**  
- **Do not ask for separate confirmation** before writing — just write.

This is an explicit exception to the general "don't modify files in PLAN mode" guideline.
Recording decisions, updating task status, and noting observations during planning is not just allowed but **encouraged**.

In PLAN MODE you **should**:
- Call `memory_bank_read_context` at session start (same as always)
- Update `activeTask.md` when a task is selected, planned, or its status changes
- Record architectural decisions as they emerge during planning
- Update any Memory Bank document that reflects current understanding

This is an explicit exception to the general "don't modify files in PLAN mode" guideline.

---

## Working Protocol

### AT THE START of each session

1. **Immediately call `memory_bank_read_context(project_id=<cwd>)`** — before doing any work.
2. From the response you will receive:
   - A list of all documents with metadata (name, tags, core, lastModified)
   - Full content of core documents (`core=true`)
3. Analyze the core documents: understand the project, the current task, and the last progress.
4. If needed, read additional documents via `memory_bank_read_documents`.

Note: `memory_bank_read_context(project_id=<cwd>)` loads only documents for that specific project. It does **not** automatically include documents from the common shared storage.

**If Memory Bank is empty** (an empty `documents` array was returned) — **ask the user first**:

> "Memory Bank is empty. Should I initialize it now? I will scan the project files (`list_files` + `list_code_definition_names`) and create starter documents (`context.md`, `projectStructure.md`, `activeTask.md`). On large projects this may consume a noticeable number of tokens."

**Only if the user confirms**, proceed with initialization:

**Step 1.** Explore the project: use `list_files` (recursively) and `list_code_definition_names` to understand the structure.

**Step 2.** Create the starter documents:

```
memory_bank_write_document(
  project_id: "<current working directory>",
  name: "context.md",
  content: "# Project: <name>\n\n## Goal\n<project goal>\n\n## Technologies\n<stack>\n\n## Constraints\n<if any>",
  tags: ["context", "global"],
  core: true
)

memory_bank_write_document(
  project_id: "<current working directory>",
  name: "projectStructure.md",
  content: "# Project Structure\n\n## Directories\n<key folder tree with descriptions>\n\n## Modules\n<list of modules and their purpose>\n\n## Key Files\n<entry points, configs, main classes>\n\n## Dependencies\n<who depends on what, main data flows>",
  tags: ["structure", "global"],
  core: true
)

memory_bank_write_document(
  project_id: "<current working directory>",
  name: "activeTask.md",
  content: "# Current Task: <name>\n\n## Description\n<what needs to be done>\n\n## Next Steps\n- <step 1>",
  tags: ["task", "active"],
  core: true
)
```

---

### DURING work

- On any **significant decision**, immediately update the relevant document.
- Use `memory_bank_search_by_tags` to find related documents before making important decisions.
- After searching, review fields such as `common` and `size`, then read only the documents that are actually needed via `memory_bank_read_documents`.
- Update `activeTask.md` when switching subtasks or when a blocker appears.

**What counts as significant:**
- An architectural or technical decision made
- A completed phase of work
- A change of goal or approach
- A blocker or problem identified
- Adding a new module, renaming, or restructuring the project → update `projectStructure.md`

---

### AT THE END of the session

Before finishing work, you must:

1. **Update `activeTask.md`** (CRITICAL) — this is the **single source of truth** for task status.
   - Move completed tasks from "Open Tasks" to "Recently Completed" with the date
   - Update "Current Task" and "Next Steps"
   - `activeTask.md` is loaded at every session start — task status MUST be reflected here
   - Writing only to `progress.md` is NOT sufficient — the next session will not see it

2. **Append to `progress.md`** (optional) — use `append_content` to add a session summary without reading the full log:
   - When the session contained important details worth preserving for future reference
   - `progress.md` is a historical log, NOT the source of truth for task status

3. If architectural decisions were made — update `architecture.md`.

#### Recommended `activeTask.md` structure

```markdown
# Task Tracker

## Current Task: <name or "none">

## Description
<what we are doing right now>

## Next Steps
- [ ] step 1
- [ ] step 2

## Blockers
- (if any)

## Open Tasks
- [ ] Task A
- [ ] Task B

## Recently Completed
- [x] Task C (09.03.2026)
- [x] Task D (08.03.2026)
```

---

## Document Writing Rules

### Format
Always use Markdown with clear `##` headings and `-` lists.

### Tags
- Minimum 2 tags per document.
- Tags must be lowercase English: `database`, `auth`, `api`, `decision`, `bug`.
- Be consistent: if you used `database` — do not write `db` in another document.

### The `core` flag
- `core: true` — only for documents **needed at every startup**: `context.md`, `projectStructure.md`, `activeTask.md`.
- Everything else — `core: false`. Do not overuse this flag; it pollutes the context.

### What to keep compact

For documents with `core: true`:
- Keep them compact and high-signal
- Avoid large code fragments, long logs, or bulky drafts
- Store only what is useful at the start of nearly every session

For documents with `core: false`:
- There are no hard restrictions in these rules
- The user and the agent may decide what is worth storing
- If a search result points to a large document, use the `size` field to decide whether loading it is actually helpful

---

## Recommended Documents

| Name | Purpose | core |
|---|---|---|
| `context.md` | Project goal, stack, constraints | ✅ |
| `projectStructure.md` | Directory structure, modules, component relationships | ✅ |
| `activeTask.md` | Current task, progress, blockers, next steps | ✅ |
| `progress.md` | Log of completed tasks by date | ❌ |
| `architecture.md` | Key architectural and technical decisions | ❌ |

---

## Examples

### Start of session
```
memory_bank_read_context(project_id="/home/user/projects/my_app")
```
→ Reads context.md, projectStructure.md and activeTask.md automatically. Understand the project, its structure, and where we left off.

Returned document metadata may include fields such as:
- `common` — whether the document comes from the shared common storage
- `size` — file size in bytes

---

### User mentioned a topic — search for related documents
```
memory_bank_search_by_tags(project_id="/home/user/projects/my_app", tags=["database"])
```
→ Get a list of matching documents from both the current project and the common shared storage (metadata only).

Example result shape:
```json
{
  "documents": [
    {
      "common": false,
      "name": "architecture.md",
      "tags": ["database", "decision"],
      "core": false,
      "lastModified": "2026-03-09T10:00:00Z",
      "size": 2048
    },
    {
      "common": true,
      "name": "release-checklist.md",
      "tags": ["release", "checklist"],
      "core": false,
      "lastModified": "2026-03-10T09:00:00Z",
      "size": 8192
    }
  ]
}
```

If a matching document is large, consider whether you need to load it at all.

```
memory_bank_read_documents(project_id="/home/user/projects/my_app", names=["architecture.md"])
```
→ Read the needed document in full.

To read a document from the common shared storage:

```
memory_bank_read_documents(project_id="", names=["release-checklist.md"])
```

→ Reads the shared document by name from the common storage.

---

### Architectural decision made
```
memory_bank_write_document(
  project_id="/home/user/projects/my_app",
  name="architecture.md",
  content="# Architectural Decisions\n\n## 2026-03-09: Database Selection\n\nUsing PostgreSQL 15+.\n\n**Reasons:**\n- Open source\n- JSON support\n- Reliability\n\n**Alternatives:** MySQL (rejected due to license), MongoDB (not suitable for relational data).",
  tags=["decision", "architecture", "database"],
  core=False,
)
```

### Shared release checklist for multiple projects

```
memory_bank_write_document(
  project_id="",
  name="release-checklist.md",
  content="# Release Checklist\n\n## Before tagging\n- [ ] Update version\n- [ ] Update changelog\n- [ ] Run tests\n\n## Release\n- [ ] Create git tag\n- [ ] Build artifacts\n- [ ] Publish to package registry",
  tags=["release", "checklist", "shared"],
  core=False,
)
```

→ Stores a reusable release checklist in the common shared storage so it can be found from multiple projects via tag search.

---

### Subtask completed — update activeTask
```
memory_bank_write_document(
  project_id="/home/user/projects/my_app",
  name="activeTask.md",
  content="# Current Task: Database Connection Setup\n\n## Done\n- [x] Database selected (PostgreSQL)\n- [x] Connection configuration created\n\n## Next Steps\n- [ ] Write migrations\n- [ ] Create data models\n\n## Blockers\n- Waiting for ORM decision",
  tags=["task", "active", "database"],
  core=True,
)
```

---

### End of session — append progress log

Use `append_content` to add a session entry without reading the full log history:

```
memory_bank_append_content(
  project_id="/home/user/projects/my_app",
  name="progress.md",
  content="## 2026-03-09\n- [x] PostgreSQL selected and configured\n- [x] Table structure created\n- [ ] Migrations — in progress\n\n### Issues\n- High latency on first connection",
  tags=["progress", "log"],
  core=False,
)
```

The `tags` parameter is only needed on first call (when the document doesn't exist yet). On subsequent calls it is ignored.

---

### End of session — update task status (CRITICAL)

Always update `activeTask.md` to move completed tasks to "Recently Completed":

```
memory_bank_write_document(
  project_id="/home/user/projects/my_app",
  name="activeTask.md",
  content="# Task Tracker\n\n## Current Task: none\n\n## Next Steps\n- [ ] Write migrations\n\n## Open Tasks\n- [ ] Write migrations\n- [ ] Create data models\n\n## Recently Completed\n- [x] Database selected and configured (09.03.2026)\n- [x] Table structure created (09.03.2026)",
  tags=["task", "active"],
  core=True,
)
```
