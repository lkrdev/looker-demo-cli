"""Unit tests for DataplexCatalogClient."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
import responses

from looker_demo_cli.exceptions import AuthError
from looker_demo_cli.utils.catalog_client import DataplexCatalogClient


@pytest.fixture
def mock_credentials() -> MagicMock:
    creds = MagicMock()
    creds.valid = True
    creds.token = "mock-bearer-token"
    return creds


@pytest.fixture
def catalog_client(mock_credentials: MagicMock) -> DataplexCatalogClient:
    return DataplexCatalogClient(
        project_id="test-project",
        location="us",
        credentials=mock_credentials,
        timeout=5.0,
    )


@pytest.mark.unit
@responses.activate
def test_lookup_entry_success(catalog_client: DataplexCatalogClient) -> None:
    expected_url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us"
        "/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-project"
        "/datasets/test_dataset/tables/orders"
    )
    mock_payload = {
        "name": "projects/test-project/locations/us/entryGroups/@bigquery/entries/test_entry",
        "entryType": "projects/test-project/locations/us/entryTypes/bigquery-table",
        "aspects": {
            "schema": {"data": {"fields": [{"name": "id", "type": "INT64"}]}},
        },
    }
    responses.add(
        responses.GET,
        expected_url,
        json=mock_payload,
        status=200,
    )

    result = catalog_client.lookup_entry(dataset_id="test_dataset", table_id="orders", view="ALL")

    assert result["name"] == mock_payload["name"]
    assert "schema" in result["aspects"]
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer mock-bearer-token"
    assert "view=ALL" in str(call.request.url)


@pytest.mark.unit
@responses.activate
def test_lookup_entry_links_with_pagination(catalog_client: DataplexCatalogClient) -> None:
    base_url = "https://dataplex.googleapis.com/v1/projects/test-project/locations/us:lookupEntryLinks"
    entry_name = "projects/test-project/locations/us/entryGroups/@bigquery/entries/test_entry"

    # Page 1
    responses.add(
        responses.GET,
        base_url,
        json={
            "entryLinks": [{"name": "link1"}, {"name": "link2"}],
            "nextPageToken": "page-2-token",
        },
        status=200,
    )
    # Page 2
    responses.add(
        responses.GET,
        base_url,
        json={
            "entryLinks": [{"name": "link3"}],
        },
        status=200,
    )

    links = catalog_client.lookup_entry_links(
        entry_name=entry_name,
        entry_link_type="SYNONYM",
        page_size=2,
    )

    assert len(links) == 3
    assert [lnk["name"] for lnk in links] == ["link1", "link2", "link3"]
    assert len(responses.calls) == 2
    url_1 = str(responses.calls[1].request.url)
    url_0 = str(responses.calls[0].request.url)
    assert "pageToken=page-2-token" in url_1
    assert "entryLinkType=SYNONYM" in url_0


@pytest.mark.unit
@responses.activate
def test_get_glossary_term_success(catalog_client: DataplexCatalogClient) -> None:
    expected_url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us/glossaries/retail_glossary/terms/revenue"
    )
    mock_term = {
        "name": "projects/test-project/locations/us/glossaries/retail_glossary/terms/revenue",
        "displayName": "Gross Revenue",
        "description": "Total monetary volume generated prior to returns.",
    }
    responses.add(
        responses.GET,
        expected_url,
        json=mock_term,
        status=200,
    )

    term = catalog_client.get_glossary_term(glossary_id="retail_glossary", term_id="revenue")
    assert term["displayName"] == "Gross Revenue"
    assert term["description"] == mock_term["description"]


@pytest.mark.unit
@responses.activate
def test_lookup_context_success(catalog_client: DataplexCatalogClient) -> None:
    expected_url = "https://dataplex.googleapis.com/v1/projects/test-project/locations/us:lookupContext"
    resources = ["//bigquery.googleapis.com/projects/test-project/datasets/analytics/tables/users"]
    mock_context = {
        "contexts": [
            {
                "resource": resources[0],
                "content": '{"summary": "User dimension table"}',
            }
        ]
    }
    responses.add(
        responses.POST,
        expected_url,
        json=mock_context,
        status=200,
    )

    context = catalog_client.lookup_context(resources=resources, format="JSON")
    assert "contexts" in context
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer mock-bearer-token"
    body = call.request.body
    body_str = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    assert "//bigquery.googleapis.com/projects/test-project/datasets/analytics/tables/users" in body_str


@pytest.mark.unit
@responses.activate
def test_401_raises_auth_error(catalog_client: DataplexCatalogClient) -> None:
    expected_url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us"
        "/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-project"
        "/datasets/test_dataset/tables/orders"
    )
    responses.add(
        responses.GET,
        expected_url,
        json={"error": {"message": "Request had invalid authentication credentials."}},
        status=401,
    )

    with pytest.raises(AuthError) as exc_info:
        catalog_client.lookup_entry("test_dataset", "orders")

    assert exc_info.value.exit_code == 3
    assert "remediation" in exc_info.value.to_dict()


@pytest.mark.unit
@responses.activate
def test_403_and_404_graceful_handling(catalog_client: DataplexCatalogClient) -> None:
    entry_url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us"
        "/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-project"
        "/datasets/test_dataset/tables/missing"
    )
    # 404 returns empty dict
    responses.add(
        responses.GET,
        entry_url,
        json={"error": {"message": "Entry not found"}},
        status=404,
    )
    res_404 = catalog_client.lookup_entry("test_dataset", "missing")
    assert res_404 == {}

    # 403 returns empty dict
    forbidden_url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us"
        "/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-project"
        "/datasets/test_dataset/tables/forbidden"
    )
    responses.add(
        responses.GET,
        forbidden_url,
        json={"error": {"message": "Permission denied"}},
        status=403,
    )
    res_403 = catalog_client.lookup_entry("test_dataset", "forbidden")
    assert res_403 == {}

    # 403 on links returns empty list
    links_url = "https://dataplex.googleapis.com/v1/projects/test-project/locations/us:lookupEntryLinks"
    responses.add(
        responses.GET,
        links_url,
        json={"error": {"message": "Permission denied"}},
        status=403,
    )
    links_403 = catalog_client.lookup_entry_links("test_entry")
    assert links_403 == []


@pytest.mark.unit
@responses.activate
def test_api_disabled_graceful_handling(catalog_client: DataplexCatalogClient) -> None:
    url = (
        "https://dataplex.googleapis.com/v1/projects/test-project/locations/us"
        "/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-project"
        "/datasets/test_dataset/tables/table1"
    )
    responses.add(
        responses.GET,
        url,
        json={
            "error": {
                "code": 403,
                "message": "Dataplex API has not been used in project test-project before or it is disabled.",
                "status": "PERMISSION_DENIED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                        "domain": "googleapis.com",
                    }
                ],
            }
        },
        status=403,
    )

    res = catalog_client.lookup_entry("test_dataset", "table1")
    assert res == {}
    assert catalog_client.api_available is False
    assert "disabled" in catalog_client.unavailable_reason.lower()


@pytest.mark.unit
def test_network_exception_returns_default(catalog_client: DataplexCatalogClient) -> None:
    # Set an unroutable port or force connection error via mock session
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.ConnectionError("Connection refused")
    catalog_client.session = mock_session

    res = catalog_client.lookup_entry("test_dataset", "orders")
    assert res == {}
    assert catalog_client.api_available is False


@pytest.mark.unit
def test_credentials_token_string_and_refresh() -> None:
    # 1. Plain string credentials
    client_str = DataplexCatalogClient(
        project_id="test-project",
        credentials="direct-token-string",
    )
    assert client_str._get_token() == "direct-token-string"

    # 2. Expired credentials triggering refresh
    creds = MagicMock()
    creds.valid = False
    creds.token = None

    def refresh_side_effect(request: object) -> None:
        creds.valid = True
        creds.token = "refreshed-token"

    creds.refresh.side_effect = refresh_side_effect

    client_refresh = DataplexCatalogClient(
        project_id="test-project",
        credentials=creds,
    )
    assert client_refresh._get_token() == "refreshed-token"
    assert creds.refresh.called


@pytest.mark.unit
def test_context_manager() -> None:
    mock_session = MagicMock()
    with DataplexCatalogClient("p", session=mock_session) as client:
        assert client.project_id == "p"
    mock_session.close.assert_called_once()
