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
├── requirements.txt
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
