"""Tests for the mass_times routes."""


class TestMassTimesStateView:
    def test_returns_200(self, client):
        response = client.get("/mass-times/ohio/")
        assert response.status_code == 200

    def test_contains_church_names(self, client):
        response = client.get("/mass-times/ohio/")
        assert b"St. Mary" in response.data
        assert b"St. Joseph" in response.data

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/")
        assert response.status_code == 404


class TestMassTimesChurchView:
    def test_by_slug(self, client):
        response = client.get("/mass-times/ohio/church/st-mary-columbus/")
        assert response.status_code == 200
        assert b"St. Mary" in response.data

    def test_by_name_fallback(self, client):
        response = client.get("/mass-times/ohio/church/St. Mary/")
        assert response.status_code == 200

    def test_nonexistent_church_returns_404(self, client):
        response = client.get("/mass-times/ohio/church/nonexistent-slug/")
        assert response.status_code == 404

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/church/st-mary-columbus/")
        assert response.status_code == 404


class TestCalendarView:
    def test_returns_200(self, client):
        response = client.get("/mass-times/ohio/calendar/")
        assert response.status_code == 200

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/calendar/")
        assert response.status_code == 404


class TestCalendarDownload:
    def test_returns_csv(self, client):
        response = client.get("/mass-times/ohio/calendar/download/")
        assert response.status_code == 200
        assert response.content_type == "text/csv; charset=utf-8"

    def test_content_disposition_header(self, client):
        response = client.get("/mass-times/ohio/calendar/download/")
        disposition = response.headers.get("Content-Disposition", "")
        assert "ohio_dated_services.csv" in disposition

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/calendar/download/")
        assert response.status_code == 404
