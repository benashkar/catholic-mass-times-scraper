"""Tests for the bulletin routes."""

import pytest

pytestmark = pytest.mark.requires_db


class TestBulletinIndex:
    def test_returns_200(self, client):
        response = client.get("/bulletin/")
        assert response.status_code == 200

    def test_lists_states_with_bulletins(self, client):
        response = client.get("/bulletin/")
        assert b"Ohio" in response.data


class TestBulletinStateView:
    def test_returns_200(self, client):
        response = client.get("/bulletin/ohio/")
        assert response.status_code == 200

    def test_contains_church_names(self, client):
        response = client.get("/bulletin/ohio/")
        assert b"St. Mary" in response.data

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/bulletin/nonexistent/")
        assert response.status_code == 404


class TestBulletinAPI:
    def test_api_returns_json(self, client):
        response = client.get("/bulletin/ohio/api/names")
        assert response.status_code == 200
        data = response.get_json()
        assert "draw" in data
        assert "recordsTotal" in data
        assert "recordsFiltered" in data
        assert "data" in data

    def test_api_records_total(self, client):
        response = client.get("/bulletin/ohio/api/names")
        data = response.get_json()
        assert data["recordsTotal"] == 3

    def test_api_pagination(self, client):
        response = client.get("/bulletin/ohio/api/names?start=0&length=2")
        data = response.get_json()
        assert len(data["data"]) == 2

    def test_api_search(self, client):
        response = client.get("/bulletin/ohio/api/names?search[value]=John")
        data = response.get_json()
        assert data["recordsFiltered"] >= 1

    def test_api_church_filter(self, client):
        response = client.get("/bulletin/ohio/api/names?church=St.+Mary")
        data = response.get_json()
        assert data["recordsFiltered"] == 2

    def test_api_nonexistent_state_returns_404(self, client):
        response = client.get("/bulletin/nonexistent/api/names")
        assert response.status_code == 404
