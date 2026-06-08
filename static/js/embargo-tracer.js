/**
 * OCAD Map Viewer — Embargo Zone Tracer
 * Modal pour tracer manuellement une zone d'embargo (polygone)
 */

// Global state
let tracingMap = null;
let tracingMarkers = [];
let tracingPolyline = null;
let tracingPolygon = null;
const tracingPoints = [];

// ── Open embargo tracing modal ──

function openEmbargoModal() {
  if (!MAP_CONFIG) {
    showToast('Carte non chargée', 'error');
    return;
  }

  const modal = document.getElementById('embargo-modal');
  if (!modal) {
    console.error('Embargo modal not found');
    return;
  }

  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  // Init map on first open
  if (!tracingMap) {
    initTracingMap();
  }

  // Load existing embargo if any
  if (MAP_CONFIG.embargoPoly) {
    drawExistingZone(MAP_CONFIG.embargoPoly.points);
    showDeleteButton();
  } else {
    hideDeleteButton();
  }

  updatePointCount();
}

function closeEmbargoModal() {
  const modal = document.getElementById('embargo-modal');
  if (modal) {
    modal.style.display = 'none';
  }
  document.body.style.overflow = '';
  resetTracing();
}

// ── Initialize tracing map ──

function initTracingMap() {
  const mapContainer = document.getElementById('embargo-map');
  if (!mapContainer) {
    console.error('embargo-map container not found');
    return;
  }

  // Default center: Rzeszow
  const center = { lat: 50.034, lng: 21.995 };

  tracingMap = new google.maps.Map(mapContainer, {
    center: center,
    zoom: 16,
    mapTypeId: 'satellite',
    mapId: GOOGLE_MAPS_MAP_ID || 'DEMO_MAP_ID',
    disableDefaultUI: true,
    gestureHandling: 'greedy'
  });

  // Click to add point
  tracingMap.addListener('click', (event) => {
    addTracingPoint(event.latLng);
  });
}

// ── Add point to tracing polygon ──

function addTracingPoint(latLng) {
  if (tracingPoints.length >= 100) {
    showToast('Max 100 points atteint', 'warning');
    return;
  }

  const point = { lat: latLng.lat(), lng: latLng.lng() };
  tracingPoints.push(point);

  // Add marker with label
  const marker = new google.maps.Marker({
    position: latLng,
    map: tracingMap,
    label: String(tracingPoints.length),
    title: `Point ${tracingPoints.length}`
  });
  tracingMarkers.push(marker);

  // Warning at 50 points
  if (tracingPoints.length === 50) {
    showToast('Zone complexe = peut être lente', 'info');
  }

  redrawTracingPolygon();
  updatePointCount();
}

// ── Redraw polygon in real-time ──

function redrawTracingPolygon() {
  // Remove old polyline/polygon
  if (tracingPolyline) tracingPolyline.setMap(null);
  if (tracingPolygon) tracingPolygon.setMap(null);

  if (tracingPoints.length < 2) return;

  // Draw polyline connecting points
  tracingPolyline = new google.maps.Polyline({
    paths: tracingPoints,
    strokeColor: '#FF0000',
    strokeOpacity: 0.7,
    strokeWeight: 2,
    map: tracingMap
  });

  // Draw filled polygon if >= 3 points
  if (tracingPoints.length >= 3) {
    tracingPolygon = new google.maps.Polygon({
      paths: tracingPoints,
      strokeColor: '#FF0000',
      strokeOpacity: 0.8,
      strokeWeight: 2,
      fillColor: '#FF0000',
      fillOpacity: 0.1,
      map: tracingMap
    });
  }
}

// ── Delete last point ──

function deleteLastPoint() {
  if (tracingPoints.length === 0) {
    showToast('Aucun point à supprimer', 'warning');
    return;
  }

  tracingPoints.pop();
  if (tracingMarkers.length > 0) {
    const marker = tracingMarkers.pop();
    marker.setMap(null);
  }

  redrawTracingPolygon();
  updatePointCount();
}

// ── Reset all points ──

function resetTracing() {
  tracingPoints.length = 0;
  tracingMarkers.forEach(m => m.setMap(null));
  tracingMarkers.length = 0;

  if (tracingPolyline) {
    tracingPolyline.setMap(null);
    tracingPolyline = null;
  }
  if (tracingPolygon) {
    tracingPolygon.setMap(null);
    tracingPolygon = null;
  }

  updatePointCount();
}

// ── Update UI point count ──

function updatePointCount() {
  const countEl = document.getElementById('embargo-point-count');
  if (countEl) {
    countEl.textContent = `${tracingPoints.length} point${tracingPoints.length !== 1 ? 's' : ''}`;
  }
}

// ── Draw existing embargo zone (read-only) ──

function drawExistingZone(points) {
  if (!tracingMap || !points || points.length < 3) return;

  // Draw in gray to indicate existing zone
  const existingPolygon = new google.maps.Polygon({
    paths: points,
    strokeColor: '#999999',
    strokeOpacity: 0.6,
    strokeWeight: 2,
    fillColor: '#999999',
    fillOpacity: 0.1,
    map: tracingMap,
    editable: false,
    clickable: false
  });
}

// ── Validate & save embargo zone ──

async function validateAndSaveEmbargo() {
  if (tracingPoints.length < 3) {
    showToast('Minimum 3 points requis', 'error');
    return;
  }

  if (!MAP_CONFIG || !MAP_CONFIG.id) {
    showToast('Erreur: Carte non chargée', 'error');
    return;
  }

  const mapId = MAP_CONFIG.id;
  const payload = { points: tracingPoints };

  try {
    const response = await fetch(`/api/maps/${encodeURIComponent(mapId)}/embargo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const error = await response.json();
      showToast(error.detail || 'Erreur lors de l\'enregistrement', 'error');
      return;
    }

    showToast('Zone embargo créée ✅', 'success');
    closeEmbargoModal();

    // Reload map config and redraw
    await loadMapConfigAndRedraw();
  } catch (err) {
    showToast('Erreur réseau: ' + err.message, 'error');
  }
}

// ── Delete existing embargo zone ──

async function deleteExistingEmbargo() {
  if (!confirm('Êtes-vous sûr de vouloir supprimer la zone embargo ?')) {
    return;
  }

  if (!MAP_CONFIG || !MAP_CONFIG.id) {
    showToast('Erreur: Carte non chargée', 'error');
    return;
  }

  const mapId = MAP_CONFIG.id;

  try {
    const response = await fetch(`/api/maps/${encodeURIComponent(mapId)}/embargo`, {
      method: 'DELETE'
    });

    if (!response.ok) {
      const error = await response.json();
      showToast(error.detail || 'Erreur lors de la suppression', 'error');
      return;
    }

    showToast('Zone embargo supprimée ✅', 'success');
    closeEmbargoModal();

    // Reload map config and redraw
    await loadMapConfigAndRedraw();
  } catch (err) {
    showToast('Erreur réseau: ' + err.message, 'error');
  }
}

// ── Show/hide delete button ──

function showDeleteButton() {
  const btn = document.getElementById('embargo-btn-delete-zone');
  if (btn) btn.classList.remove('hidden');
}

function hideDeleteButton() {
  const btn = document.getElementById('embargo-btn-delete-zone');
  if (btn) btn.classList.add('hidden');
}

function hideDeleteButton() {
  const btn = document.getElementById('embargo-delete-btn');
  if (btn) btn.style.display = 'none';
}

// ── Utility: reload map config ──

async function loadMapConfigAndRedraw() {
  try {
    const params = new URLSearchParams(window.location.search);
    const mapId = params.get('map');
    if (!mapId) return;

    const response = await fetch(`/api/maps/${encodeURIComponent(mapId)}`);
    if (response.ok) {
      MAP_CONFIG = await response.json();
      
      // Redraw embargo polygon if exists
      if (typeof window.refreshEmbargoVisualization === 'function') {
        window.refreshEmbargoVisualization();
      }
    }
  } catch (err) {
    console.error('Error reloading map config:', err);
  }
}

// ── Note: showToast() is defined in routes.js (loaded before this file)

// ── Setup event listeners (CSP-compliant) ────────────────────

function setupEmbargoEventListeners() {
  // Desktop button
  const btnEmbargo = document.getElementById('btn-embargo');
  if (btnEmbargo) {
    console.log('✓ btn-embargo found, attaching click listener');
    btnEmbargo.addEventListener('click', openEmbargoModal);
  }
  
  // Mobile FAB
  const mobileFabEmbargo = document.getElementById('mobile-fab-embargo');
  if (mobileFabEmbargo) {
    mobileFabEmbargo.addEventListener('click', () => {
      const popover = document.getElementById('mobile-embargo-popover');
      if (popover) popover.classList.toggle('open');
    });
  }
  
  // Mobile button to open modal
  const mobileBtnEmbargoOpen = document.getElementById('mobile-btn-embargo-open');
  if (mobileBtnEmbargoOpen) {
    mobileBtnEmbargoOpen.addEventListener('click', () => {
      const popover = document.getElementById('mobile-embargo-popover');
      if (popover) popover.classList.remove('open');
      openEmbargoModal();
    });
  }
  
  // Mobile button to close popover
  const mobileBtnCloseEmbargo = document.getElementById('mobile-btn-close-embargo');
  if (mobileBtnCloseEmbargo) {
    mobileBtnCloseEmbargo.addEventListener('click', () => {
      const popover = document.getElementById('mobile-embargo-popover');
      if (popover) popover.classList.remove('open');
    });
  }
  
  // Modal buttons
  const modalCloseBtn = document.getElementById('embargo-modal-close');
  if (modalCloseBtn) {
    modalCloseBtn.addEventListener('click', closeEmbargoModal);
  }
  
  const btnDeleteLast = document.getElementById('embargo-btn-delete-last');
  if (btnDeleteLast) {
    btnDeleteLast.addEventListener('click', deleteLastPoint);
  }
  
  const btnReset = document.getElementById('embargo-btn-reset');
  if (btnReset) {
    btnReset.addEventListener('click', resetTracing);
  }
  
  const btnDeleteZone = document.getElementById('embargo-btn-delete-zone');
  if (btnDeleteZone) {
    btnDeleteZone.addEventListener('click', deleteExistingEmbargo);
  }
  
  const btnSave = document.getElementById('embargo-btn-save');
  if (btnSave) {
    btnSave.addEventListener('click', validateAndSaveEmbargo);
  }
  
  const btnCancel = document.getElementById('embargo-btn-cancel');
  if (btnCancel) {
    btnCancel.addEventListener('click', closeEmbargoModal);
  }
  
  // Close modal on backdrop click
  const embargoModal = document.getElementById('embargo-modal');
  if (embargoModal) {
    embargoModal.addEventListener('click', (e) => {
      if (e.target === embargoModal) closeEmbargoModal();
    });
  }
}

// Call setup when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupEmbargoEventListeners);
} else {
  setupEmbargoEventListeners();
}
