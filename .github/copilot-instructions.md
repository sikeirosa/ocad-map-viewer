# GitHub Copilot Instructions — OCAD Map Viewer

## Vue d'ensemble du projet

Application web de visualisation de cartes OCAD géo-référencées, superposées sur Google Maps avec synchronisation Street View et planification de parcours (courses). Les cartes sont exportées depuis OCAD en PDF, traitées côté serveur, puis stockées dans Google Cloud Storage (ou sur le disque local en développement).

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Traitement PDF | PyMuPDF (fitz), Pillow |
| Stockage | Google Cloud Storage (`GCS_BUCKET`) ou filesystem local (`LOCAL_STORAGE_DIR`) |
| Frontend | HTML statique, Vanilla JS (ES6+), Google Maps JS API v3 (weekly) |
| CSS | Tailwind CSS v3 (build via `npm run build:css`) |
| Déploiement | Docker multi-stage (node:22-slim → python:3.13-slim) → Cloud Run |

## Architecture

```
server.py          # API FastAPI + serveur de fichiers statiques
processing.py      # Pipeline PDF → PNG (extraction GPTS, rasterisation, cache traversabilité)
pdf_export.py      # Export parcours → PDF haute résolution (symboles IOF)
traversability.py  # Classification pixels ISSprOM → raster de coût + arêtes de barrières
pathfinding.py     # Recherche d'itinéraires diversifiés (Dijkstra via-sommet + Theta*)
static/
  index.html       # Page d'accueil (liste des cartes)
  viewer.html      # Visionneuse (Google Maps OverlayView)
  js/
    home.js        # Chargement liste, modal d'import avec drag & drop, upload XHR
    viewer.js      # Overlay perspective, Street View sync, calibration
    routes.js      # Planification de parcours (CRUD routes, symboles IOF, analyse de tronçon)
  css/
    tailwind-input.css  # Source Tailwind (à éditer)
    tailwind.css         # Généré — NE PAS éditer directement
maps/{map_id}/           # Fixtures locales de test uniquement
  config.json
  traversability_v9.npz  # Grille de coût + arêtes de barrières (cache)
```

### Schema `config.json`
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

### Schema route (`{map_id}/routes/{route_id}.json`)
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

## Routes API

| Méthode | Chemin | Description |
|---------|--------|-------------|
| GET | `/api/config` | Renvoie `googleMapsApiKey` et `googleMapsMapId` |
| GET | `/api/maps` | Liste toutes les cartes |
| GET | `/api/maps/{map_id}` | Récupère le config.json |
| POST | `/api/upload` | Upload + traitement PDF |
| DELETE | `/api/maps/{map_id}` | Supprime une carte et tous ses fichiers |
| GET | `/maps/{map_id}/{filename}` | Stream fichier (cache 1 an, immutable) |
| GET | `/api/maps/{map_id}/routes` | Liste les parcours d'une carte |
| POST | `/api/maps/{map_id}/routes` | Crée un parcours |
| GET | `/api/maps/{map_id}/routes/{route_id}` | Récupère un parcours |
| PUT | `/api/maps/{map_id}/routes/{route_id}` | Remplace un parcours |
| DELETE | `/api/maps/{map_id}/routes/{route_id}` | Supprime un parcours |
| POST | `/api/maps/{map_id}/routes/{route_id}/export-pdf` | Lance la génération PDF du parcours (→ `{jobId}`) |
| GET | `/api/maps/{map_id}/routes/{route_id}/export-pdf/{job_id}/stream` | SSE progression PDF |
| POST | `/api/maps/{map_id}/route-choices` | Lance une analyse de tronçon (`from_point`, `to_point`, `count` 1–3) → `{jobId}` |
| GET | `/api/maps/{map_id}/route-choices/{job_id}/stream` | SSE progression + résultats de l'analyse de tronçon |
| POST/DELETE | `/api/maps/{map_id}/embargo` | Définit / supprime la zone d'embargo |
| GET | `/api/maps/{map_id}/traversability` | (Debug) Grille de coût en PNG niveaux de gris |

### Limites métier (à respecter)
- Upload max : **50 MB** (`MAX_UPLOAD_BYTES`)
- Routes par carte : **50** (`_MAX_ROUTES_PER_MAP`)
- Points par route : **2000** (`_MAX_POINTS_PER_ROUTE`)
- `map_id` : regex `^[a-z0-9][a-z0-9-]*$` (`_MAP_ID_RE`)
- `route_id` : UUID hex 32 chars `^[a-f0-9]{32}$` (`_ROUTE_ID_RE`)
- Couleur route : hex `#RRGGBB` ou `#RRGGBBAA` (validé par `RoutePayload`)

## Variables d'environnement

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `GCS_BUCKET` | Non | Nom du bucket GCS. Si absent, bascule sur le backend local. |
| `LOCAL_STORAGE_DIR` | Non | Dossier local (défaut : `./maps/`). Utilisé uniquement sans GCS. |
| `GOOGLE_MAPS_API_KEY` | Oui (prod) | Clé API Google Maps — exposée via `/api/config`, jamais dans le HTML. |
| `GOOGLE_MAPS_MAP_ID` | Non | Map ID pour les Advanced Markers. |

## Conventions de code

### Python (backend)
- Valider les IDs avec `_validate_map_id()` / `_validate_route_id()` (wrappent les regexes).
- Lever `HTTPException(404)` avec les constantes `_ERR_MAP_NOT_FOUND` / `_ERR_ROUTE_NOT_FOUND`.
- Toute nouvelle donnée persistée passe par `_bucket().blob(...)` — jamais d'écriture directe sur le disque en dehors de `_LocalBlob`.
- Utiliser `pydantic.BaseModel` avec `Field` pour les body de requêtes POST/PUT.
- Ne pas exposer les détails d'exception GCS au client (retourner 500 générique).
- Les headers de sécurité (CSP, X-Frame-Options…) sont injectés par le middleware `add_security_headers` — ne pas les dupliquer dans les réponses individuelles.

### JavaScript (frontend)
- Vanilla JS ES6+ uniquement — pas de bundler, pas de framework.
- Les variables globales partagées entre fichiers (`map`, `MAP_CONFIG`, `ROUTES`…) sont déclarées en haut de chaque fichier.
- Toute interaction Google Maps passe par `OverlayView` — ne jamais utiliser `google.maps.GroundOverlay`.
- La transformation perspective utilise l'élimination gaussienne 8 points (`computePerspectiveCSS`) dans `viewer.js` — ne pas la réécrire.
- Respecter le pattern mobile/desktop : `imageSizeMobile` si `window.innerWidth < 768` ou `'ontouchstart' in window`.
- La clé Google Maps est chargée dynamiquement via `GET /api/config` — ne jamais l'écrire en dur dans le HTML.
- Utiliser `google.maps.marker.AdvancedMarkerElement` (pas l'ancien `Marker`). Nécessite `mapId` et la bibliothèque `marker`.
- Les symboles IOF (départ triangle, contrôle cercle, arrivée double cercle) sont dessinés sur canvas dans `routes.js` — conserver `IOF_PURPLE = '#cf00cf'`.

### CSS / Tailwind
- Éditer uniquement `static/css/tailwind-input.css`, puis regénérer avec `npm run build:css`.
- Le design system suit la palette **Terra & Forest** définie dans `DESIGN.md`.
- Classes utilitaires Tailwind en priorité ; CSS custom uniquement pour les animations ou les cas non couverts.

## Design system (DESIGN.md)
- Couleur primaire : `#243624` (Forest Green)
- Couleur secondaire : `#775843` (Terra Brown)
- Fond viewer sombre : `#1a1a2e`
- Police : **Inter** (display/headline), système pour le corps.
- Toujours référencer `DESIGN.md` avant d'ajouter une couleur ou une typographie.

## Flux de traitement PDF
1. `POST /api/upload` reçoit le fichier + `title` (form data).
2. Validation : extension `.pdf`, taille ≤ 50 MB.
3. `processing.process_pdf()` :
   - `extract_gpts()` → 8 valeurs GPTS → coins `{nw, ne, se, sw}`.
   - `rasterize_pdf()` → `map.png` à 300 DPI (fond blanc, sans transparence).
   - `create_mobile_image()` → `map-mobile.png` (plafonné à ~8 MP pour iOS Safari).
   - `create_thumbnail()` → `thumb.jpg` (400 px de large, JPEG qualité 80).
   - Écriture du `config.json`.
4. Upload de tous les fichiers dans GCS sous `{slug}/`.
5. Retourne le `config.json` avec HTTP 201.

## Module routes (`routes.js`)
- `initRoutes()` est appelé depuis `viewer.js` après l'init Google Maps — vérifier son existence avec `typeof initRoutes === 'function'`.
- `isRouteDrawing()` doit être testé dans le gestionnaire de clic de la carte pour bloquer l'ouverture de Street View en mode édition.
- Les parcours sont stockés individuellement dans GCS : `{map_id}/routes/{route_id}.json`.
- Distance calculée côté serveur (haversine) et renvoyée dans `totalDistanceMeters`.
- Les symboles IOF s'adaptent au zoom (référence `SYMBOL_REF_ZOOM = 17`, max `MAX_SYMBOL_SCALE = 8`).

## Analyse de tronçon (route-choice)
- Compare jusqu'à 3 itinéraires entre deux balises en respectant les objets **infranchissables** ISSprOM (bâtiments, murs, clôtures, vert dense, zones privées olive).
- Backend : `traversability.py` (palette ISSprOM, grille de coût + arêtes de barrières, cache `traversability_v9.npz`) → `pathfinding.find_diverse_routes()` (Dijkstra SciPy + via-sommet + penalty top-up).
- Une balise réellement enclose (composante déconnectée via `connected_components`) est signalée **inaccessible** — ne jamais fabriquer un itinéraire qui traverse l'obstacle.
- Incrémenter `TRAVERSABILITY_VERSION` (`traversability.py`) à tout changement de seuil/couleur/algorithme → invalide l'ancien cache `.npz`.
- Frontend : `_displayChoices()` stocke `_choiceData` puis `_renderChoiceGraphics()` dessine via `toDisplay()`. Les choix sont redessinés par `_redrawChoices()` sur `pov_changed` / `position_changed` / `zoom_changed`.

## Synchronisation rotation Street View
- Modèle de rotation **unique et manuel**, centré sur la position du panorama : overlay OCAD (CSS perspective), masque embargo (canvas), et parcours + marqueurs + choix de tronçon (points GPS pré-pivotés via `toDisplay()`).
- Tous les calques sont redessinés sur `pov_changed` / `position_changed` pour rester collés à l'overlay OCAD. La carte de base reste **North-up**.
- ⚠️ **Ne jamais** utiliser `map.setHeading()` : modèle natif concurrent qui désynchronise l'overlay OCAD/embargo des polylignes natives.

## Ce qu'il NE faut PAS faire
- Ne pas écrire dans le dossier `maps/` en production (uniquement pour les fixtures locales de test).
- Ne pas modifier `static/css/tailwind.css` directement.
- Ne pas ajouter de dépendances npm au-delà de Tailwind et ses plugins officiels sans discussion préalable.
- Ne pas exposer le contenu de `stitch-mcp-sa-key.json` ni d'autres credentials dans le code source.
- Ne pas créer de base de données — GCS (ou `_LocalBucket`) est la seule source de vérité.
- Ne pas utiliser `google.maps.GroundOverlay` ni l'ancien `google.maps.Marker`.
- Ne pas appeler directement `storage.Client()` en dehors de `_bucket()` — passer toujours par ce helper.
- Ne pas utiliser `map.setHeading()` pour la rotation Street View — utiliser le modèle manuel (`toDisplay()` + redraw sur `pov_changed`) pour TOUS les calques.
- Ne pas dessiner les choix de tronçon avec des lat/lng bruts — toujours via `toDisplay()` + `_redrawChoices()`.
- Ne pas oublier d'incrémenter `TRAVERSABILITY_VERSION` quand la traversabilité change.
- Ne pas fabriquer un itinéraire vers une balise enclose — signaler l'inaccessibilité.
