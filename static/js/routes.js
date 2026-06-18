/**
 * OCAD Map Viewer — Route (course) planning
 * Multiple routes per map: create, edit (add/move/delete points), live total distance.
 * Relies on globals defined in viewer.js: `map`, `MAP_CONFIG`.
 */

let ROUTES = [];            // route objects loaded from the server
let activeRouteId = null;   // id of the selected route (null = none)
let isDrawing = false;      // edit mode toggle
let routeDirty = false;     // unsaved changes in current edit session
let workingPoints = [];     // [{lat, lng}] of the displayed/edited route
let legPolylines = [];      // google.maps.Polyline[] — one per (trimmed) leg
let vertexMarkers = [];     // google.maps.marker.AdvancedMarkerElement[] — start / controls / finish

// IOF standard overprint magenta (course planning purple).
const IOF_PURPLE = '#cf00cf';

// ── Utilities ────────────────────────────────────────────────

function showToast(message, type = 'info') {
  console.log(`[${type.toUpperCase()}] ${message}`);
  alert(message);
}

// ── Embargo zone validation ────────────────────────────────

/**
 * Ray-casting algorithm — checks if point {lat, lng} is inside polygon.
 * Must match server-side implementation in server.py
 */
function isPointInPolygon(point, polygon) {
  if (!point || !polygon || polygon.length < 3) return true; // no embargo = always valid
  
  const lat = point.lat;
  const lng = point.lng;
  let inside = false;
  
  const n = polygon.length;
  let p1lat = polygon[0].lat;
  let p1lng = polygon[0].lng;
  
  for (let i = 1; i <= n; i++) {
    const p2lat = polygon[i % n].lat;
    const p2lng = polygon[i % n].lng;
    
    if (lng > Math.min(p1lng, p2lng)) {
      if (lng <= Math.max(p1lng, p2lng)) {
        if (lat <= Math.max(p1lat, p2lat)) {
          let xinters = 0;
          if (p1lng !== p2lng) {
            xinters = (lng - p1lng) * (p2lat - p1lat) / (p2lng - p1lng) + p1lat;
          }
          if (p1lat === p2lat || lat <= xinters) {
            inside = !inside;
          }
        }
      }
    }
    p1lat = p2lat;
    p1lng = p2lng;
  }
  
  return inside;
}

// On-screen symbol sizes, in pixels (base size = the fixed minimum floor).
const CONTROL_RADIUS_PX = 14;  // control circle radius
const START_RADIUS_PX = 16;    // start triangle circumradius
const FINISH_OUTER_PX = 16;    // finish outer circle radius
const FINISH_INNER_PX = 11;    // finish inner circle radius
const SYMBOL_STROKE_PX = 3;    // overprint line weight

// Symbols scale 1:1 with the map (like real orienteering overprint) above
// REF_ZOOM, so they keep a constant size relative to map features. Below
// REF_ZOOM they stay fixed at the base size (floor) so they never shrink to
// an unreadable size. Capped by MAX_SYMBOL_SCALE to avoid huge symbols.
const SYMBOL_REF_ZOOM = 17;
const MAX_SYMBOL_SCALE = 8;

// Current symbol scale factor, refreshed before each (re)draw.
let symbolScale = 1;

function currentSymbolScale() {
  if (!map || typeof map.getZoom !== 'function') return 1;
  const z = map.getZoom();
  if (typeof z !== 'number') return 1;
  return Math.min(MAX_SYMBOL_SCALE, Math.max(1, 2 ** (z - SYMBOL_REF_ZOOM)));
}

// Exposed so viewer.js can suppress Street View clicks while editing.
function isRouteDrawing() {
  return isDrawing;
}

function routeApiBase() {
  return '/api/maps/' + encodeURIComponent(MAP_CONFIG.id) + '/routes';
}

// ── Geometry ──────────────────────────────────────────────

function haversineMeters(a, b) {
  const r = 6371000;
  const p1 = a.lat * Math.PI / 180;
  const p2 = b.lat * Math.PI / 180;
  const dPhi = (b.lat - a.lat) * Math.PI / 180;
  const dLmb = (b.lng - a.lng) * Math.PI / 180;
  const h = Math.sin(dPhi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLmb / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(h)));
}

function totalDistanceMeters(points) {
  let d = 0;
  for (let i = 0; i < points.length - 1; i++) d += haversineMeters(points[i], points[i + 1]);
  return d;
}

function formatDistance(meters) {
  if (workingPoints.length < 2) return '— m';
  if (meters >= 1000) return (meters / 1000).toFixed(2) + ' km';
  return Math.round(meters) + ' m';
}

// ── API ───────────────────────────────────────────────────

async function reloadRoutes() {
  try {
    const resp = await fetch(routeApiBase());
    if (!resp.ok) throw new Error('list failed');
    ROUTES = await resp.json();
  } catch {
    ROUTES = [];
  }
  refreshRouteSelect();
}

async function apiCreateRoute(payload) {
  const resp = await fetch(routeApiBase(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error('create failed');
  return resp.json();
}

async function apiUpdateRoute(routeId, payload) {
  const resp = await fetch(routeApiBase() + '/' + encodeURIComponent(routeId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error('update failed');
  return resp.json();
}

async function apiDeleteRoute(routeId) {
  const resp = await fetch(routeApiBase() + '/' + encodeURIComponent(routeId), {
    method: 'DELETE',
  });
  if (!resp.ok) throw new Error('delete failed');
  return resp.json();
}

// ── Rendering ─────────────────────────────────────────────

function activeColor() {
  const r = ROUTES.find((x) => x.id === activeRouteId);
  return r?.color || IOF_PURPLE;
}

function clearRouteGraphics() {
  legPolylines.forEach((l) => l.setMap(null));
  legPolylines = [];
  vertexMarkers.forEach((m) => m.setMap(null));
  vertexMarkers = [];
}

// ── Pixel projection helpers (for symbol-edge line trimming) ──

function latLngToWorldPixel(p) {
  const proj = map.getProjection();
  if (!proj) return null;
  const scale = 2 ** map.getZoom();
  const wp = proj.fromLatLngToPoint(new google.maps.LatLng(p.lat, p.lng));
  return { x: wp.x * scale, y: wp.y * scale };
}

function worldPixelToLatLng(px) {
  const proj = map.getProjection();
  if (!proj) return null;
  const scale = 2 ** map.getZoom();
  const ll = proj.fromPointToLatLng(new google.maps.Point(px.x / scale, px.y / scale));
  return { lat: ll.lat(), lng: ll.lng() };
}

// ── Overlay rotation sync ─────────────────────────────────
// The OCAD overlay is rotated by `-currentHeading` around the Street View
// position while walking (see viewer.js draw()). Route symbols live on the
// base map, so we apply the SAME rotation to keep them glued to the map.

function svRotationActive() {
  return typeof currentHeading === 'number' && currentHeading !== 0 &&
    typeof panorama !== 'undefined' && panorama?.getPosition();
}

function overlayProjection() {
  return (typeof overlay !== 'undefined' && overlay?.getProjection)
    ? overlay.getProjection() : null;
}

// Rotate a geographic point by `angleDeg` (div-pixel space) around the SV
// position, mirroring the OCAD overlay transform. Returns a new {lat,lng}.
function rotateLatLngBy(p, angleDeg) {
  const proj = overlayProjection();
  if (!proj || !svRotationActive()) return { lat: p.lat, lng: p.lng };
  const px = proj.fromLatLngToDivPixel(new google.maps.LatLng(p.lat, p.lng));
  const svPx = proj.fromLatLngToDivPixel(panorama.getPosition());
  if (!px || !svPx) return { lat: p.lat, lng: p.lng };
  const r = rotatePoint(px.x, px.y, svPx.x, svPx.y, angleDeg);
  const ll = proj.fromDivPixelToLatLng(new google.maps.Point(r.x, r.y));
  return { lat: ll.lat(), lng: ll.lng() };
}

// Real geographic point -> on-screen (rotated like the overlay) position.
function toDisplay(p) { return rotateLatLngBy(p, -currentHeading); }
// On-screen position (e.g. from a marker drag) -> real geographic point.
function toReal(p) { return rotateLatLngBy(p, currentHeading); }

// Geographic bearing from a -> b, in degrees clockwise from north.
function bearing(a, b) {
  const phi1 = a.lat * Math.PI / 180;
  const phi2 = b.lat * Math.PI / 180;
  const dl = (b.lng - a.lng) * Math.PI / 180;
  const y = Math.sin(dl) * Math.cos(phi2);
  const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dl);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// Role of a point given its index in the course.
function pointRole(i, n) {
  if (i === 0) return 'start';
  if (i === n - 1 && n > 1) return 'finish';
  return 'control';
}

function trimRadiusFor(role) {
  if (role === 'start') return START_RADIUS_PX * symbolScale;
  if (role === 'finish') return FINISH_OUTER_PX * symbolScale;
  return CONTROL_RADIUS_PX * symbolScale;
}

// ── SVG symbol builders (transparent fills, magenta overprint) ──

function svgUrl(svg) {
  return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg);
}

// Convert symbol object (with data URL) to HTML content for AdvancedMarkerElement.
// AdvancedMarkerElement anchors at bottom-center of the content element.
// A zero-size container puts that anchor at (0,0), so the SVG is absolutely
// offset by (-anchorX, -anchorY) to place the symbol's logical center at lat/lng.
function createSymbolContent(sym) {
  const container = document.createElement('div');
  container.style.width = '0';
  container.style.height = '0';
  container.style.overflow = 'visible';
  container.style.cursor = 'pointer';

  // Decode the data URL back to raw SVG and inject it directly to avoid
  // the async load delay of an <img> tag.
  const svgStr = decodeURIComponent(
    sym.url.replace('data:image/svg+xml;charset=UTF-8,', '')
  );
  const wrapper = document.createElement('div');
  wrapper.style.position = 'absolute';
  wrapper.style.left = (-sym.anchor.x) + 'px';
  wrapper.style.top = (-sym.anchor.y) + 'px';
  wrapper.style.lineHeight = '0';
  wrapper.style.pointerEvents = 'auto';
  wrapper.style.userSelect = 'none';
  wrapper.innerHTML = svgStr;

  container.appendChild(wrapper);
  return container;
}

function startSymbol(color, headingDeg) {
  // Equilateral triangle, one vertex pointing toward the next control.
  const s = START_RADIUS_PX * symbolScale;
  const stroke = SYMBOL_STROKE_PX * symbolScale;
  const pad = stroke + 1;
  const c = s + pad;
  const size = 2 * c;
  const pts = [];
  for (let k = 0; k < 3; k++) {
    const ang = (-90 + headingDeg + k * 120) * Math.PI / 180;
    pts.push((c + s * Math.cos(ang)).toFixed(1) + ',' + (c + s * Math.sin(ang)).toFixed(1));
  }
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '">' +
    '<polygon points="' + pts.join(' ') + '" fill="none" stroke="' + color +
    '" stroke-width="' + stroke + '" stroke-linejoin="round"/></svg>';
  return { url: svgUrl(svg), anchor: { x: c, y: c } };
}

function controlSymbol(color, number) {
  // Transparent circle with the control number placed beside it (upper-right).
  const r = CONTROL_RADIUS_PX * symbolScale;
  const stroke = SYMBOL_STROKE_PX * symbolScale;
  const pad = stroke + 1;
  const cx = r + pad;
  const cy = r + pad;
  const label = String(number);
  const fontSize = 15 * symbolScale;
  const textW = label.length * fontSize * 0.7 + 6;
  const width = cx + r + 6 + textW;
  const height = 2 * (r + pad);
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '">' +
    '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + color +
    '" stroke-width="' + stroke + '"/>' +
    '<text x="' + (cx + r + 5) + '" y="' + (cy - r + fontSize * 0.27) + '" font-family="Arial, sans-serif" ' +
    'font-size="' + fontSize + '" font-weight="700" fill="' + color + '">' + label + '</text>' +
    '</svg>';
  return { url: svgUrl(svg), anchor: { x: cx, y: cy } };
}

function finishSymbol(color) {
  // Double concentric circle, transparent.
  const ro = FINISH_OUTER_PX * symbolScale;
  const ri = FINISH_INNER_PX * symbolScale;
  const stroke = SYMBOL_STROKE_PX * symbolScale;
  const pad = stroke + 1;
  const c = ro + pad;
  const size = 2 * c;
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '">' +
    '<circle cx="' + c + '" cy="' + c + '" r="' + ro + '" fill="none" stroke="' + color +
    '" stroke-width="' + stroke + '"/>' +
    '<circle cx="' + c + '" cy="' + c + '" r="' + ri + '" fill="none" stroke="' + color +
    '" stroke-width="' + stroke + '"/></svg>';
  return { url: svgUrl(svg), anchor: { x: c, y: c } };
}

function symbolForRole(role, color, headingDeg, controlNumber) {
  if (role === 'start') return startSymbol(color, headingDeg);
  if (role === 'finish') return finishSymbol(color);
  return controlSymbol(color, controlNumber);
}

// ── Legs (connecting lines, cut at symbol edges) ──────────

// Compute the trimmed endpoints for the leg between points i and i+1.
// Returns {start, end} in {lat, lng}, cut by the symbol radius at each end.
function trimmedLeg(pts, i, n) {
  const a = pts[i];
  const b = pts[i + 1];
  if (!map.getProjection()) return { start: a, end: b };

  const pa = latLngToWorldPixel(a);
  const pb = latLngToWorldPixel(b);
  if (!pa || !pb) return { start: a, end: b };

  const dx = pb.x - pa.x;
  const dy = pb.y - pa.y;
  const len = Math.hypot(dx, dy);
  const rA = trimRadiusFor(pointRole(i, n));
  const rB = trimRadiusFor(pointRole(i + 1, n));
  if (len <= rA + rB + 1) return { start: a, end: b };

  const ux = dx / len;
  const uy = dy / len;
  return {
    start: worldPixelToLatLng({ x: pa.x + ux * rA, y: pa.y + uy * rA }) || a,
    end: worldPixelToLatLng({ x: pb.x - ux * rB, y: pb.y - uy * rB }) || b,
  };
}

function drawLegs(color, pts) {
  legPolylines.forEach((l) => l.setMap(null));
  legPolylines = [];
  if (pts.length < 2) return;

  const n = pts.length;
  for (let i = 0; i < n - 1; i++) {
    const { start, end } = trimmedLeg(pts, i, n);
    legPolylines.push(new google.maps.Polyline({
      path: [{ lat: start.lat, lng: start.lng }, { lat: end.lat, lng: end.lng }],
      strokeColor: color,
      strokeOpacity: 0.95,
      strokeWeight: SYMBOL_STROKE_PX * symbolScale,
      map,
      clickable: false,
    }));
  }
}

function redrawRoute() {
  const color = activeColor();
  symbolScale = currentSymbolScale();

  // Render in the same rotated frame as the OCAD overlay (no-op when not
  // walking). `workingPoints` stays the source of truth (real coordinates).
  const disp = workingPoints.map(toDisplay);

  drawLegs(color, disp);

  // Rebuild symbols (start triangle, control circles, finish double-circle).
  vertexMarkers.forEach((m) => m.setMap(null));
  vertexMarkers = [];

  const n = disp.length;
  let controlNumber = 0;
  disp.forEach((p, i) => {
    const role = pointRole(i, n);
    let headingDeg = 0;
    if (role === 'start' && n > 1) headingDeg = bearing(p, disp[1]);
    if (role === 'control') controlNumber += 1;

    const sym = symbolForRole(role, color, headingDeg, controlNumber);
    const marker = new google.maps.marker.AdvancedMarkerElement({
      position: { lat: p.lat, lng: p.lng },
      map,
      draggable: isDrawing,
      content: createSymbolContent(sym),
      zIndex: 1000 + i,
    });

    if (isDrawing) {
      // AdvancedMarkerElement uses gmp-* event names (not the legacy drag/click)
      marker.addListener('gmp-drag', ({ latLng }) => {
        workingPoints[i] = toReal({ lat: latLng.lat(), lng: latLng.lng() });
        drawLegs(color, workingPoints.map(toDisplay));
        updateDistanceDisplay();
      });
      marker.addListener('gmp-dragend', () => { routeDirty = true; redrawRoute(); });
      marker.addListener('gmp-click', () => removeVertex(i));
    }
    vertexMarkers.push(marker);
  });

  updateDistanceDisplay();
}

function addVertex(latLng) {
  const point = { lat: latLng.lat(), lng: latLng.lng() };
  
  // Validate against embargo zone if it exists
  if (MAP_CONFIG && MAP_CONFIG.embargoPoly && MAP_CONFIG.embargoPoly.points) {
    if (!isPointInPolygon(point, MAP_CONFIG.embargoPoly.points)) {
      showToast(`Point hors zone embargo (${point.lat.toFixed(4)}, ${point.lng.toFixed(4)})`, 'error');
      return;
    }
  }
  
  workingPoints.push(toReal(point));
  
  // Hide state bar & badge after 1st point
  if (workingPoints.length === 1) {
    const bar = document.getElementById('route-state-bar');
    if (bar) {
      bar.classList.remove('opacity-100');
      bar.classList.add('opacity-0');
      setTimeout(() => { bar.style.visibility = 'hidden'; }, 300);
    }
    const badge = document.getElementById('edit-help-badge');
    if (badge) {
      badge.classList.remove('opacity-100');
      badge.classList.add('opacity-0');
    }
  }
  
  routeDirty = true;
  redrawRoute();
}

function removeVertex(index) {
  workingPoints.splice(index, 1);
  routeDirty = true;
  redrawRoute();
}

function updateDistanceDisplay() {
  const txt = formatDistance(totalDistanceMeters(workingPoints));
  const d = document.getElementById('route-distance');
  const m = document.getElementById('mobile-route-distance');
  if (d) d.textContent = txt;
  if (m) m.textContent = txt;
  // Update choice section visibility whenever point count changes.
  if (typeof updateChoiceSection === 'function') updateChoiceSection();
}

// ── Selection / state ─────────────────────────────────────

function loadRouteIntoView(routeId) {
  clearRouteGraphics();
  activeRouteId = routeId;
  const r = ROUTES.find((x) => x.id === routeId);
  workingPoints = r ? r.points.map((p) => ({ lat: p.lat, lng: p.lng })) : [];
  redrawRoute();
  updateChoiceSection();
  syncControlsState();
}

function buildPayload() {
  const r = ROUTES.find((x) => x.id === activeRouteId);
  return {
    name: r?.name || '',
    color: r?.color || IOF_PURPLE,
    points: workingPoints.map((p) => ({ lat: p.lat, lng: p.lng })),
  };
}

async function saveActiveRoute() {
  if (!activeRouteId) return;
  try {
    const saved = await apiUpdateRoute(activeRouteId, buildPayload());
    const idx = ROUTES.findIndex((x) => x.id === saved.id);
    if (idx >= 0) ROUTES[idx] = saved; else ROUTES.push(saved);
    routeDirty = false;
  } catch {
    alert('Échec de la sauvegarde du parcours');
  }
}

// ── Controls ──────────────────────────────────────────────

function refreshRouteSelect() {
  const selects = [document.getElementById('route-select'), document.getElementById('mobile-route-select')];
  selects.forEach((sel) => {
    if (!sel) return;
    sel.innerHTML = '<option value="">— Aucun parcours —</option>';
    ROUTES.forEach((r) => {
      const opt = document.createElement('option');
      opt.value = r.id;
      opt.textContent = r.name || 'Parcours';
      sel.appendChild(opt);
    });
    sel.value = activeRouteId || '';
  });
  syncControlsState();
}

function syncControlsState() {
  const hasActive = !!activeRouteId;
  const isCreating = document.getElementById('route-panel-creating')
    && !document.getElementById('route-panel-creating').classList.contains('hidden');

  [['btn-route-edit', 'mobile-btn-route-edit'], ['btn-route-delete', 'mobile-btn-route-delete'], ['btn-route-export-pdf', 'mobile-btn-route-export-pdf']]
    .flat()
    .forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = !hasActive;
    });

  const editLabel = isDrawing ? 'Terminer' : 'Éditer';
  const editIcon = isDrawing ? 'check' : 'edit';
  ['btn-route-edit', 'mobile-btn-route-edit'].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    const icon = btn.querySelector('.material-symbols-outlined');
    if (icon) icon.textContent = editIcon;
    const labelNode = Array.from(btn.childNodes)
      .find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0);
    if (labelNode) labelNode.textContent = ' ' + editLabel;
    btn.classList.toggle('bg-primary', isDrawing);
    btn.classList.toggle('text-on-primary', isDrawing);
  });

  const hint = document.getElementById('route-edit-hint');
  if (hint) hint.classList.toggle('hidden', !isDrawing);

  const editBar = document.getElementById('mobile-edit-bar');
  if (editBar) editBar.classList.toggle('hidden', !isDrawing);

  // Nom cliquable : visible si un parcours est actif et pas en mode création
  const nameDisplay = document.getElementById('route-name-display');
  const nameLabel = document.getElementById('route-name-label');
  if (nameDisplay && nameLabel) {
    if (hasActive && !isCreating) {
      const r = ROUTES.find((x) => x.id === activeRouteId);
      nameLabel.textContent = r?.name || 'Parcours';
      nameDisplay.classList.remove('hidden');
      nameDisplay.classList.add('flex');
    } else {
      nameDisplay.classList.add('hidden');
      nameDisplay.classList.remove('flex');
    }
  }
  // Masquer renommage inline si on change d'état
  exitRenameMode(false);
}

function openRoutePanel() {
  const panel = document.getElementById('route-panel');
  if (panel) panel.classList.remove('hidden');
  const btn = document.getElementById('btn-toggle-route-panel');
  if (!btn) return;
  btn.classList.remove('bg-surface', 'text-on-surface');
  btn.classList.add('bg-primary', 'text-on-primary');
}

function closeRoutePanel() {
  const panel = document.getElementById('route-panel');
  if (panel) panel.classList.add('hidden');
  const btn = document.getElementById('btn-toggle-route-panel');
  if (!btn) return;
  btn.classList.remove('bg-primary', 'text-on-primary');
  btn.classList.add('bg-surface', 'text-on-surface');
}

async function enterDrawing() {
  isDrawing = true;
  
  // Show state bar & badge with fade transition
  const bar = document.getElementById('route-state-bar');
  if (bar) {
    bar.style.visibility = 'visible';
    bar.classList.remove('opacity-0');
    bar.classList.add('opacity-100');
  }
  const badge = document.getElementById('edit-help-badge');
  if (badge) {
    badge.classList.remove('opacity-0');
    badge.classList.add('opacity-100');
  }
  
  syncControlsState();
  redrawRoute(); // re-render markers as draggable
}

async function exitDrawing() {
  isDrawing = false;
  
  // Hide state bar
  const bar = document.getElementById('route-state-bar');
  if (bar) {
    bar.classList.remove('opacity-100');
    bar.classList.add('opacity-0');
    setTimeout(() => { bar.style.visibility = 'hidden'; }, 300);
  }
  
  if (routeDirty) await saveActiveRoute();
  syncControlsState();
  redrawRoute(); // markers become non-draggable
}

async function onNewRoute() {
  if (isDrawing) await exitDrawing();
  const defaultName = 'Parcours ' + (ROUTES.length + 1);
  
  // Desktop: afficher le panel de création
  const creatingPanel = document.getElementById('route-panel-creating');
  const normalPanel = document.getElementById('route-panel-normal');
  if (creatingPanel && normalPanel) {
    openRoutePanel();
    creatingPanel.classList.remove('hidden');
    creatingPanel.classList.add('flex');
    normalPanel.classList.add('hidden');
    const input = document.getElementById('route-name-input-new');
    if (input) {
      input.value = defaultName;
      input.select();
      setTimeout(() => input.focus(), 50);
    }
  }
  
  // Mobile: afficher le popover de création
  const mobilePopoverNormal = document.getElementById('mobile-route-popover-normal');
  const mobilePopoverCreating = document.getElementById('mobile-route-popover-creating');
  if (mobilePopoverNormal && mobilePopoverCreating) {
    mobilePopoverNormal.classList.add('hidden');
    mobilePopoverCreating.classList.remove('hidden');
    mobilePopoverCreating.classList.add('flex');
    const mobileInput = document.getElementById('mobile-route-name-input-new');
    if (mobileInput) {
      mobileInput.value = defaultName;
      mobileInput.select();
      setTimeout(() => mobileInput.focus(), 50);
    }
  }
}

async function onStartRoute() {
  // Desktop
  const desktopInput = document.getElementById('route-name-input-new');
  // Mobile
  const mobileInput = document.getElementById('mobile-route-name-input-new');
  
  const name = ((desktopInput?.value || mobileInput?.value) || '').trim() || ('Parcours ' + (ROUTES.length + 1));
  
  try {
    const created = await apiCreateRoute({ name, color: IOF_PURPLE, points: [] });
    ROUTES.push(created);
    
    // Repasser en vue normale (desktop)
    const creating = document.getElementById('route-panel-creating');
    const normal = document.getElementById('route-panel-normal');
    if (creating) { creating.classList.add('hidden'); creating.classList.remove('flex'); }
    if (normal) normal.classList.remove('hidden');
    
    // Repasser en vue normale (mobile)
    const mobilePopoverNormal = document.getElementById('mobile-route-popover-normal');
    const mobilePopoverCreating = document.getElementById('mobile-route-popover-creating');
    if (mobilePopoverNormal && mobilePopoverCreating) {
      mobilePopoverNormal.classList.remove('hidden');
      mobilePopoverCreating.classList.add('hidden');
      mobilePopoverCreating.classList.remove('flex');
    }
    
    refreshRouteSelect();
    loadRouteIntoView(created.id);
    await enterDrawing();
  } catch {
    alert('Échec de la création du parcours');
  }
}

function cancelCreating() {
  // Desktop
  const creating = document.getElementById('route-panel-creating');
  const normal = document.getElementById('route-panel-normal');
  if (creating) { creating.classList.add('hidden'); creating.classList.remove('flex'); }
  if (normal) normal.classList.remove('hidden');
  
  // Mobile
  const mobilePopoverNormal = document.getElementById('mobile-route-popover-normal');
  const mobilePopoverCreating = document.getElementById('mobile-route-popover-creating');
  if (mobilePopoverNormal && mobilePopoverCreating) {
    mobilePopoverNormal.classList.remove('hidden');
    mobilePopoverCreating.classList.add('hidden');
    mobilePopoverCreating.classList.remove('flex');
  }
}

// ── Renommage inline ──────────────────────────────────────

function enterRenameMode() {
  const r = ROUTES.find((x) => x.id === activeRouteId);
  if (!r) return;
  const display = document.getElementById('route-name-display');
  const edit = document.getElementById('route-name-edit');
  const input = document.getElementById('route-name-input-rename');
  if (!display || !edit || !input) return;
  display.classList.add('hidden');
  display.classList.remove('flex');
  input.value = r.name || '';
  edit.classList.remove('hidden');
  edit.classList.add('flex');
  input.select();
  setTimeout(() => input.focus(), 50);
}

function exitRenameMode(save) {
  const display = document.getElementById('route-name-display');
  const edit = document.getElementById('route-name-edit');
  if (!edit || edit.classList.contains('hidden')) return;
  edit.classList.add('hidden');
  edit.classList.remove('flex');
  if (!save) {
    // Ré-afficher si un parcours est actif
    if (activeRouteId && display) {
      const r = ROUTES.find((x) => x.id === activeRouteId);
      const label = document.getElementById('route-name-label');
      if (label && r) label.textContent = r.name || 'Parcours';
      display.classList.remove('hidden');
      display.classList.add('flex');
    }
  }
}

async function confirmRename() {
  const input = document.getElementById('route-name-input-rename');
  const newName = (input?.value || '').trim();
  if (!newName || !activeRouteId) { exitRenameMode(false); return; }
  const r = ROUTES.find((x) => x.id === activeRouteId);
  if (!r || r.name === newName) { exitRenameMode(false); return; }
  try {
    const saved = await apiUpdateRoute(activeRouteId, {
      name: newName,
      color: r.color,
      points: workingPoints.map((p) => ({ lat: p.lat, lng: p.lng })),
    });
    const idx = ROUTES.findIndex((x) => x.id === activeRouteId);
    if (idx >= 0) ROUTES[idx] = saved;
    refreshRouteSelect();
    exitRenameMode(false);
  } catch {
    alert('Échec du renommage du parcours');
    exitRenameMode(false);
  }
}

async function onEditToggle() {
  if (!activeRouteId) return;
  if (isDrawing) await exitDrawing(); else await enterDrawing();
}

async function onDeleteRoute() {
  if (!activeRouteId) return;
  const r = ROUTES.find((x) => x.id === activeRouteId);
  if (!confirm('Supprimer « ' + (r?.name || 'ce parcours') + ' » ?')) return;
  try {
    await apiDeleteRoute(activeRouteId);
    ROUTES = ROUTES.filter((x) => x.id !== activeRouteId);
    isDrawing = false;
    routeDirty = false;
    clearRouteGraphics();
    activeRouteId = null;
    workingPoints = [];
    refreshRouteSelect();
    updateDistanceDisplay();
  } catch {
    alert('Échec de la suppression du parcours');
  }
}

async function onSelectRoute(value) {
  if (isDrawing) await exitDrawing();
  _clearChoiceGraphics();
  _hideChoiceResults();
  _choiceLegIndex = 0;
  if (!value) {
    clearRouteGraphics();
    activeRouteId = null;
    workingPoints = [];
    updateDistanceDisplay();
    updateChoiceSection();
    refreshRouteSelect();
    return;
  }
  loadRouteIntoView(value);
  refreshRouteSelect();
}

function bind(id, evt, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(evt, handler);
}

function setupRouteControls() {
  bind('route-select', 'change', (e) => onSelectRoute(e.target.value));
  bind('mobile-route-select', 'change', (e) => onSelectRoute(e.target.value));
  bind('btn-route-new', 'click', onNewRoute);
  bind('mobile-btn-route-new', 'click', onNewRoute);
  bind('btn-route-start', 'click', onStartRoute);
  bind('mobile-btn-route-start', 'click', onStartRoute);
  bind('btn-route-create-cancel', 'click', cancelCreating);
  bind('mobile-btn-route-create-cancel', 'click', cancelCreating);
  bind('btn-route-name-confirm', 'click', confirmRename);
  bind('btn-route-name-cancel', 'click', () => exitRenameMode(false));

  // Touche Entrée dans le champ "nouveau nom" (desktop)
  const inputNew = document.getElementById('route-name-input-new');
  if (inputNew) inputNew.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); onStartRoute(); } if (e.key === 'Escape') cancelCreating(); });
  
  // Touche Entrée dans le champ "nouveau nom" (mobile)
  const mobileInputNew = document.getElementById('mobile-route-name-input-new');
  if (mobileInputNew) mobileInputNew.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); onStartRoute(); } if (e.key === 'Escape') cancelCreating(); });

  // Touche Entrée dans le champ "renommer"
  const inputRename = document.getElementById('route-name-input-rename');
  if (inputRename) inputRename.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); confirmRename(); } if (e.key === 'Escape') exitRenameMode(false); });

  // Clic sur le nom affiché → renommage inline
  const nameDisplay = document.getElementById('route-name-display');
  if (nameDisplay) nameDisplay.addEventListener('click', enterRenameMode);
  bind('btn-route-edit', 'click', onEditToggle);
  bind('mobile-btn-route-edit', 'click', onEditToggle);
  bind('btn-route-delete', 'click', onDeleteRoute);
  bind('mobile-btn-route-delete', 'click', onDeleteRoute);
   bind('btn-route-export-pdf', 'click', onExportPdf);
  bind('mobile-btn-route-export-pdf', 'click', onExportPdf);

  // Bouton toggle du panel parcours (desktop)
  bind('btn-toggle-route-panel', 'click', () => {
    const panel = document.getElementById('route-panel');
    if (!panel) return;
    if (panel.classList.contains('hidden')) openRoutePanel(); else closeRoutePanel();
  });

  // Bouton fermer du panel parcours (desktop)
  bind('btn-close-route-panel', 'click', async () => {
    if (isDrawing) await exitDrawing();
    closeRoutePanel();
  });

  // Bouton "Terminer" de la barre d'édition mobile
  bind('mobile-btn-done', 'click', async () => {
    if (isDrawing) await exitDrawing();
  });

  // Bouton fermer du popover parcours mobile
  bind('mobile-btn-close-route', 'click', () => {
    const popover = document.getElementById('mobile-route-popover');
    if (popover) popover.classList.remove('open');
  });

  setupMobileRouteFAB();
}

function setupMobileRouteFAB() {
  const fab = document.getElementById('mobile-fab-route');
  const popover = document.getElementById('mobile-route-popover');
  if (!fab || !popover) return;

  let popoverOpen = false;

  function openRoutePopover() {
    popoverOpen = true;
    popover.classList.add('open');
  }

  function closeRoutePopover() {
    popoverOpen = false;
    popover.classList.remove('open');
    // Revenir en vue normale si on ferme le popover en mode création
    cancelCreating();
  }

  fab.addEventListener('click', (e) => {
    e.stopPropagation();
    if (popoverOpen) closeRoutePopover(); else openRoutePopover();
  });

  document.addEventListener('click', (e) => {
    if (popoverOpen && !popover.contains(e.target) && e.target !== fab) {
      closeRoutePopover();
    }
  });
}

// ── Init (called from viewer.js initApp) ──────────────────

// ── PDF Export Functions ──────────────────────────────────

function showPdfProgressModal() {
  const modal = document.getElementById('pdf-export-modal');
  if (modal) {
    modal.classList.remove('hidden');
    document.getElementById('pdf-progress-bar').style.width = '0%';
    document.getElementById('pdf-progress-text').textContent = '0%';
  }
}

function hidePdfProgressModal() {
  const modal = document.getElementById('pdf-export-modal');
  if (modal) modal.classList.add('hidden');
}

async function onExportPdf() {
  if (!activeRouteId) {
    showToast('Veuillez sélectionner un parcours', 'warning');
    return;
  }

  showPdfProgressModal();

  try {
    const mapId = encodeURIComponent(MAP_CONFIG.id);
    const routeId = encodeURIComponent(activeRouteId);

    // 1. Start export job (POST)
    const startRes = await fetch(`/api/maps/${mapId}/routes/${routeId}/export-pdf`, {
      method: 'POST',
    });
    if (!startRes.ok) {
      throw new Error(`Export start failed: ${startRes.status}`);
    }
    const { jobId } = await startRes.json();

    // 2. Listen to SSE stream
    const streamUrl = `/api/maps/${mapId}/routes/${routeId}/export-pdf/${jobId}/stream`;
    const eventSource = new EventSource(streamUrl);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Handle error
        if (data.error) {
          eventSource.close();
          hidePdfProgressModal();
          showToast(`Export échoué: ${data.error}`, 'error');
          return;
        }

        // Update progress bar
        if (typeof data.progress === 'number') {
          const progress = Math.min(100, Math.max(0, data.progress));
          const bar = document.getElementById('pdf-progress-bar');
          const text = document.getElementById('pdf-progress-text');
          if (bar) bar.style.width = progress + '%';
          if (text) text.textContent = progress + '%';
        }

        // Done: decode base64 PDF and download
        if (data.done && data.pdf) {
          eventSource.close();
          
          // Decode base64 PDF
          const binaryString = atob(data.pdf);
          const bytes = new Uint8Array(binaryString.length);
          for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
          }
          const pdfBlob = new Blob([bytes], { type: 'application/pdf' });

          // Trigger download
          const route = ROUTES.find(r => r.id === activeRouteId) || {};
          const filename = `${MAP_CONFIG.title || 'route'}-${route.name || 'unnamed'}.pdf`.replace(/[^a-z0-9.-]/gi, '_');
          const url = URL.createObjectURL(pdfBlob);
          const link = document.createElement('a');
          link.href = url;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(url);

          hidePdfProgressModal();
          showToast('PDF téléchargé avec succès', 'success');
        }
      } catch (e) {
        console.error('SSE parse error:', e);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      hidePdfProgressModal();
      showToast('Erreur de connexion lors du téléchargement', 'error');
    };

  } catch (err) {
    hidePdfProgressModal();
    showToast(`Export échoué: ${err.message}`, 'error');
    console.error('PDF export error:', err);
  }
}


async function initRoutes() {
  if (!MAP_CONFIG) return;
  setupRouteControls();
  await reloadRoutes();

  // Add points by clicking the map while in drawing mode.
  map.addListener('click', (e) => {
    if (isDrawing) addVertex(e.latLng);
  });

  // Symbols scale with the map above SYMBOL_REF_ZOOM, so rebuild the whole
  // course (markers + trimmed legs) whenever the zoom level changes.
  map.addListener('zoom_changed', () => {
    if (workingPoints.length) redrawRoute();
    _redrawChoices();
  });

  // While walking in Street View, the OCAD overlay is rotated around the
  // current panorama position by the heading. Both the heading (pov_changed)
  // AND the position (position_changed, which moves the rotation centre)
  // affect where the course lands, so re-render on either to keep it glued
  // to the map. The segment-choice graphics follow the same rotation.
  if (typeof panorama !== 'undefined' && panorama) {
    const rerender = () => {
      if (workingPoints.length) redrawRoute();
      _redrawChoices();
    };
    panorama.addListener('pov_changed', rerender);
    panorama.addListener('position_changed', rerender);
  }

  // Help badge: show state bar on click
  const badge = document.getElementById('edit-help-badge');
  if (badge) {
    badge.addEventListener('click', (e) => {
      e.stopPropagation();
      const bar = document.getElementById('route-state-bar');
      if (bar) {
        // Show bar temporarily for 5 seconds
        bar.style.visibility = 'visible';
        bar.classList.remove('opacity-0');
        bar.classList.add('opacity-100');
        // Auto-hide after 5 seconds
        setTimeout(() => {
          if (!isDrawing) {
            bar.classList.remove('opacity-100');
            bar.classList.add('opacity-0');
            setTimeout(() => { bar.style.visibility = 'hidden'; }, 300);
          }
        }, 5000);
      }
      // Also make badge clickable (cursor pointer)
      badge.style.cursor = 'pointer';
    });
    badge.style.cursor = 'pointer';
  }

  updateDistanceDisplay();
  initChoicePanel();
}

// ═══════════════════════════════════════════════════════════════
// Route-choice analysis — Analyse de tronçon
// ═══════════════════════════════════════════════════════════════

// In-memory cache keyed by `${routeId}_${legIndex}` → {choices, routesFound}.
const CHOICE_CACHE = new Map();

let _choiceLegIndex = 0;
let _choiceSource = null;
let _choiceJobId = null;
let _choicePolylines = [];
let _choiceLabels = [];
let _choiceVisible = [];
let _choiceData = null; // { choices, routesFound } — retained so graphics can be re-rendered on SV rotation

// ── Init ─────────────────────────────────────────────────────

function initChoicePanel() {
  document.getElementById('btn-toggle-choices')?.addEventListener('click', _toggleChoicesOpen);
  document.getElementById('btn-generate-choices')?.addEventListener('click', _onGenerate);
  document.getElementById('mobile-btn-toggle-choices')?.addEventListener('click', _mobileToggleChoicesOpen);
  document.getElementById('mobile-btn-generate-choices')?.addEventListener('click', _onGenerate);
  document.getElementById('mobile-btn-leg-prev')?.addEventListener('click', () => _shiftLeg(-1));
  document.getElementById('mobile-btn-leg-next')?.addEventListener('click', () => _shiftLeg(1));
}

function updateChoiceSection() {
  const has = activeRouteId && workingPoints.length >= 2;
  document.getElementById('route-choices-section')?.classList.toggle('hidden', !has);
  document.getElementById('mobile-route-choices-section')?.classList.toggle('hidden', !has);
  if (!has) { _clearChoiceGraphics(); return; }
  const legCount = workingPoints.length - 1;
  _choiceLegIndex = Math.min(_choiceLegIndex, legCount - 1);
  _rebuildLegStrip(legCount);
  _rebuildMobileLegNav(legCount);
  const gen = document.getElementById('btn-generate-choices');
  if (gen) gen.disabled = false;
  const mgen = document.getElementById('mobile-btn-generate-choices');
  if (mgen) mgen.disabled = false;
}

// ── Leg strip (desktop) ──────────────────────────────────────

function _rebuildLegStrip(legCount) {
  const strip = document.getElementById('choices-leg-strip');
  if (!strip) return;
  strip.innerHTML = '';
  for (let i = 0; i < legCount; i++) {
    const btn = document.createElement('button');
    const active = i === _choiceLegIndex;
    btn.className = [
      'flex-shrink-0 w-10 h-10 rounded border flex flex-col items-center justify-center gap-0 transition-colors',
      active
        ? 'border-primary bg-primary/10 text-primary'
        : 'border-outline-variant bg-surface text-on-surface-variant hover:bg-surface-variant',
    ].join(' ');
    btn.dataset.leg = i;
    btn.innerHTML = _miniLegSvg(i, legCount);
    btn.title = `Tronçon ${i}\u202f\u2192\u202f${i + 1}`;
    btn.addEventListener('click', () => _selectLeg(i));
    strip.appendChild(btn);
  }
}

function _miniLegSvg(legIndex, total) {
  const isStart = legIndex === 0;
  const isFinish = legIndex === total - 1;
  const nextNum = legIndex;

  const leftShape = isStart
    ? `<polygon points="9,1 16,14 2,14" fill="none" stroke="currentColor" stroke-width="1.5"/>`
    : `<circle cx="9" cy="9" r="7.5" fill="none" stroke="currentColor" stroke-width="1.5"/>` +
      `<text x="9" y="9" text-anchor="middle" dominant-baseline="central" font-size="9" font-weight="700" fill="currentColor">${legIndex}</text>`;

  const rightShape = isFinish
    ? `<circle cx="35" cy="9" r="7.5" fill="none" stroke="currentColor" stroke-width="1.5"/>` +
      `<circle cx="35" cy="9" r="4" fill="none" stroke="currentColor" stroke-width="1.5"/>`
    : `<circle cx="35" cy="9" r="7.5" fill="none" stroke="currentColor" stroke-width="1.5"/>` +
      `<text x="35" y="9" text-anchor="middle" dominant-baseline="central" font-size="9" font-weight="700" fill="currentColor">${nextNum + 1}</text>`;

  return `<svg viewBox="0 0 44 18" width="38" height="16">
    ${leftShape}<line x1="17" y1="9" x2="27" y2="9" stroke="currentColor" stroke-width="1.2"/>${rightShape}
  </svg>`;
}

// ── Leg nav (mobile) ─────────────────────────────────────────

function _rebuildMobileLegNav(legCount) {
  const lbl = document.getElementById('mobile-leg-label');
  if (lbl) lbl.textContent = `Tronçon ${_choiceLegIndex}\u202f\u2192\u202f${_choiceLegIndex + 1}`;
  const prev = document.getElementById('mobile-btn-leg-prev');
  const next = document.getElementById('mobile-btn-leg-next');
  if (prev) prev.disabled = _choiceLegIndex <= 0;
  if (next) next.disabled = _choiceLegIndex >= legCount - 1;
}

function _shiftLeg(delta) {
  const legCount = workingPoints.length - 1;
  _choiceLegIndex = Math.max(0, Math.min(legCount - 1, _choiceLegIndex + delta));
  _rebuildLegStrip(legCount);
  _rebuildMobileLegNav(legCount);
  _clearChoiceGraphics();
  _hideChoiceResults();
}

function _selectLeg(i) {
  _choiceLegIndex = i;
  _rebuildLegStrip(workingPoints.length - 1);
  _rebuildMobileLegNav(workingPoints.length - 1);
  _clearChoiceGraphics();
  _hideChoiceResults();
}

// ── Collapsible ──────────────────────────────────────────────

function _toggleChoicesOpen() {
  const c = document.getElementById('choices-content');
  const v = document.getElementById('choices-chevron');
  if (!c) return;
  const open = c.classList.contains('hidden');
  c.classList.toggle('hidden', !open);
  c.classList.toggle('flex', open);
  if (v) v.style.transform = open ? 'rotate(180deg)' : '';
}

function _mobileToggleChoicesOpen() {
  const c = document.getElementById('mobile-choices-content');
  const v = document.getElementById('mobile-choices-chevron');
  if (!c) return;
  const open = c.classList.contains('hidden');
  c.classList.toggle('hidden', !open);
  c.classList.toggle('flex', open);
  if (v) v.style.transform = open ? 'rotate(180deg)' : '';
}

// ── Generate ─────────────────────────────────────────────────

async function _onGenerate() {
  if (!activeRouteId || workingPoints.length < 2) return;

  const cacheKey = `${activeRouteId}_${_choiceLegIndex}`;
  if (CHOICE_CACHE.has(cacheKey)) {
    _displayChoices(CHOICE_CACHE.get(cacheKey));
    return;
  }

  if (_choiceSource) { _choiceSource.close(); _choiceSource = null; }
  _choiceJobId = null;
  _clearChoiceGraphics();
  _showChoiceProgress('Lancement de l\'analyse…', 0);

  const from = workingPoints[_choiceLegIndex];
  const to = workingPoints[_choiceLegIndex + 1];
  const mapId = encodeURIComponent(MAP_CONFIG.id);

  let jobId;
  try {
    const res = await fetch(`/api/maps/${mapId}/route-choices`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_point: from, to_point: to, count: 3 }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    ({ jobId } = await res.json());
  } catch (err) {
    _showChoiceError('Erreur : ' + err.message);
    return;
  }

  _choiceJobId = jobId;
  _choiceSource = new EventSource(`/api/maps/${mapId}/route-choices/${jobId}/stream`);

  _choiceSource.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); } catch { return; }
    if (data.error) {
      _choiceSource.close(); _choiceSource = null;
      _showChoiceError(data.error);
      return;
    }
    if (data.done) {
      _choiceSource.close(); _choiceSource = null;
      _hideChoiceProgress();
      CHOICE_CACHE.set(cacheKey, { choices: data.choices || [], routesFound: data.routesFound });
      _displayChoices({ choices: data.choices || [], routesFound: data.routesFound });
      return;
    }
    if (typeof data.progress === 'number') _updateChoiceProgress(data.progress);
  };

  _choiceSource.onerror = () => {
    _choiceSource.close(); _choiceSource = null;
    _showChoiceError('Connexion SSE perdue. Réessayez.');
  };
}

// ── Display ──────────────────────────────────────────────────

function _displayChoices({ choices, routesFound }) {
  _clearChoiceGraphics();
  _choiceData = { choices, routesFound };
  _choiceVisible = choices.map(() => true);
  _renderChoiceGraphics();
  _buildChoiceResultRows(choices, routesFound);
}

// Draw (or redraw) the choice polylines + labels for the current heading.
// Points are pre-rotated with toDisplay() so they stay glued to the OCAD
// overlay while walking in Street View, exactly like the course legs.
function _renderChoiceGraphics() {
  // Remove existing graphics without dropping _choiceData / _choiceVisible.
  _choicePolylines.forEach((p) => p.setMap(null));
  _choicePolylines = [];
  _choiceLabels.forEach((m) => { m.map = null; });
  _choiceLabels = [];

  if (!_choiceData || !_choiceData.choices) return;

  _choiceData.choices.forEach((choice, i) => {
    const path = choice.points.map((p) => toDisplay({ lat: p.lat, lng: p.lng }));
    const vis = _choiceVisible[i] !== false;
    const poly = new google.maps.Polyline({
      path,
      strokeColor: choice.color,
      strokeOpacity: 0.85,
      strokeWeight: 4,
      map: vis ? map : null,
      zIndex: 5 + i,
    });
    poly.setVisible(vis);
    _choicePolylines.push(poly);

    const midPt = path[Math.floor(path.length / 2)];
    const el = document.createElement('div');
    Object.assign(el.style, {
      background: choice.color, color: '#fff', borderRadius: '50%',
      width: '22px', height: '22px', display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      fontSize: '12px', fontWeight: 'bold', fontFamily: 'sans-serif',
      boxShadow: '0 1px 3px rgba(0,0,0,.4)',
    });
    el.textContent = choice.label;
    const marker = new google.maps.marker.AdvancedMarkerElement({
      position: midPt, map: vis ? map : null, content: el, zIndex: 20 + i,
    });
    _choiceLabels.push(marker);
  });
}

// Re-render choice graphics with the current heading (called on SV rotation/zoom).
function _redrawChoices() {
  if (_choiceData && _choiceData.choices && _choiceData.choices.length) {
    _renderChoiceGraphics();
  }
}

function _buildChoiceResultRows(choices, routesFound) {
  const ids = [
    ['choices-results', 'choices-info', false],
    ['mobile-choices-results', 'mobile-choices-info', true],
  ];
  ids.forEach(([resId, infoId, compact]) => {
    const container = document.getElementById(resId);
    if (!container) return;
    container.innerHTML = '';
    choices.forEach((choice, i) => {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-xs';

      const badge = document.createElement('span');
      const sz = compact ? 16 : 18;
      badge.style.cssText = `background:${choice.color};color:#fff;border-radius:50%;width:${sz}px;height:${sz}px;display:inline-flex;align-items:center;justify-content:center;font-size:${sz - 8}px;font-weight:bold;flex-shrink:0`;
      badge.textContent = choice.label;

      const dist = document.createElement('span');
      dist.className = 'flex-1 font-label-sm text-on-surface' + (compact ? ' text-[11px]' : '');
      dist.textContent = `${Math.round(choice.distanceMeters)} m (+${choice.detourPercent.toFixed(0)}%)`;

      const btn = document.createElement('button');
      btn.className = `w-${compact ? 5 : 6} h-${compact ? 5 : 6} flex items-center justify-center rounded hover:bg-surface-variant transition-colors`;
      btn.innerHTML = `<span class="material-symbols-outlined text-[${compact ? 14 : 16}px]" style="font-variation-settings:'FILL' 1">visibility</span>`;
      btn.title = 'Afficher / masquer';
      btn.addEventListener('click', () => _toggleChoiceVis(i, btn, compact));

      row.append(badge, dist, btn);
      container.appendChild(row);
    });
    container.classList.remove('hidden');
    container.classList.add('flex');

    const info = document.getElementById(infoId);
    if (info) {
      const d = choices[0]?.directDistanceMeters;
      const parts = [];
      if (routesFound < 3 && routesFound > 0) parts.push(`${routesFound} route(s) trouvée(s).`);
      if (d) parts.push(`Ligne directe : ${Math.round(d)} m`);
      info.textContent = parts.join(' · ');
      info.classList.remove('hidden');
    }
  });
}

function _toggleChoiceVis(idx, btn, compact) {
  _choiceVisible[idx] = !_choiceVisible[idx];
  const vis = _choiceVisible[idx];
  if (_choicePolylines[idx]) _choicePolylines[idx].setVisible(vis);
  if (_choiceLabels[idx]) _choiceLabels[idx].map = vis ? map : null;
  const icon = btn.querySelector('.material-symbols-outlined');
  if (icon) { icon.textContent = vis ? 'visibility' : 'visibility_off'; icon.style.fontVariationSettings = vis ? "'FILL' 1" : "'FILL' 0"; }
}

// ── Progress ─────────────────────────────────────────────────

function _showChoiceProgress(msg, pct) {
  ['choices-progress', 'mobile-choices-progress'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) { el.classList.remove('hidden'); el.classList.add('flex'); }
  });
  const s = document.getElementById('choices-status-text');
  const ms = document.getElementById('mobile-choices-status');
  if (s) s.textContent = msg;
  if (ms) ms.textContent = msg;
  _updateChoiceProgress(pct);
}

function _updateChoiceProgress(pct) {
  ['choices-progress-bar', 'mobile-choices-bar'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.width = pct + '%';
  });
}

function _hideChoiceProgress() {
  ['choices-progress', 'mobile-choices-progress'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) { el.classList.add('hidden'); el.classList.remove('flex'); }
  });
}

function _hideChoiceResults() {
  ['choices-results', 'mobile-choices-results'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) { el.classList.add('hidden'); el.classList.remove('flex'); el.innerHTML = ''; }
  });
  ['choices-info', 'mobile-choices-info'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) { el.classList.add('hidden'); el.textContent = ''; }
  });
}

function _showChoiceError(msg) {
  _hideChoiceProgress();
  // Strip machine-readable prefixes so the user sees a clean French message.
  const clean = String(msg).replace(/^(unreachable|start_blocked|end_blocked):\s*/, '');
  ['choices-results', 'mobile-choices-results'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `<span class="font-label-sm text-error">${clean}</span>`;
    el.classList.remove('hidden');
    el.classList.add('flex');
  });
}

function _clearChoiceGraphics() {
  _choicePolylines.forEach((p) => p.setMap(null));
  _choicePolylines = [];
  _choiceLabels.forEach((m) => { m.map = null; });
  _choiceLabels = [];
  _choiceVisible = [];
  _choiceData = null;
}
