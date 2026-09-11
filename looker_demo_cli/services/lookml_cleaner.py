from __future__ import annotations

from typing import Any

import requests

from looker_demo_cli.errors import RemoteApiError, looker_not_authenticated
from looker_demo_cli.services.ge_service import get_looker_auth_context
from looker_demo_cli.utils.console import print_info, print_success, print_warning


def find_root_duplicate_files(project_id: str, headers: dict[str, str], base_url: str) -> list[str]:
    """Inspect Looker project files and return duplicate files sitting in the project root.

    Identifies root files (e.g. ``users.view.lkml``) that also exist in
    structured subfolders (``views/users.view.lkml``,
    ``models/marketing.model.lkml``, etc.).

    Args:
        project_id: Looker project to audit.
        headers: Authenticated request headers.
        base_url: Looker instance base URL.

    Returns:
        The root-level paths that are duplicated under ``views/``, ``models/``
        or ``dashboards/``.

    Raises:
        RemoteApiError: The project files could not be listed. This is raised
            rather than returning ``[]`` because the two outcomes are otherwise
            indistinguishable, and the caller would report a failed audit as a
            clean project.
    """
    clean_url = base_url.rstrip("/")
    endpoint = f"{clean_url}/api/4.0/projects/{project_id}/files"
    try:
        r = requests.get(endpoint, headers=headers, timeout=15)
    except Exception as exc:
        raise RemoteApiError(
            f"Could not query project files for `{project_id}`: {exc}",
            remediation="Check network reachability to the Looker instance and retry.",
            details={"project_id": project_id, "endpoint": endpoint},
        ) from exc

    if r.status_code != 200:
        raise RemoteApiError(
            f"Could not fetch project files for `{project_id}` (HTTP {r.status_code}).",
            status_code=r.status_code,
            remediation="Confirm the project exists and the credentials have `develop` permission.",
            details={"project_id": project_id, "response": r.text[:200]},
        )

    files = r.json()

    all_paths = set()
    for f in files:
        p = f.get("path") or f.get("id")
        if p:
            all_paths.add(p)

    duplicates: list[str] = []
    for p in sorted(all_paths):
        if "/" not in p and (p.endswith(".view.lkml") or p.endswith(".model.lkml") or p.endswith(".dashboard.lookml")):
            sub_view = f"views/{p}"
            sub_model = f"models/{p}"
            sub_dash = f"dashboards/{p}"
            if sub_view in all_paths or sub_model in all_paths or sub_dash in all_paths:
                duplicates.append(p)

    return duplicates


def clean_root_duplicate_files(
    project_id: str,
    headers: dict[str, str] | None = None,
    base_url: str | None = None,
    preferred_account: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Find and delete root-level duplicates that have counterparts in views/ or models/.

    This fixes and prevents permanent master branch desync caused by push fallbacks.

    Args:
        project_id: Looker project to clean.
        headers: Authenticated request headers. Resolved automatically if omitted.
        base_url: Looker instance base URL. Resolved automatically if omitted.
        preferred_account: Saved OAuth account alias used when resolving credentials.
        dry_run: Report the duplicates without deleting them.

    Returns:
        A report dict with ``status`` (``SUCCESS``/``PARTIAL``/``DRY_RUN``) and
        ``cleaned_files``.

    Raises:
        AuthError: No usable Looker credentials.
        RemoteApiError: The project files could not be listed.
    """
    if not headers or not base_url:
        headers, base_url = get_looker_auth_context(instance_url=base_url, preferred_account=preferred_account)

    if not base_url or not headers:
        raise looker_not_authenticated()

    duplicates = find_root_duplicate_files(project_id=project_id, headers=headers, base_url=base_url)
    if not duplicates:
        print_info(f"Root duplicate audit passed: 0 orphaned files found in project `{project_id}`.")
        return {
            "status": "SUCCESS",
            "cleaned_files": [],
            "message": f"No duplicate root files found in project `{project_id}`.",
        }

    print_warning(f"Detected {len(duplicates)} duplicate root orphan(s) in `{project_id}`: {duplicates}")

    if dry_run:
        return {
            "status": "DRY_RUN",
            "cleaned_files": duplicates,
            "message": f"Found {len(duplicates)} duplicate root file(s) (Dry Run).",
        }

    cleaned: list[str] = []
    clean_url = base_url.rstrip("/")
    for p in duplicates:
        del_endpoint = f"{clean_url}/api/4.0/projects/{project_id}/files"
        try:
            r = requests.delete(del_endpoint, params={"file_path": p}, headers=headers, timeout=12)
            if r.status_code in (200, 204):
                cleaned.append(p)
                print_success(f"Deleted remote duplicate root orphan: `{p}`")
            else:
                # Fallback: try endpoint with path in URL
                r2 = requests.delete(
                    f"{clean_url}/api/4.0/projects/{project_id}/files/{p}", headers=headers, timeout=12
                )
                if r2.status_code in (200, 204):
                    cleaned.append(p)
                    print_success(f"Deleted remote duplicate root orphan: `{p}`")
                else:
                    print_warning(
                        f"Could not delete `{p}` via API ({r.status_code} / {r2.status_code}): {r.text[:150]}"
                    )
        except Exception as e:
            print_warning(f"Error while attempting to delete `{p}`: {e}")

    return {
        "status": "SUCCESS" if len(cleaned) == len(duplicates) else "PARTIAL",
        "cleaned_files": cleaned,
        "total_detected": len(duplicates),
        "total_deleted": len(cleaned),
    }
