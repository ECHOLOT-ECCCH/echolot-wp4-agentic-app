# Service documentation for the agent

One Markdown file per service a script can call through `ctx.service("<name>")`
(docs/PLAN.md 7.7). The file starts with front matter:

```markdown
---
name: echolot
description: One line the agent sees in list_services
---
# echolot
Endpoints, request and response shapes, errors, quirks, and how to build good requests.
```

The agent reads the body with `service_docs(name)` before writing a script. The service's URL,
key and limits are not here: they live in the runner host's `services.toml` under the same name
(see `runner/services.example.toml`).
