# Contributors

Who built what, recorded by contribution rather than by line count.

Where the git record does not tell the whole story, this file says so.

---

## Tessa Rae Feyres — `Tizzerai` / `TessaFey`

**Cloud sync (`src/wildfire/cloud_sync.py`, `tests/test_cloud_sync.py`)**

Designed and implemented the original module: the Azure client bootstrap, the
sync marker, the upload exclusion rules, and the whole-folder upload path. Also
wrote the test suite's `FakeContainer` double and the upload/exclusion/
incremental tests.

Three of her design decisions shaped everything built on the module afterwards:

- **Lazy import.** `azure-storage-blob` is imported inside the functions that
  need it, so the application serves all its routes with the package absent —
  an optional cloud feature that does not compromise offline-first.
- **The `FakeContainer` seam.** Faking the container rather than the SDK lets
  the cloud tests run with no network and no credentials.
- **The `size:mtime` marker.** Re-syncing a run after a review pass uploads only
  the files that changed, rather than the folder again.

Her functions — the file carries section banners marking where they begin:

    CloudSyncError, SyncResult, _require_azure, _load_marker, _save_marker,
    sync_status, _iter_run_files, upload_run, _content_type

`_read_credential`, `_container_client` and `test_connection` are hers, extended
later for SAS credentials.

**Console accessibility.** A contrast pass across the interface — `--muted-2`,
`--muted-3`, `--faint`, `--red` and `--amber` darkened for readability, plus
`for=` on form labels and `alt` text on thumbnails.

**`UI Skeleton`** (`60102ca`) — an early Tkinter interface prototype, built
before the project settled on a single web console.

---

## Luna McCormick — `lateNightDebug`

**Repository initial commit** (`7c101db`).

**Brand identity** — the SAIT logo (`branding/logo.png`) and the brand blue
`#0072BC`, which the console, the PDF header and the application icon are all
drawn from.

> **Note.** The logo was brought onto `main` with
> `git checkout <branch> -- branding/logo.png` rather than a merge, so `git log`
> credits the commit author instead of Luna. The logo and the brand colour are
> hers.
>
> **Update, 2026-11-05.** After graduation the repository became a public
> portfolio project, and the SAIT logo is SAIT's registered trademark, so it was
> replaced by a neutral project mark (two conifers on a pine-green tile) and the
> brand blue by the console's own pine green. Luna's original brand work is in
> the history at `b2be7fe`.

---

## Sagar Girishbhai Kumbhar — `sagarkumbhar`

**Mobile port — in progress.**

Bringing the system to phones. This is closer to a second implementation than a
port: the desktop application runs detection locally against PyTorch and keeps
every run as files on disk, neither of which a phone can do, so the mobile side
needs its own architecture — a backend that holds the data and does the
inference, with the phone as a client.

`mobile/` holds the Expo app. `mobile/docker-compose.yml` (PostgreSQL +
PostGIS) sketches the backend it would talk to, but nothing builds or calls it
yet - the app reads Azure Blob directly. The desktop application does not use
it either; it has no database and no services, and stays that way (see the hard
rules in `AGENTS.md`).

---

## Tianao — `Tianao0110`

Detection pipeline (SAHI tiling, YOLO11 and DeepForest backends, ONNX plugin),
the operations console, GPS and RTK `.MRK` handling, the PDF report builder,
offline map tiles, the results-only cloud sync and SAS credential support,
macOS support, and the test suite.

---

## Keeping this accurate

- **Add your work here when you land it**, rather than reconstructing it later.
- **Cherry-picking a file loses its author.** Prefer `git merge`; if you do copy
  a file across, record the attribution here.
- **If your email is not on your GitHub account**, your commits show a plain
  name with no avatar and do not count toward your contribution graph. Add the
  address you commit with under GitHub → Settings → Emails; existing commits are
  credited retroactively.
