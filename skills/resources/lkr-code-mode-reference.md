# Looker Code Mode (`lkr-dev-cli`) & Monty Sandbox Reference

This shared resource defines the `lkr-dev-cli` Code Mode sandbox rules, project provisioning scripts, and QA validation snippets shared across [`looker-demo-orchestrator`](../looker-demo-orchestrator/SKILL.md) and [`lookml-qa-validator`](../looker-demo-orchestrator/subagents/lookml-qa-validator.md).

---

## 1. Monty Sandbox Execution Rules & Function Cheat-Sheet

- **Bare Top-Level Functions**: Looker SDK methods are exposed directly as bare top-level functions in the `lkr code-mode sandbox`.
- **Forbidden Calls**: **DO NOT USE** `globals()`, `sdk()`, `client = sdk()`, or `import looker_sdk` (raises `NameError` or `TypeError`).
- **Restricted Environment (No System Imports)**:
  - **NEVER IMPORT** `import time`, `import os`, `import sys`, `import requests`, or external standard library modules (raises `ModuleNotFoundError`).
  - Standard builtins (`len`, `range`, `print`, `dict`, `list`, `str`, `int`) are natively available. Do not call `time.sleep()`.
- **Core Top-Level Sandbox Functions**:
  - `session()` and `update_session(body={"workspace_id": "dev"})`
  - `create_project(body={"name": "<project>"})` and `update_project(project_id="<project>", body={"git_remote_url": None, "git_service_name": "bare"})`
  - `create_lookml_model(body={"name": "<model>", "project_name": "<project>", "allowed_db_connection_names": ["<conn>"], "unlimited_db_connections": False})`
  - `validate_project(project_id="<project>")` -> returns `{"errors": [...], "project_digest": "..."}`
  - `run_inline_query(result_format="json", body={...})` -> executes query directly against the dev workspace
  - `all_project_files(project_id="<project>")` -> lists staged files in dev mode

---

## 2. Project & Bare Git Initialization Recipe

```bash
lkr --oauth-account=<oauth_account> code-mode sandbox --code="
if session().get('workspace_id') != 'dev':
    update_session(body={'workspace_id': 'dev'})

project_name = '<project_name>'
connection_name = '<connection_name>'

create_project(body={'name': project_name})
update_project(project_id=project_name, body={'git_remote_url': None, 'git_service_name': 'bare'})
create_lookml_model(body={
    'name': project_name,
    'project_name': project_name,
    'allowed_db_connection_names': [connection_name],
    'unlimited_db_connections': False,
})
"
```

---

## 3. Single-File Push, Project Validation & Inline Query Testing

```bash
# 1. Reliable Single-File Push to Dev Workspace
for file in $(find views models dashboards -type f -name "*.lkml" -o -name "*.lookml"); do
  lkr --oauth-account=<oauth_account> tools lookml push <lookml_dir> --project=<project_name> -f "$file"
done

# 2. Run LookML Project Validator
lkr --oauth-account=<oauth_account> code-mode sandbox --dev-mode --code="
if session().get('workspace_id') != 'dev':
    update_session(body={'workspace_id': 'dev'})
res = validate_project(project_id='<project_name>')
for err in res.get('errors', []):
    print(f'  - {err.get(\"message\")} (field: {err.get(\"field_name\")})')
"

# 3. Execute Inline Query Verification
lkr --oauth-account=<oauth_account> code-mode sandbox --dev-mode --code="
if session().get('workspace_id') != 'dev':
    update_session(body={'workspace_id': 'dev'})
res = run_inline_query(result_format='json', body={
    'model': '<model_name>',
    'view': '<explore_name>',
    'fields': ['<field_1>', '<field_2>'],
    'limit': '50'
})
print('Result rows count:', len(res))
"
```
