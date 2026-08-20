# Delivery Plan

Tracked against the original requirements table; each item ticked when done.
Repo rule: commits carry no AI signature lines.

## Batch 1

- [x] **Commit all existing work** (40+ files)
- [x] **Show the drone model in Scan Detail** (EXIF Make/Model, verified as DJI L2);
      the processing GPU demoted to a "Processed on" row (older runs lack the field
      and show "not in EXIF"; new runs carry it automatically)
- [x] **Photo thumbnails in map popups** (Dashboard + Map pages)
- [x] **GPS pin map on the PDF summary page** (colored by detection type, with legend)
- [x] **Settings completed**: report/output folder (applies after restart) + a
      "download missing models" button
- [x] **Generic hardware support audit**: the code was already generic
      (any CUDA -> Apple MPS -> CPU); only comments hard-coded an RTX 4090, now fixed
- [x] Full test suite green (61 tests), wrap-up commit

## Batch 2 (2026-07-16)

- [x] **MRK GPS parsing**: DJI RTK Timestamp.MRK (centimetre-grade) preferred over
      EXIF (metre-grade); photo sequence matched automatically; verified on real
      DJI L2 data. The .MRK must sit in the same folder as the photos (importing a
      mission folder as-is already satisfies this)
- [x] **Image pre-compression**: photos over `preprocess_max_mb` (default 2 MB) are
      re-encoded before detection (resolution preserved, quality ladder); adjustable
      in Settings, 0 disables
- [x] **Real satellite basemap (Leaflet + offline Esri tiles)**:
      `scripts/fetch_map_tiles.py --bbox ... --zoom 11 15` fetched once before going
      offline; the Map page switches to the real satellite view (zoom/pan) and falls
      back to the stylized canvas when tiles are missing. 480 tiles cached for the
      Canmore operating area
- [x] **Classified roads + rivers/lakes**: `scripts/fetch_map_overlays.py` pulls
      GeoJSON from OSM (highway classes, waterways, water bodies), rendered offline
      over the satellite map. 2,880 features cached for the area
- [x] **Detection dedup tier 1 (site clustering)**: flagged images cluster into
      "sites" within 40 m; the map marks sites (count badge + max severity + member
      images) and states "N images merged into M sites"

## Batch 3 - fixes from real-device feedback (2026-07-16)

- [x] **Leaflet freeze fixed**: root cause was 2,880 road/water features rendered as
      SVG paths freezing WebView2; switched to the canvas renderer (preferCanvas) -
      SVG path count now zero and navigating away from the map works
- [x] **Dashboard map synced with the Map page**: both use the same site data
      (/api/map-data) and the same Leaflet component, and both switch to real
      satellite imagery when tiles exist
- [x] **Second-level filtering for large scans**: day groups collapse (newest day
      open); continuous flights over 250 images auto-split into part 1/N
      (1,558 images -> 7 parts); each part offers "Select images" to detect a subset
- [x] **In-app area map download**: banner button on the Map page when tiles are
      missing, plus an Offline map card in Settings; bbox inferred from all scan GPS,
      background download with progress
- [x] **Tidy folders**: map_tiles/ -> **map/** (tiles + overlays.geojson);
      models/ = models; outputs/ = run results
- [x] **Removed the droplet-looking logo**

## Batch 4 (2026-07-16)

- [x] **Image zoom in Scan Detail**: cursor-anchored wheel zoom (up to 12x), drag
      pan, double-click reset; zoom keeps working in box-review mode
- [x] **Map markers use hazard-type colors**: flame = red, smoke = orange,
      **dead tree = yellow** (matching the boxes), legend updated; popups keep the
      severity badge alongside
- [x] **Packaging**: `install.bat` (one-click environment + desktop shortcut) +
      `INSTALL.md` (package with `git archive`; recipient installs Python, unzips,
      double-clicks install.bat)

## Batch 5 (2026-07-16)

- [x] **Map no longer paints over the nav bar**: nav z-index raised above Leaflet
      and the map container gets its own stacking context
- [x] **Severity Distribution replaced by Hazard Overview**: detections by type
      (type-colored bars) + review backlog (runs awaiting confirmation, one click to
      Review) + training-set size (confirmed boxes) - three "what to do next" signals
- [x] **Image picker became a full-screen modal**: large thumbnails (170 px grid),
      instant file list, thumbnails **generated per-image on demand + lazy-loaded**
      (no more bulk-generating 100 at once), Esc / backdrop to close

## Batch 6 (2026-07-16)

- [x] **UI freeze during detection fixed (architectural)**: detection moved to a
      separate low-priority subprocess (`console/worker.py`); the server only polls
      the progress file. Measured API responses of 75-187 ms during a live detection
      (previously "Failed to fetch")
- [x] **Mission folder split into analyzed / pending**: fully detected flights
      collapse into a "✓ ANALYZED" section (grayed cards + Re-detect); partially
      analyzed flights show an n/total badge; pending flights stay on top, grouped by day
- [x] **Removed the fake Operator account chip** (there is no account system;
      replaced by a plain Local · Offline indicator)

## Batch 7 (2026-07-16)

- [x] **Removed the Alerts placeholder** (never specified; no fake features)
- [x] **Offline map download dialog**: three modes - around the scanned area
      (z12-16, recommended) / whole Alberta basemap (z8-11, ~376 MB) / custom
      rectangle with zoom range; live tile-count and size estimate before starting,
      a 30,000-tile server-side cap, and progress inside the dialog

## Batch 8 (2026-07-17)

- [x] **User manual (MANUAL.md)**: minimum/recommended specs, installation,
      first-time online setup, standard workflow, data layout, the training loop,
      troubleshooting table, known limits
- [x] **Map time filter**: month / year / all-time dropdown, defaulting to the latest
      month; dates come from **EXIF capture time** (not analysis time); the dashboard
      mini-map follows the same default period

## Batch 9 (2026-07-17)

- [x] **Cleaned leftover test runs**: removed 3 synthetic-image runs
      (console_20260710_122252 / 20260714_145931 / 20260716_203851) and their
      _uploads staging, leaving only the 3 real DJI runs
- [x] **Click a detection to locate it**: clicking any row in the detection list
      zooms the preview onto that box (about 1/3 of the view, capped at 12x) and
      pulses a spotlight ring for 5 s; in review mode it also selects the box - no
      more hunting for a tiny flame by eye

## Batch 10 (2026-07-17)

- [x] **Wording**: "Human-reviewed" -> "Reviewed" across the UI
- [x] **Professional AI analysis**: the LLM input went from a few totals to full
      survey facts (aircraft / capture time / surveyed extent / density / confidence /
      review status / overlap-double-counting caveat / ranked hotspots); the output is
      forced into five sections (Executive Summary / Findings / Priority Locations /
      Recommended Actions / Data Quality & Limitations); the PDF renders section headings

## Batch 11 (2026-07-17)

- [x] **LLM prompt fix**: heading lines contain the title only and the content
      guidance is explicitly marked "never copy into the report" (the model used to
      paste the format instructions into the body)
- [x] **Report image-page cap**: `report_max_image_pages` (default 30), hazard-densest
      images first, with a "top N of M" note on the cover; a 250-image flight no
      longer produces 250 pages
- [x] **Confirmed the AI context does not grow**: the LLM always receives aggregate
      statistics + the top 15 hotspots, independent of flight size
- [x] **Scan Detail performance**: the film strip uses server-side thumbnails
      (~17 KB each, lazy-loaded) instead of full-size annotated JPEGs
- [x] **Flight parts 250 -> 100** (one part = one review sitting)

## Batch 12 (2026-07-17)

- [x] **Wrong image in the PDF's three columns**: `_display_copy` cache keys now hash
      the full path - annotated and gridmaps `_confirmed` files share a stem and were
      overwriting each other, so the "Detections" column showed the grid map
- [x] **Black rectangle instead of the PDF map**: the summary pin map switched to a
      print-friendly light theme (white canvas, light grid, dark-outlined dots)
- [x] **Black box glyphs**: 12 em-dashes in code strings changed to ASCII;
      `_pdf_safe()` maps the LLM's typographic punctuation to ASCII and strips
      anything outside Latin-1
- [x] Verified by regenerating a real report: zero mojibake, clean five-section
      analysis with no prompt leakage, 18 distinct image copies

## Batch 13 (2026-07-17)

- [x] **PDF map upgraded to real satellite imagery**: stitched directly from the
      offline tile cache (highest fully-cached zoom auto-selected), pins projected
      precisely, white chrome boxes keeping legend/attribution readable; falls back to
      the light schematic only where no tiles are cached (and says so). Verified on
      real data: zoom-16 imagery with visible forest, river and trails

## Batch 14 - client review feedback (2026-07-23)

- [x] **Hazard-type palette reworked for accessibility**: smoke orange `#F0A500`
      and dead-tree yellow `#FFD700` were only 1.48:1 apart (WCAG 1.4.11 asks 3:1
      for graphical objects) and collapse to the same color under red-green
      colorblindness - the client could not tell them apart. Now flame red
      `#E05555` / smoke slate `#546E7A` / dead tree bone `#D9CDB0`, 3.42:1 on the
      pair that mattered. Rolled through all four definition sites (console.css
      `:root`, console.js `KIND`, annotate.py BGR, report.py RGB) plus the legacy
      Gradio annotator, and fixed a pre-existing drift where detail.html used
      different shades (`#E53935`/`#FB8C00`) than everything else
- [x] **Every hazard type now carries an icon** (flame / smoke puff / bare trunk)
      on map pins, legends and badges, so type survives greyscale printing and any
      color-vision deficiency - color is no longer the only channel (WCAG 1.4.1).
      Icon pins are DOM markers, capped at 300 sites before falling back to canvas
      circles so a huge flight cannot stall WebView2
- [x] **Bone needs an outline**: at 1.58:1 against white it disappears on cards and
      on paper, so thumbnails get an inset ring, map pins a white+dark double halo,
      and the PDF picks the pin ring by fill luminance
- [x] **Double-click a map pin to go fullscreen**: on the Map page the card fills
      the window zoomed to that site (Esc or double-click to exit); on the Dashboard,
      whose map is only 60% wide, it hands off to `/map?site=N&month=...` which opens
      there focused. Leaflet's own double-click-to-zoom is disabled so the two
      gestures stop fighting; a *Fullscreen* button makes the hidden gesture
      discoverable. Verified against the real 14-site May flight

## Batch 15 - report layout + scale (2026-07-23)

- [x] **Report reordered to conclusions-first**: cover -> batch summary ->
      full-page hazard map -> AI analysis -> imagery. Per-image material used to
      sit in the middle, so a reader had to page past 30 photos to reach the
      numbers and the map they opened the report for
- [x] **One page per image replaced with a three-tier layout**: full detail pages
      for the worst `report_detail_pages` (default 4), a gallery contact sheet at
      8 per page for the rest of the top `report_max_image_pages`, and an index
      table row for EVERY image. Measured on the real 58-image run: **33 pages ->
      15**, and it now documents all 58 images instead of only 30
- [x] **Hazard map promoted to its own landscape page** (9.4in wide). Squeezed
      under the stats table it had ~2.9in of height and was unreadable; alone it
      is the single most useful page for a crew deciding where to drive
- [x] **Marker icon limit raised to 500** after measuring: 500 DOM icon pins build
      in 24 ms and pan in 1 ms, and the canvas fallback still catches anything
      bigger. A month can hold 10k+ photos, so 300 was too tight

## Batch 16 - scale fix + the first frontend guard (2026-07-25)

- [x] **cluster_sites no longer freezes the window on a big month**: it compared
      every point against every existing site, which is fine for a 60-photo run
      and 7.5 s for a 10k-photo month - inside a web request. Sites are now
      indexed into a grid of radius-sized cells so each point only checks the 3x3
      cells that could hold a match. **7.47 s -> 0.045 s (166x)** at 10k points,
      20k in 0.08 s. Output is byte-identical: a test pins it against the old
      linear scan across seven geometries (dense overlap, nothing merging, all
      points identical, high latitude, across the equator, near the antimeridian)
- [x] **First test coverage of the frontend contract** (`test_frontend_contract.py`,
      16 tests). The console has no build step, so renaming a helper in console.js
      passed all 72 tests and broke the page at runtime. These parse each page's
      inline JS and assert every function it calls is defined somewhere - by the
      page, by console.js, or by the browser - plus that pages load console.js
      before their own script, and that no page reintroduces a retired hazard hex.
      Verified by mutation: renaming a helper, reintroducing #FFD700, and deleting
      a page each turn the suite red
- [x] **Caught a real leftover while writing it**: `detail.html` still fell back to
      the retired `#FFD700` for unknown labels; now falls back to `KIND.deadtree.color`
- [x] Suite: **72 -> 90 tests**, all green

## Batch 17 - merged Tessa; scoped cloud credentials (2026-07-26)

- [x] **Merged `Tessa`**: optional Azure Blob sync plus her console accessibility
      pass. Two strands of accessibility work met in console.css and both kept -
      hers raises text/severity contrast (`--muted-2/-3`, `--faint`, `--red`,
      `--amber`, `--green-2`, plus `label for=` and `img alt`), mine separates the
      hazard-TYPE palette and adds per-type icons. Six files conflicted; each was
      resolved as "both sides", not "pick a side"
- [x] **Fixed a silent conflict the merge exposed**: `applyBrandingColors()` writes
      `--green-2` from `branding/brand.json` at runtime, so it was overwriting her
      darkened green with the old lighter one - her contrast fix had no visible
      effect at all. brand.json now carries the darker value and the function says
      it wins over console.css
- [x] **Cloud credentials can now be scoped**: the app accepts a portal **Blob SAS
      URL** (container-level or account-level) as well as the original account-key
      connection string. A SAS reaches one container, can be issued per device,
      expires, and is revocable alone; the account key it replaces grants
      read/write/delete over the entire storage account forever. *Test connection*
      reports which kind is in use and warns on the account key
- [x] `_ensure_container()` tolerates a scoped token that can neither probe nor
      create the container - that is the intended setup, not a failure
- [x] **Confirmed no account system is needed** even though sync spans machines:
      Azure Storage is the identity layer. Recorded in AGENTS.md hard rule 7
- [x] Suite **90 -> 102** tests. Verified the app still starts and serves all 44
      routes with `azure-storage-blob` absent, so offline installs are unaffected

## Batch 18 - results-only cloud sync (2026-07-26)

- [x] **Cloud sync now shares findings, not imagery.** `cloud_upload_mode`
      defaults to `"results"`: one gzipped JSON per run holding detections, GPS,
      statistics and confirmed labels. Measured across the five real runs,
      **2,056 MB on disk becomes 48 KB uploaded** - about 12 bytes per detection,
      so the 1,558-image May mission is roughly 1 MB. PDFs are excluded (55.8 MB
      for one run on its own). Full-folder upload is kept as an opt-in backup mode
- [x] **Round trip verified on real data**: exported all five runs, imported them
      into a separate output root as a second machine, and the map came back
      identical - 67 flagged images to 14 sites on both sides, dashboard totals
      matching, zero broken image URLs
- [x] **No local paths leave the machine**: `results_payload()` strips absolute
      paths and sends each image's file name only, so `_rel_url()` returns None
      and the UI's existing "no image" path takes over
- [x] **Imported runs are marked results-only**: the Scan Detail page names the
      machine it came from, explains that the photos stayed there, replaces the
      thumbnail strip with labelled chips (no 404 image icons), and hides review,
      re-upload and mark-reviewed, which all need a frame
- [x] **Reports adapt**: `has_imagery()` drops the per-image pages and gallery when
      a run has no frames, so an imported run produces a 7-page report (cover,
      summary, hazard map, AI analysis, full index) instead of 15 with 8 pages of
      placeholder dashes. Also covers runs whose folders were deleted to free disk
- [x] **Ownership instead of conflict resolution**: `source.machine` is the host
      name, not the compute device - `batch_info["device"]` is the GPU and two
      laptops can share one. Imports are read-only and never overwrite a local run
      without an explicit flag
- [x] Scans page gained a **Cloud** card listing container contents with one-click
      import; hidden entirely when cloud sync is off
- [x] Suite **102 -> 111** tests

## Batch 19 - severity honesty, upload and layout fixes (2026-07-27)

- [x] **Severity thresholds recalibrated against real imagery**: frames hold
      27..102 dead trees (median 60) but High started at 10, so all 67 frames,
      5 runs and 14 sites graded High and the badge meant nothing. Now 80 / 45
      (~p85 / ~p22): the same data gives 1 High + 4 Medium runs and sites split
      6 / 6 / 2. The ladder test now passes thresholds explicitly instead of
      asserting the constants
- [x] **A run badge is the worst image, and now says so**: `severity_mix` and
      `flame_images` ride alongside it, with a stacked bar on the Scans table and
      Dashboard. The 58-image run reads "flame in 6 images - 14 high, 28 medium,
      16 low" instead of a bare HIGH implying 58 bad frames
- [x] **Duplicate site coordinates were a display bug, not clustering**: the
      pairs are 49 m and 77 m apart, correctly separated by the 40 m dedup, but
      printed at 3 decimals (~111 m lat / ~70 m lon here). Now 5 decimals
- [x] **Upload could only ever take one image**: each visit to the file dialog
      replaced the selection, and the dropzone hid once anything was picked so
      there was no way back. Picking now accumulates and de-duplicates; the whole
      page accepts a drop; Mission Folder carries the upload button
- [x] **Mission-folder scan shows real progress**: 12.2 s of silence for 21,200
      images, since every image costs an EXIF read. `scan_source()` reports
      progress and the page polls it beside the blocking request
- [x] **GPU model no longer presented as identity**: `batch_info["machine"]`
      records the host name; reports and "Processed on" use it, and the graphics
      card is gone from the PDF entirely
- [x] Layout: detector status shrunk from the top card to one line; Settings
      moved from a fixed 2-column grid (which left half the page empty) to
      balanced CSS columns; Scan Detail's overloaded header split in two
- [x] Docstrings trimmed 109 lines -> 62, none now longer than its own function
- [x] Suite **112 -> 113** tests

## Batch 20 - open a site, not a run (2026-07-27)

- [x] **Map sites open their own images**: a hazard site is a physical location,
      but clicking one opened the entire flight that happened to include it. The
      popup now links to `?images=a|b|c`, which narrows the viewer, thumbnails
      and detection table to the frames taken there, with a banner and a "show
      the whole run" way back. Review is disabled while filtered, since saving
      would write labels for only the visible subset
- [x] **Scan Detail sidebar leads with the current image**: it described the run
      while the operator flipped through 58 frames one at a time, so the column
      they were watching never moved. "This image" now shows that frame's own
      severity, review state, per-type counts, GPS, altitude and capture time,
      and it redraws with the viewer. Run-level facts moved into a collapsed
      "Whole run" fold whose header still carries the badge and mix bar
- [x] Per-frame grading uses thresholds sent by the server, so it cannot drift
      from Settings the way a hardcoded copy would
- [x] **Mission folder day groups start collapsed**: the newest group auto-opened
      and unrolled 108 flights on load
- [x] Dashboard Recent Scans rows restructured into three tiers (when + verdict,
      what was found, then the id) instead of one wrapping line

## Batch 21 - one red, shared formatting, no Docker (2026-07-27)

- [x] **The two reds were one axis disagreeing with itself.** `SEV` in console.js
      held hex copies of the `:root` tokens; when those were darkened for contrast
      the copies were missed, so the severity mix bar drew `#E05555` while the
      badge beside it drew `#D82929` - on the same row. SEV now references
      `var(--red)` / `var(--amber)` / `var(--green-2)`, and a test fails if a hex
      literal reappears there. The stat-card icons and the locate-a-detection ring
      point at the tokens too
- [x] **Did NOT darken `--kind-flame` to match**, reversing an earlier suggestion:
      measured, `#D82929` drops flame-vs-dark-map contrast to 2.34 (below the 3:1
      WCAG asks of graphics, and pins sit on dark satellite) and collapses
      flame-vs-smoke separation to 1.10, undoing the type distinction. Hazard type
      and severity stay separate axes, as the hard rules require
- [x] **Shared formatting config**: `.editorconfig` plus `.prettierrc.json` with
      `printWidth: 120` - measured against the existing files, where 96% of lines
      already fit, so adopting it does not itself cause a reformat storm.
      Prettier's default 80 would have rewrapped 19%
- [x] **Decided against containerising** and wrote it into the hard rules. The
      product is a Windows desktop app with a native window and local GPU
      inference; a Linux container has neither
- [x] Suite **113 -> 114** tests

## Batch 22 - macOS is a supported target (2026-07-27)

- [x] **Verified on real Mac hardware**, closing the item that had sat under
      "deliberately deferred": macOS 26 / Apple Silicon / Python 3.13, torch 2.13
      on **MPS**, DeepForest and YOLO both loading, a detection job run through
      the real console worker subprocess, and a PDF report generated from it.
      `device.py` needed no change - the CUDA -> MPS -> CPU order was already right
- [x] **Every root script now names its OS**, because two platforms sharing one
      archive is exactly how someone runs the wrong installer and ends up with
      CUDA wheels on a Mac. `install.bat` -> `install-windows.bat`,
      `run_console.bat` -> `run-console-windows.bat`, `run_app.bat` ->
      `run-app-windows.bat`, and the new macOS side is
      `install-macos.command` / `run-console-macos.command` /
      `run-app-macos.command`. `.command` rather than `.sh` so Finder
      double-click works, matching the `.bat` experience instead of asking Mac
      users to open a terminal. The macOS installer also hard-stops on a
      non-Darwin host with a message naming the right script
- [x] **`scripts/install_desktop_app.py` is cross-platform.** It now builds an
      `.icns` with `iconutil` and writes `~/Applications/Wildfire Hazard
      Detection.app` - Info.plist, icon, and a shell launcher pointing at
      `.venv/bin/python`. Deliberately NOT py2app: freezing torch + DeepForest is
      the same 8 GB problem INSTALL.md already rejected on Windows, and a
      three-file bundle keeps working after a `git pull`. The Windows `.lnk` path
      is untouched
- [x] **Fixed the menu bar reading "Python"**: a framework Python re-execs into its
      own `Python.app`, so the bundle's Info.plist never reaches AppKit.
      `desktop.py` overwrites `CFBundleName` at runtime instead. It uses the SHORT
      app name on purpose - the window server clips an owner name at 30 characters
      and the full title is 32
- [x] **Low-priority detection worker on POSIX**: `BELOW_NORMAL_PRIORITY_CLASS` is a
      Windows creation flag with no Unix equivalent, and `preexec_fn` is unsafe
      from the server's threads, so `worker.py` calls `os.nice(10)` on itself
      before torch loads
- [x] **`.gitattributes` pins line endings per file type** (`*.sh` LF, `*.bat`
      CRLF), matching `.editorconfig`. Without it a Windows checkout hands the Mac
      shell scripts with `\r` in the shebang, which fail with an unreadable error
- [x] **`pypdf` added to `requirements-dev.txt`** - two report tests import it and
      AGENTS.md already claimed it was installed, so a fresh venv failed 2 tests
      that had nothing to do with the platform
- [x] **Fixed a blank app icon on macOS 26**, found by looking at the installed
      app rather than the code. `render_icon` used `Image.thumbnail()`, which
      only ever SHRINKS: the mark cropped from `branding/logo.png` is ~200 px, so
      the 256 px `.ico` scaled down to the intended 70% while the 1024 px `.icns`
      kept it at 200 px - 20% of the tile. macOS 26 insets that into its own
      rounded tile, so the app read as a blank white square in Finder and the
      Dock. Now an explicit proportional resize that scales up as well as down.
      `tests/test_app_icon.py` measures the mark's span at every generated size
      and fails below 55% - it fails on the old code at 512 and 1024 and passes
      at 256, which is exactly why eyeballing the `.ico` missed it. The mark is a
      genuine upscale from a 200 px source; a larger `branding/logo.png` sharpens
      it for free
- [x] **A Desktop shortcut, and "it is in Launchpad" removed from every doc.**
      `~/Applications` is not the "Applications" in the Finder sidebar (that is
      `/Applications`), and **macOS 26 deleted Launchpad** - so the two places
      the docs sent people were one wrong folder and one app that no longer
      exists. The installer now also symlinks the bundle onto the Desktop, which
      is what the Windows installer has always done, and re-registers with
      LaunchServices + restarts the Dock so a rebuilt icon appears immediately
      instead of after a logout
- [x] Suite **114 -> 120** tests, green on macOS (4 skipped: the fire/smoke
      weights, and icon sizes too small to measure), and the docs now describe
      two supported platforms

## Batch 23 - the Scans page stops re-reading the card (2026-08-19)

- [x] **`folder_signature` stats directories, not every file.** It runs on EVERY
      /api/source request to decide whether the cached scan is still good, and on
      the 21,200-image folder it walked and stat-ed all 21,494 files: 150 ms of a
      178 ms response, more than the result it exists to protect. The file count
      now comes free from the listing `os.walk` already did and the mtime comes
      from the directories - 89 stats instead of 21,494 - which still moves when
      an entry is added, removed or renamed, i.e. on exactly the changes a
      mission folder makes between visits. Hidden directories are pruned to match
      `scan_source`, so a `.Trashes` write no longer invalidates a scan that
      never read it
- [x] **The scan survives a restart.** The cache lived only in `app.state`, so
      every launch of the console repeated the whole EXIF pass before the Scans
      page could paint. It is now also written to `outputs/.scan_cache.json`
      (atomic replace, last 3 folders, 2.9 MB for this folder) and read back on
      the next launch. Anything unexpected - missing file, half-written JSON, an
      older layout, a signature that moved - returns None and costs a rescan,
      because a stale cache would show a flight list that is not on disk
- [x] **Measured on the real 21,200-image folder**: first paint after a restart
      **13.7 s -> 0.075 s**, warm request **178 ms -> 40 ms**. The /api/source
      payload is byte-for-byte identical across old code, new cold scan and
      cache hit (509,779 bytes, 21,200 images, 218 sessions, 294 ignored)
- [x] Suite **121 -> 131** tests. The two new behaviours are pinned by tests that
      fail on the old code: the hidden-directory case fails against the
      file-stat signature, and the restart case fails with the disk read removed

## Batch 24 - three ways the console failed quietly (2026-08-19)

- [x] **The console stamps its own static files, closing gotcha 11.** Every page
      linked a bare `/static/console.js`, so a browser holding the old HTML and
      the new JS ran a mismatched pair and reported a missing helper that plainly
      exists. `server._page()` now rewrites each `/static/` link with a version
      taken from that file's mtime and size and serves the page `no-cache`; the
      assets keep their ETags, so a matching pair still validates in one 304.
      The "hard-refresh after every update" line is out of AGENTS.md and the
      manual - a "helper is not defined" now means a real bug
- [x] **The macOS app says why it will not open.** The bundle's launcher bakes in
      an absolute path, and `cd "$PROJECT" || exit 1` meant that moving the
      project folder made double-clicking the app do nothing at all: no window,
      no error, nothing in Console. It now raises an alert naming the old path
      and telling the operator to re-run `install-macos.command`, and does the
      same when `.venv` is missing. Windows needed no change - a broken `.lnk`
      raises its own dialog
- [x] **`worker.py` has tests.** It is the process that actually runs detection,
      and the console learns what happened ONLY from the progress file it writes,
      so a worker that died without writing `"error"` left the Scans page
      spinning forever and the suite green. Nine tests pin the protocol -
      running -> done, the error path, a models-failed-to-load path, the atomic
      write, and the settings snapshot - with `detectors`/`pipeline` stubbed so
      they run in 60 ms without torch
- [x] Suite **131 -> 150** tests. Each new behaviour was checked against a
      deliberately broken copy first: reverting the pages to `FileResponse` fails
      10 of them, and dropping the worker's `_write_progress` from its `except`
      fails 2

## Batch 25 - repository hygiene (2026-08-19)

- [x] **Removed `1.JPG`** - a 9.5 MB drone frame committed to the repository root
      by an "Add files via upload", referenced by nothing. It stays in the pack
      history; only a rewrite would reclaim the space, which is not worth
      rewriting shared history for
- [x] **`docker-compose.yml` moved to `mobile/` and labelled a sketch.** At the
      root it read as "this project runs on Docker", which is the opposite of
      hard rule 9. It also does not run: it builds from a `./backend` that does
      not exist and points at an `ai-service:8001` the file never defines.
      Deliberately NOT built out - nothing calls it. The desktop console has no
      services by design and the phone reads Azure Blob directly, so writing
      that backend would be starting a new subsystem, not finishing this one
- [x] **The mobile SAS token left the source tree.** `sync.js` held a
      `PASTE_YOUR_..._HERE` constant, which is an invitation to commit a
      credential; it now reads `EXPO_PUBLIC_WILDFIRE_SAS_URL` from
      `.env.local` (already gitignored) and fails with a sentence naming the
      variable instead of a bare "Network request failed"
- [x] **`mobile/README.md`**, the folder had none: what the app is and is not,
      how to run it, how to configure the credential, and what the compose file
      is. It also records a mismatch found while writing it - the header claimed
      "Read + List only" but `uploadNotes` PUTs a blob, so field-note sync-back
      needs Create + Write and returns 403 on a read-only token
- [x] **The mobile app is now visible from the entry-point docs.** Neither
      README.md nor AGENTS.md mentioned a whole subsystem living in the repo

## Batch 26 - runs that survive a move, and 36% off every image (2026-08-20)

- [x] **`batch.json` stores derived images relative to their run folder.** The
      three per-image artifacts were written as absolute paths, so moving the
      project into `SAIT/` left the one real run serving `original_url`,
      `annotated_url` and `gridmap_url` all `None` - no boxes, no grid map, no
      original, and "no imagery is stored for this run" in the PDF - while the
      files sat untouched on disk. Same bug class as the launcher in batch 24.
      Runs written the old way recover too: an absolute path that no longer
      resolves is re-anchored on the run folder name rather than given up on.
      `path` stays absolute on purpose - it is the operator's source photo,
      outside the run, and it identifies an image across re-runs
- [x] **Crowns are filtered before the alive/dead classifier runs.**
      `predict_tile(crop_model=...)` classified every crown DeepForest found and
      `predict` then threw away everything below `conf_threshold`: measured at
      1,406 crowns for 270 keepers (19%), so ~17 of 44 seconds per image went
      into classifying trees the run would never report. Split into two calls -
      detect crowns, filter, classify the survivors. **44.8 s -> 28.6 s per
      image, and the boxes and scores come out identical.** On a 200-image
      flight that is roughly 54 minutes
- [x] **The fast path degrades rather than breaking.** It calls the private
      helper `predict_tile` itself uses; if a future DeepForest moves it, the
      detector says so and falls back to classifying every crown. Slower than
      before the split (it re-runs the tiling), which is the right trade for a
      path that only fires on a library change and announces itself
- [x] **Measured first, and the measurement changed the plan.** Model loading is
      7.5 s per job (DeepForest 5.6, YOLO 0.3) against 47 s PER IMAGE, so it is
      0.7% of a 20-image run and 0.1% of a 200-image one - not worth touching.
      DeepForest was 95% of per-image time; that is where the work went
- [x] Suite **150 -> 159** tests. Both halves are pinned by tests that fail on
      the old code, and the two-stage output was diffed box-for-box and
      score-for-score against the one-call path

## Batch 27 - detect at the scale the model was trained on (2026-08-20)

- [x] **GSD is computed from EXIF** (`gps.ground_sample_distance_cm`), using the
      35mm-equivalent focal length so it needs no per-camera sensor table, and
      DJI's `RelativeAltitude` rather than GPS altitude - the latter reads 1354 m
      over Canmore, which is the valley floor, not the 53 m the drone was flying
- [x] **DeepForest now sees frames at roughly the scale it was trained on.**
      NEON is ~10 cm/px; the L2 at 53 m gives 1.4 cm/px. At 7x too fine the model
      called branches, shrubs and dead grass "crowns": 70 detections on one frame
      with a MEDIAN width of **0.9 m of ground**, boxes on shadow and bare earth.
      Resampled to 4 cm/px the same frame gives 14 detections at 1.7 m median,
      the ground false positives are gone, and it runs in **5.3 s instead of
      28.4 s**. `df_target_gsd_cm` (0 disables); frames whose EXIF cannot give a
      GSD are left at native scale rather than guessed at
- [x] **Recall is NOT fixed by this and should not be claimed as fixed.** The
      frame still holds many obvious grey snags that go undetected. Precision and
      speed improved; recall is limited by what a crown detector plus an
      alive/dead crown classifier can express
- [x] **Measured what the detectors cannot do**, so the manual can say it:
      a snag that has lost its crown and any fallen tree are invisible (no crown
      to detect - and downed wood is fuel); the box covers the crown, not the
      tree, because the photo is nadir and the trunk is under its own crown; and
      the flame/smoke model fired on **13 of 30 fire-free frames** (17 false
      flames, scores 0.30-0.61, none above 0.70)
- [x] Suite **159 -> 168** tests

## Batch 28 - two bugs the operator found (2026-08-20)

- [x] **A deleted detection stays deleted.** `labels.json` records confirmed
      boxes, so an image the reviewer emptied was indistinguishable from one
      never opened, and both fell back to the model's proposals. Delete a false
      flame, save, go back - and it was still on the detail page, still colouring
      its thumbnail, and still a pin on the Map, which never read labels.json at
      all and so also fed the hazard-type filter a hazard the operator had
      rejected. A save now records `reviewed_images`, its coverage; one
      `data.review_verdict()` / `boxes_for()` pair serves the detail page and
      the map. Files written before this keep their old meaning on purpose - an
      old file cannot say what was looked at, and hiding unreviewed hazards is
      the worse error
- [x] **`_count_types` read only `display`**, so every reviewer-confirmed box
      (which carries `class`) would have counted as "?" the moment the map
      started reading verdicts. Found by wiring the two together
- [x] **Zoom shows real detail instead of a magnified thumbnail.** The viewer
      zoomed with `transform: scale()`, which enlarges the bitmap the browser
      already rasterised: a 5280 px frame fitted to 596 px, so 12x displayed
      that same 596 px of data - the operator was told to look closely at detail
      the browser had thrown away. Zoom is an explicit width on `#stage` now, so
      the browser re-rasterises from the source: **596 px -> 3575 px of real
      pixels at 6x**, measured in a real browser. Two traps found doing it -
      `#stage` is a flex item and flex-shrink squashed the width straight back
      down, and the fit-to-box `max-height` had to be lifted while zoomed
- [x] **The box editor still lines up**, checked at 1x and 5x in the browser:
      the SVG overlay tracks the frame to the pixel, and a click at the centre
      maps to (2640, 1978) of a 5280x3956 image at both zooms
- [x] Suite **168 -> 174** tests

## Batch 29 - a confidence floor per hazard type (2026-08-20)

- [x] **Flame and smoke now need 0.60 where dead trees need 0.30.** One global
      threshold cannot serve both: the crown model is reasonable at 0.30, while
      the fire model was trained on fire photographed from the ground and reads
      sunlit dead grass as flame from a nadir frame. Over 36 fire-free frames
      from real flights it emitted 19 false flames and 11 false smokes across
      16 of them; with the floor, **two flames on two frames and no smoke**
- [x] **Smoke is no better than flame, it just fails more quietly.** Probing at
      0.05 on the same frames: flame gave 175 activations (median 0.09, max 0.76),
      smoke 73 (median 0.13, max 0.58). The class is not dormant - it
      hallucinates too, with lower scores. 0.60 clears every false smoke; one
      false flame still survives 0.70
- [x] **Applied where detections are merged**, not inside a detector, so it
      holds for whatever produced the box - and still will when the ONNX
      dead-tree model replaces the DeepForest path
- [x] **The caveat is recorded in the code, the manual and here, because it
      matters more than the number**: this is tuned against false positives
      ONLY. No imagery we hold contains fire, so nobody has measured what 0.60
      costs in recall. It buys a usable flame marker at an unmeasured price
- [x] Suite **174 -> 183** tests

## Batch 30 - Windows parity for the day's work (2026-08-20)

- [x] **Audited every change from batches 23-29 against Windows**, because all of
      it was written and measured on a Mac. Two real defects, the rest clean
- [x] **Cross-platform paths in `run_absolute`.** Absoluteness is not portable:
      a macOS path has no drive letter so `WindowsPath` calls it relative, and a
      `D:\...` path is relative to `PosixPath`. A run folder copied between the
      two machines therefore took the relative branch and had its foreign
      absolute path glued onto the run directory. Now judged with both
      `PurePosixPath` and `PureWindowsPath`, and split with the latter since it
      accepts either separator. Four flavours resolve - POSIX and Windows,
      relative and absolute - and what gets WRITTEN is still always POSIX
- [x] **`TemporaryDirectory(ignore_cleanup_errors=True)`** for the resampled
      frame. Windows will not delete a file something still holds open and
      GDAL/rasterio are famous for it; a leaked temp folder beats a
      PermissionError killing the detection of an otherwise fine image
- [x] **Checked and found clean**: the installer's Windows branch (one comment
      line changed, no code); `Path.replace` for the scan cache and the worker
      progress file (`os.replace` overwrites atomically on both, unlike
      `os.rename`); `st_mtime_ns`/`st_size` for the asset stamps; no hard-coded
      separators outside URLs, which are correctly POSIX. The scan signature
      also carries a file COUNT, so it still invalidates on add or remove even
      where a filesystem is lazy about directory mtimes
- [x] **Not verifiable from here, stated rather than claimed**: the zoom fix is
      standard CSS on WebView2, which is Chromium, and the same engine family
      the change was tested in - but nobody has run it on Windows yet
- [x] Suite **183 -> 190** tests, the new ones running identically on either OS

## Waiting on external conditions

- [ ] **Plug in the trained dead_tree.onnx** (model still training on Azure; drop it
      into models/ and adjust the preprocessing switches in Settings)
- [ ] **Full-flight shakedown on 100-200 real images**

## Deliberately deferred (roadmap talking points)

- [ ] Detection dedup tiers 2/3 (geometric projection -> ODM orthomosaic)
- [ ] Slimmer reports for 200-image flights + GeoJSON export
- [ ] Pipeline parallelism (switching to the ONNX model is itself the biggest speedup)
- [ ] Intel Mac verification (Apple Silicon is done; Intel is CPU-only, same code path)
- [ ] The Alerts feature itself

## Completed (archive)

- [x] Layer 1 detection (SAHI + YOLO flame/smoke + DeepForest dead-tree proposals)
- [x] Layer 1.5 human review -> labels.json training set (box editor built into the console)
- [x] Layer 2 reporting (LM Studio with graceful fallback + ReportLab PDF,
      timestamped filenames that never overwrite)
- [x] ONNX detector plugin (adapts to Custom Vision / YOLO exports, drop-in)
- [x] Custom Vision dataset export + upload scripts (tiling matched to inference)
- [x] Six-page operations console: Dashboard / Scans / Review / Map / Reports / Settings
- [x] Mission-folder ingest (grouped by day/flight, telemetry files ignored)
- [x] Display-level severity (dead-tree density driven, configurable thresholds)
- [x] Sorted run outputs (originals / annotated / gridmaps)
- [x] Desktop application form (pywebview native window + icon + shortcuts)
- [x] Windows and macOS both supported (CUDA / Metal-MPS / CPU, per-platform installers)
- [x] Automated test suite
