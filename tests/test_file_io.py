"""Tests for src/utils/file_io.py — CSV and JSON round-trip I/O."""

from src.utils.file_io import load_from_csv, load_from_json, save_to_csv, save_to_json


class TestCsvRoundTrip:
    def test_save_and_load(self, tmp_path):
        data = [
            {"name": "St. Mary", "city": "Columbus"},
            {"name": "St. Joseph", "city": "Cleveland"},
        ]
        path = tmp_path / "churches.csv"
        save_to_csv(data, path)
        loaded = load_from_csv(path)
        assert len(loaded) == 2
        assert loaded[0]["name"] == "St. Mary"
        assert loaded[1]["city"] == "Cleveland"

    def test_special_characters_preserved(self, tmp_path):
        data = [{"name": "St. Thérèse", "city": "São Paulo"}]
        path = tmp_path / "special.csv"
        save_to_csv(data, path)
        loaded = load_from_csv(path)
        assert loaded[0]["name"] == "St. Thérèse"
        assert loaded[0]["city"] == "São Paulo"

    def test_missing_file_returns_empty_list(self, tmp_path):
        result = load_from_csv(tmp_path / "nonexistent.csv")
        assert result == []

    def test_empty_data_does_not_write(self, tmp_path):
        path = tmp_path / "empty.csv"
        save_to_csv([], path)
        assert not path.exists()


class TestJsonRoundTrip:
    def test_save_and_load_dict(self, tmp_path):
        data = {"church": "St. Mary", "masses": [1, 2, 3]}
        path = tmp_path / "church.json"
        save_to_json(data, path)
        loaded = load_from_json(path)
        assert loaded["church"] == "St. Mary"
        assert loaded["masses"] == [1, 2, 3]

    def test_save_and_load_list(self, tmp_path):
        data = [{"name": "A"}, {"name": "B"}]
        path = tmp_path / "list.json"
        save_to_json(data, path)
        loaded = load_from_json(path)
        assert len(loaded) == 2

    def test_missing_file_returns_none(self, tmp_path):
        result = load_from_json(tmp_path / "nonexistent.json")
        assert result is None

    def test_special_characters_preserved(self, tmp_path):
        data = {"name": "St. Thérèse de Lisieux"}
        path = tmp_path / "accent.json"
        save_to_json(data, path)
        loaded = load_from_json(path)
        assert loaded["name"] == "St. Thérèse de Lisieux"
