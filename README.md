# OCAD Map Viewer

Application web pour naviguer dans des cartes OCAD géo-référencées avec Google Street View.

## Fonctionnalités

- **Upload** : glisser-déposer un PDF exporté depuis OCAD (avec géo-référencement)
- **Traitement automatique** : extraction des coordonnées GPTS, rasterisation 300 DPI
- **Navigation** : overlay perspective sur Google Maps + rotation synchronisée avec Street View
- **Multi-cartes** : accédez à toutes vos cartes depuis la page d'accueil
- **Calibration** : ajustement fin lat/lng de l'overlay

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
├── processing.py      # Extraction GPTS + rasterisation
├── Dockerfile         # Image Docker pour Cloud Run
├── requirements.txt
├── .env.example       # Template des variables d'environnement
├── static/
│   ├── index.html     # Page d'accueil (liste + upload)
│   ├── viewer.html    # Viewer (carte + Street View)
│   ├── css/style.css
│   └── js/
│       ├── home.js    # Logique page d'accueil
│       └── viewer.js  # Logique viewer (overlay, rotation, calibration)
└── maps/              # Cartes traitées (auto-généré)
    └── {slug}/
        ├── config.json
        ├── map.png
        └── thumb.jpg
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
