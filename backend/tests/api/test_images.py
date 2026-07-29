"""Image retrieval, end to end.

Phase 5's acceptance criterion: a full scan session produces retrievable images, and one user
404s on another's image id.
"""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from tests.api.test_ws_scan import (  # reuse the WebSocket harness
    ScriptedDetector,
    jpeg,
    make_client,  # noqa: F401 - fixture
    open_session,
    register,
    ticket_for,
    ws_path,
)


def scan_one_fig(client: TestClient, headers: dict, session_uuid: str) -> dict:
    """Drive a socket until one fig is recorded; returns the inspection message."""
    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(12):
            ws.send_bytes(jpeg())
            time.sleep(0.04)
            while True:
                message = ws.receive_json()
                if message["type"] == "inspection":
                    return message
                if message["type"] in {"frame", "stats"}:
                    break

    raise AssertionError("no fig was recorded")


@pytest.fixture
def scanning_client(make_client, tmp_path):  # noqa: F811
    return make_client(
        ScriptedDetector(),
        ingest={"max_fps": 1000},
        storage={"backend": "local", "root": str(tmp_path / "images")},
    )


def test_a_scanned_fig_produces_a_retrievable_image(scanning_client):
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)

    assert inspection["image_url"] is not None

    response = client.get(inspection["image_url"], headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8"), "not a JPEG"


def test_the_stored_image_is_the_frame_the_model_saw(scanning_client):
    """Archived bytes are the client's original JPEG, not a re-encode.

    Re-encoding would cost CPU on the hot path and store something subtly different from what
    was actually classified — useless as evidence for a disputed reading.
    """
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)
    response = client.get(inspection["image_url"], headers=headers)

    assert response.content == jpeg()


def test_image_appears_in_the_inspection_listing(scanning_client):
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    scan_one_fig(client, headers, session_uuid)

    rows = client.get(
        f"/api/v1/sessions/{session_uuid}/inspections", headers=headers
    ).json()["items"]

    assert rows[0]["image_key"] is not None
    assert rows[0]["image_key"].endswith("_Healthy.jpg")


def test_another_farmer_cannot_fetch_the_image(scanning_client):
    """The acceptance criterion. An inspection image is a photograph of someone's crop."""
    client = scanning_client
    owner = register(client, "owner@example.com")
    intruder = register(client, "intruder@example.com")
    session_uuid = open_session(client, owner)

    inspection = scan_one_fig(client, owner, session_uuid)

    response = client.get(inspection["image_url"], headers=intruder)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_image_requires_authentication(scanning_client):
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)

    assert client.get(inspection["image_url"]).status_code == 401


def test_unknown_inspection_is_404(scanning_client):
    client = scanning_client
    headers = register(client)

    response = client.get("/api/v1/inspections/999999/image", headers=headers)

    assert response.status_code == 404


def test_deleting_a_session_removes_its_images(scanning_client):
    """Otherwise a deleted harvest keeps costing storage, and the objects outlive consent."""
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)
    assert client.get(inspection["image_url"], headers=headers).status_code == 200

    assert client.delete(f"/api/v1/sessions/{session_uuid}", headers=headers).status_code == 204

    assert client.get(inspection["image_url"], headers=headers).status_code == 404


def test_csv_export_links_to_the_image_endpoint(scanning_client):
    client = scanning_client
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)
    client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=headers)

    export = client.get(f"/api/v1/sessions/{session_uuid}/export.csv", headers=headers)

    assert inspection["image_url"] in export.text


def test_archiving_can_be_disabled(make_client):  # noqa: F811
    """The cheapest answer to retention: keep decisions and statistics, store no images."""
    client = make_client(
        ScriptedDetector(), ingest={"max_fps": 1000}, storage={"enabled": False}
    )
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspection = scan_one_fig(client, headers, session_uuid)

    assert inspection["image_url"] is None

    rows = client.get(
        f"/api/v1/sessions/{session_uuid}/inspections", headers=headers
    ).json()["items"]
    assert rows[0]["image_key"] is None
    assert rows[0]["decision"] == "Healthy"
