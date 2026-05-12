/**
 * Home page: upload maps + display list
 */

document.addEventListener('DOMContentLoaded', function() {
  const zone = document.getElementById('upload-zone');
  const fileInput = document.getElementById('file-input');
  const progress = document.getElementById('upload-progress');
  const errorEl = document.getElementById('upload-error');
  const grid = document.getElementById('maps-grid');
  const emptyState = document.getElementById('empty-state');

  // Load map list
  loadMaps();

  // Click to select file
  zone.addEventListener('click', () => fileInput.click());

  // Drag & drop
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('dragover');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length > 0) uploadFile(files[0]);
  });

  // File input change
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) uploadFile(fileInput.files[0]);
  });

  async function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      showError('Seuls les fichiers PDF géo-référencés (export OCAD) sont acceptés.');
      return;
    }

    errorEl.style.display = 'none';
    progress.style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);

    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: formData });
      if (!resp.ok) {
        const data = await resp.json();
        throw new Error(data.detail || 'Erreur lors du traitement');
      }
      await loadMaps();
    } catch (err) {
      showError(err.message);
    } finally {
      progress.style.display = 'none';
      fileInput.value = '';
    }
  }

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.style.display = 'block';
    setTimeout(() => { errorEl.style.display = 'none'; }, 8000);
  }

  async function loadMaps() {
    try {
      const resp = await fetch('/api/maps');
      const maps = await resp.json();
      renderMaps(maps);
    } catch (err) {
      console.error('Failed to load maps:', err);
    }
  }

  function renderMaps(maps) {
    grid.innerHTML = '';
    if (maps.length === 0) {
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';

    maps.forEach(map => {
      const card = document.createElement('div');
      card.className = 'map-card';
      card.innerHTML = `
        <img class="map-card-thumb" src="/maps/${map.id}/thumb.jpg" alt="${map.title}" loading="lazy">
        <div class="map-card-info">
          <h3>${map.title}</h3>
          <span class="meta">${map.scale ? '1:' + map.scale : ''} &middot; ${map.imageSize[0]}×${map.imageSize[1]}px</span>
        </div>
        <div class="map-card-actions">
          <button class="btn-delete" data-id="${map.id}" title="Supprimer">🗑️ Supprimer</button>
        </div>
      `;

      // Click card → open viewer
      card.addEventListener('click', (e) => {
        if (e.target.closest('.btn-delete')) return;
        window.location.href = `/viewer.html?map=${map.id}`;
      });

      // Delete button
      card.querySelector('.btn-delete').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Supprimer la carte "${map.title}" ?`)) return;
        try {
          await fetch(`/api/maps/${map.id}`, { method: 'DELETE' });
          await loadMaps();
        } catch (err) {
          showError('Échec de la suppression');
        }
      });

      grid.appendChild(card);
    });
  }
});
