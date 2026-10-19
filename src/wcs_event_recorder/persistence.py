"""Explicit analysis commit boundaries and optional database-to-JSONL export."""

import logging
from contextlib import ExitStack
from pathlib import Path

from .aggregation import SongPlayAggregator
from .codecs import json_text
from .models import RecognitionStatus
from .resolution import TrackResolver
from .run_output import RunOutputError
from .run_store import RunStore
from .storage import Database, SQLiteTrackCatalog, StorageError

logger = logging.getLogger(__name__)


def persist_results(results, store: RunStore, *, on_play=None):
    resolver = TrackResolver(SQLiteTrackCatalog(store.db), store.resolution_policy)
    aggregator = SongPlayAggregator(store.source, store.config, store.run_id, store.policy)
    iterator = iter(results)
    try:
        with ExitStack() as cleanup:
            close = getattr(iterator, "close", None)
            if close is not None:
                cleanup.callback(close)
            for result in iterator:
                store.result(result)
                resolution = store.resolve(result, resolver) if result.status == RecognitionStatus.MATCH else None
                for play in aggregator.accept(result, resolution):
                    store.play(play)
                    if on_play is not None:
                        on_play(play, resolver.catalog.get(play.track_id))
            for play in aggregator.finish():
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
