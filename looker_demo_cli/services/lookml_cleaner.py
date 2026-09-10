from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests

from looker_demo_cli.services.ge_service import get_looker_auth_context
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


def find_root_duplicate_files(project_id: str, headers: Dict[str, str], base_url: str) -> List[str]:
    """Inspect Looker project files and return a list of duplicate files sitting in the project root.
    
    Identifies root files (e.g. 'users.view.lkml') that also exist in structured subfolders
    ('views/users.view.lkml', 'models/marketing.model.lkml', etc.).
    """
    clean_url = base_url.rstrip("/")
    endpoint = f"{clean_url}/api/4.0/projects/{project_id}/files"
    try:
        r = requests.get(endpoint, headers=headers, timeout=15)
        if r.status_code != 200:
            print_warning(f"Could not fetch project files for `{project_id}` (HTTP {r.status_code}): {r.text[:200]}")
            return []
        files = r.json()
    except Exception as e:
        print_warning(f"Could not query project files for `{project_id}`: {e}")
        return []

    all_paths = set()
    for f in files:
        p = f.get("path") or f.get("id")
        if p:
            all_paths.add(p)

    duplicates: List[str] = []
    for p in sorted(list(all_paths)):
        if "/" not in p and (p.endswith(".view.lkml") or p.endswith(".model.lkml") or p.endswith(".dashboard.lookml")):
            sub_view = f"views/{p}"
            sub_model = f"models/{p}"
            sub_dash = f"dashboards/{p}"
            if sub_view in all_paths or sub_model in all_paths or sub_dash in all_paths:
                duplicates.append(p)

    return duplicates


def clean_root_duplicate_files(
    project_id: str,
    headers: Optional[Dict[str, str]] = None,
    base_url: Optional[str] = None,
    preferred_account: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Find and delete root-level duplicate files that have structured counterparts in views/ or models/.
    
    This fixes and prevents permanent master branch desync caused by push fallbacks.
    """
    if not headers or not base_url:
        headers, base_url = get_looker_auth_context(instance_url=base_url, preferred_account=preferred_account)

    if not base_url or not headers:
        return {
            "status": "FAILED",
            "error": "No Looker authentication available. Authenticate with `lkr auth login`.",
            "cleaned_files": [],
        }

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

    cleaned: List[str] = []
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
                r2 = requests.delete(f"{clean_url}/api/4.0/projects/{project_id}/files/{p}", headers=headers, timeout=12)
                if r2.status_code in (200, 204):
                    cleaned.append(p)
                    print_success(f"Deleted remote duplicate root orphan: `{p}`")
                else:
                    print_warning(f"Could not delete `{p}` via API ({r.status_code} / {r2.status_code}): {r.text[:150]}")
        except Exception as e:
            print_warning(f"Error while attempting to delete `{p}`: {e}")

    return {
        "status": "SUCCESS" if len(cleaned) == len(duplicates) else "PARTIAL",
        "cleaned_files": cleaned,
        "total_detected": len(duplicates),
        "total_deleted": len(cleaned),
    }
