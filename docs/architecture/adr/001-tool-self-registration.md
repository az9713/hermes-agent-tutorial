# ADR 001: Tool self-registration at import time

**Status:** Accepted

## Context

Hermes has 40+ tools implemented across 53 files in `tools/`. Each tool needs to be:
1. Known to the system (name, schema, handler, availability check)
2. Discoverable at runtime so the LLM can be told what tools exist
3. Easy to add without touching unrelated files

The alternatives were:
1. A central registry file that manually lists every tool
2. A configuration file (YAML/JSON) that maps tool names to handlers
3. Tool files that self-register when imported

## Decision

Tools self-register at import time. Each tool file calls `registry.register(name, toolset, schema, handler, check_fn)` at module level. `model_tools.py` imports all tool modules to trigger these registrations.

## Alternatives considered

### Option A: Central registry file

A file like `tools/__init__.py` that explicitly imports and registers each tool:

```python
from tools.web_tools import web_search, web_extract
from tools.file_tools import read_file, write_file
registry.register("web_search", ..., web_search)
registry.register("web_extract", ..., web_extract)
# ... 50+ more registrations
```

**Pros:** Explicit, easy to see all tools in one place.
**Cons:** Every new tool requires editing this central file. Creates merge conflicts when multiple people add tools. The file becomes a maintenance burden.

### Option B: Configuration-driven (YAML schema)

Tool schemas defined in YAML files; code discovers them automatically:

```yaml
# tools/web_tools.yaml
- name: web_search
  handler: tools.web_tools._web_search_handler
  schema: ...
```

**Pros:** Separates schema definition from code.
**Cons:** Schemas drift out of sync with code. Two files to maintain per tool. Harder to type-check. Schema-to-handler mapping is implicit.

### Option C: Self-registration (chosen)

Each tool file registers itself at import:

```python
registry.register(
    name="web_search",
    schema={...},
    handler=_web_search,
    check_fn=lambda: bool(os.environ.get("PARALLEL_API_KEY")),
)
```

**Pros:** Schema, handler, and availability check are co-located. Adding a tool is one file + one import line in `model_tools.py`. No central list to maintain.
**Cons:** Registration happens as a side effect of import, which is unusual. `model_tools.py` still needs the import line.

## Rationale

The co-location benefit outweighs the import-side-effect concern. When a tool changes (new parameter, different availability check, renamed handler), everything is in one file. The pattern is consistent across all 53 tool files, making it easy to understand.

The `model_tools.py` import requirement is a known tradeoff — it's one line per tool and is explicitly documented as the mechanism.

## Trade-offs

- **What we gave up:** Explicit central registry view. You can't see all tools without reading each file.
- **What we accepted:** Tools are "magic" in that they appear in the registry just by being imported.
- **What this makes harder:** If a tool import fails (import error in that module), the tool silently disappears from the registry. Error handling at import time needs care.

## Consequences

- Adding a new tool: create the file, add `registry.register()`, add one import to `model_tools.py`. Done.
- Removing a tool: delete the file, remove the import. Done.
- The registry is the source of truth for what tools exist. You cannot list tools from source files alone — you need to run the import.
