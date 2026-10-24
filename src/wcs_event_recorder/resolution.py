"""Conservative catalog identity decisions, independent of occurrence timing."""

import unicodedata
from dataclasses import dataclass, replace
from typing import Iterable, Protocol
from uuid import uuid4

from .models import RecognitionResult, RecognitionStatus, ResolutionDecision, Track, TrackResolution

NORMALIZATION_VERSION = "conservative_metadata_v1"


class TrackCatalog(Protocol):
    def get(self, track_id: str) -> Track: ...
    def find_metadata(self, key: tuple[str, str]) -> set[str]: ...
    def find_mapping(self, service: str, external_id: str) -> set[str]: ...
    def save(self, track: Track) -> None: ...


def metadata_key(title: str, artist: str) -> tuple[str, str]:
    return tuple(" ".join(unicodedata.normalize("NFC", value).split()).casefold() for value in (title, artist))


@dataclass(frozen=True)
class ResolutionPolicy:
    version: str = "conservative_metadata_v1"

    def __post_init__(self) -> None:
        if self.version != "conservative_metadata_v1":
            raise ValueError("unsupported track resolution policy")


class InMemoryTrackCatalog:
    """Retaining this catalog preserves IDs; durable storage belongs to step 6."""

    def __init__(self, tracks: Iterable[Track] = ()):
        self._tracks: dict[str, Track] = {}
        self._metadata: dict[tuple[str, str], set[str]] = {}
        self._external: dict[tuple[str, str], set[str]] = {}
        for track in tracks:
            self.save(track)

    def __len__(self) -> int:
        return len(self._tracks)

    def get(self, track_id: str) -> Track:
        return self._tracks[track_id]

    def find_metadata(self, key: tuple[str, str]) -> set[str]:
        return set(self._metadata.get(key, ()))

    def find_mapping(self, service: str, external_id: str) -> set[str]:
        return set(self._external.get((service, external_id), ()))

    def save(self, track: Track) -> None:
        old = self._tracks.get(track.track_id)
        if old is not None and (
            (old.title, old.artist) != (track.title, track.artist)
            or not set(old.external_mappings) <= set(track.external_mappings)
        ):
            raise ValueError("catalog updates must preserve display metadata and existing mappings")
        self._tracks[track.track_id] = track
        self._metadata.setdefault(metadata_key(track.title, track.artist), set()).add(track.track_id)
        for mapping in track.external_mappings:
            self._external.setdefault((mapping.service, mapping.external_id), set()).add(track.track_id)

    def _restore_track(self, track_id: str, previous: Track | None) -> None:
        """Undo a journaled mutation without rebuilding unrelated lookup indexes."""
        current = self._tracks.get(track_id)
        if previous is None:
            self._tracks.pop(track_id, None)
        if current is not None:
            memberships = [(self._metadata, metadata_key(current.title, current.artist))]
            memberships.extend((self._external, (mapping.service, mapping.external_id))
                               for mapping in current.external_mappings)
            for index, key in memberships:
                members = index.get(key)
                if members is not None:
                    members.discard(track_id)
                    if not members:
                        del index[key]
        if previous is not None:
            self._tracks[track_id] = previous
            self._metadata.setdefault(metadata_key(previous.title, previous.artist), set()).add(track_id)
            for mapping in previous.external_mappings:
                self._external.setdefault((mapping.service, mapping.external_id), set()).add(track_id)


class TrackResolver:
    def __init__(self, catalog: TrackCatalog, policy: ResolutionPolicy = ResolutionPolicy(), *, new_track_id=None):
        self.catalog = catalog
        self.policy = policy
        self.new_track_id = new_track_id or (lambda result: str(uuid4()))

    def resolve(self, result: RecognitionResult) -> TrackResolution:
        if result.status != RecognitionStatus.MATCH:
            raise ValueError("only matched candidates can be resolved")
        candidate = result.candidate
        mappings = tuple(dict.fromkeys(candidate.external_mappings))
        services: dict[str, set[str]] = {}
        known = set()
        for mapping in mappings:
            services.setdefault(mapping.service, set()).add(mapping.external_id)
            known.update(self.catalog.find_mapping(mapping.service, mapping.external_id))

        def unresolved(reason, conflicts=()):
            return TrackResolution(result.result_id, ResolutionDecision.UNRESOLVED, self.policy.version, reason, conflicting_track_ids=tuple(sorted(conflicts)))

        if any(len(ids) > 1 for ids in services.values()):
            return unresolved("contradictory_candidate_ids", known)
        if len(known) > 1:
            return unresolved("external_ids_reference_multiple_tracks", known)
        key = metadata_key(candidate.title, candidate.artist)
        if known:
            target_id = next(iter(known))
            target = self.catalog.get(target_id)
            if metadata_key(target.title, target.artist) != key:
                return unresolved("external_id_metadata_conflict", known)
            reason = "known_external_id"
        else:
            matches = self.catalog.find_metadata(key)
            if len(matches) > 1:
                return unresolved("ambiguous_metadata", matches)
            target = self.catalog.get(next(iter(matches))) if matches else None
            reason = "metadata_match" if target else "new_track"
        if target is not None:
            for mapping in mappings:
                existing = {item.external_id for item in target.external_mappings if item.service == mapping.service}
                if existing and existing != {mapping.external_id}:
                    return unresolved("service_id_conflict", (target.track_id,))
            target = replace(target, external_mappings=tuple(dict.fromkeys((*target.external_mappings, *mappings))))
        else:
            target = Track(self.new_track_id(result), candidate.title, candidate.artist, mappings)
        self.catalog.save(target)
        return TrackResolution(result.result_id, ResolutionDecision.RESOLVED, self.policy.version, reason, target)
