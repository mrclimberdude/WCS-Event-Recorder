"""Explicit analysis commit boundaries and optional database-to-JSONL export."""

import logging
from contextlib import ExitStack
from pathlib import Path

from .aggregation import SongPlayAggregator
from .codecs import json_text
from .models import RecognitionStatus
from .lineage import FrozenTrackCatalog, load_catalog_snapshot, prefix_aware, recovery_aware, stable_track_allocator
from .resolution import TrackResolver
from .run_output import RunOutputError
from .run_store import RunStore
from .storage import Database, SQLiteTrackCatalog, StorageError

logger = logging.getLogger(__name__)


def persist_results(results, store: RunStore, *, on_play=None, on_result=None):
    iterator = iter(results)
    try:
        with ExitStack() as cleanup:
            close = getattr(iterator, "close", None)
            if close is not None:
                cleanup.callback(close)
            catalog = (FrozenTrackCatalog(store.db, store.run_id, load_catalog_snapshot(store.db, store.derivation["catalog_snapshot_id"]))
                       if store.derivation is not None else SQLiteTrackCatalog(store.db))
            resolver = (TrackResolver(catalog, store.resolution_policy, new_track_id=stable_track_allocator(store))
                        if recovery_aware(store) else TrackResolver(catalog, store.resolution_policy))
            aggregator = SongPlayAggregator(store.source, store.config, store.run_id, store.policy, analysis_stage=store.analysis_stage)
            for result in iterator:
                store.result(result)
                if on_result is not None:
                    try:
                        on_result(result)
                    except OSError:
                        # Optional diagnostic output must not undo committed
                        # analysis or replace the original failure.
                        pass
                resolution = store.resolve(result, resolver) if result.status == RecognitionStatus.MATCH else None
                for play in aggregator.accept(result, resolution):
                    store.play(play)
                    if on_play is not None:
                        on_play(play, resolver.catalog.get(play.track_id))
            termination = store.run.configuration.get("aggregation_termination_policy")
            if prefix_aware(store):
                from .saved_recovery import prefix_input_count, validate_saved_analysis
                validate_saved_analysis(store, complete=True)
                finished = aggregator.finish(termination_policy=termination, expected_results=prefix_input_count(store))
            else:
                finished = aggregator.finish(termination_policy=termination) if termination else aggregator.finish()
            for play in finished:
                store.play(play)
                if on_play is not None:
                    on_play(play, resolver.catalog.get(play.track_id))
        store.complete()
    except BaseException as exc:
        try:
            store.stop("interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", str(exc))
        except (StorageError, ValueError) as stop_error:
            logger.error("Could not record terminal status for run %s: %s; completion is unconfirmed", store.run_id, stop_error)
        raise
    return store.summary()


def export_run(database: Path, run_id: str, output: Path):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            store = RunStore(db, run_id)
            try:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("x", encoding="utf-8", newline="\n") as stream:
                    for record in store.records():
                        stream.write(json_text(record) + "\n")
                        stream.flush()
            except (OSError, ValueError, TypeError) as exc:
                raise RunOutputError(f"cannot export run to '{output}': {exc}") from exc
