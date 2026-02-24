"""Tests for the main routes (homepage and health endpoint)."""


class TestHomePage:
    def test_homepage_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_homepage_contains_state_name(self, client):
        response = client.get("/")
        assert b"Ohio" in response.data

    def test_homepage_shows_church_count(self, client):
        response = client.get("/")
        # We have 3 churches in our fixture data
        assert b"3" in response.data


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
