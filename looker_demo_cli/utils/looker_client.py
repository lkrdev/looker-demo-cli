# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import looker_sdk
from looker_sdk import models40
from lkr.extended_sdk_methods import ExtendedLooker40SDK, FileContent, ProjectCommitRequest

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
)
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class LookerDeployHelper:
    def __init__(
        self,
        base_url: str = DEFAULT_LOOKER_INSTANCE_URL,
        client_id: str = DEFAULT_LOOKER_CLIENT_ID,
        client_secret: str = DEFAULT_LOOKER_CLIENT_SECRET,
    ):
        os.environ["LOOKERSDK_BASE_URL"] = base_url
        os.environ["LOOKERSDK_CLIENT_ID"] = client_id
        os.environ["LOOKERSDK_CLIENT_SECRET"] = client_secret
        os.environ["LOOKERSDK_VERIFY_SSL"] = "true"

        self.std_sdk = looker_sdk.init40()
        self.ext_sdk = ExtendedLooker40SDK(
            auth=self.std_sdk.auth,
            deserialize=self.std_sdk.deserialize,
            serialize=self.std_sdk.serialize,
            transport=self.std_sdk.transport,
            api_version="4.0",
        )

    def set_dev_mode(self) -> str:
        self.ext_sdk.update_session(models40.WriteApiSession(workspace_id="dev"))
        return self.ext_sdk.session().workspace_id or "dev"

    def ensure_project(self, project_id: str) -> None:
        """Create project and configure bare Git if not already registered."""
        self.set_dev_mode()
        projects = [p.name for p in (self.std_sdk.all_projects() or [])]
        if project_id not in projects:
            self.std_sdk.create_project(models40.WriteProject(name=project_id))
            try:
                self.std_sdk.update_project(project_id, models40.WriteProject(git_remote_url=None, git_service_name="bare"))
            except Exception:
                pass

    def ensure_model_configuration(self, model_name: str, project_id: str, connection_name: str) -> None:
        """Register LookML model configuration linked to target connection."""
        models = [m.name for m in (self.std_sdk.all_lookml_models() or [])]
        if model_name not in models:
            self.std_sdk.create_lookml_model(
                models40.WriteLookmlModel(
                    name=model_name,
                    project_name=project_id,
                    allowed_db_connection_names=[connection_name],
                    unlimited_db_connections=False,
                )
            )

    def upload_lookml_directory(self, project_id: str, local_lookml_dir: Path) -> List[str]:
        """Upload all LookML view, model, and dashboard files to Looker dev workspace."""
        self.set_dev_mode()
        uploaded_files = []

        for root, _, files in os.walk(local_lookml_dir):
            for f in sorted(files):
                if f.endswith((".lkml", ".lookml", ".json", ".md")):
                    full_p = Path(root) / f
                    rel_p = full_p.relative_to(local_lookml_dir).as_posix()
                    content = full_p.read_text(encoding="utf-8")
                    fc = FileContent(path=rel_p, content=content)

                    # Ensure parent directories
                    parts = rel_p.split("/")[:-1]
                    cur = ""
                    for p in parts:
                        cur = f"{cur}/{p}" if cur else p
                        try:
                            self.ext_sdk.create_project_directory(project_id=project_id, directory_path=cur)
                        except Exception:
                            pass

                    try:
                        self.ext_sdk.create_file(project_id=project_id, file_content=fc)
                    except Exception:
                        try:
                            self.ext_sdk.update_file(project_id=project_id, file_content=fc)
                        except Exception as e:
                            print_warning(f"Notice on file `{rel_p}`: {e}")

                    uploaded_files.append(rel_p)

        return uploaded_files

    def validate_and_deploy(self, project_id: str, commit_message: str = "Deploy from demo-create CLI") -> Dict[str, Any]:
        """Validate LookML, commit on dev branch, and deploy to production."""
        self.set_dev_mode()
        val = self.ext_sdk.validate_project(project_id=project_id)
        errors = [e.message for e in (val.errors or [])] if hasattr(val, "errors") and val.errors else []

        commit_res = self.ext_sdk.commit(project_id=project_id, body=ProjectCommitRequest(message=commit_message))
        deploy_res = self.ext_sdk.post(path=f"/projects/{project_id}/deploy_to_production", structure=dict, body={})

        return {
            "validation_errors": errors,
            "commit": commit_res,
            "deploy": deploy_res,
        }
