// Wildfire Hazard Field Viewer — React Native (Expo)
// Mobile viewer for the team's Wildfire Hazard Detection System.
// Reads the results schema cloud_sync.py uploads; no AI runs on the phone.
// Features: Azure sync + offline cache, run picker, risk/type filters,
// live GPS distance, compass dial, in-app wayfinding, native maps handoff,
// field notes with sync-back to Azure. UI: Apple-style bottom sheet.

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  SafeAreaView, View, Text, StyleSheet, FlatList, TouchableOpacity,
  StatusBar, Platform, Linking, TextInput, ScrollView, Modal, Image,
  Animated, Keyboard, Easing,
} from "react-native";
import MapView, { Marker, Polyline } from "react-native-maps";
import * as Location from "expo-location";
import { Magnetometer } from "expo-sensors";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  syncFromAzure, loadCache, isConfigured, listRunIds, uploadNotes,
} from "./sync";

const SAMPLE = require("./assets/sample_results.json"); // fallback until first sync
const LOGO = require("./assets/logo.png");                // project mark, matches desktop console

const RISK_COLORS = { High: "#D93A3A", Medium: "#E5A50A", Low: "#3A7D44" };

// ── Build hazard "sites" from the results schema ────────────────────
function buildSites(results) {
  const sites = [];
  (results.images || []).forEach((im, idx) => {
    if (!im.gps || !im.detections || im.detections.length === 0) return;
    const [lat, lon] = im.gps;
    const counts = {};
    im.detections.forEach((d) => { counts[d.display] = (counts[d.display] || 0) + 1; });
    let risk = "Low";
    if (counts["Flame"]) risk = "High";
    else if (counts["Smoke"]) risk = "Medium";
    else if ((counts["Dead Tree"] || 0) >= 3) risk = "Medium";
    sites.push({
      id: `${results.run_id}-${idx}`,
      name: im.name, lat, lon, risk, counts,
      timestamp: im.timestamp,
      reviewed: (results.reviewed_images || []).includes(im.name),
    });
  });
  return sites;
}

// ── Field notes stored on-phone (visited flag + text per site) ──────
const noteKey = (siteId) => `wildfire.note.${siteId}`;
async function loadNotes(siteIds) {
  const pairs = await AsyncStorage.multiGet(siteIds.map(noteKey));
  const out = {};
  pairs.forEach(([k, v]) => { if (v) out[k.replace("wildfire.note.", "")] = JSON.parse(v); });
  return out;
}
async function saveNote(siteId, note) {
  await AsyncStorage.setItem(noteKey(siteId), JSON.stringify(note));
}

// ── Geometry: distance, bearing, compass words ──────────────────────
function distanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
function bearingDeg(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compassWord = (deg) => COMPASS[Math.round(deg / 45) % 8];
function fmtDistance(m) {
  if (m == null) return "—";
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

// Native maps handoff (useful for the on-road drive to the site)
function navigateTo(site) {
  const { lat, lon } = site;
  const url = Platform.select({
    ios: `http://maps.apple.com/?daddr=${lat},${lon}&dirflg=w`,
    android: `google.navigation:q=${lat},${lon}&mode=w`,
  });
  Linking.openURL(url).catch(() => {
    Linking.openURL(`https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=walking`);
  });
}

export default function App() {
  const [results, setResults] = useState(SAMPLE);
  const [meta, setMeta] = useState(null);
  const [syncState, setSyncState] = useState({ busy: false, msg: null });
  const [runList, setRunList] = useState([]);
  const [runPickerOpen, setRunPickerOpen] = useState(false);
  const [riskFilter, setRiskFilter] = useState("All");
  const [typeFilter, setTypeFilter] = useState("All");
  const [notes, setNotes] = useState({});
  const [noteDraft, setNoteDraft] = useState("");
  const [notesSync, setNotesSync] = useState(null);
  const [selected, setSelected] = useState(null);
  const [guiding, setGuiding] = useState(null);
  const [me, setMe] = useState(null);
  const [gpsDenied, setGpsDenied] = useState(false);
  const [heading, setHeading] = useState(null);

  // Keyboard offset for the bottom sheet (see effect below).
  const kbOffset = useRef(new Animated.Value(0)).current;
  // Backdrop opacity behind the sheet.
  const scrim = useRef(new Animated.Value(0)).current;

  // Cache-first startup: last synced run, else bundled sample.
  useEffect(() => {
    (async () => {
      const { results: cached, meta: m } = await loadCache();
      if (cached) { setResults(cached); setMeta(m); }
    })();
  }, []);

  // Phone GPS (satellites — works offline).
  useEffect(() => {
    let sub;
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") { setGpsDenied(true); return; }
      sub = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.Balanced, distanceInterval: 5 },
        (loc) => setMe({ lat: loc.coords.latitude, lon: loc.coords.longitude })
      );
    })();
    return () => sub && sub.remove();
  }, []);

  // Fade the backdrop in when a hazard is selected.
  useEffect(() => {
    Animated.timing(scrim, {
      toValue: selected ? 1 : 0,
      duration: 180,
      useNativeDriver: true, // opacity can be native-driven
    }).start();
  }, [selected]);

  // Keyboard-aware bottom sheet.
  // iOS floats the keyboard OVER the app, so an absolutely-positioned sheet
  // pinned to bottom:0 gets covered. We lift it by the keyboard height using
  // the "will" events, which fire before the keyboard moves and carry Apple's
  // own animation duration — so the sheet travels with it, not after it.
  // Android (Expo default softwareKeyboardLayoutMode: "resize") already
  // shrinks the window, so bottom:0 is above the keyboard — no offset needed.
  useEffect(() => {
    if (Platform.OS !== "ios") return;
    const ease = Easing.bezier(0.17, 0.59, 0.4, 0.77); // matches iOS keyboard curve
    const slide = (toValue, e) =>
      Animated.timing(kbOffset, {
        toValue,
        duration: e?.duration || 250,
        easing: ease,
        useNativeDriver: false, // "bottom" is a layout prop — cannot be native-driven
      }).start();
    const show = Keyboard.addListener("keyboardWillShow", (e) =>
      slide(e.endCoordinates.height, e));
    const hide = Keyboard.addListener("keyboardWillHide", (e) => slide(0, e));
    return () => { show.remove(); hide.remove(); };
  }, []);

  // Compass heading from the magnetometer (offline sensor).
  useEffect(() => {
    Magnetometer.setUpdateInterval(250);
    const sub = Magnetometer.addListener(({ x, y }) => {
      let deg = Math.atan2(y, x) * (180 / Math.PI);
      setHeading((deg + 360) % 360);
    });
    return () => sub.remove();
  }, []);

  const sites = useMemo(() => buildSites(results), [results]);

  useEffect(() => {
    loadNotes(sites.map((s) => s.id)).then(setNotes);
  }, [results]);

  const visibleSites = useMemo(() => sites.filter((s) => {
    if (riskFilter !== "All" && s.risk !== riskFilter) return false;
    if (typeFilter !== "All" && !s.counts[typeFilter]) return false;
    return true;
  }), [sites, riskFilter, typeFilter]);

  const initialRegion = useMemo(() => {
    if (sites.length === 0)
      return { latitude: 56.587, longitude: -114.6, latitudeDelta: 0.05, longitudeDelta: 0.05 };
    const lats = sites.map((s) => s.lat);
    const lons = sites.map((s) => s.lon);
    return {
      latitude: (Math.min(...lats) + Math.max(...lats)) / 2,
      longitude: (Math.min(...lons) + Math.max(...lons)) / 2,
      latitudeDelta: Math.max(0.012, (Math.max(...lats) - Math.min(...lats)) * 2.2),
      longitudeDelta: Math.max(0.012, (Math.max(...lons) - Math.min(...lons)) * 2.2),
    };
  }, [sites]);

  const withDistance = (site) => {
    if (!me || !site) return { d: null, dir: null };
    const d = distanceM(me.lat, me.lon, site.lat, site.lon);
    const dir = compassWord(bearingDeg(me.lat, me.lon, site.lat, site.lon));
    return { d, dir };
  };

  // Arrow = bearing to hazard minus the direction the phone faces.
  function arrowRotation(site) {
    if (!me || heading == null) return null;
    const b = bearingDeg(me.lat, me.lon, site.lat, site.lon);
    return (b - heading + 360) % 360;
  }

  async function onSync(runId = null) {
    if (!isConfigured()) {
      setSyncState({ busy: false, msg: "Set EXPO_PUBLIC_WILDFIRE_SAS_URL in .env.local" });
      return;
    }
    setSyncState({ busy: true, msg: "Syncing from Azure…" });
    try {
      const fresh = await syncFromAzure(runId);
      setResults(fresh);
      setMeta({ syncedAt: new Date().toISOString(), runId: fresh.run_id });
      setSyncState({ busy: false, msg: null });
      setSelected(null);
      try { setRunList(await listRunIds()); } catch { }
    } catch (e) {
      setSyncState({ busy: false, msg: `Sync failed: ${e.message}` });
    }
  }

  async function openRunPicker() {
    setRunPickerOpen(true);
    if (runList.length === 0 && isConfigured()) {
      try { setRunList(await listRunIds()); } catch { }
    }
  }

  async function onUploadNotes() {
    setNotesSync("Uploading notes…");
    try {
      await uploadNotes(results.run_id, notes);
      setNotesSync(`Notes uploaded ${new Date().toLocaleTimeString()}`);
    } catch (e) {
      setNotesSync(`Upload failed: ${e.message}`);
    }
  }

  // Single exit path for the sheet: drop the keyboard, clear any half-typed
  // note, deselect. Used by the backdrop and the close button alike.
  function closeSheet() {
    Keyboard.dismiss();
    setNoteDraft("");
    setSelected(null);
  }

  const countsLine = (c) => Object.entries(c).map(([k, v]) => `${v} ${k}`).join(" · ");

  return (
    <SafeAreaView style={st.root}>
      <StatusBar barStyle="dark-content" />

      {/* Header — brand lockup mirrors the desktop Operations Console */}
      <View style={st.header}>
        <View style={st.brandRow}>
          <Image source={LOGO} style={st.logo} resizeMode="contain" />
          <View style={st.brandDivider} />
          <View style={{ flex: 1 }}>
            <Text style={st.brandTitle} numberOfLines={1}
              adjustsFontSizeToFit minimumFontScale={0.85}>
              Wildfire Hazard Detection System
            </Text>
            <Text style={st.brandSub} numberOfLines={1}>Field Viewer · Offline</Text>
          </View>
        </View>

        <View style={st.runRow}>
          <TouchableOpacity style={{ flex: 1 }} onPress={openRunPicker}>
            <Text style={st.subtitle} numberOfLines={1}>
              Run <Text style={st.mono}>{results.run_id}</Text> ▾ ·{" "}
              {visibleSites.length}/{sites.length} sites
            </Text>
          </TouchableOpacity>
          <TouchableOpacity style={st.syncBtn} onPress={() => onSync()} disabled={syncState.busy}>
            <Text style={st.syncBtnText}>{syncState.busy ? "Syncing…" : "Sync"}</Text>
          </TouchableOpacity>
        </View>

        <Text style={st.syncMeta} numberOfLines={1}>
          {syncState.msg
            ? syncState.msg
            : meta
              ? `Last synced ${new Date(meta.syncedAt).toLocaleString()} — works offline`
              : "Showing bundled sample — press Sync to pull the latest run"}
        </Text>
      </View>

      {/* Filter chips */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={st.chips}>
        {["All", "High", "Medium", "Low"].map((r) => (
          <TouchableOpacity key={r}
            style={[st.chip, riskFilter === r && st.chipOn]}
            onPress={() => setRiskFilter(r)}>
            <Text style={[st.chipText, riskFilter === r && st.chipTextOn]}>{r}</Text>
          </TouchableOpacity>
        ))}
        <View style={st.chipDivider} />
        {["All", "Flame", "Smoke", "Dead Tree", "Fallen Tree"].map((t) => (
          <TouchableOpacity key={"t" + t}
            style={[st.chip, typeFilter === t && st.chipOn]}
            onPress={() => setTypeFilter(t)}>
            <Text style={[st.chipText, typeFilter === t && st.chipTextOn]}>{t}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Map */}
      <MapView style={st.map} initialRegion={initialRegion} showsUserLocation>
        {visibleSites.map((s) => (
          <Marker key={s.id}
            coordinate={{ latitude: s.lat, longitude: s.lon }}
            pinColor={RISK_COLORS[s.risk]}
            onPress={() => setSelected(s)} />
        ))}
      </MapView>

      {/* Hazard list */}
      <FlatList
        style={st.list}
        data={visibleSites}
        keyExtractor={(s) => s.id}
        renderItem={({ item }) => {
          const { d, dir } = withDistance(item);
          return (
            <TouchableOpacity style={st.row} onPress={() => setSelected(item)}>
              <View style={[st.riskDot, { backgroundColor: RISK_COLORS[item.risk] }]} />
              <View style={{ flex: 1 }}>
                <Text style={st.rowTitle}>
                  {notes[item.id]?.visited ? "✓ " : ""}{item.risk} · {countsLine(item.counts)}
                </Text>
                <Text style={st.rowSub}>{item.name}</Text>
              </View>
              <Text style={st.rowDist}>{me ? `${fmtDistance(d)} ${dir}` : ""}</Text>
            </TouchableOpacity>
          );
        }}
      />

      {/* Selected hazard — Apple-style bottom sheet */}
      {selected && (() => {
        const { d, dir } = withDistance(selected);
        const rot = arrowRotation(selected);
        return (
          <>
            {/* Backdrop: tap anywhere outside the sheet to dismiss it. */}
            <Animated.View style={[st.scrim, { opacity: scrim }]}>
              <TouchableOpacity style={{ flex: 1 }} activeOpacity={1} onPress={closeSheet} />
            </Animated.View>

            <Animated.View style={[st.sheet, { bottom: kbOffset }]}>
            <View style={st.grabber} />

            <View style={st.sheetHead}>
              <View style={[st.riskPillBadge, { backgroundColor: RISK_COLORS[selected.risk] }]}>
                <Text style={st.riskPillText}>{selected.risk.toUpperCase()}</Text>
              </View>
              {selected.reviewed && <Text style={st.reviewedTag}>✓ Reviewed</Text>}
              <View style={{ flex: 1 }} />
              <TouchableOpacity style={st.closeCircle} onPress={closeSheet}>
                <Text style={st.closeGlyph}>✕</Text>
              </TouchableOpacity>
            </View>

            <Text style={st.sheetTitle}>{countsLine(selected.counts)}</Text>
            <Text style={st.sheetSub}>
              {selected.lat.toFixed(5)}°N, {Math.abs(selected.lon).toFixed(5)}°W
              {"  ·  "}{selected.name}
            </Text>

            <View style={st.guideRow}>
              <View style={{ flex: 1 }}>
                <Text style={st.distBig}>{me ? fmtDistance(d) : "—"}</Text>
                <Text style={st.distSub}>
                  {me ? `${dir} of you` : gpsDenied ? "location off" : "locating…"}
                </Text>
              </View>
              {rot != null && (
                <View style={st.dial}>
                  <Text style={[st.dialArrow,
                  { transform: [{ rotate: `${(rot - 90 + 360) % 360}deg` }] }]}>➤</Text>
                </View>
              )}
            </View>

            <TouchableOpacity style={st.primaryBtn}
              onPress={() => { const s = selected; closeSheet(); setGuiding(s); }}>
              <Text style={st.primaryBtnText}>Guide Me There</Text>
            </TouchableOpacity>

            <View style={st.secondaryRow}>
              <TouchableOpacity style={st.secondaryBtn} onPress={() => navigateTo(selected)}>
                <Text style={st.secondaryBtnText}>Open in Maps</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[st.secondaryBtn, notes[selected.id]?.visited && st.secondaryBtnOn]}
                onPress={async () => {
                  const cur = notes[selected.id] || {};
                  const next = { ...cur, visited: !cur.visited };
                  await saveNote(selected.id, next);
                  setNotes({ ...notes, [selected.id]: next });
                }}>
                <Text style={st.secondaryBtnText}>
                  {notes[selected.id]?.visited ? "✓ Visited" : "Mark Visited"}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity style={st.secondaryBtn} onPress={onUploadNotes}>
                <Text style={st.secondaryBtnText}>☁ Sync Notes</Text>
              </TouchableOpacity>
            </View>

            <TextInput
              style={st.noteInput}
              placeholder="Add a field note…"
              placeholderTextColor="#A9B2AB"
              returnKeyType="done"
              blurOnSubmit
              value={noteDraft !== "" ? noteDraft : notes[selected.id]?.text || ""}
              onChangeText={setNoteDraft}
              onEndEditing={async () => {
                const cur = notes[selected.id] || {};
                const next = { ...cur, text: noteDraft || cur.text || "" };
                await saveNote(selected.id, next);
                setNotes({ ...notes, [selected.id]: next });
                setNoteDraft("");
              }}
            />
            {notesSync && <Text style={st.notesSyncMsg}>{notesSync}</Text>}
            </Animated.View>
          </>
        );
      })()}

      {/* In-app guidance: full-screen offline wayfinding */}
      {guiding && (() => {
        const { d, dir } = withDistance(guiding);
        const rot = arrowRotation(guiding);
        return (
          <View style={st.guide}>
            <TouchableOpacity style={st.guideClose} onPress={() => setGuiding(null)}>
              <Text style={st.guideCloseText}>✕ End</Text>
            </TouchableOpacity>
            <Text style={st.guideRisk}>{guiding.risk} risk · {countsLine(guiding.counts)}</Text>
            {rot != null ? (
              <Text style={[st.guideArrow,
              { transform: [{ rotate: `${(rot - 90 + 360) % 360}deg` }] }]}>➤</Text>
            ) : (
              <Text style={st.guideWaiting}>Waiting for GPS + compass…</Text>
            )}
            <Text style={st.guideDistance}>{me ? fmtDistance(d) : "—"}</Text>
            <Text style={st.guideDir}>{me ? `head ${dir}` : "locating you…"}</Text>
            <MapView
              style={st.guideMap}
              showsUserLocation
              region={{
                latitude: me ? (me.lat + guiding.lat) / 2 : guiding.lat,
                longitude: me ? (me.lon + guiding.lon) / 2 : guiding.lon,
                latitudeDelta: me ? Math.max(0.005, Math.abs(me.lat - guiding.lat) * 2.6) : 0.02,
                longitudeDelta: me ? Math.max(0.005, Math.abs(me.lon - guiding.lon) * 2.6) : 0.02,
              }}>
              <Marker coordinate={{ latitude: guiding.lat, longitude: guiding.lon }}
                pinColor={RISK_COLORS[guiding.risk]} />
              {me && (
                <Polyline
                  coordinates={[
                    { latitude: me.lat, longitude: me.lon },
                    { latitude: guiding.lat, longitude: guiding.lon },
                  ]}
                  strokeColor="#B4451F" strokeWidth={3} lineDashPattern={[8, 6]} />
              )}
            </MapView>
            <Text style={st.guideNote}>
              Straight-line bearing — offline, GPS + compass only. Watch your footing.
            </Text>
          </View>
        );
      })()}

      {/* Run picker */}
      <Modal visible={runPickerOpen} transparent animationType="fade"
        onRequestClose={() => setRunPickerOpen(false)}>
        <TouchableOpacity style={st.modalBg} onPress={() => setRunPickerOpen(false)}>
          <View style={st.modalBox}>
            <Text style={st.modalTitle}>Choose a run</Text>
            {runList.length === 0 && (
              <Text style={st.modalEmpty}>No runs loaded — press Sync first.</Text>
            )}
            {runList.map((id) => (
              <TouchableOpacity key={id} style={st.modalRow}
                onPress={() => { setRunPickerOpen(false); onSync(id); }}>
                <Text style={[st.modalRowText,
                id === results.run_id && { fontWeight: "700", color: "#2D5A3D" }]}>
                  {id === results.run_id ? "● " : ""}{id}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </TouchableOpacity>
      </Modal>
    </SafeAreaView>
  );
}

const st = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#FFFFFF", paddingTop: Platform.OS === "android" ? 28 : 0 },

  // White app bar, lifted off the recessed filter strip below it.
  header: {
    paddingHorizontal: 16, paddingTop: 10, paddingBottom: 11,
    backgroundColor: "#FFFFFF",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#D9D7CC",
    shadowColor: "#000", shadowOpacity: 0.05, shadowRadius: 5,
    shadowOffset: { width: 0, height: 2 }, elevation: 3, zIndex: 10,
  },
  brandRow: { flexDirection: "row", alignItems: "center" },
  logo: { width: 76, height: 21 },
  brandDivider: {
    width: StyleSheet.hairlineWidth, height: 26,
    backgroundColor: "#D9D7CC", marginHorizontal: 10,
  },
  brandTitle: { fontSize: 15.5, fontWeight: "700", color: "#1C2B22", letterSpacing: -0.3 },
  brandSub: { fontSize: 10.5, color: "#8B978E", marginTop: 1 },

  runRow: { flexDirection: "row", alignItems: "center", marginTop: 10 },
  subtitle: { fontSize: 12.5, color: "#2D5A3D", fontWeight: "600" },
  mono: { fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", fontSize: 11.5 },
  syncMeta: { fontSize: 11, color: "#8B978E", marginTop: 6 },
  syncBtn: {
    backgroundColor: "#2D5A3D", paddingHorizontal: 14, paddingVertical: 7,
    borderRadius: 9, marginLeft: 10,
  },
  syncBtnText: { color: "#FFFFFF", fontSize: 13, fontWeight: "700" },

  chips: {
    maxHeight: 44, paddingHorizontal: 10,
    backgroundColor: "#F1F0EA",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E2E0D6",
  },
  chip: {
    paddingHorizontal: 13, paddingVertical: 7, borderRadius: 20,
    borderWidth: 1, borderColor: "#E8E6DC", backgroundColor: "#FFFFFF",
    marginRight: 7, marginVertical: 6,
  },
  chipOn: { backgroundColor: "#2D5A3D", borderColor: "#2D5A3D" },
  chipText: { fontSize: 12.5, color: "#5A6B60", fontWeight: "600" },
  chipTextOn: { color: "#FFFFFF" },
  chipDivider: { width: 1, backgroundColor: "#E8E6DC", marginVertical: 10, marginRight: 7 },

  map: { flex: 1.4 },
  list: { flex: 1, backgroundColor: "#FFFFFF" },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingHorizontal: 16, paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderColor: "#E8E6DC",
  },
  riskDot: { width: 12, height: 12, borderRadius: 6 },
  rowTitle: { fontSize: 14, fontWeight: "600", color: "#1C2B22" },
  rowSub: { fontSize: 12, color: "#8B978E", marginTop: 1 },
  rowDist: { fontSize: 12, color: "#2D5A3D", fontWeight: "600" },

  // Backdrop sits above the map/list/header (header is zIndex 10) but
  // below the sheet, so any tap outside the sheet lands here and closes it.
  scrim: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(28,43,34,0.18)", zIndex: 20,
  },

  // Bottom sheet (Apple pattern: grabber, rounded top, one primary action)
  sheet: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    maxHeight: "82%", zIndex: 30,
    backgroundColor: "#FFFFFF",
    borderTopLeftRadius: 28, borderTopRightRadius: 28,
    paddingHorizontal: 20, paddingTop: 8, paddingBottom: 28,
    shadowColor: "#1C2B22", shadowOpacity: 0.18, shadowRadius: 24,
    shadowOffset: { width: 0, height: -6 }, elevation: 16,
  },
  grabber: {
    alignSelf: "center", width: 36, height: 5, borderRadius: 3,
    backgroundColor: "#E3E2DB", marginBottom: 12,
  },
  sheetHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  riskPillBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20 },
  riskPillText: { color: "#FFFFFF", fontSize: 11, fontWeight: "800", letterSpacing: 0.8 },
  reviewedTag: { fontSize: 12, color: "#3A7D44", fontWeight: "600" },
  closeCircle: {
    width: 30, height: 30, borderRadius: 15, backgroundColor: "#F2F1EC",
    alignItems: "center", justifyContent: "center",
  },
  closeGlyph: { fontSize: 13, color: "#5A6B60", fontWeight: "700" },
  sheetTitle: { fontSize: 22, fontWeight: "700", letterSpacing: -0.4, color: "#1C2B22" },
  sheetSub: { fontSize: 13, color: "#8B978E", marginTop: 3 },
  guideRow: { flexDirection: "row", alignItems: "center", marginTop: 14, marginBottom: 16 },
  distBig: { fontSize: 34, fontWeight: "800", letterSpacing: -0.8, color: "#1C2B22" },
  distSub: { fontSize: 13.5, color: "#2D5A3D", fontWeight: "600", marginTop: 1 },
  dial: {
    width: 64, height: 64, borderRadius: 32, borderWidth: 1.5, borderColor: "#E8E6DC",
    alignItems: "center", justifyContent: "center", backgroundColor: "#FBFAF6",
  },
  dialArrow: { fontSize: 28, color: "#B4451F" },
  primaryBtn: { backgroundColor: "#2D5A3D", borderRadius: 14, paddingVertical: 15, alignItems: "center" },
  primaryBtnText: { color: "#FFFFFF", fontSize: 16.5, fontWeight: "700", letterSpacing: -0.2 },
  secondaryRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  secondaryBtn: {
    flex: 1, backgroundColor: "#F2F1EC", borderRadius: 12,
    paddingVertical: 11, alignItems: "center",
  },
  secondaryBtnOn: { backgroundColor: "#EBF2EB" },
  secondaryBtnText: { color: "#1C2B22", fontSize: 12.5, fontWeight: "600" },
  noteInput: {
    borderWidth: 1, borderColor: "#E8E6DC", borderRadius: 12,
    paddingHorizontal: 12, paddingVertical: 9, marginTop: 10, fontSize: 13.5,
    color: "#1C2B22", backgroundColor: "#FBFAF6",
  },
  notesSyncMsg: { fontSize: 11.5, color: "#5A6B60", marginTop: 6 },

  // Guidance overlay
  guide: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "#FBFAF6", alignItems: "center", paddingTop: 70, zIndex: 50,
  },
  guideClose: { position: "absolute", top: 54, right: 18 },
  guideCloseText: { fontSize: 14, color: "#B4451F", fontWeight: "700" },
  guideRisk: { fontSize: 15, fontWeight: "600", color: "#5A6B60", marginBottom: 12 },
  guideArrow: { fontSize: 130, color: "#B4451F", lineHeight: 150 },
  guideWaiting: { fontSize: 15, color: "#8B978E", marginVertical: 50 },
  guideDistance: { fontSize: 46, fontWeight: "800", color: "#1C2B22", marginTop: 4 },
  guideDir: { fontSize: 17, color: "#2D5A3D", fontWeight: "700", marginBottom: 14 },
  guideMap: { width: "88%", height: 230, borderRadius: 16, overflow: "hidden" },
  guideNote: { fontSize: 11.5, color: "#8B978E", marginTop: 12, paddingHorizontal: 30, textAlign: "center" },

  // Run picker
  modalBg: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "center", padding: 28 },
  modalBox: { backgroundColor: "#FFFFFF", borderRadius: 16, padding: 18 },
  modalTitle: { fontSize: 16, fontWeight: "700", marginBottom: 10, color: "#1C2B22" },
  modalEmpty: { fontSize: 13, color: "#8B978E" },
  modalRow: { paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth, borderColor: "#E8E6DC" },
  modalRowText: { fontSize: 14, color: "#1C2B22" },
});