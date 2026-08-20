# Wildfire Hazard Field Viewer (mobile)

The phone half of the Wildfire Hazard Detection System: a React Native / Expo
app that shows a crew where the hazards are and how to walk to them.

It is **not** a port of the desktop console. The desktop app runs detection
locally against PyTorch and keeps every run as files on disk, neither of which a
phone can do. This app is a viewer: the desktop console uploads one small
`results.json.gz` per run (see `src/wildfire/cloud_sync.py`), and the phone
reads it. **No AI runs on the phone.**

## What it does

- **Sync from Azure** - lists the runs in the container, pulls the newest (or one
  you pick), un-gzips it and parses the same schema `cloud_sync.py` writes
- **Works offline** - the last synced run is cached in AsyncStorage, so the app
  is usable in the field with no signal. Before the first sync it shows the
  bundled `assets/sample_results.json`
- **Find the hazard** - map with risk-coloured sites, live GPS distance, a
  compass dial, in-app wayfinding, and handoff to the native maps app
- **Field notes** - mark a site visited and type a note; notes sync back to the
  container as `field_notes.json`

## Running it

```bash
cd mobile
npm install
cp .env.example .env.local     # then edit .env.local, see below
npx expo start
```

Scan the QR code with Expo Go, or press `i` / `a` for a simulator.

## The Azure credential

The app needs a SAS URL for the results container. It is read from the
environment, never from a tracked file:

```bash
# mobile/.env.local   (gitignored; restart expo after editing)
EXPO_PUBLIC_WILDFIRE_SAS_URL=https://<account>.blob.core.windows.net/wildfire-runs?sv=...&sig=...
```

That is the **container** url with its `?sv=...` token attached.

**Permissions.** `Read + List` is enough for everything except field-note
sync-back, which `PUT`s a blob and therefore also needs `Create + Write`. With a
Read + List token every screen works and only note upload fails, with HTTP 403.
Never put the storage account key here.

`EXPO_PUBLIC_` values are compiled into the app bundle. That is unavoidable for
any client-side SAS - the phone has to carry the token to read the container -
which is exactly why the token must be scoped and should carry an expiry.

Without the variable set the app still runs on the bundled sample, and the sync
button says what to configure.

## `docker-compose.yml` - a sketch, not a service

The compose file in this folder (PostgreSQL + PostGIS + a FastAPI `backend`) is
an **unbuilt plan** for a server-side version of this app. Nothing in the
repository uses it today:

- the desktop console has no database and no services, deliberately
  (`AGENTS.md`, hard rule 9)
- this app talks to Azure Blob directly and never calls a backend

It does not currently run: the `./backend` directory it builds from does not
exist, and the `ai-service:8001` it points at is not defined in the file. Treat
it as a design note until someone decides to build that backend.

## Notes for whoever works on this next

- Expo has changed a lot between versions. Read the exact versioned docs at
  <https://docs.expo.dev/versions/v54.0.0/> before writing code (see `AGENTS.md`
  in this folder).
- The results schema is owned by `src/wildfire/cloud_sync.py`. If you change
  what the phone expects, change it there first - `assets/sample_results.json`
  is a copy of that shape and should be kept in step.
