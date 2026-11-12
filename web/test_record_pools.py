"""Outbound browser links only: synthetic queries, no provider network requests."""
import copy
from urllib.parse import parse_qs, urlsplit

import pytest

from web import record_pools


def request(status="missing", **fields):
    return {"key": "synthetic-request", "needs_review": True, "status": status,
            "request": {"title": "Paper Moons", "artist": "Demo Artist",
                        "version": "Radio Edit", **fields}}


@pytest.mark.parametrize("title", [
    "Paper Moons", "Don't Stop & Go? #1 / + Remix", 'Synthetic "<script>"',
    "Corazón – Démo", "Literal %26 &q=another",
])
def test_search_links_round_trip_the_exact_query_without_parameter_injection(title):
    row = record_pools.acquisition_rows([request(title=title)])[0]
    direct = [provider for provider in row["providers"] if provider["link_mode"] == "search"]
    assert {provider["name"] for provider in direct} == {"DJcity", "Beatport"}
    for provider in direct:
        parsed = urlsplit(provider["url"])
        assert parsed.scheme == "https"
        assert parsed.netloc in {"www.djcity.com", "www.beatport.com"}
        assert parse_qs(parsed.query) == {"q": [f"Demo Artist {title} Radio Edit"]}
        assert not parsed.fragment
        assert provider["checked_on"] == "2026-11-12"


def test_unverified_destinations_remain_homepage_fallbacks_not_invented_searches():
    links = record_pools.provider_links("Demo Artist Paper Moons Radio Edit")
    fallbacks = [provider for provider in links if provider["link_mode"] == "homepage"]
    assert len(links) == 6 and len(fallbacks) == 4
    for provider in fallbacks:
        original = next(item for item in record_pools.PROVIDERS if item["name"] == provider["name"])
        assert provider["url"] == original["url"]
        assert "search" in provider["note"].lower()


def test_link_queries_contain_song_fields_only_and_do_not_mutate_progress():
    source = request()
    source["request"]["metadata"] = {"email": "not-sent@example.invalid",
                                      "first_name": "Not Sent", "path": "/Demo/Private.mp3"}
    source["event_name"] = "Not sent event"
    progress = {"synthetic-request": "acquired"}
    before = copy.deepcopy((source, progress, record_pools.PROVIDERS))
    result = record_pools.acquisition_rows([source], progress)[0]
    assert result["query"] == "Demo Artist Paper Moons Radio Edit"
    assert result["progress"] == "acquired"
    for provider in result["providers"]:
        assert "not-sent" not in provider["url"] and "Private" not in provider["url"]
    assert (source, progress, record_pools.PROVIDERS) == before


def test_only_unresolved_missing_and_unavailable_requests_have_lookup_rows():
    missing, unavailable = request(), request("version_unavailable", artist=None)
    unavailable["key"] = "version"
    matched, ambiguous, blocked, dismissed = (
        request("matched"), request("ambiguous"), request("blocked"), request())
    dismissed["needs_review"] = False
    rows = record_pools.acquisition_rows([missing, unavailable, matched, ambiguous, blocked, dismissed])
    assert len(rows) == 2
    assert rows[1]["query"] == "Paper Moons Radio Edit"
    assert all("fulfilled" not in row for row in rows)


def test_different_requests_get_independent_deterministic_search_links():
    a = record_pools.acquisition_rows([request()])[0]
    b = record_pools.acquisition_rows([request(title="Synthetic Orbit")])[0]
    assert a["providers"] != b["providers"]
    assert a == record_pools.acquisition_rows([request()])[0]
    a["providers"][0]["url"] = "modified locally"
    assert record_pools.PROVIDERS[0]["url"] == "https://www.directmusicservice.com/"
