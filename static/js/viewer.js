/**
 * OCAD Map Viewer — Main viewer logic
 * Handles: overlay perspective rendering, rotation sync, Street View, calibration
 */

let map, overlay, panorama, svService, marker;
let rotationLocked = false;
let currentHeading = 0;
let overlayVisible = true;
let overlayOpacity = 1.0;
let MapImageOverlay;
let MAP_CONFIG = null;
let GOOGLE_MAPS_MAP_ID = 'DEMO_MAP_ID';

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

  const [mapResp, configResp] = await Promise.all([
    fetch('/api/maps/' + encodeURIComponent(mapId)),
    fetch('/api/config'),
  ]);

  if (!mapResp.ok) {
    alert('Carte introuvable');
    window.location.href = '/';
    return;
  }
  if (!configResp.ok) {
    alert('Erreur de configuration serveur');
    return;
  }

  MAP_CONFIG = await mapResp.json();
  const { googleMapsApiKey, googleMapsMapId } = await configResp.json();
  if (googleMapsMapId) GOOGLE_MAPS_MAP_ID = googleMapsMapId;
  document.title = MAP_CONFIG.title + ' — OCAD Map Viewer';

  // Load Google Maps API
  const script = document.createElement('script');
  script.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(googleMapsApiKey) + '&callback=initApp&v=weekly&loading=async&libraries=marker';
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

  // Use mobile-optimized image on small screens / touch devices
  const useMobile = window.innerWidth < 768 || ('ontouchstart' in window);
  const hasMobileImage = MAP_CONFIG.imageSizeMobile && MAP_CONFIG.imageSizeMobile[0] > 0;
  const imageFilename = (useMobile && hasMobileImage) ? 'map-mobile.png' : 'map.png';
  const imageUrl = '/maps/' + encodeURIComponent(MAP_CONFIG.id) + '/' + imageFilename;
  const [imgW, imgH] = (useMobile && hasMobileImage) ? MAP_CONFIG.imageSizeMobile : MAP_CONFIG.imageSize;

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
    mapId: GOOGLE_MAPS_MAP_ID,
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
  const markerContent = document.createElement('div');
  markerContent.style.width = '28px';
  markerContent.style.height = '28px';
  markerContent.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28"><polygon points="14,3 23,25 14,20 5,25" fill="#FFDD00" fill-opacity="0.95" stroke="#333" stroke-width="2" stroke-linejoin="round"/></svg>';
  marker = new google.maps.marker.AdvancedMarkerElement({
    map: null,
    position: center,
    content: markerContent,
    title: 'Position Street View',
  });

  // Events
  map.addListener('click', (e) => {
    if (typeof isRouteDrawing === 'function' && isRouteDrawing()) return;
    openStreetView(e.latLng);
  });

  panorama.addListener('position_changed', () => {
    const pos = panorama.getPosition();
    if (pos) {
      marker.position = pos;
      marker.map = map;
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
  });

  panorama.addListener('visible_changed', () => {
    if (!panorama.getVisible()) marker.map = null;
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
  setupMobileFAB();

  // Route (course) planning layer
  if (typeof initRoutes === 'function') initRoutes();
}

// ── UI helpers ────────────────────────────────────────────

function updateCompass(heading) {
  const svg = document.getElementById('compass-svg');
  if (svg) svg.style.transform = 'rotate(' + (-heading) + 'deg)';
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
  const icon = btn.querySelector('.material-symbols-outlined');
  btn.addEventListener('click', () => {
    rotationLocked = !rotationLocked;
    if (rotationLocked) {
      icon.textContent = 'lock';
      btn.title = 'Rotation verrouillée — cliquez pour déverrouiller';
      btn.classList.add('text-primary', 'bg-surface-variant');
    } else {
      icon.textContent = 'lock_open';
      btn.title = 'Verrouiller la rotation';
      btn.classList.remove('text-primary', 'bg-surface-variant');
      updateCompass(currentHeading);
      if (overlay) overlay.draw();
    }
  });
}

function openStreetView(latLng) {
  const msgEl = document.getElementById('no-streetview-msg');
  msgEl.classList.add('hidden');
  svService.getPanorama({ location: latLng, radius: 100, source: google.maps.StreetViewSource.OUTDOOR })
    .then((response) => {
      const location = response.data.location;
      panorama.setPano(location.pano);
      panorama.setPov({ heading: 0, pitch: 0 });
      panorama.setVisible(true);
      document.getElementById('street-panel-placeholder').classList.add('hidden');
      document.getElementById('pano').classList.remove('hidden');
      marker.position = location.latLng;
      marker.map = map;
    })
    .catch(() => {
      msgEl.classList.remove('hidden');
      setTimeout(() => { msgEl.classList.add('hidden'); }, 3000);
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
    panel.classList.toggle('hidden');
  });
  btnClose.addEventListener('click', () => { panel.classList.add('hidden'); });
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

function isMobile() {
  return window.innerWidth < 768;
}

function setupDivider() {
  const divider = document.getElementById('divider');
  const mapPanel = document.getElementById('map-panel');
  const streetPanel = document.getElementById('street-panel');
  let isDragging = false;

  // Desktop: horizontal drag
  divider.addEventListener('mousedown', (e) => {
    if (isMobile()) return;
    isDragging = true;
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!isDragging || isMobile()) return;
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

  // Mobile: vertical drag (touch)
  divider.addEventListener('touchstart', (e) => {
    if (!isMobile()) return;
    isDragging = true;
    e.preventDefault();
  }, { passive: false });
  document.addEventListener('touchmove', (e) => {
    if (!isDragging || !isMobile()) return;
    const touch = e.touches[0];
    const containerHeight = document.getElementById('container').offsetHeight;
    const ratio = touch.clientY / containerHeight;
    const clamped = Math.max(0.25, Math.min(0.75, ratio));
    mapPanel.style.flex = 'none';
    streetPanel.style.flex = 'none';
    mapPanel.style.height = (clamped * 100) + '%';
    mapPanel.style.width = '100%';
    streetPanel.style.height = ((1 - clamped) * 100 - 1) + '%';
    streetPanel.style.width = '100%';
    google.maps.event.trigger(map, 'resize');
    google.maps.event.trigger(panorama, 'resize');
  }, { passive: true });
  document.addEventListener('touchend', () => { isDragging = false; });

  // Handle responsive reset on resize
  window.addEventListener('resize', () => {
    mapPanel.style.flex = '';
    mapPanel.style.width = '';
    mapPanel.style.height = '';
    streetPanel.style.flex = '';
    streetPanel.style.width = '';
    streetPanel.style.height = '';
    if (map) google.maps.event.trigger(map, 'resize');
    if (panorama) google.maps.event.trigger(panorama, 'resize');
  });
}

// ── Mobile FAB + Opacity Popover ──────────────────────────

function setupMobileFAB() {
  const fab = document.getElementById('mobile-fab');
  const popover = document.getElementById('mobile-fab-popover');
  if (!fab || !popover) return;

  let popoverOpen = false;
  let autoCloseTimer = null;

  function openPopover() {
    popoverOpen = true;
    popover.classList.add('open');
    resetAutoClose();
  }

  function closePopover() {
    popoverOpen = false;
    popover.classList.remove('open');
    clearTimeout(autoCloseTimer);
  }

  function resetAutoClose() {
    clearTimeout(autoCloseTimer);
    autoCloseTimer = setTimeout(closePopover, 4000);
  }

  fab.addEventListener('click', (e) => {
    e.stopPropagation();
    if (popoverOpen) closePopover(); else openPopover();
  });

  // Close when tapping outside
  document.addEventListener('click', (e) => {
    if (popoverOpen && !popover.contains(e.target) && e.target !== fab) {
      closePopover();
    }
  });

  // Opacity slider
  const slider = document.getElementById('mobile-opacity-slider');
  const val = document.getElementById('mobile-opacity-value');
  if (slider) {
    slider.addEventListener('input', function() {
      const v = parseInt(this.value) / 100;
      overlay.setOpacity(v);
      val.textContent = this.value + '%';
      // Sync desktop
      const ds = document.getElementById('opacity-slider');
      const dv = document.getElementById('opacity-value');
      if (ds) { ds.value = this.value; dv.textContent = this.value + '%'; }
      resetAutoClose();
    });
    // Keep popover open while interacting with slider
    slider.addEventListener('touchstart', () => clearTimeout(autoCloseTimer), { passive: true });
    slider.addEventListener('touchend', resetAutoClose, { passive: true });
  }
}

// ── Start ─────────────────────────────────────────────────

loadMapConfig();
