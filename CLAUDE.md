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
processing.py      # Pipeline PDF → PNG (extraction GPTS, rasterisation)
static/
  index.html       # Page d'accueil (grille des cartes + upload)
  viewer.html      # Visionneuse (overlay + Street View + parcours)
  js/
    home.js        # Upload XHR, modal drag & drop, liste cartes
    viewer.js      # OverlayView perspective, rotation Street View, calibration
    routes.js      # CRUD parcours IOF, rendu SVG, sync rotation
  css/
    tailwind-input.css   # SOURCE Tailwind (à éditer)
    tailwind.css         # GÉNÉRÉ — ne pas toucher
maps/              # Stockage local de développement uniquement
  {map_id}/
    config.json
    map.png        # 300 DPI
    map-mobile.png # ~8 MP (iOS Safari)
    thumb.jpg      # 400 px
    routes/
      {uuid}.json
```

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Traitement PDF | PyMuPDF (`fitz`), Pillow |
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

## Conventions CSS / Tailwind

- Éditer uniquement `static/css/tailwind-input.css`
- Regénérer avec `npm run build:css` après toute modification
- Design system **Terra & Forest** défini dans `DESIGN.md` :
  - Couleur primaire : `#243624` (Forest Green)
  - Couleur secondaire : `#775843` (Terra Brown)
  - Fond viewer sombre : `#1a1a2e`
  - Police : **Inter**
- Classes Tailwind en priorité ; CSS custom uniquement pour les animations ou cas non couverts

## Flux de traitement PDF

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

## Ce qu'il ne faut PAS faire

- Ne pas modifier `static/css/tailwind.css` directement
- Ne pas écrire dans `maps/` en production (fixtures locales uniquement)
- Ne pas ajouter de dépendances npm au-delà de Tailwind et ses plugins officiels
- Ne pas exposer `stitch-mcp-sa-key.json` ni d'autres credentials dans le code
- Ne pas créer de base de données — GCS/`_LocalBucket` est la seule source de vérité
- Ne pas utiliser `google.maps.GroundOverlay` ni l'ancien `google.maps.Marker`
- Ne pas appeler `storage.Client()` directement hors du helper `_bucket()`
- Ne pas écrire la clé Google Maps en dur dans le HTML

## Déploiement CI/CD

GitHub Actions → Docker build → Artifact Registry → Cloud Run (`europe-west1`)

Déclenché sur chaque push sur `main`. Auth sans clé via Workload Identity Federation.
