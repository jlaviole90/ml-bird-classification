"""Unit tests for the eBird sync service helper functions."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from catalog.api.ebird.sync import _fuzzy_match, _get_ebird_week, get_ebird_week_number


class TestGetEBirdWeek:
    def test_jan_1(self):
        assert _get_ebird_week(date(2026, 1, 1)) == 1

    def test_jan_7(self):
        assert _get_ebird_week(date(2026, 1, 7)) == 1

    def test_jan_8(self):
        assert _get_ebird_week(date(2026, 1, 8)) == 2

    def test_dec_31(self):
        week = _get_ebird_week(date(2026, 12, 31))
        assert week == 48

    def test_mid_year(self):
        week = _get_ebird_week(date(2026, 7, 1))
        assert 25 <= week <= 27

    def test_week_clamped_to_48(self):
        assert _get_ebird_week(date(2026, 12, 31)) <= 48

    def test_week_minimum_1(self):
        assert _get_ebird_week(date(2026, 1, 1)) >= 1


class TestGetEBirdWeekNumber:
    def test_with_datetime(self):
        dt = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        week = get_ebird_week_number(dt)
        assert 10 <= week <= 12

    def test_without_argument(self):
        week = get_ebird_week_number()
        assert 1 <= week <= 48


class TestFuzzyMatch:
    def test_exact_match(self):
        candidates = {
            "american robin": {"speciesCode": "amerob"},
            "house sparrow": {"speciesCode": "houspa"},
        }
        result = _fuzzy_match("american robin", candidates)
        assert result is not None
        assert result["speciesCode"] == "amerob"

    def test_close_match(self):
        candidates = {
            "black-footed albatross": {"speciesCode": "bkfal"},
        }
        result = _fuzzy_match("black footed albatross", candidates)
        assert result is not None
        assert result["speciesCode"] == "bkfal"

    def test_no_match_below_threshold(self):
        candidates = {
            "american robin": {"speciesCode": "amerob"},
        }
        result = _fuzzy_match("bald eagle", candidates)
        assert result is None

    def test_picks_best_match(self):
        candidates = {
            "house finch": {"speciesCode": "houfin"},
            "house sparrow": {"speciesCode": "houspa"},
            "house wren": {"speciesCode": "houwre"},
        }
        result = _fuzzy_match("house finch", candidates)
        assert result["speciesCode"] == "houfin"

    def test_empty_candidates(self):
        result = _fuzzy_match("anything", {})
        assert result is None
