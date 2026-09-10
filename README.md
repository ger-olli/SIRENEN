# SIRENEN

Python-Script zum Abruf der Düsseldorfer Sirenen-Daten aus dem Layer `feuerwehr37:fwr_sirenen`.

## Sicherheit

Der AuthKey wird **nicht** im Repository gespeichert. Das Script liest ihn aus der Umgebungsvariable `DUESSELDORF_AUTHKEY`.

Lokaler Aufruf:

```bash
export DUESSELDORF_AUTHKEY='DEIN_AUTHKEY'
python sirenen.py
```

Unter Windows PowerShell:

```powershell
$env:DUESSELDORF_AUTHKEY='DEIN_AUTHKEY'
python sirenen.py
```

## Ausgabe

Das Script schreibt:

- `sirenen.geojson` – vollständige GeoJSON FeatureCollection
- `sirenen.csv` – Attribute der gefundenen Sirenen

## GitHub Actions

Lege im Repository unter **Settings → Secrets and variables → Actions** ein Repository Secret mit dem Namen `DUESSELDORF_AUTHKEY` an. Der Workflow `.github/workflows/update-sirenen.yml` kann dann manuell gestartet werden und läuft außerdem täglich.
