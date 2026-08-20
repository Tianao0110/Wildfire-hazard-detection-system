// sync.js — Azure pull for the Wildfire Field Viewer (Milestone 2)
//
// What this does, in plain words:
//   1. LIST the wildfire-runs container (read-only SAS) to find all runs
//   2. Pick the NEWEST run (run ids are console_YYYYMMDD_HHMMSS, so
//      sorting the names alphabetically also sorts them by time)
//   3. DOWNLOAD that run's results.json.gz
//   4. UN-GZIP it (pako) and parse the JSON — the same schema cloud_sync.py writes
//   5. CACHE it on the phone (AsyncStorage) so the app works offline afterwards
//
// Permissions, which are NOT uniform across this file: viewing a run needs
// Read + List, but uploadNotes() PUTs field_notes.json back and so needs Create
// + Write as well. A Read + List token makes every screen work and fails only
// on note sync-back, with HTTP 403. Never the account key either way; the token
// comes from the environment, not from this file.

import AsyncStorage from "@react-native-async-storage/async-storage";
import pako from "pako";

// ── CONFIG ──────────────────────────────────────────────────────────
// The SAS URL is read from the environment rather than pasted in here: editing
// a tracked source file to hold a credential is exactly how one ends up in a
// commit. Put it in mobile/.env.local (gitignored), then restart Expo:
//
//   EXPO_PUBLIC_WILDFIRE_SAS_URL=https://<account>.blob.core.windows.net/wildfire-runs?sv=...&sig=...
//
// It is the CONTAINER url with its ?sv=... token, Read + List only. See README.
//
// EXPO_PUBLIC_ values are compiled into the app bundle. That is unavoidable for
// any client-side SAS - the phone has to carry the token to read the container -
// which is the whole reason it must be scoped to Read + List, never the account
// key, and should be given an expiry.
const SAS_BASE = (process.env.EXPO_PUBLIC_WILDFIRE_SAS_URL || "").trim();

const CACHE_KEY = "wildfire.results.v1";
const CACHE_META = "wildfire.results.meta.v1";

// Split "https://host/container?sv=..." into base and query once.
// Unconfigured fails HERE with a sentence you can act on, rather than later as
// a bare "Network request failed" against the url "undefined".
function parts() {
  if (!isConfigured()) {
    throw new Error(
      "No Azure SAS URL configured. Add EXPO_PUBLIC_WILDFIRE_SAS_URL to " +
        "mobile/.env.local and restart Expo - see mobile/README.md.",
    );
  }
  const [base, query] = SAS_BASE.split("?");
  return { base, query };
}

// ── 1. List ALL run ids in the container (newest first) ─────────────
export async function listRunIds() {
  const { base, query } = parts();
  const url = `${base}?${query}&restype=container&comp=list`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`List failed: HTTP ${res.status}`);
  const xml = await res.text();

  // Blob names look like "console_20260728_095202/results.json.gz".
  const runIds = new Set();
  const re = /<Name>([^<]+)<\/Name>/g;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const name = m[1];
    if (name.endsWith("results.json.gz")) runIds.add(name.split("/")[0]);
  }
  if (runIds.size === 0) throw new Error("No runs found in the container yet.");
  // console_YYYYMMDD_HHMMSS sorts as text = sorts by time. Newest first:
  return [...runIds].sort().reverse();
}

// ── 2. Newest run id (kept for the simple sync path) ────────────────
export async function newestRunId() {
  return (await listRunIds())[0];
}

// ── 3+4. Download one run's results and decode it ───────────────────
export async function fetchRun(runId) {
  const { base, query } = parts();
  const url = `${base}/${runId}/results.json.gz?${query}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`);

  const buf = await res.arrayBuffer();
  const bytes = new Uint8Array(buf);

  // The blob is gzipped by cloud_sync.py. Some networks/CDNs auto-decompress,
  // so handle both: try plain JSON first, fall back to gunzip.
  let text;
  try {
    text = new TextDecoder("utf-8").decode(bytes);
    JSON.parse(text); // throws if it's still compressed binary
  } catch {
    text = pako.ungzip(bytes, { to: "string" });
  }
  return JSON.parse(text); // schema 1 — same shape as the bundled sample
}

// ── 5. Cache on the phone so it works offline afterwards ────────────
export async function saveCache(results) {
  await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(results));
  await AsyncStorage.setItem(
    CACHE_META,
    JSON.stringify({ syncedAt: new Date().toISOString(), runId: results.run_id })
  );
}

export async function loadCache() {
  const raw = await AsyncStorage.getItem(CACHE_KEY);
  const meta = await AsyncStorage.getItem(CACHE_META);
  return {
    results: raw ? JSON.parse(raw) : null,
    meta: meta ? JSON.parse(meta) : null,
  };
}

// ── The calls the app uses ──────────────────────────────────────────
// Sync the newest run, or a specific one if runId is given.
export async function syncFromAzure(runId = null) {
  const id = runId || (await newestRunId());
  const results = await fetchRun(id);
  await saveCache(results);
  return results;
}

export function isConfigured() {
  return SAS_BASE.startsWith("https://");
}

// ════════════════════════════════════════════════════════════════════
// Tier 2 — Field notes sync-back (phone → Azure → visible to the team)
//
// Design decisions (worth explaining in QA):
//  • We write to a SEPARATE blob per run: {run_id}/field_notes.json
//    We NEVER touch results.json.gz — the laptop's data stays read-only
//    to us, so there is no conflict with Eden's uploads.
//  • Last-write-wins per phone is acceptable here because one crew phone
//    is the expected case; the blob includes device + timestamp so the
//    console could merge later if needed.
//  • This needs a SAS with Create+Write. If your read-only token lacks
//    them, notes upload fails gracefully and stays on the phone.
// ════════════════════════════════════════════════════════════════════

export async function uploadNotes(runId, notesMap, deviceName = "field-phone") {
  const { base, query } = parts();
  const url = `${base}/${runId}/field_notes.json?${query}`;

  const payload = JSON.stringify({
    schema: 1,
    run_id: runId,
    device: deviceName,
    uploaded_at: new Date().toISOString(),
    notes: notesMap, // { siteId: { visited: bool, text: string } }
  });

  const res = await fetch(url, {
    method: "PUT",
    headers: {
      // Azure requires this header to create a block blob via REST
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": "application/json",
    },
    body: payload,
  });
  if (!res.ok) {
    throw new Error(`Notes upload failed: HTTP ${res.status}`);
  }
  return true;
}

export async function downloadNotes(runId) {
  const { base, query } = parts();
  const url = `${base}/${runId}/field_notes.json?${query}`;
  const res = await fetch(url);
  if (res.status === 404) return null; // no notes uploaded yet — normal
  if (!res.ok) throw new Error(`Notes download failed: HTTP ${res.status}`);
  return res.json();
}