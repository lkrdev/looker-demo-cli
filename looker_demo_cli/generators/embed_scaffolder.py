from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from looker_sdk import models40
from looker_sdk.rtl import transport
from pydantic import BaseModel, Field
from rich.prompt import Prompt

from looker_demo_cli.config import LOOKER_EMBED_DEMO_REPO, SKILLS_CACHE_DIR
from looker_demo_cli.precheck.env_checker import ensure_gitignore
from looker_demo_cli.sdk import get_looker_sdk
from looker_demo_cli.utils.console import print_info, print_success, print_warning

SA_CREDENTIAL_PROMPT_MESSAGE = (
    "Looker Core requires API Service Account credentials for headless embed token generation. "
    "Please enter LOOKERSDK_CLIENT_ID and CLIENT_SECRET:"
)

DEFAULT_EMBED_ALLOWLIST_DOMAINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://localhost:8008",
    "http://localhost:5173",
    "https://localhost:8008",
    "https://localhost:8008/",
)


class EmbedConfigOptions(BaseModel):
    demo_name: str
    target_dir: Path
    brand_name: str
    brand_title: str
    looker_instance_url: str
    looker_project_name: str
    lookml_model_name: str
    dashboard_id: str
    agent_id: str = ""
    explore_path: str = ""
    primary_color: str = "#1A73E8"
    accent_color: str = "#4285F4"
    client_id: str = ""
    client_secret: str = ""
    group_id: str = "8"
    folder_id: str = "1"
    connection_name: str = "default_bigquery_connection"
    embed_domain: str = "https://localhost:8008"
    theme_light: str = ""
    theme_dark: str = ""


class EmbedProvisioningResult(BaseModel):
    provisioned: bool = False
    sa_credentials_configured: bool = False
    allowlist_configured: bool = False
    cookieless_enabled: bool = False
    brand_attribute_ensured: bool = False
    group_id: str = "8"
    group_name: str = ""
    folder_id: str = "12542"
    folder_name: str = ""
    folder_access_granted: bool = False
    dashboard_moved: bool = False
    agent_shared: bool = False
    themes_created: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def sanitize_brand_theme_prefix(brand_name: str) -> str:
    """Sanitize a brand display name into a valid Looker theme prefix (alphanumeric + underscores)."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", re.sub(r"\s+", "_", (brand_name or "").strip()))
    return cleaned or "Embed_Demo"


def resolve_service_account_credentials(
    client_id: str | None = None,
    client_secret: str | None = None,
    *,
    interactive: bool = True,
) -> tuple[str, str]:
    """Resolve Looker API Service Account credentials from flags, env, or interactive prompt."""
    cid = (client_id or os.getenv("LOOKERSDK_CLIENT_ID") or "").strip()
    csecret = (client_secret or os.getenv("LOOKERSDK_CLIENT_SECRET") or "").strip()

    is_tty = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if (not cid or not csecret) and interactive and is_tty:
        print_info(SA_CREDENTIAL_PROMPT_MESSAGE)
        try:
            if not cid:
                cid = Prompt.ask("LOOKERSDK_CLIENT_ID", default="").strip()
            if not csecret:
                csecret = Prompt.ask("LOOKERSDK_CLIENT_SECRET", password=True, default="").strip()
        except (EOFError, KeyboardInterrupt):
            pass

    return cid, csecret


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Read an attribute or dict key from an SDK model or raw dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _grant_group_content_access(
    sdk: Any,
    content_metadata_id: str,
    group_id: str,
    *,
    disable_inheritance_if_needed: bool = False,
) -> bool:
    """Grant a Looker group view access on a content_metadata_id, handling folder inheritance."""
    if not content_metadata_id or not group_id:
        return False

    # Check if group already has access
    if hasattr(sdk, "all_content_metadata_accesss"):
        try:
            existing = sdk.all_content_metadata_accesss(content_metadata_id=str(content_metadata_id)) or []
            for entry in existing:
                if str(_get_field(entry, "group_id", "")) == str(group_id):
                    return True
        except Exception:
            pass

    body = models40.ContentMetaGroupUser(
        content_metadata_id=str(content_metadata_id),
        group_id=str(group_id),
        permission_type=models40.PermissionType.view,
    )
    try:
        sdk.create_content_metadata_access(body=body)
        return True
    except Exception as exc:
        msg = str(exc).lower()
        if "already" in msg or "exists" in msg:
            return True
        if disable_inheritance_if_needed and "inherit" in msg and hasattr(sdk, "update_content_metadata"):
            try:
                sdk.update_content_metadata(
                    str(content_metadata_id),
                    body=models40.WriteContentMeta(inherits=False),
                )
                sdk.create_content_metadata_access(body=body)
                return True
            except Exception as inner_exc:
                if "already" in str(inner_exc).lower():
                    return True
                raise
        raise


def _move_lookml_dashboard_to_folder(
    sdk: Any,
    base_url: str,
    req_headers: dict[str, str],
    dashboard_id: str,
    folder_id: str,
) -> bool:
    """Relocate a LookML dashboard into a target folder via PUT /api/4.0/lookml_dashboards/move."""
    if not dashboard_id or not folder_id:
        return False

    payload = {
        "method": "put",
        "dashboard_ids": [dashboard_id],
        "folder_id": str(folder_id),
    }

    if hasattr(sdk, "move_lookml_dashboards"):
        sdk.move_lookml_dashboards(body=payload)
        return True

    sdk_transport = getattr(sdk, "transport", None)
    if sdk_transport is not None and hasattr(sdk_transport, "put"):
        sdk_transport.put(path="/api/4.0/lookml_dashboards/move", body=payload)
        return True

    if base_url and req_headers.get("Authorization"):
        clean_url = base_url.rstrip("/")
        for path in ("/api/4.0/lookml_dashboards/move", "/api/internal/lookml_dashboards/move"):
            resp = requests.put(f"{clean_url}{path}", json=payload, headers=req_headers, timeout=15)
            if resp.status_code in (200, 201, 202, 204):
                return True
            if resp.status_code not in (404, 405):
                break

    return False


def provision_embed_instance(
    opts: EmbedConfigOptions,
    headers: dict[str, str] | None = None,
    sdk: Any = None,
) -> EmbedProvisioningResult:
    """Execute the 6 Looker instance provisioning steps required for an external embed portal.

    1. Ensure 'brand' user attribute and configure instance embed domain allowlist + cookieless_v2.
    2. Create dedicated '<Brand> Embed Users' group and grant view access on Shared Root (CM 1).
    3. Create '<Brand> Dashboards' folder under Shared Root and grant view access to the group.
    4. Move the LookML dashboard into the shared folder via PUT /api/4.0/lookml_dashboards/move.
    5. Share the Conversational Analytics (CA) agent's content_metadata_id with the embed group.
    6. Create/update '<Brand>_Light' and '<Brand>_Dark' Looker embed themes.
    """
    prefix = sanitize_brand_theme_prefix(opts.brand_name)
    opts.theme_light = opts.theme_light or f"{prefix}_Light"
    opts.theme_dark = opts.theme_dark or f"{prefix}_Dark"

    result = EmbedProvisioningResult(
        sa_credentials_configured=bool(opts.client_id and opts.client_secret),
        group_id=opts.group_id,
        folder_id=opts.folder_id,
    )

    has_auth_header = bool(headers and (headers.get("Authorization") or headers.get("authorization")))
    has_sa_creds = bool(opts.looker_instance_url and opts.client_id and opts.client_secret)

    if sdk is None and not (opts.looker_instance_url and (has_auth_header or has_sa_creds)):
        return result

    base_url = (opts.looker_instance_url or "").rstrip("/")
    req_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }

    if sdk is None:
        try:
            sdk = get_looker_sdk(
                base_url=base_url,
                headers=req_headers if has_auth_header else None,
                client_id=opts.client_id or None,
                client_secret=opts.client_secret or None,
            )
            if "Authorization" not in req_headers and hasattr(sdk, "auth"):
                auth_h = sdk.auth.authenticate(transport.TransportOptions())
                if "Authorization" in auth_h:
                    req_headers["Authorization"] = auth_h["Authorization"]
        except Exception as exc:
            result.warnings.append(f"Could not initialize Looker SDK for embed provisioning: {exc}")
            return result

    # 1. Brand user attribute + Instance Embed Settings (domain_allowlist & embed_cookieless_v2)
    try:
        uas = sdk.all_user_attributes() or []
        if not any(_get_field(ua, "name") == "brand" for ua in uas):
            sdk.create_user_attribute(
                body=models40.WriteUserAttribute(
                    name="brand",
                    label="Brand",
                    type="string",
                    value_is_hidden=False,
                    user_can_view=True,
                    user_can_edit=False,
                )
            )
        result.brand_attribute_ensured = True
    except Exception as exc:
        result.warnings.append(f"User attribute 'brand' check notice: {exc}")

    try:
        current_setting = sdk.get_setting() if hasattr(sdk, "get_setting") else {}
        raw_embed_cfg = _get_field(current_setting, "embed_config") or {}
        if isinstance(raw_embed_cfg, dict):
            allowlist = list(raw_embed_cfg.get("domain_allowlist") or [])
        else:
            allowlist = list(_get_field(raw_embed_cfg, "domain_allowlist") or [])

        for dom in (*DEFAULT_EMBED_ALLOWLIST_DOMAINS, opts.embed_domain):
            if dom and dom not in allowlist:
                allowlist.append(dom)

        embed_cfg_payload = {
            "domain_allowlist": allowlist,
            "embed_enabled": True,
            "sso_auth_enabled": True,
            "embed_cookieless_v2": True,
        }
        setting_payload = {
            "embed_config": embed_cfg_payload,
            "embed_enabled": True,
            "embed_cookieless_v2": True,
        }
        set_setting_fn = getattr(sdk, "set_setting", None)
        if callable(set_setting_fn):
            set_setting_fn(body=setting_payload)
            result.allowlist_configured = True
            result.cookieless_enabled = True
        elif base_url and req_headers.get("Authorization"):
            resp = requests.patch(f"{base_url}/api/4.0/setting", json=setting_payload, headers=req_headers, timeout=15)
            if resp.status_code in (200, 201, 204):
                result.allowlist_configured = True
                result.cookieless_enabled = True
    except Exception as exc:
        result.warnings.append(f"Instance embed settings update notice: {exc}")

    # 2. Dedicated Embed User Group ('<Brand> Embed Users') & Shared Root (CM 1) view permission
    group_name = f"{opts.brand_name} Embed Users"
    try:
        groups = sdk.all_groups() or []
        target_group = next((g for g in groups if _get_field(g, "name") == group_name), None)
        if not target_group:
            target_group = sdk.create_group(
                body=models40.WriteGroup(name=group_name, can_add_to_content_metadata=True)
            )
        gid = _get_field(target_group, "id")
        if gid is not None:
            result.group_id = str(gid)
            result.group_name = group_name
            opts.group_id = result.group_id

        # Step 1 of mandatory 2-step folder access protocol: grant view on Shared root (CM 1)
        _grant_group_content_access(sdk, "1", result.group_id)
    except Exception as exc:
        result.warnings.append(f"Embed group provisioning notice: {exc}")

    # 3. Dedicated Shared Folder ('<Brand> Dashboards') & 2-Step Group View Access
    folder_name = f"{opts.brand_name} Dashboards"
    try:
        folders = sdk.all_folders() or []
        shared_root = next(
            (f for f in folders if _get_field(f, "name") == "Shared" or _get_field(f, "is_shared_root")),
            None,
        )
        parent_id = str(_get_field(shared_root, "id", "1")) if shared_root else "1"
        shared_cm_id = str(_get_field(shared_root, "content_metadata_id", "1")) if shared_root else "1"
        if shared_cm_id != "1":
            _grant_group_content_access(sdk, shared_cm_id, result.group_id)

        target_folder = next(
            (
                f
                for f in folders
                if _get_field(f, "name") == folder_name and str(_get_field(f, "parent_id")) == parent_id
            ),
            None,
        )
        if not target_folder:
            target_folder = sdk.create_folder(
                body=models40.CreateFolder(name=folder_name, parent_id=parent_id)
            )
        fid = _get_field(target_folder, "id")
        if fid is not None:
            result.folder_id = str(fid)
            result.folder_name = folder_name
            opts.folder_id = result.folder_id

        folder_cm_id = _get_field(target_folder, "content_metadata_id")
        if folder_cm_id is not None:
            result.folder_access_granted = _grant_group_content_access(
                sdk,
                str(folder_cm_id),
                result.group_id,
                disable_inheritance_if_needed=True,
            )
    except Exception as exc:
        result.warnings.append(f"Shared folder provisioning notice: {exc}")

    # 4. Relocate LookML Dashboard into Shared Folder via PUT /api/4.0/lookml_dashboards/move
    try:
        result.dashboard_moved = _move_lookml_dashboard_to_folder(
            sdk=sdk,
            base_url=base_url,
            req_headers=req_headers,
            dashboard_id=opts.dashboard_id,
            folder_id=result.folder_id,
        )
    except Exception as exc:
        result.warnings.append(f"LookML dashboard move notice: {exc}")

    # 5. Share Conversational Analytics (CA) Agent with Embed Group
    if opts.agent_id:
        try:
            agent_obj = sdk.get_agent(opts.agent_id) if hasattr(sdk, "get_agent") else None
            agent_cm_id = _get_field(agent_obj, "content_metadata_id") if agent_obj is not None else None
            if agent_cm_id is not None:
                result.agent_shared = _grant_group_content_access(
                    sdk,
                    str(agent_cm_id),
                    result.group_id,
                )
        except Exception as exc:
            result.warnings.append(f"CA Agent sharing notice: {exc}")

    # 6. Provision Looker Embed Themes (<Brand>_Light and <Brand>_Dark)
    try:
        existing_themes = sdk.all_themes() or []
        theme_map = {
            str(_get_field(t, "name")): str(_get_field(t, "id"))
            for t in existing_themes
            if _get_field(t, "name") and _get_field(t, "id") is not None
        }
        for theme_name, is_dark in ((opts.theme_light, False), (opts.theme_dark, True)):
            theme_body = models40.WriteTheme(
                name=theme_name,
                settings=models40.ThemeSettings(
                    background_color="#0b0f19" if is_dark else "#f8fafc",
                    tile_background_color="#111827" if is_dark else "#ffffff",
                    text_tile_background_color="#111827" if is_dark else "#ffffff",
                    font_color="#e2e8f0" if is_dark else "#334155",
                    tile_text_color="#e2e8f0" if is_dark else "#334155",
                    text_tile_text_color="#e2e8f0" if is_dark else "#334155",
                    title_color="#f8fafc" if is_dark else "#0f172a",
                    primary_button_color=opts.primary_color,
                    font_family="'Inter', system-ui, sans-serif",
                    show_filters_bar=True,
                    show_title=False,
                    show_dashboard_header=True,
                    tile_shadow=True,
                    border_radius="12px",
                ),
            )
            if theme_name in theme_map and hasattr(sdk, "update_theme"):
                sdk.update_theme(theme_map[theme_name], body=theme_body)
            else:
                sdk.create_theme(body=theme_body)
            result.themes_created.append(theme_name)
    except Exception as exc:
        result.warnings.append(f"Embed theme provisioning notice: {exc}")

    result.provisioned = True
    for warn in result.warnings:
        print_warning(warn)

    return result


class EmbedScaffolder:
    """Clones, provisions, and customizes a standalone workspace for external embedded demos."""

    @staticmethod
    def _resolve_template_repo() -> Path:
        """Resolve template repo path from local checkout, cache, or clone."""
        if LOOKER_EMBED_DEMO_REPO.exists():
            return LOOKER_EMBED_DEMO_REPO
        cache_path = SKILLS_CACHE_DIR / "looker-embed-demo"
        if cache_path.exists():
            return cache_path

        # Clone on demand if needed
        SKILLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        urls = [
            "https://github.com/lkrdev/looker-embed-demo.git",
            "https://github.com/LukaFontanilla/looker-embed-demo.git",
        ]
        for url in urls:
            try:
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(cache_path)], capture_output=True, text=True, env=env
                )
                if res.returncode == 0:
                    return cache_path
            except Exception:
                continue
        raise FileNotFoundError("Could not find or clone looker-embed-demo template repository.")

    @classmethod
    def provision_looker_instance(
        cls,
        opts: EmbedConfigOptions,
        headers: dict[str, str] | None = None,
        sdk: Any = None,
    ) -> EmbedProvisioningResult:
        """Delegate Looker instance embed provisioning and sync returned group/folder IDs onto opts."""
        return provision_embed_instance(opts, headers=headers, sdk=sdk)

    @classmethod
    def hydrate_workspace_files(cls, target_dir: Path, opts: EmbedConfigOptions) -> None:
        """Hydrate backend and frontend files with dynamic group_id, folder_id, brand, and model config."""
        explore_path = opts.explore_path or f"{opts.lookml_model_name}/{opts.lookml_model_name}"
        prefix = sanitize_brand_theme_prefix(opts.brand_name)
        theme_light = opts.theme_light or f"{prefix}_Light"
        theme_dark = opts.theme_dark or f"{prefix}_Dark"

        # 1. Constants files
        for rel in ("frontend/src/config/constants.ts", "frontend/src/constants.ts", "src/constants.ts"):
            constants_file = target_dir / rel
            if constants_file.exists():
                content = constants_file.read_text(encoding="utf-8")
                content = content.replace("embed_demo::brand_overview", opts.dashboard_id)
                content = content.replace("embed_demo/order_items", explore_path)
                content = content.replace("embed_demo", opts.lookml_model_name)
                content = content.replace("ea1262d262ab43b1a9bb23152f25c236", opts.agent_id)
                content = content.replace("12542", str(opts.folder_id))
                content = content.replace('["8"]', f'["{opts.group_id}"]')
                content = content.replace("['8']", f'["{opts.group_id}"]')
                content = content.replace("Levi's", opts.brand_name)
                content = content.replace("Embed_Demo_Light", theme_light)
                content = content.replace("Embed_Demo_Dark", theme_dark)
                constants_file.write_text(content, encoding="utf-8")

        # 2. Backend models and auth endpoints
        for rel in ("backend/app/models.py", "backend/models.py"):
            models_file = target_dir / rel
            if models_file.exists():
                content = models_file.read_text(encoding="utf-8")
                content = content.replace('["8"]', f'["{opts.group_id}"]')
                content = content.replace("['8']", f'["{opts.group_id}"]')
                content = content.replace("Levi's", opts.brand_name)
                models_file.write_text(content, encoding="utf-8")

        auth_file = target_dir / "backend/app/api/endpoints/auth.py"
        if auth_file.exists():
            content = auth_file.read_text(encoding="utf-8")
            content = content.replace('"thelook"', f'"{opts.lookml_model_name}"')
            content = content.replace('"embed_demo"', f'"{opts.lookml_model_name}"')
            content = content.replace("Levi's", opts.brand_name)
            auth_file.write_text(content, encoding="utf-8")

        # 3. Frontend UserDetailsDialog and domain copy files
        dialog_file = target_dir / "frontend/src/components/dialogs/UserDetailsDialog.tsx"
        if dialog_file.exists():
            content = dialog_file.read_text(encoding="utf-8")
            content = content.replace('["8"]', f'["{opts.group_id}"]')
            content = content.replace("['8']", f'["{opts.group_id}"]')
            content = content.replace("Levi's", opts.brand_name)
            dialog_file.write_text(content, encoding="utf-8")

        for rel in (
            "frontend/src/components/layout/Sidebar.tsx",
            "frontend/src/components/layout/Navbar.tsx",
            "frontend/src/context/PortalContext.tsx",
            "frontend/src/pages/LoginPage.tsx",
            "frontend/src/pages/Home.tsx",
            "frontend/src/components/home/SalesActivityFeed.tsx",
        ):
            comp_file = target_dir / rel
            if comp_file.exists():
                content = comp_file.read_text(encoding="utf-8")
                content = content.replace("Looker Embed", opts.brand_name)
                content = content.replace("Levi's", opts.brand_name)
                comp_file.write_text(content, encoding="utf-8")

    @classmethod
    def scaffold_demo_workspace(cls, opts: EmbedConfigOptions) -> Path:
        """Scaffold a new standalone workspace from looker-embed-demo template."""
        source_repo = cls._resolve_template_repo()

        target_dir = opts.target_dir
        if target_dir.exists():
            print_info(f"Target directory `{target_dir}` already exists. Reusing existing folder.")
        else:
            print_info(f"Scaffolding fresh embed portal into `{target_dir}`...")
            shutil.copytree(
                source_repo,
                target_dir,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "node_modules",
                    ".venv",
                    "dist",
                    "build",
                    ".pytest_cache",
                    ".ruff_cache",
                    "scratch",
                    "lookml",
                    "2_project_setup.md",
                ),
            )

        explore_path = opts.explore_path or f"{opts.lookml_model_name}/{opts.lookml_model_name}"
        prefix = sanitize_brand_theme_prefix(opts.brand_name)
        theme_light = opts.theme_light or f"{prefix}_Light"
        theme_dark = opts.theme_dark or f"{prefix}_Dark"

        root_env_content = f"""# Looker Embed Demo Environment
LOOKER_INSTANCE_URL={opts.looker_instance_url}
VITE_LOOKER_INSTANCE_URL={opts.looker_instance_url}
LOOKERSDK_BASE_URL={opts.looker_instance_url}
LOOKERSDK_CLIENT_ID={opts.client_id}
LOOKERSDK_CLIENT_SECRET={opts.client_secret}
LOOKERSDK_VERIFY_SSL=true
LOOKER_PROJECT_NAME={opts.looker_project_name}
LOOKER_CONNECTION_NAME={opts.connection_name}
LOOKER_EMBED_DOMAIN={opts.embed_domain}
DEFAULT_LOOKER_GROUP_IDS=["{opts.group_id}"]
VITE_DASHBOARD_ID={opts.dashboard_id}
VITE_CHAT_AGENT_ID={opts.agent_id}
VITE_LOOKER_FOLDER_ID={opts.folder_id}
VITE_EXPLORE_PATH={explore_path}
VITE_THEME={theme_light}
VITE_THEME_LIGHT={theme_light}
VITE_THEME_DARK={theme_dark}
VITE_APP_TITLE={opts.brand_title}
VITE_BRAND_NAME={opts.brand_name}
VITE_PRIMARY_COLOR={opts.primary_color}
VITE_ACCENT_COLOR={opts.accent_color}
"""
        backend_env_content = f"""# Looker Embed Portal Backend Environment (Headless API Service Account)
LOOKERSDK_BASE_URL={opts.looker_instance_url}
LOOKERSDK_CLIENT_ID={opts.client_id}
LOOKERSDK_CLIENT_SECRET={opts.client_secret}
LOOKERSDK_VERIFY_SSL=true
LOOKER_INSTANCE_URL={opts.looker_instance_url}
LOOKER_PROJECT_NAME={opts.looker_project_name}
LOOKER_CONNECTION_NAME={opts.connection_name}
LOOKER_EMBED_DOMAIN={opts.embed_domain}
DEFAULT_LOOKER_GROUP_IDS=["{opts.group_id}"]
"""
        frontend_env_content = f"""# Looker Embed Portal Frontend Environment
LOOKER_INSTANCE_URL={opts.looker_instance_url}
VITE_LOOKER_INSTANCE_URL={opts.looker_instance_url}
LOOKER_PROJECT_NAME={opts.looker_project_name}
LOOKER_CONNECTION_NAME={opts.connection_name}
LOOKER_EMBED_DOMAIN={opts.embed_domain}
VITE_DASHBOARD_ID={opts.dashboard_id}
VITE_CHAT_AGENT_ID={opts.agent_id}
VITE_LOOKER_FOLDER_ID={opts.folder_id}
VITE_EXPLORE_PATH={explore_path}
VITE_THEME={theme_light}
VITE_THEME_LIGHT={theme_light}
VITE_THEME_DARK={theme_dark}
VITE_APP_TITLE={opts.brand_title}
VITE_BRAND_NAME={opts.brand_name}
VITE_PRIMARY_COLOR={opts.primary_color}
VITE_ACCENT_COLOR={opts.accent_color}
"""
        ensure_gitignore(target_dir)
        (target_dir / ".env").write_text(root_env_content, encoding="utf-8")

        backend_dir = target_dir / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        ensure_gitignore(backend_dir)
        (backend_dir / ".env").write_text(backend_env_content, encoding="utf-8")

        if (target_dir / "frontend").exists():
            ensure_gitignore(target_dir / "frontend")
            (target_dir / "frontend" / ".env").write_text(frontend_env_content, encoding="utf-8")

        cls.hydrate_workspace_files(target_dir, opts)

        print_success(f"Embed demo workspace scaffolded at `{target_dir}`.")
        return target_dir
