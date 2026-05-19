/**
 * Home page — upload maps + display list
 * Terra & Forest design system
 */
document.addEventListener('DOMContentLoaded', () => {
  // ── DOM refs ──
  const errorEl       = document.getElementById('upload-error');
  const grid          = document.getElementById('maps-grid');
  const emptyState    = document.getElementById('empty-state');
  const btnAdd        = document.getElementById('btn-add');

  // Import Modal
  const importModal      = document.getElementById('import-modal');
  const importClose      = document.getElementById('import-modal-close');
  const modalDropZone    = document.getElementById('modal-drop-zone');
  const modalBrowseBtn   = document.getElementById('modal-browse-btn');
  const modalFileInput   = document.getElementById('modal-file-input');
  const modalFilePreview = document.getElementById('modal-file-preview');
  const modalFileName    = document.getElementById('modal-file-name');
  const modalFileSize    = document.getElementById('modal-file-size');
  const modalFileRemove  = document.getElementById('modal-file-remove');
  const modalError       = document.getElementById('modal-error');
  const modalCancelBtn   = document.getElementById('modal-cancel-btn');
  const modalSubmitBtn   = document.getElementById('modal-submit-btn');

  // Progress Modal
  const progressModal    = document.getElementById('progress-modal');
  const progressFilename = document.getElementById('progress-filename');
  const progressPercent  = document.getElementById('progress-percent');
  const progressBar      = document.getElementById('progress-bar');
  const progressSteps    = document.getElementById('progress-steps');
  const progressCancelBtn = document.getElementById('progress-cancel-btn');

  const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 Mo
  let selectedFile = null;
  let uploadAbortController = null;

  // ── Init ──
  loadMaps();

  // ── "Ajouter" button → open import modal ──
  btnAdd.addEventListener('click', () => openImportModal());

  // ── Import Modal Logic ──
  function openImportModal() {
    selectedFile = null;
    modalFileInput.value = '';
    modalFilePreview.classList.add('hidden');
    modalDropZone.classList.remove('hidden');
    modalError.classList.add('hidden');
    modalSubmitBtn.disabled = true;
    importModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  }

  function closeImportModal() {
    importModal.classList.add('hidden');
    document.body.style.overflow = '';
    selectedFile = null;
  }

  importClose.addEventListener('click', closeImportModal);
  modalCancelBtn.addEventListener('click', closeImportModal);
  importModal.addEventListener('click', (e) => {
    if (e.target === importModal) closeImportModal();
  });

  // Modal drag & drop
  modalDropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    modalDropZone.classList.add('border-primary', 'bg-surface-container-low');
    modalDropZone.classList.remove('border-outline-variant');
  });
  modalDropZone.addEventListener('dragleave', () => {
    modalDropZone.classList.remove('border-primary', 'bg-surface-container-low');
    modalDropZone.classList.add('border-outline-variant');
  });
  modalDropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    modalDropZone.classList.remove('border-primary', 'bg-surface-container-low');
    modalDropZone.classList.add('border-outline-variant');
    if (e.dataTransfer.files.length > 0) selectFile(e.dataTransfer.files[0]);
  });

  modalBrowseBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    modalFileInput.click();
  });
  modalDropZone.addEventListener('click', (e) => {
    if (e.target !== modalBrowseBtn && !modalBrowseBtn.contains(e.target)) {
      modalFileInput.click();
    }
  });

  modalFileInput.addEventListener('change', () => {
    if (modalFileInput.files.length > 0) selectFile(modalFileInput.files[0]);
    modalFileInput.value = '';
  });

  modalFileRemove.addEventListener('click', () => {
    selectedFile = null;
    modalFilePreview.classList.add('hidden');
    modalDropZone.classList.remove('hidden');
    modalSubmitBtn.disabled = true;
    modalError.classList.add('hidden');
  });

  modalSubmitBtn.addEventListener('click', () => {
    if (selectedFile) {
      const fileToUpload = selectedFile;
      closeImportModal();
      startUpload(fileToUpload);
    }
  });

  function selectFile(file) {
    modalError.classList.add('hidden');

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      showModalError('Seuls les fichiers PDF sont acceptés.');
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      showModalError('Le fichier dépasse la taille maximale de 50 Mo.');
      return;
    }

    selectedFile = file;
    modalFileName.textContent = file.name;
    modalFileSize.textContent = formatFileSize(file.size);
    modalDropZone.classList.add('hidden');
    modalFilePreview.classList.remove('hidden');
    modalSubmitBtn.disabled = false;
  }

  function showModalError(msg) {
    modalError.textContent = msg;
    modalError.classList.remove('hidden');
  }

  // ── Upload with Progress Modal ──
  function startUpload(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      showError('Seuls les fichiers PDF géo-référencés (export OCAD) sont acceptés.');
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      showError('Le fichier dépasse la taille maximale de 50 Mo.');
      return;
    }

    openProgressModal(file.name);
    uploadFile(file);
  }

  async function uploadFile(file) {
    uploadAbortController = new AbortController();

    setStepState('upload', 'in-progress');
    updateProgress(5);

    const formData = new FormData();
    formData.append('file', file);

    try {
      // Simulate upload progress via XHR for real progress tracking
      const result = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload');

        xhr.upload.addEventListener('progress', (e) => {
          if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 25); // 0-25%
            updateProgress(pct);
          }
        });

        xhr.addEventListener('load', () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(JSON.parse(xhr.responseText));
          } else {
            let detail = 'Erreur lors du traitement';
            try { detail = JSON.parse(xhr.responseText).detail || detail; } catch {}
            reject(new Error(detail));
          }
        });

        xhr.addEventListener('error', () => reject(new Error('Erreur réseau')));
        xhr.addEventListener('abort', () => reject(new Error('Importation annulée')));

        // Connect abort controller
        uploadAbortController.signal.addEventListener('abort', () => xhr.abort());

        xhr.send(formData);
      });

      // Upload done
      setStepState('upload', 'completed');
      updateProgress(30);

      // Structure parsing (simulated timing since server processes synchronously)
      setStepState('structure', 'in-progress');
      await delay(400);
      setStepState('structure', 'completed');
      updateProgress(55);

      // Rasterization
      setStepState('rasterize', 'in-progress');
      await delay(500);
      setStepState('rasterize', 'completed');
      updateProgress(85);

      // Finalization
      setStepState('done', 'in-progress');
      await delay(300);
      setStepState('done', 'completed');
      updateProgress(100);

      // Wait a moment then close
      await delay(600);
      closeProgressModal();
      await loadMaps();

    } catch (err) {
      closeProgressModal();
      if (err.message !== 'Importation annulée') {
        showError(err.message);
      }
    } finally {
      uploadAbortController = null;
    }
  }

  // ── Progress Modal ──
  function openProgressModal(filename) {
    progressFilename.textContent = filename;
    progressPercent.textContent = '0%';
    progressBar.style.width = '0%';

    // Reset all steps
    const steps = progressSteps.querySelectorAll('[data-step]');
    steps.forEach(step => setStepState(step.dataset.step, 'pending'));

    progressModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  }

  function closeProgressModal() {
    progressModal.classList.add('hidden');
    document.body.style.overflow = '';
  }

  progressCancelBtn.addEventListener('click', () => {
    if (uploadAbortController) uploadAbortController.abort();
    closeProgressModal();
  });

  function updateProgress(pct) {
    progressPercent.textContent = pct + '%';
    progressBar.style.width = pct + '%';
  }

  function setStepState(stepName, state) {
    const stepEl = progressSteps.querySelector(`[data-step="${stepName}"]`);
    if (!stepEl) return;
    const icon = stepEl.querySelector('.step-icon');
    const status = stepEl.querySelector('.step-status');

    // Reset icon classes
    icon.className = 'step-icon mt-xs w-6 h-6 rounded-full shrink-0 flex items-center justify-center';

    // Reset parent opacity
    stepEl.classList.remove('opacity-50');

    switch (state) {
      case 'pending':
        stepEl.classList.add('opacity-50');
        icon.classList.add('border-2', 'border-outline-variant');
        icon.innerHTML = '';
        status.textContent = 'En attente';
        status.className = 'step-status text-label-sm font-label-sm text-on-surface-variant';
        break;
      case 'in-progress':
        icon.classList.add('border-2', 'border-primary', 'animate-pulse-dot');
        icon.innerHTML = '<div class="w-2 h-2 bg-primary rounded-full"></div>';
        status.textContent = 'En cours...';
        status.className = 'step-status text-label-sm font-label-sm text-primary';
        break;
      case 'completed':
        icon.classList.add('bg-primary', 'text-on-primary');
        icon.innerHTML = '<span class="material-symbols-outlined text-[16px]" style="font-variation-settings: \'FILL\' 1;">check</span>';
        status.textContent = 'Terminé';
        status.className = 'step-status text-label-sm font-label-sm text-primary';
        break;
    }
  }

  // ── Error Toast ──
  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove('hidden');
    setTimeout(() => errorEl.classList.add('hidden'), 8000);
  }

  // ── Load & Render Maps ──
  async function loadMaps() {
    try {
      const resp = await fetch('/api/maps');
      if (!resp.ok) throw new Error('Erreur chargement');
      const maps = await resp.json();
      renderMaps(maps);
    } catch (err) {
      console.error('Failed to load maps:', err);
    }
  }

  function renderMaps(maps) {
    grid.innerHTML = '';
    if (maps.length === 0) {
      emptyState.classList.remove('hidden');
      return;
    }
    emptyState.classList.add('hidden');

    maps.forEach(map => {
      const card = document.createElement('div');
      card.className = 'bg-surface-container-lowest rounded-lg shadow-sm border border-outline-variant overflow-hidden flex flex-col hover:-translate-y-0.5 hover:shadow-md transition-all duration-200 cursor-pointer group active:scale-[0.98]';

      const title = escapeHtml(map.title);
      const scaleText = map.scale ? '1:' + map.scale : '';
      const sizeText = map.imageSize[0] + '×' + map.imageSize[1] + 'px';

      card.innerHTML = `
        <div class="h-[140px] sm:h-[180px] w-full bg-surface-variant relative overflow-hidden">
          <img class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
               src="/maps/${encodeURIComponent(map.id)}/thumb.jpg"
               alt="${title}" loading="lazy">
          <div class="absolute inset-0 bg-gradient-to-t from-black/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300"></div>
        </div>
        <div class="p-md flex flex-col gap-xs flex-grow justify-between">
          <div>
            <h3 class="font-body-lg font-semibold text-on-surface line-clamp-1">${title}</h3>
            <p class="font-label-md text-on-surface-variant">${scaleText}${scaleText && sizeText ? ' · ' : ''}${sizeText}</p>
          </div>
          <div class="flex justify-end mt-sm">
            <button class="btn-delete font-label-sm text-error hover:bg-error-container px-sm py-sm rounded transition-colors min-h-[36px] min-w-[36px]" data-id="${encodeURIComponent(map.id)}">Supprimer</button>
          </div>
        </div>
      `;

      // Thumbnail fallback — avoids inline onerror (CSP + quoting issues)
      card.querySelector('img').addEventListener('error', function () {
        this.parentElement.innerHTML = '<div class="w-full h-full flex items-center justify-center bg-surface-container-high text-outline"><span class="material-symbols-outlined text-[48px]" style="font-variation-settings: \'FILL\' 0;">map</span></div>';
      });

      card.addEventListener('click', (e) => {
        if (e.target.closest('.btn-delete')) return;
        window.location.href = '/viewer.html?map=' + encodeURIComponent(map.id);
      });

      card.querySelector('.btn-delete').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm('Supprimer la carte "' + map.title + '" ?')) return;
        try {
          const resp = await fetch('/api/maps/' + encodeURIComponent(map.id), { method: 'DELETE' });
          if (!resp.ok) throw new Error();
          await loadMaps();
        } catch {
          showError('Échec de la suppression');
        }
      });

      grid.appendChild(card);
    });
  }

  // ── Utilities ──
  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' o';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' Ko';
    return (bytes / (1024 * 1024)).toFixed(1) + ' Mo';
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function delay(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  // ── Keyboard: Escape closes modals ──
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!progressModal.classList.contains('hidden')) {
        if (uploadAbortController) uploadAbortController.abort();
        closeProgressModal();
      } else if (!importModal.classList.contains('hidden')) {
        closeImportModal();
      }
    }
  });
});
