import datetime
import gzip
import threading
import time
from urllib.parse import quote

import pytest
from google.api_core.exceptions import ServiceUnavailable, Unauthorized

import app as viewer

OBJECTS = [
    'scripts/',
    'scripts/a.sh',
    'scripts/BASH/',                          # folder created from the console
    'scripts/BASH/x.sh',
    'scripts/BASH/SUB/Mise à jour #1.bash',   # no 'scripts/BASH/SUB/' placeholder
    'scripts/NEW/y.sh',                       # no 'scripts/NEW/' placeholder
    'scripts/EMPTY/',
    'other/hidden.sh',                        # outside FOLDER_SRC
]


class FakeBlob:
    def __init__(self, name):
        self.name = name
        self.size = 0 if name.endswith('/') else 1024
        self.time_created = self.updated = datetime.datetime(2024, 3, 1, 12, 0, tzinfo=datetime.timezone.utc)


class FakeIterator:
    def __init__(self, blobs, prefixes):
        self.blobs = blobs
        self.prefixes = prefixes

    def __iter__(self):
        return iter(self.blobs)


class FakeClient:
    """Mimics list_blobs: with a delimiter, deeper objects are rolled up into "prefixes"."""

    def __init__(self, names):
        self.blobs = [FakeBlob(name) for name in names]
        self.calls = []
        self.error = None
        self.delay = 0

    def list_blobs(self, bucket, prefix, delimiter=None, **kwargs):
        self.calls.append((prefix, delimiter))
        time.sleep(self.delay)
        if self.error:
            raise self.error
        blobs, prefixes = [], set()
        for blob in self.blobs:
            if not blob.name.startswith(prefix):
                continue
            rest = blob.name[len(prefix):]
            if delimiter and delimiter in rest:
                prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
            else:
                blobs.append(blob)
        return FakeIterator(blobs, prefixes)


@pytest.fixture
def gcs(monkeypatch):
    client = FakeClient(OBJECTS)
    monkeypatch.setattr(viewer, 'folder_cache', viewer.FolderCache(ttl=60, client=client))
    return client


@pytest.fixture
def http(gcs):
    return viewer.app.test_client()


def age_listing(path, seconds):
    viewer.folder_cache.listings[path].loaded_at -= seconds


def test_folders_without_placeholder_are_listed(http):
    page = http.get('/').text
    assert all(name in page for name in ['>BASH<', '>NEW<', '>EMPTY<', 'a.sh'])
    assert 'hidden.sh' not in page
    page = http.get('/BASH/').text
    assert '>SUB<' in page and 'x.sh' in page
    assert 'Mise à jour #1.bash' in http.get('/BASH/SUB/').text


def test_only_the_displayed_folder_is_listed(http, gcs):
    http.get('/BASH/SUB/')
    assert gcs.calls == [('scripts/BASH/SUB/', '/')]
    for path in ['/', '/BASH/', '/NEW/', '/a.sh', '/NOPE/']:
        http.get(path)
    assert all(delimiter == '/' for _, delimiter in gcs.calls)


def test_empty_and_unknown_folders(http):
    assert 'Ce dossier est vide' in http.get('/EMPTY/').text
    assert http.get('/NOPE/').status_code == 404
    assert http.get('/NOPE').status_code == 404
    assert http.get('/NOPE/x.sh').status_code == 404


def test_redirects(http):
    assert http.get('/BASH').location == '/BASH/'
    response = http.get('/' + quote('BASH/SUB/Mise à jour #1.bash'))
    assert response.location == 'https://pub.example/scripts/BASH/SUB/Mise%20%C3%A0%20jour%20%231.bash'


def test_listings_are_cached(http, gcs):
    for path in ['/', '/BASH/', '/', '/BASH/', '/BASH', '/a.sh', '/BASH/x.sh']:
        http.get(path)
    assert len(gcs.calls) == 2  # the redirects reuse the parent's listing


def test_stale_listing_is_refreshed(http, gcs):
    http.get('/')
    age_listing('', 61)
    http.get('/')
    assert len(gcs.calls) == 2


def test_new_upload_is_found_before_ttl(http, gcs):
    http.get('/FRESH/')
    gcs.blobs.append(FakeBlob('scripts/FRESH/new.sh'))
    age_listing('FRESH/', 6)
    assert http.get('/FRESH/').status_code == 200
    http.get('/BASH/')
    gcs.blobs.append(FakeBlob('scripts/BASH/new.sh'))
    age_listing('BASH/', 6)
    assert http.get('/BASH/new.sh').status_code == 302


def test_unknown_paths_do_not_hammer_gcs(http, gcs):
    for _ in range(20):
        http.get('/NOPE/')
    assert len(gcs.calls) == 1


def test_memory_stays_bounded(http, monkeypatch):
    monkeypatch.setattr(viewer, 'MAX_CACHED_FOLDERS', 10)
    for index in range(50):
        http.get(f'/random-{index}/')
    assert len(viewer.folder_cache.listings) <= 10


def test_gcs_outage_serves_last_listing(http, gcs):
    http.get('/BASH/')
    gcs.error = ServiceUnavailable('down')
    age_listing('BASH/', 61)
    for _ in range(5):
        response = http.get('/BASH/')
        assert response.status_code == 200 and 'x.sh' in response.text
    assert len(gcs.calls) == 2  # one failed refresh, then RETRY_DELAY before the next attempt


def test_gcs_outage_without_listing(http, gcs):
    gcs.error = ServiceUnavailable('down')
    assert http.get('/').status_code == 502
    gcs.error = None
    viewer.folder_cache.retry_at = 0
    assert http.get('/').status_code == 200


def test_private_listing_falls_back_to_credentials(http, gcs, monkeypatch):
    gcs.error = Unauthorized('anonymous')
    authenticated = FakeClient(OBJECTS)
    monkeypatch.setattr(viewer.storage, 'Client', lambda: authenticated)
    assert http.get('/').status_code == 200
    assert len(authenticated.calls) == 1


def test_concurrent_requests_share_one_listing(gcs):
    gcs.delay = 0.2
    threads = [threading.Thread(target=viewer.folder_cache.get, args=('BASH/',)) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(gcs.calls) == 1


def test_gzip(http):
    response = http.get('/', headers={'Accept-Encoding': 'gzip'})
    assert response.headers['Content-Encoding'] == 'gzip'
    assert b'BASH' in gzip.decompress(response.data)
    assert 'Content-Encoding' not in http.get('/').headers


def test_healthz_never_refreshes(http, gcs):
    assert http.get('/healthz').json['status'] == 'ok'
    age_listing('', 3600)
    assert http.get('/healthz').status_code == 200
    assert len(gcs.calls) == 1
