# google-cloud-storage-objects-viewer
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
| `PORT`        | no       | `8080`                                                       | `8080`                                       |

Credentials: the app uses the default Google credentials when available (GCE / Cloud Run service account,
`gcloud auth application-default login`...). Without any credentials it falls back to an anonymous client,
which is enough for a bucket where `allUsers` has the `Storage Object Viewer` role.

## Run

With Docker:

```bash
./run.bash
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

## URLs

- `/BASH/INSTALLATIONS/` lists a folder.
- `/BASH/INSTALLATIONS/LAMP.sh` redirects to the public URL of the file, handy for
  `curl -sL https://your-site/BASH/INSTALLATIONS/LAMP.sh | bash`.

## Folders without placeholder objects

GCS has no real folders. Before v2.0 a folder only showed up if an empty `folder/` object existed, which the console
creates with "Create folder" but most tools (`gsutil cp -r`, `gcloud storage cp -r`, `rsync`, SDKs...) do not.
Those folders (and everything inside them) were invisible until deleted and re-uploaded.
Since v2.0 the listing uses the `/` delimiter, so every folder is displayed however it was uploaded.
