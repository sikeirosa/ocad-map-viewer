# OCAD Map Viewer

Application web pour naviguer dans des cartes OCAD géo-référencées avec Google Street View.

## Fonctionnalités

- **Upload** : glisser-déposer un PDF exporté depuis OCAD (avec géo-référencement)
- **Traitement automatique** : extraction des coordonnées GPTS, rasterisation 300 DPI
- **Navigation** : overlay perspective sur Google Maps + rotation synchronisée avec Street View (overlay OCAD, parcours **et** analyses de tronçon suivent le mouvement)
- **Multi-cartes** : accédez à toutes vos cartes depuis la page d'accueil
- **Calibration** : ajustement fin lat/lng de l'overlay
- **Parcours d'orientation** : tracé conforme aux normes IOF (triangle départ, ronds balises, double-rond arrivée) avec sauvegarde, distance totale et symboles proportionnels au zoom
- **Analyse de tronçon** : compare jusqu'à 3 itinéraires entre deux balises en respectant les objets infranchissables ISSprOM (bâtiments, murs, clôtures, vert dense, zones privées) ; une balise enclose est signalée comme inaccessible
- **Export PDF** : génération serveur d'un parcours en PDF A3 300 DPI (symboles IOF)

## Installation

```bash
cd ocad-map-viewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Éditer .env avec votre clé Google Maps API
```

## Lancer

```bash
uvicorn server:app --reload --port 8080
```

Ouvrir http://localhost:8080

## Ajouter une carte

1. Exporter la carte depuis OCAD en PDF avec géo-référencement activé
2. Sur la page d'accueil, glisser-déposer le fichier PDF
3. La carte apparaît automatiquement dans la liste

## Structure

```
├── server.py          # Serveur FastAPI
├── processing.py      # Extraction GPTS + rasterisation + cache traversabilité
├── pdf_export.py      # Export parcours → PDF (symboles IOF)
├── traversability.py  # Classification pixels ISSprOM → raster de coût + barrières
├── pathfinding.py     # Itinéraires diversifiés (Dijkstra via-sommet + Theta*)
├── Dockerfile         # Image Docker pour Cloud Run
├── requirements.txt
├── .env.example       # Template des variables d'environnement
├── static/
│   ├── index.html     # Page d'accueil (liste + upload)
│   ├── viewer.html    # Viewer (carte + Street View)
│   ├── css/style.css
│   └── js/
│       ├── home.js    # Logique page d'accueil
│       ├── viewer.js  # Logique viewer (overlay, rotation, calibration)
│       └── routes.js  # Parcours d'orientation (tracé IOF, API, rendu, analyse de tronçon)
└── maps/              # Cartes traitées (auto-généré)
    └── {slug}/
        ├── config.json
        ├── map.png
        ├── thumb.jpg
        ├── traversability_v9.npz   # Cache grille de coût + barrières
        └── routes/{uuid}.json
```

## Déploiement (CI/CD)

Le projet utilise **GitHub Actions** pour le déploiement automatique sur **Google Cloud Run**.

### Pipeline

À chaque push sur `main` :
1. Build de l'image Docker
2. Push vers Artifact Registry (`europe-west1`)
3. Déploiement sur Cloud Run

### Prérequis GCP

- Projet : `ocad-map-viewer`
- APIs activées : Cloud Run, Artifact Registry, IAM Credentials
- Artifact Registry : `ocad-map-viewer` (Docker, `europe-west1`)
- Workload Identity Federation configurée pour GitHub Actions (auth sans clé)

### Secret GitHub

Le secret suivant doit être configuré dans **Settings → Secrets → Actions** :

| Secret | Description |
|--------|-------------|
| `GOOGLE_MAPS_API_KEY` | Clé API Google Maps (Maps JavaScript API) |
| `GOOGLE_MAPS_MAP_ID` | Map ID Google Maps (requis pour AdvancedMarkerElement) |

### Secrets GCP (Secret Manager)

Les secrets suivants doivent être créés dans **Secret Manager** et montés sur le service Cloud Run :

| Secret | Description |
|--------|-------------|
| `GOOGLE_MAPS_MAP_ID` | Map ID Google Maps — créé via `gcloud secrets create` |

Création initiale (une seule fois) :
```bash
echo -n "<valeur>" | gcloud secrets create GOOGLE_MAPS_MAP_ID --data-file=- --replication-policy=automatic
gcloud secrets add-iam-policy-binding GOOGLE_MAPS_MAP_ID \
  --member="serviceAccount:<SA>@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
gcloud run services update ocad-map-viewer --region europe-west1 \
  --update-secrets="GOOGLE_MAPS_MAP_ID=GOOGLE_MAPS_MAP_ID:latest"
```

## CSS (Tailwind)

Tailwind CSS est compilé statiquement — **ne pas utiliser le CDN en production**.

```bash
npm install
npm run build:css      # compile static/css/tailwind.css
npm run watch:css      # mode watch (développement)
```

Le `Dockerfile` exécute automatiquement `npm run build:css` via un multi-stage build (image Node → image Python).

## Stockage local (développement)

En l'absence de la variable `GCS_BUCKET`, l'application utilise le dossier local `maps/` comme stockage.
Pour modifier le répertoire : `LOCAL_STORAGE_DIR=./mon-dossier uvicorn server:app --reload --port 8080`

## Parcours d'orientation

Les parcours sont stockés par carte dans `{map_id}/routes/{uuid}.json` (GCS ou local).

- **Départ** : triangle équilatéral (magenta IOF `#cf00cf`)
- **Balises** : cercle transparent avec numéro, couleur magenta
- **Arrivée** : double-cercle concentrique magenta
- Symboles proportionnels au zoom (référence zoom 17, plancher à zoom < 17)
- Synchronisation avec la rotation Street View

### API Routes

| Méthode | Chemin | Description |
|---------|--------|-------------|
| GET | `/api/maps/{map_id}/routes` | Liste les parcours |
| POST | `/api/maps/{map_id}/routes` | Crée un parcours |
| GET | `/api/maps/{map_id}/routes/{route_id}` | Détail d'un parcours |
| PUT | `/api/maps/{map_id}/routes/{route_id}` | Met à jour un parcours |
| DELETE | `/api/maps/{map_id}/routes/{route_id}` | Supprime un parcours |
| POST | `/api/maps/{map_id}/routes/{route_id}/export-pdf` | Génère le PDF du parcours (→ `{jobId}`, SSE) |

## Analyse de tronçon

Compare jusqu'à **3 itinéraires** entre deux balises consécutives en respectant les
règles de sprint **ISSprOM**. Les objets **infranchissables** (bâtiments, murs,
clôtures non franchissables, vert dense, zones privées olive) ne sont jamais
traversés ; si une balise est réellement enclose sur la carte, elle est signalée
comme **inaccessible** plutôt que de tracer un itinéraire fantaisiste.

### Fonctionnement

1. `traversability.py` classe chaque pixel de `map.png` selon la palette ISSprOM
   (distance Chebyshev L∞) et construit une **grille de coût** (~1.27 m/cellule,
   résolution ciblée en mètres réels — voir ci-dessous) plus des **arêtes de
   barrières** (modèle edge-cut). Le résultat est mis en cache dans
   `{map_id}/traversability_v9.npz`.
2. `pathfinding.find_diverse_routes()` exécute un **Dijkstra** (SciPy) depuis le
   départ et l'arrivée, puis dérive des alternatives par **via-sommet** et
   **pénalité de détour** (plafonnées à ~1.6× l'optimal). `connected_components`
   détecte les balises encloses ; un **re-ancrage composante** borné au rayon du
   cercle de balise secourt les fausses isolations dues à la quantification (sans
   jamais tracer de connecteur traversant un obstacle). Une **déduplication
   multi-niveaux** (Jaccard, overlap de corridor tamponné, Bresenham sur le rendu
   final) élimine les choix quasi-identiques visuellement avant de les renvoyer
   au client.
3. Les itinéraires (string-pull tenant compte des obstacles) sont renvoyés au
   frontend via SSE, puis affichés en bleu (A), rouge (B) et vert (C) avec leur
   distance et pourcentage de détour.

> **Cache** : le fichier `.npz` est régénéré à l'upload PDF ou à la première
> analyse. Incrémenter `TRAVERSABILITY_VERSION` dans `traversability.py` invalide
> automatiquement l'ancien cache.

### API Analyse de tronçon

| Méthode | Chemin | Description |
|---------|--------|-------------|
| POST | `/api/maps/{map_id}/route-choices` | Lance l'analyse (`from_point`, `to_point`, `count` 1–3) → `{jobId}` |
| GET | `/api/maps/{map_id}/route-choices/{job_id}/stream` | SSE progression + résultats |
| GET | `/api/maps/{map_id}/traversability` | (Debug) Grille de coût en PNG niveaux de gris |
