"""Tests for the main routes (homepage and health endpoint)."""

import pytest


class TestHomePage:
    def test_homepage_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    @pytest.mark.requires_db
    def test_homepage_contains_state_name(self, client):
        response = client.get("/")
        assert b"Ohio" in response.data

    @pytest.mark.requires_db
    def test_homepage_shows_church_count(self, client):
        response = client.get("/")
        # We have 3 churches in our fixture data
        assert b"3" in response.data


class TestHealthEndpoint:
    def test_health_reports_honestly_about_the_database(self, client):
        """/health answers ok/200 when db99 is reachable, faulted/503 when not.

        This used to assert a flat 200 with status "ok". That encoded the bug:
        the endpoint returned 200 while reporting the database was unreachable,
        so every monitor that reads the status code saw a dead database as
        healthy. CI has no AWS credentials, cannot fetch the db99 secret, and so
        exercises exactly the failure path -- it was asserting 200 on a service
        that could not reach its database at all.

        Both outcomes are legitimate depending on where this runs, so the test
        pins the PAIRING rather than either code on its own. A body saying
        "faulted" alongside a 200 is the thing that must never happen again.
        """
        response = client.get("/health")
        data = response.get_json()

        assert data["status"] in ("ok", "degraded", "faulted"), data["status"]
        if data["status"] == "faulted":
            assert response.status_code == 503, (
                f"body says faulted but the status code says {response.status_code} -- that "
                "contradiction is the original bug"
            )
        else:
            assert response.status_code == 200
