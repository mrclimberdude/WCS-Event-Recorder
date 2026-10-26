"""Narrow Spotify catalog metadata adapter; no playback or playlist operations."""

import re
from urllib.parse import urlsplit

from .catalog_transport import (CatalogTransport, CatalogTransportError, optional_metadata,
                                region_code, search_inputs, text)


def _track_id(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9]{22}', value):
        raise CatalogTransportError('invalid_response')
    return value


class SpotifyCatalogAdapter(CatalogTransport):
    service = 'spotify'

    def _candidate(self, track, region, requested=None):
        if not isinstance(track, dict) or track.get('type') != 'track':
            raise CatalogTransportError('invalid_response')
        target = _track_id(track.get('id'))
        if (requested is not None and target != requested) or track.get('linked_from') is not None:
            # The v1 candidate contract cannot preserve both requested and relinked IDs.
            raise CatalogTransportError('relinked')
        playable = track.get('is_playable')
        if playable is not None and type(playable) is not bool:
            raise CatalogTransportError('invalid_response')
        if playable is False or track.get('restrictions'):
            raise CatalogTransportError('unavailable')
        markets = track.get('available_markets')
        if markets is not None:
            if (not isinstance(markets, list) or len(markets) > 300
                    or any(not isinstance(m, str) or not re.fullmatch('[A-Z]{2}', m) for m in markets)):
                raise CatalogTransportError('invalid_response')
            if region not in markets:
                raise CatalogTransportError('unavailable')
        artists = track.get('artists')
        if not isinstance(artists, list) or not 1 <= len(artists) <= 100:
            raise CatalogTransportError('invalid_response')
        if any(not isinstance(a, dict) for a in artists):
            raise CatalogTransportError('invalid_response')
        artist = text(', '.join(text(a.get('name')) for a in artists))
        external = track.get('external_ids', {})
        if not isinstance(external, dict):
            raise CatalogTransportError('invalid_response')
        isrc = external.get('isrc')
        duration, explicit = track.get('duration_ms'), track.get('explicit')
        optional_metadata(isrc, duration, explicit)
        urls = track.get('external_urls')
        url = f'https://open.spotify.com/track/{target}'
        if not isinstance(urls, dict) or urls.get('spotify') != url:
            raise CatalogTransportError('invalid_response')
        return {'service': self.service, 'target_id': target, 'title': text(track.get('name')),
                'artist': artist, 'url': url, 'region': region, 'isrc': isrc,
                'duration_ms': duration, 'version': None, 'explicit': explicit}

    def search(self, title, artist, region, *, limit=10):
        search_inputs(title, artist, limit)
        region_code(region, self.service)
        # Strip only query-language delimiters, retaining remix/edit/live title words.
        query = 'track:"' + title.replace('"', ' ') + '" artist:"' + artist.replace('"', ' ') + '"'
        response = self.request_json(self.service, '/v1/search', params={
            'q': query, 'type': 'track', 'market': region, 'limit': limit, 'offset': 0})
        tracks = response.get('tracks')
        if not isinstance(tracks, dict) or not isinstance(tracks.get('items'), list):
            raise CatalogTransportError('invalid_response')
        items = tracks['items']
        if len(items) > limit:
            raise CatalogTransportError('invalid_response')
        next_url = tracks.get('next')
        if next_url is not None:
            text(next_url, maximum=4096)
            try:
                parsed = urlsplit(next_url)
            except ValueError:
                raise CatalogTransportError('invalid_response') from None
            if (parsed.scheme != 'https' or parsed.netloc != 'api.spotify.com'
                    or parsed.path != '/v1/search' or parsed.fragment or parsed.username):
                raise CatalogTransportError('invalid_response')
        # Deliberately retain only this bounded first page; never fetch returned URLs.
        candidates = [self._candidate(item, region) for item in items]
        if len({item['target_id'] for item in candidates}) != len(candidates):
            raise CatalogTransportError('invalid_response')
        return candidates

    def get_track(self, target_id, region):
        _track_id(target_id)
        region_code(region, self.service)
        response = self.request_json(self.service, '/v1/tracks/' + target_id,
                                     params={'market': region}, not_found=True)
        return None if response is None else self._candidate(response, region, target_id)
