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
let vertexMarkers = [];     // google.maps.Marker[] — start / controls / finish

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
    const marker = new google.maps.Marker({
      position: { lat: p.lat, lng: p.lng },
      map,
      draggable: isDrawing,
      crossOnDrag: false,
      icon: {
        url: sym.url,
        anchor: new google.maps.Point(sym.anchor.x, sym.anchor.y),
      },
      zIndex: 1000 + i,
    });

    if (isDrawing) {
      marker.addListener('drag', (e) => {
        workingPoints[i] = toReal({ lat: e.latLng.lat(), lng: e.latLng.lng() });
        drawLegs(color, workingPoints.map(toDisplay));
        updateDistanceDisplay();
      });
      marker.addListener('dragend', () => { routeDirty = true; redrawRoute(); });
      marker.addListener('click', () => removeVertex(i));
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
}

// ── Selection / state ─────────────────────────────────────

function loadRouteIntoView(routeId) {
  clearRouteGraphics();
  activeRouteId = routeId;
  const r = ROUTES.find((x) => x.id === routeId);
  workingPoints = r ? r.points.map((p) => ({ lat: p.lat, lng: p.lng })) : [];
  redrawRoute();
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

  [['btn-route-edit', 'mobile-btn-route-edit'], ['btn-route-delete', 'mobile-btn-route-delete']]
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
  if (!value) {
    clearRouteGraphics();
    activeRouteId = null;
    workingPoints = [];
    updateDistanceDisplay();
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
  });

  // While walking in Street View, the OCAD overlay is rotated around the
  // current panorama position by the heading. Both the heading (pov_changed)
  // AND the position (position_changed, which moves the rotation centre)
  // affect where the course lands, so re-render on either to keep it glued
  // to the map.
  if (typeof panorama !== 'undefined' && panorama) {
    const rerender = () => { if (workingPoints.length) redrawRoute(); };
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
}
