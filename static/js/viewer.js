/**
 * OCAD Map Viewer — Main viewer logic
 * Handles: overlay perspective rendering, rotation sync, Street View, calibration
 */

const API_KEY = 'AIzaSyBPeSD8OvJBFZW65UbFkciGb0jsXFdAwkc';

let map, overlay, panorama, svService, marker;
let rotationLocked = false;
let currentHeading = 0;
let overlayVisible = true;
let overlayOpacity = 0.7;
let MapImageOverlay;
let MAP_CONFIG = null;

// ── Perspective transform helpers ──────────────────────────

function rotatePoint(px, py, cx, cy, angleDeg) {
  const rad = angleDeg * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  return {
    x: cx + (px - cx) * cos - (py - cy) * sin,
    y: cy + (px - cx) * sin + (py - cy) * cos,
  };
}

function computePerspectiveCSS(src, dst) {
  const n = 8;
  const A = [], B = [];
  for (let i = 0; i < 4; i++) {
    const sx = src[i].x, sy = src[i].y;
    const dx = dst[i].x, dy = dst[i].y;
    A.push([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]);
    B.push(dx);
    A.push([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]);
    B.push(dy);
  }
  const M = A.map((row, i) => [...row, B[i]]);
  for (let col = 0; col < n; col++) {
    let maxVal = Math.abs(M[col][col]), maxRow = col;
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(M[row][col]) > maxVal) { maxVal = Math.abs(M[row][col]); maxRow = row; }
    }
    [M[col], M[maxRow]] = [M[maxRow], M[col]];
    for (let row = col + 1; row < n; row++) {
      const f = M[row][col] / M[col][col];
      for (let j = col; j <= n; j++) M[row][j] -= f * M[col][j];
    }
  }
  const x = new Array(n);
  for (let row = n - 1; row >= 0; row--) {
    x[row] = M[row][n];
    for (let col = row + 1; col < n; col++) x[row] -= M[row][col] * x[col];
    x[row] /= M[row][row];
  }
  const [a, b, c, d, e, f, g, h] = x;
  return `matrix3d(${a},${d},0,${g}, ${b},${e},0,${h}, 0,0,1,0, ${c},${f},0,1)`;
}

// ── Load map config and initialize ────────────────────────

async function loadMapConfig() {
  const params = new URLSearchParams(window.location.search);
  const mapId = params.get('map');
  if (!mapId) {
    window.location.href = '/';
    return;
  }

  const resp = await fetch(`/api/maps/${mapId}`);
  if (!resp.ok) {
    alert('Carte introuvable');
    window.location.href = '/';
    return;
  }

  MAP_CONFIG = await resp.json();
  document.title = `${MAP_CONFIG.title} — OCAD Map Viewer`;

  // Load Google Maps API
  const script = document.createElement('script');
  script.src = `https://maps.googleapis.com/maps/api/js?key=${API_KEY}&callback=initApp&v=weekly`;
  script.async = true;
  script.defer = true;
  document.head.appendChild(script);
}

// ── Main init (called by Google Maps callback) ────────────

function initApp() {
  const corners = MAP_CONFIG.corners;
  const center = {
    lat: (corners.nw.lat + corners.se.lat) / 2,
    lng: (corners.nw.lng + corners.se.lng) / 2,
  };
  const imageUrl = `/maps/${MAP_CONFIG.id}/map.png`;
  const [imgW, imgH] = MAP_CONFIG.imageSize;

  // Define OverlayView class
  MapImageOverlay = class extends google.maps.OverlayView {
    constructor(imageUrl, corners) {
      super();
      this.imageUrl_ = imageUrl;
      this.corners_ = corners;
      this.div_ = null;
      this.img_ = null;
    }

    onAdd() {
      this.div_ = document.createElement('div');
      this.div_.style.position = 'absolute';
      this.div_.style.border = 'none';

      this.img_ = document.createElement('img');
      this.img_.src = this.imageUrl_;
      this.img_.style.width = '100%';
      this.img_.style.height = '100%';
      this.img_.style.display = 'block';
      this.img_.style.opacity = overlayOpacity;
      this.div_.appendChild(this.img_);

      const panes = this.getPanes();
      panes.overlayLayer.appendChild(this.div_);
    }

    draw() {
      const proj = this.getProjection();
      if (!proj || !this.div_) return;

      const pxNW = proj.fromLatLngToDivPixel(this.corners_.nw);
      const pxNE = proj.fromLatLngToDivPixel(this.corners_.ne);
      const pxSE = proj.fromLatLngToDivPixel(this.corners_.se);
      const pxSW = proj.fromLatLngToDivPixel(this.corners_.sw);

      let dst = [
        { x: pxNW.x, y: pxNW.y },
        { x: pxNE.x, y: pxNE.y },
        { x: pxSE.x, y: pxSE.y },
        { x: pxSW.x, y: pxSW.y },
      ];

      if (currentHeading !== 0 && panorama && panorama.getPosition()) {
        const pxSV = proj.fromLatLngToDivPixel(panorama.getPosition());
        dst = dst.map(p => rotatePoint(p.x, p.y, pxSV.x, pxSV.y, -currentHeading));
      }

      const W = imgW;
      const H = imgH;
      const src = [
        { x: 0, y: 0 },
        { x: W, y: 0 },
        { x: W, y: H },
        { x: 0, y: H },
      ];

      this.div_.style.left = '0px';
      this.div_.style.top = '0px';
      this.div_.style.width = W + 'px';
      this.div_.style.height = H + 'px';
      this.div_.style.transformOrigin = '0 0';
      this.div_.style.transform = computePerspectiveCSS(src, dst);
    }

    onRemove() {
      if (this.div_ && this.div_.parentNode) {
        this.div_.parentNode.removeChild(this.div_);
        this.div_ = null;
        this.img_ = null;
      }
    }

    setOpacity(val) {
      overlayOpacity = val;
      if (this.img_) this.img_.style.opacity = val;
    }

    hide() { if (this.div_) this.div_.style.display = 'none'; }
    show() { if (this.div_) this.div_.style.display = ''; }

    updateCorners(newCorners) {
      this.corners_ = newCorners;
      this.draw();
    }
  };

  // Create map
  map = new google.maps.Map(document.getElementById('map'), {
    center: center,
    zoom: 16,
    mapTypeId: 'satellite',
    heading: 0,
    tilt: 0,
    streetViewControl: true,
    mapTypeControl: true,
    mapTypeControlOptions: { position: google.maps.ControlPosition.TOP_RIGHT },
  });

  // Create overlay
  const mapCorners = {
    nw: new google.maps.LatLng(corners.nw.lat, corners.nw.lng),
    ne: new google.maps.LatLng(corners.ne.lat, corners.ne.lng),
    se: new google.maps.LatLng(corners.se.lat, corners.se.lng),
    sw: new google.maps.LatLng(corners.sw.lat, corners.sw.lng),
  };
  overlay = new MapImageOverlay(imageUrl, mapCorners);
  overlay.setMap(map);

  // Street View
  svService = new google.maps.StreetViewService();
  panorama = new google.maps.StreetViewPanorama(document.getElementById('pano'), {
    enableCloseButton: false,
    addressControl: true,
    linksControl: true,
    panControl: true,
    zoomControl: true,
  });
  map.setStreetView(panorama);

  // Marker
  marker = new google.maps.Marker({
    map: map,
    icon: {
      path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW,
      scale: 7,
      fillColor: '#FFDD00',
      fillOpacity: 0.95,
      strokeColor: '#333',
      strokeWeight: 2,
      rotation: 0,
    },
    title: 'Position Street View',
    visible: false,
  });

  // Events
  map.addListener('click', (e) => openStreetView(e.latLng));

  panorama.addListener('position_changed', () => {
    const pos = panorama.getPosition();
    if (pos) {
      marker.setPosition(pos);
      marker.setVisible(true);
      map.setCenter(pos);
    }
  });

  panorama.addListener('pov_changed', () => {
    const heading = panorama.getPov().heading;
    currentHeading = heading;
    if (!rotationLocked) {
      updateCompass(heading);
      if (overlay) overlay.draw();
    }
    marker.setIcon({
      path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW,
      scale: 7,
      fillColor: '#FFDD00',
      fillOpacity: 0.95,
      strokeColor: '#333',
      strokeWeight: 2,
      rotation: 0,
    });
  });

  panorama.addListener('visible_changed', () => {
    if (!panorama.getVisible()) marker.setVisible(false);
  });

  map.addListener('heading_changed', () => {
    if (overlay) overlay.draw();
  });

  // Controls
  setupOpacity();
  setupToggle();
  setupDivider();
  setupCalibration();
  setupRotationLock();
  setupBackButton();
}

// ── UI helpers ────────────────────────────────────────────

function updateCompass(heading) {
  const svg = document.querySelector('#compass svg');
  if (svg) svg.style.transform = `rotate(${-heading}deg)`;
}

function setupBackButton() {
  document.getElementById('btn-back').addEventListener('click', () => {
    window.location.href = '/';
  });
}

function setupOpacity() {
  const slider = document.getElementById('opacity-slider');
  const val = document.getElementById('opacity-value');
  slider.addEventListener('input', function() {
    const v = parseInt(this.value) / 100;
    overlay.setOpacity(v);
    val.textContent = this.value + '%';
  });
}

function setupToggle() {
  document.getElementById('toggle-overlay').addEventListener('change', function() {
    overlayVisible = this.checked;
    if (overlayVisible) overlay.show(); else overlay.hide();
  });
}

function setupRotationLock() {
  const btn = document.getElementById('btn-lock-rotation');
  btn.addEventListener('click', () => {
    rotationLocked = !rotationLocked;
    btn.classList.toggle('locked', rotationLocked);
    btn.innerHTML = rotationLocked ? '&#x1F513;' : '&#x1F512;';
    btn.title = rotationLocked ? 'Rotation verrouillée' : 'Verrouiller la rotation';
    if (!rotationLocked) {
      updateCompass(currentHeading);
      if (overlay) overlay.draw();
    }
  });
}

function openStreetView(latLng) {
  const msgEl = document.getElementById('no-streetview-msg');
  msgEl.style.display = 'none';
  svService.getPanorama({ location: latLng, radius: 100, source: google.maps.StreetViewSource.OUTDOOR })
    .then((response) => {
      const location = response.data.location;
      panorama.setPano(location.pano);
      panorama.setPov({ heading: 0, pitch: 0 });
      panorama.setVisible(true);
      document.getElementById('street-panel-placeholder').style.display = 'none';
      document.getElementById('pano').style.display = 'block';
      marker.setPosition(location.latLng);
      marker.setVisible(true);
    })
    .catch(() => {
      msgEl.style.display = 'block';
      setTimeout(() => { msgEl.style.display = 'none'; }, 3000);
    });
}

// ── Calibration ───────────────────────────────────────────

let calOffsetLat = 0;
let calOffsetLng = 0;

function applyCalibration() {
  const corners = MAP_CONFIG.corners;
  const centerLat = (corners.nw.lat + corners.se.lat) / 2;
  const dLat = calOffsetLat / 111320;
  const dLng = calOffsetLng / (111320 * Math.cos(centerLat * Math.PI / 180));

  const newCorners = {
    nw: new google.maps.LatLng(corners.nw.lat + dLat, corners.nw.lng + dLng),
    ne: new google.maps.LatLng(corners.ne.lat + dLat, corners.ne.lng + dLng),
    se: new google.maps.LatLng(corners.se.lat + dLat, corners.se.lng + dLng),
    sw: new google.maps.LatLng(corners.sw.lat + dLat, corners.sw.lng + dLng),
  };
  overlay.updateCorners(newCorners);
}

function setupCalibration() {
  const panel = document.getElementById('calibration-panel');
  const btnOpen = document.getElementById('btn-calibrate');
  const btnClose = document.getElementById('cal-close');
  const btnReset = document.getElementById('cal-reset');
  const sliderLat = document.getElementById('cal-lat');
  const sliderLng = document.getElementById('cal-lng');
  const valLat = document.getElementById('cal-lat-val');
  const valLng = document.getElementById('cal-lng-val');

  btnOpen.addEventListener('click', () => {
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  });
  btnClose.addEventListener('click', () => { panel.style.display = 'none'; });
  btnReset.addEventListener('click', () => {
    sliderLat.value = 0; sliderLng.value = 0;
    calOffsetLat = 0; calOffsetLng = 0;
    valLat.textContent = '0.0m'; valLng.textContent = '0.0m';
    applyCalibration();
  });
  sliderLat.addEventListener('input', function() {
    calOffsetLat = parseFloat(this.value);
    valLat.textContent = calOffsetLat.toFixed(1) + 'm';
    applyCalibration();
  });
  sliderLng.addEventListener('input', function() {
    calOffsetLng = parseFloat(this.value);
    valLng.textContent = calOffsetLng.toFixed(1) + 'm';
    applyCalibration();
  });
}

// ── Resizable split ───────────────────────────────────────

function setupDivider() {
  const divider = document.getElementById('divider');
  const mapPanel = document.getElementById('map-panel');
  const streetPanel = document.getElementById('street-panel');
  let isDragging = false;

  divider.addEventListener('mousedown', (e) => { isDragging = true; e.preventDefault(); });
  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const containerWidth = document.getElementById('container').offsetWidth;
    const ratio = e.clientX / containerWidth;
    const clamped = Math.max(0.2, Math.min(0.8, ratio));
    mapPanel.style.flex = 'none';
    streetPanel.style.flex = 'none';
    mapPanel.style.width = (clamped * 100) + '%';
    streetPanel.style.width = ((1 - clamped) * 100 - 0.5) + '%';
    google.maps.event.trigger(map, 'resize');
    google.maps.event.trigger(panorama, 'resize');
  });
  document.addEventListener('mouseup', () => { isDragging = false; });
}

// ── Start ─────────────────────────────────────────────────

loadMapConfig();
