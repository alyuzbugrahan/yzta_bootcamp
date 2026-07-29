"""Every endpoint taking an id, checked against both failure modes.

This is the acceptance criterion for Phase 3. It runs as a table rather than as prose so that a
route added later without an ownership check shows up as a missing entry — see
``test_every_id_route_is_covered``.

Two properties are asserted for each route:

* **Unauthenticated** requests get 401, never data.
* **Another farmer's** id gets 404, not 403. A 403 would confirm the id exists, which is enough
  to enumerate how many sessions a competitor is running.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest

# (method, path template, expected status when the caller is not the owner)
ID_ROUTES = [
    ("GET", "/api/v1/sessions/{uuid}", 404),
    ("POST", "/api/v1/sessions/{uuid}/stop", 404),
    ("POST", "/api/v1/sessions/{uuid}/ticket", 404),
    ("DELETE", "/api/v1/sessions/{uuid}", 404),
    ("GET", "/api/v1/sessions/{uuid}/inspections", 404),
    ("GET", "/api/v1/sessions/{uuid}/export.csv", 404),
    ("GET", "/api/v1/sessions/{uuid}/report", 404),
    ("GET", "/api/v1/sessions/{uuid}/report.pdf", 404),
]

UNAUTHENTICATED_ROUTES = [
    ("GET", "/api/v1/me"),
    ("POST", "/api/v1/sessions"),
    ("GET", "/api/v1/sessions"),
    *[(method, path) for method, path, _ in ID_ROUTES],
]


@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_requires_authentication(client, method, path):
    response = await client.request(
        method, path.format(uuid=uuid_module.uuid4()), json={}
    )

    assert response.status_code == 401, f"{method} {path} served an unauthenticated caller"
    assert response.json()["error"]["code"] in {
        "UNAUTHENTICATED",
        "TOKEN_INVALID",
        "TOKEN_EXPIRED",
    }


@pytest.mark.parametrize(("method", "path", "expected"), ID_ROUTES)
async def test_another_farmers_session_is_not_reachable(
    client, farmer, neighbour, open_session, method, path, expected
):
    response = await client.request(
        method, path.format(uuid=open_session["uuid"]), headers=neighbour.headers, json={}
    )

    assert response.status_code == expected, (
        f"{method} {path} exposed another farmer's session"
    )


@pytest.mark.parametrize(("method", "path", "_expected"), ID_ROUTES)
async def test_owner_can_reach_their_own_session(
    client, farmer, open_session, method, path, _expected
):
    """The mirror of the test above — proves the 404s are about ownership, not broken routes."""
    response = await client.request(
        method, path.format(uuid=open_session["uuid"]), headers=farmer.headers, json={}
    )

    assert response.status_code < 400, response.text


async def test_a_neighbour_cannot_delete_another_farmers_session(
    client, farmer, neighbour, open_session
):
    """A 404 on delete must also mean nothing was deleted."""
    await client.delete(
        f"/api/v1/sessions/{open_session['uuid']}", headers=neighbour.headers
    )

    still_there = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}", headers=farmer.headers
    )

    assert still_there.status_code == 200


async def test_a_neighbour_cannot_stop_another_farmers_session(
    client, farmer, neighbour, open_session
):
    await client.post(
        f"/api/v1/sessions/{open_session['uuid']}/stop", headers=neighbour.headers
    )

    detail = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}", headers=farmer.headers
    )

    assert detail.json()["session"]["is_open"] is True, "session was stopped by a stranger"


async def test_unknown_and_forbidden_ids_are_indistinguishable(
    client, neighbour, open_session
):
    """A real-but-not-yours id must respond identically to one that never existed."""
    forbidden = await client.get(
        f"/api/v1/sessions/{open_session['uuid']}", headers=neighbour.headers
    )
    nonexistent = await client.get(
        f"/api/v1/sessions/{uuid_module.uuid4()}", headers=neighbour.headers
    )

    assert forbidden.status_code == nonexistent.status_code
    assert forbidden.json() == nonexistent.json()


def test_every_id_route_is_covered(app):
    """Fails when a route taking a session id is added without an entry in ID_ROUTES.

    Without this the table silently stops being a matrix and becomes a list of the routes
    someone happened to remember.

    Reads the OpenAPI schema rather than ``app.routes``: FastAPI keeps included routers as
    opaque wrapper objects there, so iterating it finds no endpoints at all — a guard that
    quietly checks nothing.
    """
    covered = {(method, path) for method, path, _ in ID_ROUTES}

    declared = {
        (method.upper(), path.replace("{session_uuid}", "{uuid}"))
        for path, operations in app.openapi()["paths"].items()
        if "{session_uuid}" in path
        for method in operations
    }

    assert declared, "no session-id routes discovered — the guard is not actually checking"
    assert declared == covered, (
        f"authorization matrix out of date; uncovered: {sorted(declared - covered)}"
    )
