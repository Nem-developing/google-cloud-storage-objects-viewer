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
    def __init__(self, name, day=1):
        self.name = name
        self.size = 0 if name.endswith('/') else 1024
        self.time_created = self.updated = datetime.datetime(2024, 3, day, 12, 0, tzinfo=datetime.timezone.utc)


class FakeClient:
    def __init__(self, names):
        self.blobs = [FakeBlob(name) for name in names]
        self.calls = 0
        self.error = None
        self.delay = 0

    def list_blobs(self, bucket, prefix, **kwargs):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return [blob for blob in self.blobs if blob.name.startswith(prefix)]


@pytest.fixture
def gcs(monkeypatch):
    client = FakeClient(OBJECTS)
    monkeypatch.setattr(viewer, 'bucket_index', viewer.BucketIndex(ttl=30, client=client))
    return client


@pytest.fixture
def http(gcs):
    return viewer.app.test_client()


def age_copy(seconds):
    viewer.bucket_index.snapshot.loaded_at -= seconds


def test_folders_without_placeholder_are_listed(http):
    page = http.get('/').text
    assert all(name in page for name in ['>BASH<', '>NEW<', '>EMPTY<', 'a.sh'])
    assert 'hidden.sh' not in page
    page = http.get('/BASH/').text
    assert '>SUB<' in page and 'x.sh' in page
    assert 'Mise à jour #1.bash' in http.get('/BASH/SUB/').text


def test_empty_and_unknown_folders(http):
    assert 'Ce dossier est vide' in http.get('/EMPTY/').text
    assert http.get('/NOPE/').status_code == 404
    assert http.get('/NOPE').status_code == 404


def test_redirects(http):
    assert http.get('/BASH').location == '/BASH/'
    response = http.get('/' + quote('BASH/SUB/Mise à jour #1.bash'))
    assert response.location == 'https://pub.example/scripts/BASH/SUB/Mise%20%C3%A0%20jour%20%231.bash'


def test_folder_totals(gcs):
    folders = viewer.bucket_index.get().folders
    assert (folders['']['count'], folders['']['size']) == (4, 4096)
    assert (folders['BASH/']['count'], folders['BASH/']['size']) == (2, 2048)
    assert folders['EMPTY/']['updated'] is None


def test_browsing_uses_the_copy(http, gcs):
    for path in ['/', '/BASH/', '/BASH/SUB/', '/NEW/', '/BASH', '/a.sh']:
        http.get(path)
    assert gcs.calls == 1


def test_stale_copy_is_reloaded(http, gcs):
    http.get('/')
    age_copy(31)
    http.get('/')
    assert gcs.calls == 2


def test_new_upload_is_found_before_ttl(http, gcs):
    http.get('/')
    gcs.blobs.append(FakeBlob('scripts/FRESH/new.sh'))
    age_copy(6)
    assert http.get('/FRESH/').status_code == 200
    assert gcs.calls == 2


def test_unknown_paths_do_not_hammer_gcs(http, gcs):
    for _ in range(20):
        http.get('/NOPE/')
    assert gcs.calls == 1


def test_gcs_outage_serves_last_copy(http, gcs):
    http.get('/')
    gcs.error = ServiceUnavailable('down')
    age_copy(31)
    for _ in range(5):
        response = http.get('/BASH/')
        assert response.status_code == 200 and 'x.sh' in response.text
    assert gcs.calls == 2  # one failed reload, then RETRY_DELAY before the next attempt


def test_gcs_outage_without_copy(http, gcs):
    gcs.error = ServiceUnavailable('down')
    assert http.get('/').status_code == 502
    gcs.error = None
    viewer.bucket_index.retry_at = 0
    assert http.get('/').status_code == 200


def test_private_listing_falls_back_to_credentials(http, gcs, monkeypatch):
    gcs.error = Unauthorized('anonymous')
    authenticated = FakeClient(OBJECTS)
    monkeypatch.setattr(viewer.storage, 'Client', lambda: authenticated)
    assert http.get('/').status_code == 200
    assert authenticated.calls == 1


def test_concurrent_requests_share_one_reload(gcs):
    viewer.bucket_index.get()
    age_copy(31)
    gcs.delay = 0.2
    threads = [threading.Thread(target=viewer.bucket_index.get) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert gcs.calls == 2


def test_gzip(http):
    response = http.get('/', headers={'Accept-Encoding': 'gzip'})
    assert response.headers['Content-Encoding'] == 'gzip'
    assert b'BASH' in gzip.decompress(response.data)
    assert 'Content-Encoding' not in http.get('/').headers


def test_healthz_never_reloads(http, gcs):
    assert http.get('/healthz').json['objects'] == 4
    age_copy(3600)
    assert http.get('/healthz').status_code == 200
    assert gcs.calls == 1
