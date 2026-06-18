# CLAUDE.md — OCAD Map Viewer

Application web de visualisation de cartes OCAD géo-référencées, superposées sur Google Maps avec synchronisation Street View et planification de parcours d'orientation.

## Commandes essentielles

### Développement local
```bash
# Installation Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lancer le serveur (hot-reload)
uvicorn server:app --reload --port 8080
# → http://localhost:8080
```

### CSS (Tailwind)
```bash
npm install
npm run build:css    # compile static/css/tailwind.css (une fois)
npm run watch:css    # mode watch (développement)
```

**Ne jamais éditer** `static/css/tailwind.css` directement — c'est un fichier généré.
Éditer uniquement `static/css/tailwind-input.css`, puis regénérer.

### Docker
```bash
docker build -t ocad-map-viewer .
docker run -p 8080:8080 --env-file .env ocad-map-viewer
```

### Variables d'environnement
Copier `.env.example` → `.env` et renseigner :

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `GOOGLE_MAPS_API_KEY` | Oui (prod) | Jamais écrite dans le HTML — servie via `/api/config` |
| `GOOGLE_MAPS_MAP_ID` | Non | Requis pour `AdvancedMarkerElement` |
| `GCS_BUCKET` | Non | Bucket GCS. Absent = fallback filesystem local (`maps/`) |
| `LOCAL_STORAGE_DIR` | Non | Dossier local (défaut : `./maps/`) |

## Architecture

```
server.py          # API FastAPI + StaticFiles
processing.py      # Pipeline PDF → PNG (extraction GPTS, rasterisation, cache traversabilité)
pdf_export.py      # Export parcours → PDF haute résolution (symboles IOF)
traversability.py  # Classification pixels ISSprOM → raster de coût + arêtes de barrières
pathfinding.py     # Recherche d'itinéraires diversifiés (Dijkstra via-sommet + Theta*)
static/
  index.html       # Page d'accueil (grille des cartes + upload)
  viewer.html      # Visionneuse (overlay + Street View + parcours)
  js/
    home.js        # Upload XHR, modal drag & drop, liste cartes
    viewer.js      # OverlayView perspective, rotation Street View, calibration
    routes.js      # CRUD parcours IOF, rendu SVG, sync rotation, export PDF, analyse de tronçon
  css/
    tailwind-input.css   # SOURCE Tailwind (à éditer)
    tailwind.css         # GÉNÉRÉ — ne pas toucher
maps/              # Stockage local de développement uniquement
  {map_id}/
    config.json
    map.png        # 300 DPI
    map-mobile.png # ~8 MP (iOS Safari)
    thumb.jpg      # 400 px
    traversability_v8.npz  # Grille de coût + arêtes de barrières (cache, auto-généré)
    routes/
      {uuid}.json
```

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Traitement PDF (upload) | PyMuPDF (`fitz`), Pillow |
| Export PDF (routes) | ReportLab 4.0+, géométrie IOF |
| Analyse de tronçon | NumPy, SciPy (`csgraph.dijkstra`, `connected_components`) |
| Stockage | GCS (`google-cloud-storage`) ou `_LocalBucket` (shim filesystem) |
| Frontend | Vanilla JS ES6+, aucun framework, aucun bundler |
| Maps | Google Maps JS API v3 weekly, `OverlayView`, `StreetViewPanorama`, `AdvancedMarkerElement` |
| CSS | Tailwind CSS v3 compilé statiquement |
| Déploiement | Docker multi-stage (node:22-slim → python:3.13-slim) → Cloud Run europe-west1 |

## API REST

| Méthode | Chemin | Description |
|---------|--------|-------------|
| GET | `/api/config` | Retourne `googleMapsApiKey` et `googleMapsMapId` |
| GET | `/api/maps` | Liste toutes les cartes |
| GET | `/api/maps/{map_id}` | `config.json` d'une carte |
| POST | `/api/upload` | Upload + traitement PDF (form: `file`, `title`) |
| DELETE | `/api/maps/{map_id}` | Supprime une carte et tous ses fichiers |
| GET | `/maps/{map_id}/{filename}` | Stream PNG/JPEG (cache 1 an immutable) |
| GET | `/api/maps/{map_id}/routes` | Liste les parcours |
| POST | `/api/maps/{map_id}/routes` | Crée un parcours |
| GET | `/api/maps/{map_id}/routes/{route_id}` | Détail d'un parcours |
| PUT | `/api/maps/{map_id}/routes/{route_id}` | Remplace un parcours |
| DELETE | `/api/maps/{map_id}/routes/{route_id}` | Supprime un parcours |
| POST | `/api/maps/{map_id}/routes/{route_id}/export-pdf` | Lance la génération PDF du parcours |
| GET | `/api/maps/{map_id}/routes/{route_id}/export-pdf/{job_id}/stream` | SSE pour progression PDF |
| POST | `/api/maps/{map_id}/route-choices` | Lance une analyse de tronçon (body : `from_point`, `to_point`, `count` 1–3) → `{jobId}` |
| GET | `/api/maps/{map_id}/route-choices/{job_id}/stream` | SSE progression + résultats de l'analyse de tronçon |
| POST | `/api/maps/{map_id}/embargo` | Définit la zone d'embargo (interdite) |
| DELETE | `/api/maps/{map_id}/embargo` | Supprime la zone d'embargo |
| GET | `/api/maps/{map_id}/traversability` | (Debug) Grille de coût en PNG niveaux de gris |

## Schémas JSON

### `config.json`
```json
{
  "id": "slug-de-la-carte",
  "title": "Titre lisible",
  "scale": 3000,
  "filename": "source.pdf",
  "imageSize": [largeur, hauteur],
  "imageSizeMobile": [largeur, hauteur],
  "corners": {
    "nw": {"lat": 0.0, "lng": 0.0},
    "ne": {"lat": 0.0, "lng": 0.0},
    "se": {"lat": 0.0, "lng": 0.0},
    "sw": {"lat": 0.0, "lng": 0.0}
  },
  "createdAt": "ISO8601"
}
```

### `routes/{route_id}.json`
```json
{
  "id": "uuid-hex-32-chars",
  "mapId": "slug-de-la-carte",
  "name": "Nom du parcours",
  "color": "#cf00cf",
  "points": [{"lat": 0.0, "lng": 0.0}],
  "totalDistanceMeters": 1234.5,
  "createdAt": "ISO8601",
  "updatedAt": "ISO8601"
}
```

## Conventions Python (backend)

- Valider les IDs avec `_validate_map_id()` / `_validate_route_id()` — jamais accéder aux ressources sans validation préalable
- `_MAP_ID_RE = r"^[a-z0-9][a-z0-9-]*$"` et `_ROUTE_ID_RE = r"^[a-f0-9]{32}$"`
- Lever `HTTPException(404)` avec les constantes `_ERR_MAP_NOT_FOUND` / `_ERR_ROUTE_NOT_FOUND`
- Toute persistance passe par `_bucket().blob(...)` — jamais d'écriture disque directe hors `_LocalBlob`
- Ne jamais appeler `storage.Client()` directement — toujours passer par le helper `_bucket()`
- Utiliser `pydantic.BaseModel` + `Field` pour tous les body de requêtes POST/PUT
- Ne pas exposer les détails d'exception GCS au client (retourner 500 générique)
- Les headers de sécurité (CSP, X-Frame-Options…) sont gérés par le middleware `add_security_headers` — ne pas les dupliquer
- Limites métier : upload ≤ 50 MB, ≤ 50 routes par carte, ≤ 2000 points par route
- **PDF export** : utiliser reportlab pour 300 DPI, A3 format sans marge, géométrie GPS → pixels via `gps_to_pixels()` (interpolation bilinéaire)

## Conventions JavaScript (frontend)

- **Vanilla JS ES6+ uniquement** — pas de bundler, pas de framework, pas de npm runtime
- Variables globales partagées (`map`, `MAP_CONFIG`, `ROUTES`…) déclarées en haut de chaque fichier
- Toute interaction Google Maps via `OverlayView` — **jamais** `google.maps.GroundOverlay`
- La transformation perspective (`computePerspectiveCSS` dans `viewer.js`) est une homographie 8 points par élimination gaussienne — ne pas la réécrire
- Clé Google Maps chargée dynamiquement via `GET /api/config` — **jamais** écrite en dur dans le HTML
- Utiliser `google.maps.marker.AdvancedMarkerElement` (pas l'ancien `Marker`)
- Pattern mobile/desktop : choisir `map-mobile.png` si `window.innerWidth < 768` ou `'ontouchstart' in window`
- Couleur IOF magenta : `IOF_PURPLE = '#cf00cf'` — constante à conserver
- `initRoutes()` est appelé depuis `viewer.js` après init Google Maps — tester `typeof initRoutes === 'function'`
- `isRouteDrawing()` doit bloquer l'ouverture de Street View en mode édition de parcours
- **PDF export** : SSE listener dans `onExportPdf()`, modal `#pdf-export-modal` avec barre de progression, base64 decode + browser download automatique

## Conventions CSS / Tailwind

- Éditer uniquement `static/css/tailwind-input.css`
- Regénérer avec `npm run build:css` après toute modification
- Design system **Terra & Forest** défini dans `DESIGN.md` :
  - Couleur primaire : `#243624` (Forest Green)
  - Couleur secondaire : `#775843` (Terra Brown)
  - Fond viewer sombre : `#1a1a2e`
  - Police : **Inter**
- Classes Tailwind en priorité ; CSS custom uniquement pour les animations ou cas non couverts

## Flux de traitement PDF (upload de cartes)

1. `POST /api/upload` reçoit le fichier + `title` (multipart form)
2. Validation : extension `.pdf`, taille ≤ 50 MB
3. `processing.process_pdf()` orchestre :
   - `extract_gpts()` → regex sur la page PDF → 8 valeurs GPTS → coins `{nw, ne, se, sw}`
   - `rasterize_pdf()` → `map.png` à 300 DPI (fond blanc, sans transparence)
   - `create_mobile_image()` → `map-mobile.png` (plafonné à ~8 MP pour iOS Safari)
   - `create_thumbnail()` → `thumb.jpg` (400 px, JPEG qualité 80)
   - Écriture de `config.json`
4. Upload de tous les fichiers dans GCS sous `{slug}/`
5. Retourne le `config.json` avec HTTP 201

## Flux d'export PDF (parcours)

1. `POST /api/maps/{map_id}/routes/{route_id}/export-pdf` lance la génération
2. `start_export_pdf()` crée un job ID et enregistre la tâche dans `BackgroundTasks`
3. `_generate_pdf_sync_wrapper()` lance un thread daemon non-bloquant
4. `_generate_pdf_async()` orchestre :
   - Récupère `config.json` et `map.png` depuis GCS/`_LocalBucket`
   - Convertit GPS → pixels (via `gps_to_pixels()` avec interpolation bilinéaire sur les 4 coins)
   - Crée PDF 300 DPI, A3 format (297 × 420 mm)
   - Dessine la carte, polyline, symboles IOF, labels
   - Envoie la progression via SSE (10%, 50%, 100%)
   - Encode PDF en base64 et transmet au client
5. Frontend décode, télécharge automatiquement
6. Timeout 60s avec nettoyage des ressources

## Symboles IOF (conformité normes)

| Symbole | Description | Implémentation |
|---------|-------------|-----------------|
| START | Triangle équilatéral | Creux (contour seul), pointe vers le contrôle 1 |
| CONTROL n | Cercle vide | Rempli blanc (transparent), bordure couleur, label numéroté |
| FINISH | Double cercle | Deux anneaux concentriques, rempli blanc |

- Couleur magenta IOF : `#cf00cf`
- Symboles rendu via reportlab drawing context (`c.beginPath()`, `c.circle()`, etc.)
- Rayon START: 5 pt, FINISH: 6 pt externe / 3.3 pt interne, CONTROLS: 5 pt

## Fichiers clés - PDF export

### Backend (`pdf_export.py`)
- `export_route_to_pdf(map_bytes, route, config)` → `bytes` PDF encodés
- `_draw_route_on_pdf(c, route, config, map_size)` → Dessine la polyline + symboles IOF + labels
- `gps_to_pixels(lat, lng, corners, map_size)` → Convertit GPS → coordonnées image (interpolation bilinéaire)
- `hex_to_rgb(hex_color)` → Parse couleur hex → tuple (r,g,b) 0-1

### Backend (`server.py`)
- `start_export_pdf()` → Endpoint POST, lance le job, retourne job_id
- `_generate_pdf_sync_wrapper()` → Thread wrapper non-bloquant
- `_generate_pdf_async()` → Orchestration complète, SSE streaming, cleanup
- `_LocalBlob.download_as_bytes()` → Fallback filesystem pour GCS

### Frontend (`static/js/routes.js`)
- `onExportPdf()` → POST + SSE listener, gestion progress bar
- `showPdfProgressModal()` / `hidePdfProgressModal()` → UI modal
- Base64 decode + `fetch(...).blob()` + auto-download

### Frontend (`static/viewer.html`)
- Modal `#pdf-export-modal` avec spinner + barre de progression
- Bouton `#btn-route-export-pdf` dans le panneau Parcours

## Analyse de tronçon (route-choice analysis)

Compare jusqu'à **3 itinéraires** entre deux balises consécutives en respectant
les objets **infranchissables** ISSprOM (bâtiments, murs, clôtures, vert dense,
zones privées olive). Aucun segment ne traverse un obstacle ; une balise
réellement enclose est signalée comme **inaccessible** (pas d'itinéraire fabriqué).

### Pipeline (backend)
1. `POST /api/maps/{map_id}/route-choices` (`from_point`, `to_point`, `count`) → `{jobId}`, puis SSE.
2. `traversability.py` classe les pixels de `map.png` selon la palette ISSprOM
   (distance Chebyshev L∞), produit une **grille de coût** (downsample facteur 8,
   ~1.5 m/cellule) + des **arêtes de barrières** (modèle edge-cut : interdit le
   passage entre deux cellules séparées par un mur, mais autorise le longement et
   les ouvertures). Résultat mis en cache : `traversability_v8.npz`.
3. `pathfinding.find_diverse_routes()` :
   - **Dijkstra** (SciPy `csgraph`) depuis le départ ET l'arrivée sur le graphe des
     cellules franchissables → itinéraire optimal (route A).
   - **Via-sommet** : meilleur sommet de détour de part et d'autre de la ligne
     directe (≤ `_VIA_MAX_STRETCH` = 1.50× l'optimal) → routes diverses.
   - **Penalty top-up** : pénalise itérativement les corridors déjà utilisés et
     relance Dijkstra (cap `_TOPUP_MAX_STRETCH` = 1.60×).
   - `connected_components` → si départ/arrivée sont dans des composantes
     différentes, la balise est **enclose** → message d'erreur clair.
4. `path_to_gps()` applique un string-pull tenant compte des obstacles : garantie
   qu'aucun segment GPS ne croise un bâtiment/barrière.

### Versionnage cache traversabilité
- `TRAVERSABILITY_VERSION` dans `traversability.py` (actuel `v8`).
- **Incrémenter** dès qu'un seuil/couleur/algorithme change → `_CACHE_FILENAME`
  devient `traversability_{version}.npz` et invalide l'ancien cache.
- Le cache est (re)généré à l'upload PDF (`processing._build_traversability`) ou à
  la demande lors de la première analyse.

### Objet `choice` (renvoyé par SSE)
```json
{
  "label": "A",
  "color": "#1565C0",
  "points": [{"lat": 0.0, "lng": 0.0}],
  "distanceMeters": 235.0,
  "directDistanceMeters": 209.0,
  "detourPercent": 12.0
}
```
Couleurs : A `#1565C0` (bleu), B `#C62828` (rouge), C `#2E7D32` (vert).

### Frontend (`static/js/routes.js`)
- `onAnalyzeLeg()` / `_runChoiceAnalysis()` → POST + EventSource SSE, progress bar.
- `_displayChoices()` mémorise les données dans `_choiceData` puis appelle
  `_renderChoiceGraphics()`.
- **Sync rotation Street View** : les polylignes + labels de choix sont
  pré-pivotés via `toDisplay()` (comme le parcours) et redessinés par
  `_redrawChoices()` sur `pov_changed` / `position_changed` / `zoom_changed`.
- Le ruban de tronçons (`_miniLegSvg`) affiche les numéros de balises DANS les
  cercles (triangle départ, double-cercle arrivée).

## Synchronisation rotation Street View

Modèle de rotation **unique et manuel**, centré sur la position du panorama :
- **Overlay OCAD** : transformation perspective CSS pivotée de `-currentHeading`
  autour de la position SV (`viewer.js` `MapImageOverlay.draw`).
- **Masque d'embargo** : canvas pivoté de la même façon.
- **Parcours, marqueurs, choix de tronçon** : points GPS pré-pivotés via
  `toDisplay()` (`rotateLatLngBy(p, -currentHeading)`) puis redessinés sur
  `pov_changed` / `position_changed`.
- ⚠️ **Ne pas** utiliser `map.setHeading()` : cela introduit un second modèle de
  rotation natif qui entre en conflit avec la rotation manuelle (l'overlay OCAD et
  l'embargo ne pivotent pas avec, mais les polylignes natives oui → désync).
- La carte de base reste **North-up** ; tous les calques sont pivotés pour
  rester collés à l'overlay OCAD.

## Ce qu'il ne faut PAS faire

- Ne pas modifier `static/css/tailwind.css` directement
- Ne pas écrire dans `maps/` en production (fixtures locales uniquement)
- Ne pas ajouter de dépendances npm au-delà de Tailwind et ses plugins officiels
- Ne pas exposer `stitch-mcp-sa-key.json` ni d'autres credentials dans le code
- Ne pas créer de base de données — GCS/`_LocalBucket` est la seule source de vérité
- Ne pas utiliser `google.maps.GroundOverlay` ni l'ancien `google.maps.Marker`
- Ne pas appeler `storage.Client()` directement hors du helper `_bucket()`
- Ne pas écrire la clé Google Maps en dur dans le HTML
- Ne pas modifier les symboles IOF sans respecter les normes orienteering (triangle START, cercles vides CONTROL, double cercle FINISH)
- Ne pas régénérer les PDFs côté client — toujours passer par le serveur (contexte GCS/permissions)
- Ne pas utiliser `map.setHeading()` pour la rotation Street View — utiliser le modèle manuel (`toDisplay()` + redraw sur `pov_changed`) pour TOUS les calques
- Ne pas dessiner les choix de tronçon avec des lat/lng bruts — toujours passer par `toDisplay()` et les redessiner via `_redrawChoices()` sur rotation
- Ne pas oublier d'incrémenter `TRAVERSABILITY_VERSION` quand un seuil/couleur/algorithme de traversabilité change (sinon l'ancien cache `.npz` reste servi)
- Ne pas fabriquer un itinéraire vers une balise enclose — signaler l'inaccessibilité

## Déploiement CI/CD

GitHub Actions → Docker build → Artifact Registry → Cloud Run (`europe-west1`)

Déclenché sur chaque push sur `main`. Auth sans clé via Workload Identity Federation.
