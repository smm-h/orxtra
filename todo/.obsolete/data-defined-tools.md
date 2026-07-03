# Data-defined tools

## Problem

Agents are TOML+md, workflows are TOML, categories are TOML -- but tools require Python code (`@tool` decorator). The only data-level escape hatch is `[[exec]]` in agent definitions, which is limited to "run this binary with these args." There is no way to define a new tool purely from a data file.

This is inconsistent with orxtra's philosophy of data-driven definitions and creates a hard boundary: adding a custom tool requires writing Python and modifying the tool module, while adding a custom agent or workflow requires only a TOML file.

## What data-defined tools would enable

- Fleet/deployment-specific tools without forking orxtra's tool module
- Tool definitions that live alongside agent and workflow definitions
- Custom tools that compose built-in tools (e.g., "read file, transform, write back")
- HTTP-based tools (call an API endpoint with typed parameters)
- Script-based tools richer than `[[exec]]` (typed params, output schema, write-safety)

## Possible shape (TOML)

```toml
[tool]
name = "fetch-ticket"
description = "Fetch a Jira ticket by ID"
namespace = "custom"
tags = ["read"]

[params]
ticket_id = { type = "string", description = "Jira ticket ID", pattern = "^[A-Z]+-[0-9]+$" }

[execution]
type = "http"
method = "GET"
url = "https://jira.example.com/rest/api/2/issue/{ticket_id}"
headers = { "Authorization" = "Bearer {{secret:JIRA_TOKEN}}" }

[output]
schema = { type = "object", properties = { summary = { type = "string" }, status = { type = "string" } } }
```

Other execution types could include `script` (Python callable path), `command` (subprocess with typed args), and `composite` (sequence of other tool calls).

## Affected modules

- `tool/` -- loader would need to support TOML tool definitions alongside decorator-registered tools
- `protocols/` -- Tool type may need to accommodate data-defined metadata
- `scheduler/` -- tool registry would need to discover and load TOML tool files
- `agent/` -- agent definitions could reference data-defined tools by name in `allow` lists (already works if the tool is registered)

## Effort

Medium-large. The tool decorator system works well for built-in tools and doesn't need to change. This is additive: a TOML tool loader that produces the same `Tool` objects the decorator produces, registered in the same `ToolRegistry`.
