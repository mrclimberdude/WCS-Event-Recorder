"""Apple Music catalog songs only; developer bearer authorization, no user library."""

import re
from urllib.parse import parse_qs, urlsplit

from .catalog_transport import (CatalogTransport, CatalogTransportError, optional_metadata,
                                region_code, search_inputs, text)


def _song_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9]{1,30}', value):
        raise CatalogTransportError('invalid_response')
    return value


def _song_url(value, target, region):
    text(value, maximum=2048)
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise CatalogTransportError('invalid_response') from None
    if (parsed.scheme != 'https' or parsed.netloc != 'music.apple.com'
            or parsed.fragment or parsed.username
            or not parsed.path.startswith('/' + region + '/')):
        raise CatalogTransportError('invalid_response')
    # Documented catalog URLs use either /song/<name>/<id> or an album URL with i=<song ID>.
    pieces = parsed.path.split('/')
    query = parse_qs(parsed.query, keep_blank_values=True)
    direct = (len(pieces) == 5 and pieces[2] == 'song' and pieces[3]
              and pieces[4] == target and not query)
    album = (len(pieces) == 5 and pieces[2] == 'album' and pieces[3]
             and re.fullmatch('[0-9]{1,30}', pieces[4]) and query == {'i': [target]})
    if not (direct or album):
        raise CatalogTransportError('invalid_response')
    return value


class AppleCatalogAdapter(CatalogTransport):
    service = 'apple'

    def _candidate(self, item, region, requested=None):
        if not isinstance(item, dict) or item.get('type') != 'songs':
            raise CatalogTransportError('invalid_response')
        target = _song_id(item.get('id'))
        if requested is not None and requested != target:
            raise CatalogTransportError('invalid_response')
        attributes = item.get('attributes')
        if not isinstance(attributes, dict):
            raise CatalogTransportError('invalid_response')
        rating = attributes.get('contentRating')
        if rating not in (None, 'clean', 'explicit'):
            raise CatalogTransportError('invalid_response')
        explicit = None if rating is None else rating == 'explicit'
        isrc, duration = attributes.get('isrc'), attributes.get('durationInMillis')
        optional_metadata(isrc, duration, explicit)
        return {'service': self.service, 'target_id': target, 'title': text(attributes.get('name')),
                'artist': text(attributes.get('artistName')),
                'url': _song_url(attributes.get('url'), target, region), 'region': region,
                'isrc': isrc, 'duration_ms': duration, 'version': None, 'explicit': explicit}

    def search(self, title, artist, region, *, limit=10):
        search_inputs(title, artist, limit)
        region_code(region, self.service)
        response = self.request_json(self.service, f'/v1/catalog/{region}/search', params={
            'term': title + ' ' + artist, 'types': 'songs', 'limit': limit})
        results = response.get('results')
        if not isinstance(results, dict):
            raise CatalogTransportError('invalid_response')
        # Apple explicitly represents no matching song resources by omitting songs.
        if not results:
            return []
        songs = results.get('songs')
        if not isinstance(songs, dict) or not isinstance(songs.get('data'), list):
            raise CatalogTransportError('invalid_response')
        if len(songs['data']) > limit:
            raise CatalogTransportError('invalid_response')
        next_path = songs.get('next')
        if next_path is not None and (not isinstance(next_path, str)
                or not next_path.startswith(f'/v1/catalog/{region}/search?')
                or '#' in next_path or '\\' in next_path):
            raise CatalogTransportError('invalid_response')
        candidates = [self._candidate(item, region) for item in songs['data']]
        if len({item['target_id'] for item in candidates}) != len(candidates):
            raise CatalogTransportError('invalid_response')
        return candidates

    def get_track(self, target_id, region):
        _song_id(target_id)
        region_code(region, self.service)
        response = self.request_json(self.service, f'/v1/catalog/{region}/songs/{target_id}',
                                     not_found=True)
        if response is None:
            return None
        data = response.get('data')
        if not isinstance(data, list) or len(data) != 1:
            raise CatalogTransportError('invalid_response')
        return self._candidate(data[0], region, target_id)
