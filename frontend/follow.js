// Route following: walk the selected route with GPS, see progress, and share a
// live link. Also the viewer that opens from a share link (?follow=<id>).
// Loaded after app.js and uses its globals (map, routeData, activeRoute, stops,
// mode, C, ROUTE_COLOR, API_URL, ...). Share endpoints: Contract 5.

const WALK_MPS = 1.4;            // Contract 4 walking speed
const OFF_ROUTE_M = 40;          // farther than this from the route counts as off it
const ARRIVE_M = 25;
const SHARE_EVERY_MS = 3000;     // walker -> server location updates, at most this often
const HEARTBEAT_MS = 10_000;     // resend the last fix when standing still (GPS goes quiet)
const VIEW_POLL_MS = 3000;       // viewer <- server
const STALE_MS = 60_000;         // viewer warns when updates stop
const AUTOPAN_PAUSE_MS = 15_000; // after the user drags the map, stop following the dot
const ROUTE_LABEL = { lit: "brightest", shortest: "shortest" };

const params = new URLSearchParams(location.search);
const SIMULATE = Number(params.get("simulate")) || 0; // ?simulate=10 walks the route at 10x, no GPS

const follow = {
  route: null, name: null, geo: null, gaps: [],
  progress: 0, offCount: 0, fix: null,
  watchId: null, simTimer: null, heartbeat: null, trailing: null,
  share: null, lastSent: 0, sending: false,
  me: null, userPannedAt: 0,
};

// ---- Setup (called from app.js once the map has loaded) ---------------------------

function addFollowLayers() {
  map.addSource("walked", { type: "geojson", data: emptyFC() });
  // The part already walked sits over the route, dimmed, like a used-up beam.
  map.addLayer({
    id: "walked-line",
    type: "line",
    source: "walked",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": C.night1, "line-width": 7, "line-opacity": 0.7 },
  }, "dark-gaps-edge");

  map.on("dragstart", () => (follow.userPannedAt = Date.now()));
  document.getElementById("start-btn").addEventListener("click", startFollowing);
  document.getElementById("stop-btn").addEventListener("click", () => stopFollowing(false));
  document.getElementById("done-btn").addEventListener("click", () => stopFollowing(false));
  document.getElementById("share-btn").addEventListener("click", openShare);
  document.getElementById("reroute-btn").addEventListener("click", reroute);
  document.getElementById("viewer-center").addEventListener("click", () => {
    follow.userPannedAt = 0;
    if (follow.fix) map.easeTo({ center: follow.fix });
  });
  setupShareDialog();
}

// Returns true when the page was opened from a share link.
function startViewerFromURL() {
  const id = params.get("follow");
  if (!id) return false;
  startViewer(id);
  return true;
}

function setMode(next) {
  mode = next;
  document.body.dataset.mode = next;
  for (const s of stops) s.marker?.setDraggable(next === "find");
}

// ---- Route geometry (local meters; fine at city scale) ------------------------------

function routeGeometry(coords) {
  const kx = Math.cos((coords[0][1] * Math.PI) / 180) * 111_320;
  const ky = 110_540;
  const xy = coords.map(([lon, lat]) => [lon * kx, lat * ky]);
  const cum = [0];
  for (let i = 1; i < xy.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]));
  }
  return { coords, xy, cum, kx, ky, total: cum[cum.length - 1] };
}

// Closest point on the route to [lon, lat]: { along, dist }. With `near`, look
// around that distance first, so a route that doubles back on itself doesn't
// make the walker jump ahead or behind.
function projectOnRoute(g, [lon, lat], near = null) {
  const p = [lon * g.kx, lat * g.ky];
  const search = (lo, hi) => {
    let best = null;
    for (let i = 0; i < g.xy.length - 1; i++) {
      if (g.cum[i + 1] < lo || g.cum[i] > hi) continue;
      const [ax, ay] = g.xy[i];
      const dx = g.xy[i + 1][0] - ax, dy = g.xy[i + 1][1] - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / len2)) : 0;
      const dist = Math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy));
      if (!best || dist < best.dist) best = { along: g.cum[i] + t * (g.cum[i + 1] - g.cum[i]), dist };
    }
    return best;
  };
  if (near !== null) {
    const local = search(near - 60, near + 300);
    if (local && local.dist <= OFF_ROUTE_M) return local;
  }
  return search(-Infinity, Infinity);
}

function pointAt(g, along) {
  along = Math.max(0, Math.min(g.total, along));
  let i = 1;
  while (i < g.cum.length - 1 && g.cum[i] < along) i++;
  const seg = g.cum[i] - g.cum[i - 1];
  const t = seg ? (along - g.cum[i - 1]) / seg : 0;
  const [a, b] = [g.coords[i - 1], g.coords[i]];
  return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
}

function sliceRoute(g, a, b) {
  const out = [pointAt(g, a)];
  for (let i = 0; i < g.cum.length; i++) if (g.cum[i] > a && g.cum[i] < b) out.push(g.coords[i]);
  out.push(pointAt(g, b));
  return out;
}

// Each dark gap as distances along the route.
function gapSpans(g, route) {
  return route.dark_gaps
    .map((gap) => ({
      start: projectOnRoute(g, gap.coords[0]).along,
      end: projectOnRoute(g, gap.coords[gap.coords.length - 1]).along,
      length: gap.length_m,
    }))
    .sort((x, y) => x.start - y.start);
}

// ---- Progress display (shared by walker and viewer) ---------------------------------

// `prefix` is "follow" or "viewer"; `along` and the gaps are in route-geometry meters.
function renderProgress(prefix, g, route, along, gaps) {
  const frac = g.total ? Math.min(1, along / g.total) : 0;
  const walked = frac * route.length_m;
  const left = Math.max(0, route.length_m - walked);
  const minsLeft = left / WALK_MPS / 60;
  const eta = new Date(Date.now() + minsLeft * 60_000);

  document.getElementById(`${prefix}-fill`).style.width = `${frac * 100}%`;
  document.getElementById(`${prefix}-pct`).textContent = `${Math.round(frac * 100)}%`;
  document.getElementById(`${prefix}-left`).textContent =
    `${formatDistance(walked)} walked · ${formatDistance(left)} to go`;
  document.getElementById(`${prefix}-eta`).textContent = left < ARRIVE_M ? "Arriving now"
    : `About ${Math.max(1, Math.round(minsLeft))} min left · arrive ${eta.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;

  const gapEl = document.getElementById(`${prefix}-gap`);
  const inside = gaps.find((s) => along >= s.start && along < s.end);
  const next = gaps.find((s) => s.start > along);
  gapEl.textContent = inside ? `Dark stretch for the next ${formatDistance(inside.end - along)}.`
    : next ? `Next dark stretch in ${formatDistance(next.start - along)} (${formatDistance(next.length)} long).`
    : "No more dark stretches on this route.";
  gapEl.classList.toggle("warn", Boolean(inside || (next && next.start - along < 150)));

  map.getSource("walked").setData(along > 1 ? lineFC(sliceRoute(g, 0, along)) : emptyFC());
}

function meMarker(lonLat) {
  if (!follow.me) {
    const el = document.createElement("div");
    el.className = "marker-me";
    el.title = mode === "view" ? "Walker" : "You";
    follow.me = new maplibregl.Marker({ element: el }).setLngLat(lonLat).addTo(map);
  }
  follow.me.setLngLat(lonLat);
  follow.fix = lonLat;
  if (Date.now() - follow.userPannedAt > AUTOPAN_PAUSE_MS) map.easeTo({ center: lonLat, duration: 600 });
}

// ---- Following mode -------------------------------------------------------------------

function startFollowing() {
  if (!routeData) return;
  const name = routeData.same_route ? "lit" : activeRoute;
  beginRoute(routeData.routes[name], name);
  setMode("follow");
  document.getElementById("follow-title").textContent = `Walking the ${ROUTE_LABEL[name]} route`;
  document.getElementById("follow-done").hidden = true;
  document.getElementById("follow-live").hidden = false;
  map.easeTo({ center: follow.route.coords[0], zoom: Math.max(map.getZoom(), 16) });
  renderProgress("follow", follow.geo, follow.route, 0, follow.gaps);
  watchPosition();
}

function beginRoute(route, name) {
  Object.assign(follow, { route, name, geo: routeGeometry(route.coords), progress: 0, offCount: 0 });
  follow.gaps = gapSpans(follow.geo, route);
  setOffRoute(false);
}

function watchPosition() {
  if (SIMULATE) return simulateWalk();
  const gps = document.getElementById("follow-gps");
  if (!("geolocation" in navigator)) {
    gps.textContent = "This browser can't share its location.";
    return;
  }
  if (!window.isSecureContext) {
    gps.textContent = "GPS needs a secure page (https, or localhost on this device).";
    return;
  }
  gps.textContent = "Finding you…";
  follow.watchId = navigator.geolocation.watchPosition(
    (pos) => onFix([pos.coords.longitude, pos.coords.latitude], pos.coords.accuracy),
    (err) => {
      gps.textContent = err.code === err.PERMISSION_DENIED
        ? "Location is blocked. Allow it in your browser settings to track your walk."
        : "Can't get a GPS fix right now. Still trying…";
    },
    { enableHighAccuracy: true, maximumAge: 2000, timeout: 20_000 },
  );
}

// No GPS needed: walk the route at SIMULATE times walking speed, with a little jitter.
function simulateWalk() {
  let along = 0;
  document.getElementById("follow-gps").textContent = `Simulated walk (${SIMULATE}× speed).`;
  follow.simTimer = setInterval(() => {
    along = Math.min(follow.geo.total, along + WALK_MPS * SIMULATE);
    const [lon, lat] = pointAt(follow.geo, along);
    const j = () => (Math.random() - 0.5) * 0.00004;
    onFix([lon + j(), lat + j()], 5);
  }, 1000);
}

function stopWatching() {
  if (follow.watchId !== null) navigator.geolocation.clearWatch(follow.watchId);
  clearInterval(follow.simTimer);
  follow.watchId = follow.simTimer = null;
}

function onFix(lonLat, accuracy) {
  if (mode !== "follow") return;
  const gps = document.getElementById("follow-gps");
  gps.textContent = SIMULATE ? `Simulated walk (${SIMULATE}× speed).`
    : accuracy ? `GPS accurate to about ${Math.round(accuracy)} m.` : "";
  meMarker(lonLat);
  follow.accuracy = accuracy ?? null;

  const hit = projectOnRoute(follow.geo, lonLat, follow.progress);
  const off = hit.dist > Math.max(OFF_ROUTE_M, accuracy || 0);
  follow.offCount = off ? follow.offCount + 1 : 0;
  if (!off) follow.progress = hit.along;
  setOffRoute(follow.offCount >= 2);

  renderProgress("follow", follow.geo, follow.route, follow.progress, follow.gaps);
  sendLocation(lonLat, accuracy);

  const end = follow.route.coords[follow.route.coords.length - 1];
  if (follow.geo.total - follow.progress <= ARRIVE_M || distanceM(lonLat, end) <= ARRIVE_M) arrive();
}

function setOffRoute(off) {
  document.getElementById("follow-off").hidden = !off;
}

function arrive() {
  stopWatching();
  follow.progress = follow.geo.total;
  renderProgress("follow", follow.geo, follow.route, follow.progress, follow.gaps);
  document.getElementById("follow-live").hidden = true;
  document.getElementById("follow-done").hidden = false;
  endShare(true);
}

function stopFollowing() {
  stopWatching();
  endShare(false);
  follow.me?.remove();
  follow.me = follow.fix = null;
  map.getSource("walked").setData(emptyFC());
  setMode("find");
  if (routeData) fitRoutes(routeData);
}

// Off route: plan again from where the walker is through the stops still ahead,
// and keep the same share link. Stops already passed drop off the list.
async function reroute() {
  const placed = stops.filter((s) => s.lonLat);
  if (!follow.fix || placed.length < 2) return;
  const btn = document.getElementById("reroute-btn");
  btn.disabled = true;
  try {
    const [first, ...rest] = placed;
    const ahead = rest.filter((s, k) =>
      k === rest.length - 1 || projectOnRoute(follow.geo, s.lonLat).along > follow.progress + ARRIVE_M);
    const points = [follow.fix, ...ahead.map((s) => s.lonLat)];
    const legs = await Promise.all(points.slice(1).map((b, k) => fetchRoute(points[k], b)));
    const data = joinLegs(legs);

    // Match the planner to the new route: start here, then the stops still ahead.
    for (const s of stops) if (s !== first && !ahead.includes(s)) s.marker?.remove();
    Object.assign(first, { lonLat: follow.fix, label: "Your location" });
    first.marker?.setLngLat(follow.fix);
    stops = [first, ...ahead];
    renderStops();
    setMode("follow"); // re-lock the markers renderStops refreshed

    showRoute(data);
    showStats(data);
    const name = data.same_route ? "lit" : follow.name;
    selectRoute(name);
    beginRoute(data.routes[name], name);
    renderProgress("follow", follow.geo, follow.route, 0, follow.gaps);
    if (follow.share) {
      await api(`/shares/${follow.share.id}/route`, { token: follow.share.token, route: follow.route, route_name: name });
    }
  } catch (err) {
    document.getElementById("follow-gps").textContent = `Couldn't reroute: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

// ---- Sharing (walker side) --------------------------------------------------------

async function api(path, body) {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);
  return data;
}

async function ensureShare() {
  if (follow.share) return follow.share;
  follow.share = await api("/shares", {
    route: follow.route,
    route_name: follow.name,
    from_label: stopLabel(0),
    to_label: stopLabel(-1),
  });
  follow.lastSent = 0;
  if (follow.fix) sendLocation(follow.fix, follow.accuracy);
  clearInterval(follow.heartbeat);
  follow.heartbeat = setInterval(() => {
    if (follow.fix && Date.now() - follow.lastSent >= HEARTBEAT_MS) sendLocation(follow.fix, follow.accuracy);
  }, HEARTBEAT_MS / 2);
  document.getElementById("share-live").hidden = false;
  return follow.share;
}

// Label of the first (0) or last (-1) stop that has a point, for the viewer.
function stopLabel(which) {
  const placed = stops.filter((s) => s.lonLat);
  return placed.at(which)?.label?.slice(0, 80) || null;
}

// Sends the walker's latest fix. Fixes that arrive too soon after the last send
// aren't dropped: one trailing send goes out once SHARE_EVERY_MS has passed.
async function sendLocation(lonLat, accuracy) {
  if (!follow.share) return;
  const wait = SHARE_EVERY_MS - (Date.now() - follow.lastSent);
  if (follow.sending || wait > 0) {
    clearTimeout(follow.trailing);
    follow.trailing = setTimeout(() => follow.fix && sendLocation(follow.fix, follow.accuracy), Math.max(wait, 250));
    return;
  }
  clearTimeout(follow.trailing);
  follow.sending = true;
  follow.lastSent = Date.now();
  const scale = follow.route.length_m / follow.geo.total;
  try {
    await api(`/shares/${follow.share.id}/location`, {
      token: follow.share.token,
      coord: lonLat,
      accuracy_m: accuracy ?? null,
      progress_m: follow.progress * scale,
    });
  } catch {
    // A missed update is fine; the next fix tries again.
  } finally {
    follow.sending = false;
  }
}

function endShare(arrived) {
  const share = follow.share;
  follow.share = null;
  clearInterval(follow.heartbeat);
  clearTimeout(follow.trailing);
  document.getElementById("share-live").hidden = true;
  if (share) api(`/shares/${share.id}/end`, { token: share.token, arrived }).catch(() => {});
}

function shareURL(id) {
  return `${location.origin}${location.pathname}?follow=${encodeURIComponent(id)}`;
}

function setupShareDialog() {
  const dialog = document.getElementById("share-dialog");
  const urlInput = document.getElementById("share-url");
  const toast = document.getElementById("share-toast");
  const native = dialog.querySelector('[data-share="more"]');
  native.hidden = !navigator.share;

  const message = () => `I'm walking with Polaris. Follow my walk live: ${urlInput.value}`;
  const copy = async (note) => {
    try {
      await navigator.clipboard.writeText(urlInput.value);
    } catch {
      urlInput.select();
      document.execCommand("copy");
    }
    toast.textContent = note;
  };

  document.getElementById("share-copy").addEventListener("click", () => copy("Link copied."));
  for (const btn of dialog.querySelectorAll("[data-share]")) {
    btn.addEventListener("click", async () => {
      const text = encodeURIComponent(message());
      const subject = encodeURIComponent("Follow my walk");
      const open = (url) => window.open(url, "_blank", "noopener");
      switch (btn.dataset.share) {
        case "messages": location.href = `sms:?&body=${text}`; break;
        case "whatsapp": open(`https://wa.me/?text=${text}`); break;
        case "gmail": open(`https://mail.google.com/mail/?view=cm&fs=1&su=${subject}&body=${text}`); break;
        case "email": location.href = `mailto:?subject=${subject}&body=${text}`; break;
        // No share URL for these: copy the link to paste in.
        case "slack": copy("Link copied. Paste it into a Slack message."); break;
        case "instagram": copy("Link copied. Paste it into an Instagram DM."); break;
        case "more": navigator.share({ title: "Follow my walk", text: message(), url: urlInput.value }).catch(() => {}); break;
      }
    });
  }
}

async function openShare() {
  const dialog = document.getElementById("share-dialog");
  const toast = document.getElementById("share-toast");
  const btn = document.getElementById("share-btn");
  btn.disabled = true;
  try {
    const share = await ensureShare();
    document.getElementById("share-url").value = shareURL(share.id);
    toast.textContent = "";
    dialog.showModal();
  } catch (err) {
    document.getElementById("follow-gps").textContent =
      `Sharing needs the routing server (${err.message}).`;
  } finally {
    btn.disabled = false;
  }
}

// ---- Viewer (opened from a share link) ----------------------------------------------

const viewer = { id: null, timer: null, ticker: null, routeKey: null, view: null, fitted: false, ends: [] };

function startViewer(id) {
  viewer.id = id;
  setMode("view");
  setStatus("");
  pollViewer();
  viewer.timer = setInterval(pollViewer, VIEW_POLL_MS);
  viewer.ticker = setInterval(renderViewerTimes, 1000);
}

async function pollViewer() {
  let res;
  try {
    res = await fetch(`${API_URL}/shares/${encodeURIComponent(viewer.id)}`);
  } catch {
    setViewerStatus("Can't reach the server. Retrying…", "warn");
    return;
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    clearInterval(viewer.timer);
    setViewerStatus(body?.message || "This walk isn't available.", "warn");
    document.getElementById("viewer-title").textContent = "Walk not found";
    return;
  }
  showViewer(body);
}

function showViewer(view) {
  viewer.view = view;
  const routeKey = JSON.stringify(view.route.coords.slice(0, 2)) + view.route.length_m;
  if (routeKey !== viewer.routeKey) {
    viewer.routeKey = routeKey;
    drawSharedRoute(view);
  }
  document.getElementById("viewer-title").textContent =
    view.to_label ? `Walking to ${view.to_label.replace(/^Near /, "near ")}` : "Walking home";
  document.getElementById("viewer-route").textContent =
    `On the ${ROUTE_LABEL[view.route_name]} route · ${Math.round(view.route.lit_frac * 100)}% lit · ${formatDistance(view.route.length_m)}`;

  const g = follow.geo;
  const along = view.location ? (view.location.progress_m / view.route.length_m) * g.total : 0;
  renderProgress("viewer", g, view.route, view.status === "arrived" ? g.total : along, follow.gaps);
  if (view.location) meMarker(view.location.coord);
  if (view.status !== "active") clearInterval(viewer.timer);
  renderViewerTimes();
}

function drawSharedRoute(view) {
  const name = view.route_name;
  for (const n of ROUTES) map.getSource(`route-${n}`).setData(n === name ? lineFC(view.route.coords) : emptyFC());
  routeData = { routes: { [name]: view.route }, same_route: true };
  selectRoute(name);
  beginRoute(view.route, name);

  for (const m of viewer.ends) m.remove();
  // Start and destination, in app.js's lettered stop-marker style.
  const end = (kind, letter, lonLat) => {
    const el = document.createElement("div");
    el.className = `stop-marker ${kind} view`;
    el.textContent = letter;
    return new maplibregl.Marker({ element: el }).setLngLat(lonLat).addTo(map);
  };
  const cs = view.route.coords;
  viewer.ends = [end("first", "A", cs[0]), end("last", "B", cs[cs.length - 1])];

  if (!viewer.fitted) {
    viewer.fitted = true;
    const bounds = new maplibregl.LngLatBounds();
    for (const c of cs) bounds.extend(c);
    map.fitBounds(bounds, { padding: 60, maxZoom: 16 });
    follow.userPannedAt = Date.now(); // show the whole route first; follow the walker after
  }
}

function renderViewerTimes() {
  const view = viewer.view;
  if (!view) return;
  const updated = Date.parse(view.location?.updated_at ?? view.updated_at);
  const ago = Date.now() - updated;
  const status =
    view.status === "arrived" ? ["They made it. Arrived " + timeAgo(Date.parse(view.ended_at)) + ".", "ok"]
    : view.status === "ended" ? ["They stopped sharing " + timeAgo(Date.parse(view.ended_at)) + ".", ""]
    : !view.location ? ["Waiting for their first GPS fix…", ""]
    : ago > STALE_MS ? [`No update for ${timeAgo(updated, true)}. Their phone may have lost signal.`, "warn"]
    : [`Live · updated ${timeAgo(updated)}`, "live"];
  setViewerStatus(...status);
}

function setViewerStatus(text, tone) {
  const el = document.getElementById("viewer-status");
  el.textContent = text;
  el.dataset.tone = tone || "";
}

// ---- Small helpers --------------------------------------------------------------------

function distanceM([lon1, lat1], [lon2, lat2]) {
  const kx = Math.cos((((lat1 + lat2) / 2) * Math.PI) / 180) * 111_320;
  return Math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110_540);
}

function timeAgo(t, bare = false) {
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  const text = s < 5 ? "just now" : s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
  return bare || text === "just now" ? text : `${text} ago`;
}
