
# google-cloud-storage-objects-viewer
<img width="957" height="482" alt="image" src="https://github.com/user-attachments/assets/18c53b6c-325b-4f33-a5da-93177ef86685" />

Easy Way To Dispay The Content Of PUBLIC Google Cloud Storage Bucket

A small Flask app that turns a folder of a public GCS bucket into a clean, browsable file listing:
breadcrumbs, folders first, sortable columns, instant filter (`/`), copy-link button, light & dark themes, mobile layout.

## Configuration

| Variable      | Required | Example                                                      | Default                                      |
|---------------|----------|--------------------------------------------------------------|----------------------------------------------|
| `BUCKET_NAME` | yes      | `prod-nehemiebarkia-publique`                                | —                                            |
| `FOLDER_SRC`  | no       | `scripts/`                                                   | whole bucket                                 |
| `PUB_URL`     | no       | `https://storage.googleapis.com/prod-nehemiebarkia-publique` | `https://storage.googleapis.com/BUCKET_NAME` |
| `TITLE`       | no       | `SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE`                         | `BUCKET_NAME`                                |
| `TIMEZONE`    | no       | `Europe/Paris`                                               | `Europe/Paris`                               |
| `CACHE_TTL`   | no       | `60`                                                         | `60` (seconds)                               |
| `PORT`        | no       | `8080`                                                       | `8080`                                       |

Credentials: the bucket is read anonymously, which works when `allUsers` has the `Storage Object Viewer` role.
If the anonymous listing is refused, the app switches to the default Google credentials
(GCE / Cloud Run service account, `gcloud auth application-default login`...).

## Run

With Docker (rebuilds the image and replaces the running container, restarted automatically after a crash or a reboot):

```bash
./run.bash
```

```bash
docker logs -f gcs-list
```

or by hand:

```bash
docker build -t gcs-list .
docker run --env-file variables.env -p 8080:8080 gcs-list
```

Locally with [uv](https://docs.astral.sh/uv/) (installs Python 3.15 and the dependencies on first run; `variables.env` is read by Flask through `python-dotenv`, a dev-only dependency):

```bash
uv run flask --app app --env-file variables.env run --debug --port 8080
```

## Dependencies

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`; the Docker image installs exactly the locked versions.

```bash
uv add <package>      # add a dependency
uv lock --upgrade     # upgrade everything to the latest versions
```

The app version shown in the footer is the `version` field of `pyproject.toml`.

Tests:

```bash
uv run pytest
```

## URLs

- `/BASH/INSTALLATIONS/` lists a folder.
- `/BASH/INSTALLATIONS/LAMP.sh` redirects to the public URL of the file, handy for
  `curl -sL https://your-site/BASH/INSTALLATIONS/LAMP.sh | bash`.
- `/healthz` returns `{"status": "ok", "objects": …, "age": …}` for monitoring (502 if the bucket can't be read).

## Speed & reliability

- Only the displayed folder is listed (never the whole bucket), and each listing is kept `CACHE_TTL` seconds:
  going back to a folder seen in the last minute doesn't wait for GCS.
  An unknown path is listed again right away (just uploaded), at most once every 5 seconds.
- If GCS is unreachable, the last good listings keep being served; requests to GCS time out after 10–15 s.
- No credential lookup by default: outside Google Cloud it took ~12 s at startup.
- Pages are gzipped, and Chrome / Edge prefetch a folder when its link is hovered.

## Folders without placeholder objects

GCS has no real folders. Before v2.0 a folder only showed up if an empty `folder/` object existed, which the console
creates with "Create folder" but most tools (`gsutil cp -r`, `gcloud storage cp -r`, `rsync`, SDKs...) do not.
Those folders (and everything inside them) were invisible until deleted and re-uploaded.
Since v2.0 the listing uses the `/` delimiter, so every folder is displayed however it was uploaded.
