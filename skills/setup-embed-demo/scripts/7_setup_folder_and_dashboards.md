# Step 2: Shared Folder, 2-Step Group Access, LookML Dashboard Move & CA Agent Share Script

> [!NOTE]
> This reference script is automated by `demo-create embed scaffold` (`provision_embed_instance` in `looker_demo_cli/generators/embed_scaffolder.py`).
> It operates on the Looker project, LookML dashboard (`<model_name>::<dashboard_name>`), and CA Agent already deployed by the main `demo-create` CLI pipeline (no dummy `embed-demo` project setup is needed).
> It implements the mandatory 2-step folder inheritance access protocol, moves the deployed LookML dashboard via `PUT /api/4.0/lookml_dashboards/move` (never `import_lookml_dashboard`), and shares the Conversational Analytics (CA) agent `content_metadata_id` with the embed user group.

```python
import os, sys

log_msgs = []
def log(msg):
    log_msgs.append(msg)
    sys.stderr.write(msg + "\n")

try:
    project_name = os.getenv("LOOKER_PROJECT_NAME", "")
    brand_name = os.getenv("VITE_BRAND_NAME") or project_name.replace("_", " ").title() or "Demo"
    dashboard_id = os.getenv("VITE_DASHBOARD_ID") or f"{project_name}::{project_name}_overview"
    agent_id = os.getenv("VITE_CHAT_AGENT_ID", "")

    # 1. Resolve Shared root folder (parent_id: "1")
    folders = all_folders()
    shared_folder = next((f for f in folders if f["name"] == "Shared" or f.get("is_shared_root")), None)
    parent_id = str(shared_folder["id"]) if shared_folder else "1"
    shared_cm_id = str(shared_folder.get("content_metadata_id", "1")) if shared_folder else "1"

    # 2. Create or find "<Brand> Dashboards" folder in Shared space
    folder_name = f"{brand_name} Dashboards"
    existing_folder = next(
        (f for f in folders if f["name"] == folder_name and str(f.get("parent_id")) == parent_id),
        None,
    )
    if existing_folder:
        target_folder = existing_folder
        folder_id = str(existing_folder["id"])
        log(f"Found existing folder '{folder_name}' with ID: {folder_id}")
    else:
        log(f"Creating folder '{folder_name}' inside Shared folder (parent_id: {parent_id})...")
        target_folder = create_folder(body={"name": folder_name, "parent_id": parent_id})
        folder_id = str(target_folder["id"])
        log(f"Created folder '{folder_name}' with ID: {folder_id}")

    folder_cm_id = str(target_folder.get("content_metadata_id", ""))

    # 3. Mandatory 2-Step Folder Access Protocol (Shared Root CM 1 -> Subfolder CM)
    groups = all_groups()
    group_name = f"{brand_name} Embed Users"
    embed_group = next((g for g in groups if g["name"] == group_name), None)
    group_id = str(embed_group["id"]) if embed_group else None

    if group_id:
        # Step 3a: Ensure group has view access on Shared Root (CM 1)
        try:
            create_content_metadata_access(body={
                "content_metadata_id": shared_cm_id,
                "group_id": group_id,
                "permission_type": "view",
            })
        except Exception:
            pass

        # Step 3b: Grant group view access on target subfolder content_metadata_id
        if folder_cm_id:
            try:
                create_content_metadata_access(body={
                    "content_metadata_id": folder_cm_id,
                    "group_id": group_id,
                    "permission_type": "view",
                })
            except Exception as cm_exc:
                if "inherit" in str(cm_exc).lower():
                    update_content_metadata(content_metadata_id=folder_cm_id, body={"inherits": False})
                    create_content_metadata_access(body={
                        "content_metadata_id": folder_cm_id,
                        "group_id": group_id,
                        "permission_type": "view",
                    })
            log(f"Granted group '{group_name}' (ID: {group_id}) view access on folder CM {folder_cm_id}.")

    # 4. Relocate the deployed LookML Dashboard into folder via PUT /api/4.0/lookml_dashboards/move
    dashboard_ids = [dashboard_id]
    log(f"Moving LookML dashboard {dashboard_ids} into folder_id {folder_id} via PUT /api/4.0/lookml_dashboards/move...")
    transport = getattr(all_folders, "__self__", getattr(session, "__self__", None))
    if hasattr(transport, "put"):
        try:
            response = transport.put(
                path="/api/4.0/lookml_dashboards/move",
                body={
                    "method": "put",
                    "dashboard_ids": dashboard_ids,
                    "folder_id": folder_id,
                },
            )
        except Exception:
            response = transport.put(
                path="/api/internal/lookml_dashboards/move",
                body={
                    "method": "put",
                    "dashboard_ids": dashboard_ids,
                    "folder_id": folder_id,
                },
            )
        log(f"Moved LookML dashboards response: {response}")

    # 5. Share Conversational Analytics (CA) Agent with Embed Group
    agent_shared = False
    if agent_id and group_id:
        try:
            agent_obj = get_agent(agent_id=agent_id)
            agent_cm_id = agent_obj.get("content_metadata_id") if isinstance(agent_obj, dict) else getattr(agent_obj, "content_metadata_id", None)
            if agent_cm_id:
                create_content_metadata_access(body={
                    "content_metadata_id": str(agent_cm_id),
                    "group_id": group_id,
                    "permission_type": "view",
                })
                agent_shared = True
                log(f"Shared CA Agent {agent_id} (CM {agent_cm_id}) with group {group_id}.")
        except Exception as ag_err:
            log(f"CA Agent sharing notice: {ag_err}")

    return {
        "status": "success",
        "logs": log_msgs,
        "group_id": group_id,
        "folder_id": folder_id,
        "moved_dashboards": dashboard_ids,
        "agent_shared": agent_shared,
    }
except Exception as e:
    log(f"Folder setup & dashboard move failed: {e}")
    return {
        "status": "error",
        "logs": log_msgs,
        "error": str(e),
    }
```
