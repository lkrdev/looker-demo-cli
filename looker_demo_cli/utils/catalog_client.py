"""REST client for Google Cloud Dataplex Universal Catalog API.

Handles authentication via Google Application Default Credentials or supplied credentials,
implements table entry lookups, entry links with pagination, glossary terms, and context lookups,
with graceful degradation when Dataplex is disabled or inaccessible.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from looker_demo_cli.exceptions import AuthError

logger = logging.getLogger(__name__)


class DataplexCatalogClient:
    """REST client for interacting with Google Cloud Dataplex Knowledge Catalog."""

    def __init__(
        self,
        project_id: str,
        location: str = "us",
        credentials: Any = None,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.project_id = project_id
        self.location = location.lower() if location else "us"
        self.credentials = credentials
        self.timeout = timeout
        self.last_status_code: int | None = None
        self.api_available: bool = True
        self.unavailable_reason: str = ""

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            try:
                retries = Retry(
                    total=2,
                    backoff_factor=0.3,
                    status_forcelist=[500, 502, 503, 504],
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retries)
                self.session.mount("https://", adapter)
            except Exception:
                pass

        if self.credentials is None:
            try:
                import google.auth

                self.credentials, default_project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                if not self.project_id and default_project:
                    self.project_id = default_project
            except Exception:
                self.credentials = None

    def _get_token(self) -> str:
        """Resolve Bearer token from credentials, refreshing if needed."""
        if self.credentials is None:
            try:
                import google.auth

                self.credentials, default_project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                if not self.project_id and default_project:
                    self.project_id = default_project
            except Exception as e:
                raise AuthError(
                    f"Failed to resolve Google Cloud credentials: {e}",
                    remediation="Run `gcloud auth application-default login`.",
                ) from e

        if isinstance(self.credentials, str):
            return self.credentials

        if hasattr(self.credentials, "refresh") and not getattr(self.credentials, "valid", False):
            try:
                import google.auth.transport.requests

                request = google.auth.transport.requests.Request()
                self.credentials.refresh(request)
            except Exception as e:
                raise AuthError(
                    f"Failed to refresh Google Cloud credentials: {e}",
                    remediation="Run `gcloud auth application-default login`.",
                ) from e

        token = getattr(self.credentials, "token", None)
        if token:
            return str(token)

        if hasattr(self.credentials, "apply"):
            headers: dict[str, str] = {}
            try:
                self.credentials.apply(headers)
                auth_val = headers.get("Authorization", "")
                if auth_val.startswith("Bearer "):
                    return auth_val[7:]
            except Exception:
                pass

        return ""

    def _get_headers(self) -> dict[str, str]:
        """Construct request headers with authorization and JSON content type."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        token = self._get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _is_api_disabled(self, response: requests.Response) -> bool:
        """Check if response indicates that Dataplex API is disabled or not activated."""
        try:
            text = response.text.lower()
            if (
                "service_disabled" in text
                or "has not been used in project" in text
                or "is not enabled" in text
                or "is disabled" in text
                or "access_token_scope_insufficient" in text
            ):
                return True
            data = response.json()
            error = data.get("error", {})
            status = error.get("status")
            if status in ("PERMISSION_DENIED", "FAILED_PRECONDITION"):
                details = error.get("details", [])
                for d in details:
                    if (
                        d.get("reason") in ("SERVICE_DISABLED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
                        or "serviceusage" in str(d).lower()
                    ):
                        return True
        except Exception:
            pass
        return False

    def _process_response(self, response: requests.Response, default: Any) -> Any:
        """Process API response, raising AuthError on 401 or returning default on 403/404/disabled."""
        self.last_status_code = response.status_code

        if response.status_code == 401:
            raise AuthError(
                f"Dataplex authentication failed (HTTP 401): {response.text}",
                remediation="Run `gcloud auth application-default login` to refresh credentials.",
            )

        if response.status_code in (403, 404):
            logger.debug("Dataplex resource unavailable (HTTP %s): %s", response.status_code, response.text)
            if response.status_code == 403:
                if self._is_api_disabled(response):
                    self.api_available = False
                    self.unavailable_reason = "Dataplex API is disabled"
                else:
                    self.unavailable_reason = f"Access forbidden (HTTP 403): {response.text}"
            elif response.status_code == 404:
                self.unavailable_reason = f"Resource not found (HTTP 404): {response.text}"
            return default

        if self._is_api_disabled(response):
            self.api_available = False
            self.unavailable_reason = "Dataplex API is disabled"
            logger.warning("Dataplex API is disabled for project %s", self.project_id)
            return default

        if not response.ok:
            logger.warning("Dataplex request failed with HTTP %s: %s", response.status_code, response.text)
            return default

        try:
            return response.json()
        except Exception as e:
            logger.warning("Failed to parse Dataplex JSON: %s", e)
            return default

    def _get_json(self, url: str, params: dict[str, Any] | None, default: Any) -> Any:
        """Perform a GET request and parse JSON safely."""
        try:
            headers = self._get_headers()
            response = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
            return self._process_response(response, default)
        except AuthError:
            raise
        except requests.RequestException as e:
            logger.warning("Dataplex GET request error for %s: %s", url, e)
            self.api_available = False
            self.unavailable_reason = str(e)
            return default

    def _post_json(self, url: str, json_data: dict[str, Any], default: Any) -> Any:
        """Perform a POST request and parse JSON safely."""
        try:
            headers = self._get_headers()
            response = self.session.post(url, headers=headers, json=json_data, timeout=self.timeout)
            return self._process_response(response, default)
        except AuthError:
            raise
        except requests.RequestException as e:
            logger.warning("Dataplex POST request error for %s: %s", url, e)
            self.api_available = False
            self.unavailable_reason = str(e)
            return default

    def lookup_entry(self, dataset_id: str, table_id: str, view: str = "ALL") -> dict[str, Any]:
        """Lookup Dataplex entry for a BigQuery table.

        Calls GET https://dataplex.googleapis.com/v1/projects/{project}/locations/{location}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project}/datasets/{dataset}/tables/{table}?view={view}
        """
        url = (
            f"https://dataplex.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}"
            f"/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{self.project_id}"
            f"/datasets/{dataset_id}/tables/{table_id}"
        )
        return self._get_json(url, params={"view": view}, default={})

    def lookup_entry_links(
        self,
        entry_name: str,
        entry_link_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Lookup linked entries and aspects for an entry with pagination support.

        Calls GET https://dataplex.googleapis.com/v1/projects/{project}/locations/{location}:lookupEntryLinks?entry={entry_name}
        """
        url = (
            f"https://dataplex.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}:lookupEntryLinks"
        )
        params: dict[str, Any] = {
            "entry": entry_name,
            "pageSize": page_size,
        }
        if entry_link_type:
            params["entryLinkType"] = entry_link_type

        all_links: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            req_params = dict(params)
            if page_token:
                req_params["pageToken"] = page_token

            data = self._get_json(url, params=req_params, default=None)
            if data is None:
                # In case of error (403/404/disabled), stop and return collected links (or empty list)
                break

            links = data.get("entryLinks", [])
            if isinstance(links, list):
                all_links.extend(links)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return all_links

    def get_glossary_term(self, glossary_id: str, term_id: str) -> dict[str, Any]:
        """Fetch details for a glossary term in Dataplex.

        Calls GET https://dataplex.googleapis.com/v1/projects/{project}/locations/{location}/glossaries/{glossary_id}/terms/{term_id}
        """
        url = (
            f"https://dataplex.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}"
            f"/glossaries/{glossary_id}/terms/{term_id}"
        )
        return self._get_json(url, params=None, default={})

    def lookup_context(self, resources: list[str], format: str = "JSON") -> dict[str, Any]:
        """Lookup rich context for resources.

        Calls POST https://dataplex.googleapis.com/v1/projects/{project}/locations/{location}:lookupContext
        with body {"resources": resources, "options": {"format": format}}
        """
        url = f"https://dataplex.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}:lookupContext"
        payload = {
            "resources": resources,
            "options": {
                "format": format,
            },
        }
        return self._post_json(url, json_data=payload, default={})

    def close(self) -> None:
        """Close the underlying requests session."""
        self.session.close()

    def __enter__(self) -> DataplexCatalogClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
