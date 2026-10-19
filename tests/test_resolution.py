from dataclasses import replace

import pytest

from wcs_event_recorder.models import ExternalTrackMapping, ResolutionDecision, Track
from wcs_event_recorder.resolution import InMemoryTrackCatalog, ResolutionPolicy, TrackResolver


@pytest.fixture(params=['memory', 'sqlite'])
def catalog_factory(request, tmp_path):
    from contextlib import ExitStack
    from wcs_event_recorder.storage import Database, SQLiteTrackCatalog
    with ExitStack() as stack:
        serial = 0
        def create(tracks=()):
            nonlocal serial
            if request.param == 'memory':
                return InMemoryTrackCatalog(tracks)
            serial += 1
            db = stack.enter_context(Database(tmp_path / f'catalog-{serial}.sqlite3'))
            stack.enter_context(db.transaction())
            catalog = SQLiteTrackCatalog(db)
            for track in tracks:
                catalog.save(track)
            return catalog
        yield create


def test_repeated_candidates_reuse_internal_id_and_preserve_evidence(make_result, catalog_factory):
    catalog = catalog_factory()
    resolver = TrackResolver(catalog)
    first = make_result("  Song A ", mappings=(ExternalTrackMapping("example", "external-a"),))
    second = make_result("song a")
    one, two = resolver.resolve(first), resolver.resolve(second)
    assert one.track.track_id == two.track.track_id
    assert one.track.track_id != "external-a"
    assert two.track.title == "  Song A "
    assert len(catalog) == 1
    assert first.candidate.title == "  Song A "
    assert second.candidate.title == "song a"
    assert one.result_id == first.result_id
    assert two.result_id == second.result_id


@pytest.mark.parametrize("first,second", [("Song\t A", " song   a "), ("Café", "Cafe\u0301"), ("STRASSE", "Straße")])
def test_documented_text_normalization(make_result, first, second, catalog_factory):
    resolver = TrackResolver(catalog_factory())
    assert resolver.resolve(make_result(first)).track.track_id == resolver.resolve(make_result(second)).track.track_id


@pytest.mark.parametrize("variant", ["A (Remix)", "A (Live)", "A (Acoustic)", "A!", "A Radio Edit"])
def test_version_and_punctuation_differences_are_preserved(make_result, variant, catalog_factory):
    resolver = TrackResolver(catalog_factory())
    assert resolver.resolve(make_result("A")).track.track_id != resolver.resolve(make_result(variant)).track.track_id


def test_artist_aliases_are_not_rewritten(make_result, catalog_factory):
    resolver = TrackResolver(catalog_factory())
    first = resolver.resolve(make_result(artist="Artist feat. Guest"))
    second = resolver.resolve(make_result(artist="Artist ft. Guest"))
    assert first.track.track_id != second.track.track_id


def test_new_mappings_replace_snapshot_without_mutating_old_resolution(make_result, catalog_factory):
    resolver = TrackResolver(catalog_factory())
    first = resolver.resolve(make_result())
    mapping = ExternalTrackMapping("example", "id-a")
    second = resolver.resolve(make_result(mappings=(mapping,)))
    third = resolver.resolve(make_result(mappings=(mapping,)))
    assert first.track.external_mappings == ()
    assert second.track.track_id == first.track.track_id
    assert second.track.external_mappings == (mapping,)
    assert third.reason == "known_external_id"
    assert third.track == second.track


def test_known_mapping_disambiguates_metadata(make_result, catalog_factory):
    mapping = ExternalTrackMapping("example", "id-a")
    catalog = catalog_factory([Track("one", "A", "Artist", (mapping,)), Track("two", "A", "Artist")])
    resolution = TrackResolver(catalog).resolve(make_result(mappings=(mapping,)))
    assert resolution.track.track_id == "one"


def test_ambiguous_metadata_remains_unresolved(make_result, catalog_factory):
    catalog = catalog_factory([Track("one", "A", "Artist"), Track("two", "a", "Artist")])
    resolution = TrackResolver(catalog).resolve(make_result())
    assert resolution.decision == ResolutionDecision.UNRESOLVED
    assert resolution.reason == "ambiguous_metadata"
    assert resolution.conflicting_track_ids == ("one", "two")
    assert len(catalog) == 2


@pytest.mark.parametrize("case,reason", [
    ("contradictory", "contradictory_candidate_ids"),
    ("multiple_tracks", "external_ids_reference_multiple_tracks"),
    ("metadata_conflict", "external_id_metadata_conflict"),
    ("service_conflict", "service_id_conflict"),
])
def test_identity_conflicts_do_not_mutate_catalog(make_result, case, reason, catalog_factory):
    a = ExternalTrackMapping("example", "a")
    b = ExternalTrackMapping("other", "b")
    catalog = catalog_factory([Track("one", "A", "Artist", (a,)), Track("two", "B", "Artist", (b,))])
    candidates = {
        "contradictory": make_result(mappings=(a, ExternalTrackMapping("example", "different"))),
        "multiple_tracks": make_result(mappings=(a, b)),
        "metadata_conflict": make_result("B", mappings=(a,)),
        "service_conflict": make_result(mappings=(ExternalTrackMapping("example", "different"),)),
    }
    resolution = TrackResolver(catalog).resolve(candidates[case])
    assert resolution.decision == ResolutionDecision.UNRESOLVED
    assert resolution.track is None
    assert resolution.reason == reason
    assert catalog.get("one").external_mappings == (a,)
    assert len(catalog) == 2


def test_same_external_id_attached_to_multiple_catalog_tracks_is_unresolved(make_result, catalog_factory):
    mapping = ExternalTrackMapping("example", "id-a")
    catalog = catalog_factory([Track("one", "A", "Artist", (mapping,)), Track("two", "B", "Artist", (mapping,))])
    assert TrackResolver(catalog).resolve(make_result(mappings=(mapping,))).reason == "external_ids_reference_multiple_tracks"


def test_shared_catalog_preserves_identity_across_runs(make_result, catalog_factory):
    catalog = catalog_factory()
    one = TrackResolver(catalog).resolve(make_result(run_id="first"))
    two = TrackResolver(catalog).resolve(make_result(run_id="second"))
    independent = TrackResolver(catalog_factory()).resolve(make_result())
    assert one.track.track_id == two.track.track_id
    assert one.track.track_id != independent.track.track_id


def test_catalog_updates_cannot_erase_mappings_or_rewrite_display(catalog_factory):
    original = Track("one", "A", "Artist", (ExternalTrackMapping("example", "a"),))
    catalog = catalog_factory([original])
    for changed in (replace(original, title="B"), replace(original, external_mappings=())):
        with pytest.raises(ValueError, match="preserve"):
            catalog.save(changed)


def test_non_match_cannot_be_resolved(make_result, catalog_factory):
    with pytest.raises(ValueError, match="matched"):
        TrackResolver(catalog_factory()).resolve(make_result(None))


def test_unsupported_policy_is_rejected():
    with pytest.raises(ValueError):
        ResolutionPolicy("unimplemented")
