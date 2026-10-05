---
name: setup-embed-demo
description: Low-level technical reference specification and Code Mode scripts for Looker Embed Portal instance provisioning (API Service Account credentials, embed allowlist & cookieless v2, 2-step folder inheritance permissions, PUT /api/4.0/lookml_dashboards/move, CA agent sharing, and brand themes) using the LookML project and dashboard already deployed by the main demo-create CLI.
---

# Looker Embed Portal Instance Provisioning — Reference Specification (`setup-embed-demo`)

> [!IMPORTANT]
> **Role in the `demo-create` 13-Gate Architecture (Uses the Existing CLI-Created Demo Project)**:
> - **Do NOT create or push a dummy `embed-demo` LookML project.** The Looker project, LookML model, and LookML dashboard (`<model_name>::<dashboard_name>`) created and deployed during Gates 5–8 (`demo-create lookml model` and `demo-create lookml deploy`) of the main `demo-create` CLI pipeline are used directly by the embed portal.
> - At **Gate 12 (`demo-create embed scaffold`)**, the CLI automatically executes this entire instance provisioning pipeline against the already-deployed demo project via the Looker 4.0 SDK and hydrates the local portal workspace.
> - This skill serves as the **Low-Level Technical Reference Specification** and standalone remediation runbook for `demo-create embed scaffold` and the [`embed-portal-engineer`](../looker-demo-orchestrator/subagents/embed-portal-engineer.md) subagent.

---

## 1. Critical Architectural Protocols

### A. Headless Backend Looker API Service Account vs Developer CLI OAuth

1. **Developer CLI (`~/.lkr/auth.db`)**:
   - Uses interactive 3-legged user OAuth tied to personal developer credentials (`user@example.com`).
   - Short-lived and unsuitable for headless server daemon processes or Looker Core embed user impersonation.
2. **Embed Portal Backend (`backend/.env`)**:
   - The FastAPI backend (`app/services/looker.py`) runs headlessly to issue signed SSO tokens and call `acquire_embed_cookieless_session`.
   - **Looker Core instances strictly require a dedicated API Service Account credential pair** (`LOOKERSDK_CLIENT_ID` and `LOOKERSDK_CLIENT_SECRET`), created in Looker Admin $\to$ Users with **no** email login credentials (API3 keys only).
   - In `demo-create embed scaffold`, pass `--client-id` and `--client-secret` (or respond to the interactive prompt) so `backend/.env` is populated with Service Account credentials.

### B. Mandatory 2-Step Folder Inheritance & Group Access Protocol

Folders created inside Looker's Shared root space (`parent_id: "1"`) inherit permissions (`inherits: True`) by default:
- Calling `create_content_metadata_access()` directly on an inheriting subfolder fails with:
  `SDKError: Can't add access when inheriting`
- Disabling inheritance (`inherits: False`) before the group has parent space access fails with:
  `SDKError: Group ... can not be added because they do not have access to the parent space`

**Mandatory 2-Step Execution Order**:
1. **Step 1 (Parent Space)**: Grant the `<Brand> Embed Users` group (`group_id`) `view` access on Shared Root (`content_metadata_id: "1"`):
   ```python
   sdk.create_content_metadata_access(
       body=models40.ContentMetaGroupUser(
           content_metadata_id="1",
           group_id=embed_group_id,
           permission_type=models40.PermissionType.view,
       )
   )
   ```
2. **Step 2 (Target Subfolder)**: Grant the `<Brand> Embed Users` group (`group_id`) `view` access on the created `<Brand> Dashboards` folder (`target_folder.content_metadata_id`), disabling inheritance first if required:
   ```python
   sdk.create_content_metadata_access(
       body=models40.ContentMetaGroupUser(
           content_metadata_id=str(target_folder.content_metadata_id),
           group_id=embed_group_id,
           permission_type=models40.PermissionType.view,
       )
   )
   ```

### C. LookML Dashboard Relocation: Dedicated Move Endpoint (`PUT /api/4.0/lookml_dashboards/move`)

> [!CAUTION]
> **NEVER Import LookML Dashboards as User Dashboards (`sdk.import_lookml_dashboard`)**:
> Importing creates detached, user-defined duplicates that sever LookML synchronization and version control.

Always relocate the demo's deployed LookML dashboard (`<model_name>::<dashboard_name>`) directly into the target Shared subfolder using Looker's move endpoint:
- **Endpoint**: `PUT /api/4.0/lookml_dashboards/move` (with fallback to `PUT /api/internal/lookml_dashboards/move`)
- **Request Body**:
  ```json
  {
    "method": "put",
    "dashboard_ids": [
      "<model_name>::<dashboard_name>"
    ],
    "folder_id": "<target_folder_id>"
  }
  ```

### D. Conversational Analytics (CA) Agent Group Sharing Protocol

When a Conversational Analytics agent is created at Gate 10 (`demo-create agent create`), its `content_metadata_id` is private to the creator (User 1 Admin). Non-admin embed users will receive `403 Forbidden` or `"Agent not found"` unless the embed group is granted `view` access on the agent's `content_metadata_id`:

```python
sdk.create_content_metadata_access(
    body=models40.ContentMetaGroupUser(
        content_metadata_id=str(agent.content_metadata_id),
        group_id=embed_group_id,
        permission_type=models40.PermissionType.view,
    )
)
```

### E. Looker 4.0 Embed Themes (`<Brand>_Light` & `<Brand>_Dark`)

Follow the [`embed-themes`](../embed-themes/SKILL.md) specification to create `<CleanBrand>_Light` and `<CleanBrand>_Dark` via `POST /api/4.0/themes` (`sdk.create_theme` / `sdk.update_theme`) with `show_dashboard_header=True`, `show_title=False`, and `tile_shadow=True`.

---

## 2. Synchronized Workspace Files (`group_id` & `folder_id`)

After provisioning the instance group and folder against the deployed demo project, `demo-create embed scaffold` synchronizes the generated `group_id`, `folder_id`, `dashboard_id`, `agent_id`, and `lookml_model_name` across:
1. **`backend/.env`**: `LOOKERSDK_CLIENT_ID`, `LOOKERSDK_CLIENT_SECRET`, `LOOKER_PROJECT_NAME=<looker_project_name>`, `DEFAULT_LOOKER_GROUP_IDS=["<group_id>"]`
2. **`frontend/.env`** (and root **`.env`**): `VITE_LOOKER_FOLDER_ID=<folder_id>`, `VITE_DASHBOARD_ID=<model_name>::<dashboard_name>`, `VITE_EXPLORE_PATH=<model_name>/<primary_explore>`, `VITE_CHAT_AGENT_ID=<agent_id>`, `VITE_THEME=<CleanBrand>_Light`
3. **`backend/app/models.py`**: `DEFAULT_LOOKER_GROUP_IDS = ["<group_id>"]` and default `brand = "<brand_name>"`
4. **`frontend/src/config/constants.ts`**: `LOOKER_FOLDER_ID = "<folder_id>"`, `group_ids: ["<group_id>"]` in `getRoleUserObject`, `DASHBOARD_ID`, `EXPLORE_PATH`, and `CHAT_AGENT_ID`
5. **`frontend/src/components/dialogs/UserDetailsDialog.tsx`**: `group_ids: ["<group_id>"]` in `userSettingsJson`

---

## 3. Modular Reference Scripts (`scripts/`)

- [Step 1: Administrative Settings, Allowlist & Embed Group Setup (`./scripts/1_admin_settings.md`)](./scripts/1_admin_settings.md)
- [Step 2: Shared Folder, 2-Step Group Access, Dashboard Move & CA Agent Share (`./scripts/7_setup_folder_and_dashboards.md`)](./scripts/7_setup_folder_and_dashboards.md)
- [Verify Credentials (`./scripts/verify_credentials.md`)](./scripts/verify_credentials.md)
