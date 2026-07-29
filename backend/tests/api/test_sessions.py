from __future__ import annotations

import uuid as uuid_module


async def test_create_session(client, farmer):
    response = await client.post(
        "/api/v1/sessions",
        json={"conf_threshold": 0.62, "device_label": "Barn cam"},
        headers=farmer.headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["batch_id"].startswith("BATCH_")
    assert body["conf_threshold"] == 0.62
    assert body["device_label"] == "Barn cam"
    assert body["is_open"] is True
    assert body["ws_url"] == f"/api/v1/ws/scan/{body['uuid']}"


async def test_create_session_defaults_the_threshold(client, farmer, settings):
    response = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)

    assert response.json()["conf_threshold"] == settings.model.conf_threshold


async def test_second_session_while_one_is_open_is_refused(client, farmer, open_session):
    """Silently opening a second session would strand the first with no totals."""
    response = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "SESSION_ALREADY_OPEN"
    assert body["detail"]["session_uuid"] == open_session["uuid"]


async def test_a_new_session_is_allowed_after_stopping(client, farmer, open_session):
    await client.post(
        f"/api/v1/sessions/{open_session['uuid']}/stop", headers=farmer.headers
    )

    response = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)

    assert response.status_code == 201


async def test_two_farmers_can_scan_at_the_same_time(client, farmer, neighbour, open_session):
    """The concurrency the desktop schema's global unique batch_id would have blocked."""
    response = await client.post("/api/v1/sessions", json={}, headers=neighbour.headers)

    assert response.status_code == 201


async def test_stop_writes_totals_and_summary(client, farmer, open_session):
    response = await client.post(
        f"/api/v1/sessions/{open_session['uuid']}/stop", headers=farmer.headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["is_open"] is False
    assert body["session"]["end_time"] is not None
    assert body["summary"]["total"] == 0


async def test_stopping_twice_is_a_conflict(client, farmer, open_session):
    await client.post(f"/api/v1/sessions/{open_session['uuid']}/stop", headers=farmer.headers)

    response = await client.post(
        f"/api/v1/sessions/{open_session['uuid']}/stop", headers=farmer.headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_CLOSED"


async def test_get_session_returns_detail(client, farmer, open_session):
    response = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.json()["session"]["batch_id"] == open_session["batch_id"]


async def test_unknown_session_is_404(client, farmer):
    response = await client.get(
        f"/api/v1/sessions/{uuid_module.uuid4()}", headers=farmer.headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_history_lists_only_your_own(client, farmer, neighbour):
    await client.post("/api/v1/sessions", json={}, headers=farmer.headers)
    await client.post("/api/v1/sessions", json={}, headers=neighbour.headers)

    response = await client.get("/api/v1/sessions", headers=farmer.headers)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


async def test_history_paginates(client, farmer):
    for _ in range(5):
        created = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)
        await client.post(
            f"/api/v1/sessions/{created.json()['uuid']}/stop", headers=farmer.headers
        )

    first = await client.get("/api/v1/sessions?limit=2", headers=farmer.headers)
    body = first.json()

    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    second = await client.get(
        f"/api/v1/sessions?limit=2&cursor={body['next_cursor']}", headers=farmer.headers
    )

    assert len(second.json()["items"]) == 2
    assert second.json()["items"][0]["uuid"] != body["items"][0]["uuid"]


async def test_last_page_has_no_cursor(client, farmer, open_session):
    response = await client.get("/api/v1/sessions?limit=10", headers=farmer.headers)

    assert response.json()["next_cursor"] is None


async def test_page_size_is_capped(client, farmer):
    response = await client.get("/api/v1/sessions?limit=5000", headers=farmer.headers)

    assert response.status_code == 422


async def test_delete_removes_the_session(client, farmer, open_session):
    response = await client.delete(
        f"/api/v1/sessions/{open_session['uuid']}", headers=farmer.headers
    )

    assert response.status_code == 204

    follow_up = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}", headers=farmer.headers
    )
    assert follow_up.status_code == 404


async def test_inspections_listing_is_empty_for_a_new_session(client, farmer, open_session):
    response = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}/inspections", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
