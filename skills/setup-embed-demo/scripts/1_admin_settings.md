# Step 1: Administrative Settings, Allowlist & Embed Group Reference Script

> [!NOTE]
> This reference script is automated by `demo-create embed scaffold` (`provision_embed_instance` in `looker_demo_cli/generators/embed_scaffolder.py`).
> It operates on the Looker project and brand already established by the main `demo-create` CLI pipeline (no dummy `embed-demo` project setup is needed).
> It can also be executed standalone via `lkr code-mode sandbox` or the Looker 4.0 Python SDK.

```python
import os, sys

log_msgs = []
def log(msg):
    log_msgs.append(msg)
    sys.stderr.write(msg + "\n")

try:
    target_domain = os.getenv("LOOKER_EMBED_DOMAIN", "https://localhost:8008")
    project_name = os.getenv("LOOKER_PROJECT_NAME", "")
    brand_name = os.getenv("VITE_BRAND_NAME") or project_name.replace("_", " ").title() or "Demo"

    # 1. Provision 'brand' User Attribute
    log("Checking for required 'brand' user attribute...")
    uas = all_user_attributes()
    if not any(ua["name"] == "brand" for ua in uas):
        log("Creating 'brand' user attribute...")
        create_user_attribute(body={
            "name": "brand",
            "label": "Brand",
            "type": "string",
            "value_is_hidden": False,
            "user_can_view": True,
            "user_can_edit": False,
        })

    # 2. Enforce Instance Embed Settings (Allowlist & Cookieless v2)
    log("Inspecting instance embed settings...")
    current_settings = get_setting()
    embed_config = current_settings.get("embed_config", {})

    embed_config["embed_enabled"] = True
    embed_config["sso_auth_enabled"] = True
    embed_config["embed_cookieless_v2"] = True

    localhost_domains = [
        "http://localhost:3000",
        "http://localhost:8008",
        "http://localhost:5173",
        "https://localhost:8008",
        "https://localhost:8008/",
    ]
    allowlist = embed_config.get("domain_allowlist", [])
    for dom in localhost_domains + [target_domain]:
        if dom and dom not in allowlist:
            log(f"Adding {dom} to embed domain allowlist...")
            allowlist.append(dom)
    embed_config["domain_allowlist"] = allowlist

    log("Committing updated embed configuration via PATCH /api/4.0/setting...")
    set_setting(body={
        "embed_config": embed_config,
        "embed_enabled": True,
        "embed_cookieless_v2": True,
    })

    # 3. Provision Dedicated Embed Content Access Group (<Brand> Embed Users)
    target_group_name = f"{brand_name} Embed Users"
    groups = all_groups()
    target_group = next((g for g in groups if g["name"] == target_group_name), None)
    if not target_group:
        log(f"Creating '{target_group_name}' group...")
        target_group = create_group(body={"name": target_group_name, "can_add_to_content_metadata": True})
    group_id = str(target_group["id"])
    log(f"Embed content access group ID: {group_id}")

    # 4. Step 1 of 2-Step Folder Access Protocol: Grant view access on Shared Root (CM 1)
    try:
        create_content_metadata_access(body={
            "content_metadata_id": "1",
            "group_id": group_id,
            "permission_type": "view",
        })
        log(f"Granted group {group_id} view access on Shared Root (content_metadata_id=1).")
    except Exception as cm_err:
        log(f"Shared Root access notice: {cm_err}")

    return {
        "status": "success",
        "logs": log_msgs,
        "group_id": group_id,
        "group_name": target_group_name,
        "domain_allowlist": embed_config.get("domain_allowlist"),
    }
except Exception as e:
    log(f"Admin settings configuration failed: {e}")
    return {
        "status": "error",
        "logs": log_msgs,
        "error": str(e),
    }
```
