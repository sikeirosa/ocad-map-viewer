# GitHub Copilot Instructions — OCAD Map Viewer

## Vue d'ensemble du projet

Application web de visualisation de cartes OCAD géo-référencées, superposées sur Google Maps avec synchronisation Street View. Les cartes sont exportées depuis OCAD en PDF, traitées côté serveur, puis stockées dans Google Cloud Storage.

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python 3, FastAPI, Uvicorn |
| Traitement PDF | PyMuPDF (fitz), Pillow |
| Stockage | Google Cloud Storage (variable `GCS_BUCKET`) |
| Frontend | HTML statique, Vanilla JS (ES6+), Google Maps JS API |
| CSS | Tailwind CSS v3 (build via `npm run build:css`) |
| Déploiement | Docker → Cloud Run |

## Architecture

```
server.py          # API FastAPI + serveur de fichiers statiques
processing.py      # Pipeline PDF → PNG (extraction GPTS, rasterisation)
static/
  index.html       # Page d'accueil (liste des cartes)
  viewer.html      # Visionneuse (Google Maps OverlayView)
  js/
    home.js        # Chargement liste, modal d'import
    viewer.js      # Overlay perspective, Street View sync
  css/
    tailwind-input.css  # Source Tailwind (à éditer)
    tailwind.css         # Généré — NE PAS éditer directement
maps/{map_id}/
  config.json      # Métadonnées + coins géo (schema ci-dessous)
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

## Routes API

| Méthode | Chemin | Description |
|---------|--------|-------------|
| GET | `/api/maps` | Liste toutes les cartes |
| GET | `/api/maps/{map_id}` | Récupère le config.json |
| POST | `/api/upload` | Upload + traitement PDF |
| DELETE | `/api/maps/{map_id}` | Supprime une carte |
| GET | `/maps/{map_id}/{filename}` | Stream fichier (cache 1 an) |

### Limites métier (à respecter)
- Upload max : **50 MB** (`MAX_UPLOAD_BYTES`)
- Routes par carte : **50** (`_MAX_ROUTES_PER_MAP`)
- Points par route : **2000** (`_MAX_POINTS_PER_ROUTE`)
- `map_id` : regex `^[a-z0-9][a-z0-9-]*$`
- `route_id` : UUID hex 32 chars `^[a-f0-9]{32}$`

## Conventions de code

### Python (backend)
- Valider les IDs avec les regexes `_MAP_ID_RE` / `_ROUTE_ID_RE` déjà définies dans `server.py`.
- Lever `HTTPException(404)` avec les constantes `_ERR_MAP_NOT_FOUND` / `_ERR_ROUTE_NOT_FOUND`.
- Toute nouvelle donnée persistée va dans GCS sous `{map_id}/` — jamais sur le disque local.
- Utiliser `pydantic.BaseModel` pour les body de requêtes POST/PUT.
- Ne pas exposer les détails d'exception GCS au client (retourner 500 générique).

### JavaScript (frontend)
- Vanilla JS ES6+ uniquement — pas de bundler, pas de framework.
- Les modules n'existent pas ; les variables globales partagées entre fichiers sont déclarées en haut de chaque fichier.
- Toute interaction Google Maps passe par `OverlayView` — ne pas utiliser `google.maps.GroundOverlay`.
- La transformation perspective utilise l'élimination gaussienne 8 points déjà implémentée dans `viewer.js`.
- Respecter le pattern mobile/desktop : charger `imageSizeMobile` sur écrans < 768 px.

### CSS / Tailwind
- Éditer uniquement `static/css/tailwind-input.css`, puis regénérer avec `npm run build:css`.
- Le design system suit la palette **Terra & Forest** définie dans `DESIGN.md` (variable CSS préfixées `--color-*`).
- Classes utilitaires Tailwind en priorité ; CSS custom uniquement pour les animations ou les cas non couverts.

## Design system (DESIGN.md)
- Couleur primaire : `#243624` (Forest Green)
- Couleur secondaire : `#775843` (Terra Brown)
- Fond viewer sombre : `#1a1a2e`
- Police : **Inter** (display/headline), système pour le corps.
- Toujours référencer `DESIGN.md` avant d'ajouter une couleur ou une typographie.

## Flux de traitement PDF
1. `POST /api/upload` reçoit le fichier + métadonnées de formulaire.
2. `processing.process_pdf()` extrait les 8 valeurs GPTS (coins lat/lng).
3. Rasterisation 300 DPI → PNG desktop, puis version mobile (~8 MP max).
4. Génération d'une miniature 400 px.
5. Écriture dans GCS : `{slug}/map.png`, `{slug}/map-mobile.png`, `{slug}/thumbnail.png`, `{slug}/config.json`.

## Points d'extension connus
- **Routes** : ajouter un tableau `routes` dans `config.json` + endpoint `POST /api/maps/{map_id}/routes` + fonction `drawRoutes()` dans `viewer.js`.
- **Auth/ACL** : actuellement toutes les cartes sont publiques ; ajouter un middleware FastAPI avant d'exposer des données sensibles.

## Ce qu'il NE faut PAS faire
- Ne pas écrire dans le dossier `maps/` en production (uniquement pour les fixtures locales de test).
- Ne pas modifier `static/css/tailwind.css` directement.
- Ne pas ajouter de dépendances npm au-delà de Tailwind et ses plugins officiels sans discussion préalable.
- Ne pas exposer le contenu de `stitch-mcp-sa-key.json` ni d'autres credentials dans le code.
- Ne pas créer de base de données — GCS est la seule source de vérité.
