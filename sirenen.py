#!/usr/bin/env python3
"""Düsseldorfer Sirenen abrufen und als GeoJSON/CSV speichern."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import requests

AUTH_ENV = "DUESSELDORF_AUTHKEY"
LAYER = "feuerwehr37:fwr_sirenen"
WFS_URL = "https://maps.duesseldorf.de/services/feuerwehr37/wfs"
WMS_URL = "https://maps.duesseldorf.de/services/feuerwehr37/wms"
EXPECTED_COUNT = 101


def get_authkey() -> str:
    key = os.environ.get(AUTH_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"Umgebungsvariable {AUTH_ENV} fehlt. "
            "Den AuthKey niemals direkt in den Quellcode schreiben."
        )
    return key


def request_json(url: str, params: dict[str, object]) -> dict:
    response = requests.get(
        url,
        params=params,
        headers={"Accept": "application/json", "User-Agent": "SIRENEN/1.0"},
        timeout=60,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        preview = response.text[:500]
        raise RuntimeError(f"Server lieferte kein JSON: {preview}") from exc

    if not isinstance(data, dict) or "features" not in data:
        raise RuntimeError("Antwort ist keine GeoJSON FeatureCollection.")
    return data


def fetch_via_wfs(authkey: str) -> dict:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": LAYER,
        "OUTPUTFORMAT": "application/json",
        "COUNT": 500,
        "authkey": authkey,
    }
    return request_json(WFS_URL, params)


def fetch_via_wms(authkey: str) -> dict:
    # Fallback: sehr großer GetFeatureInfo-Puffer über einer Düsseldorf-BBOX.
    # BUFFER ist ein GeoServer-Erweiterungsparameter in Pixeln.
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "QUERY_LAYERS": LAYER,
        "LAYERS": LAYER,
        "INFO_FORMAT": "application/json",
        "FORMAT": "image/png",
        "STYLES": "",
        "TRANSPARENT": "TRUE",
        "CRS": "EPSG:25832",
        "FEATURE_COUNT": 500,
        "WIDTH": 101,
        "HEIGHT": 101,
        "I": 50,
        "J": 50,
        "BBOX": "330000,5660000,370000,5700000",
        "BUFFER": 5000,
        "authkey": authkey,
    }
    return request_json(WMS_URL, params)


def deduplicate(data: dict) -> dict:
    features = data.get("features", [])
    seen: set[str] = set()
    unique = []

    for feature in features:
        props = feature.get("properties") or {}
        key = str(
            props.get("nummer")
            or props.get("id")
            or feature.get("id")
            or json.dumps(feature, sort_keys=True, ensure_ascii=False)
        )
        if key not in seen:
            seen.add(key)
            unique.append(feature)

    result = dict(data)
    result["features"] = unique
    result["numberReturned"] = len(unique)
    return result


def save_geojson(data: dict, path: Path) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_csv(data: dict, path: Path) -> None:
    rows = [(feature.get("properties") or {}) for feature in data.get("features", [])]
    fields = sorted({key for row in rows for key in row.keys()})

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    try:
        authkey = get_authkey()

        try:
            print("Versuche WFS GetFeature …")
            data = fetch_via_wfs(authkey)
            source = "WFS"
        except Exception as wfs_error:
            print(f"WFS nicht erfolgreich: {wfs_error}", file=sys.stderr)
            print("Versuche WMS GetFeatureInfo-Fallback …")
            data = fetch_via_wms(authkey)
            source = "WMS"

        data = deduplicate(data)
        count = len(data.get("features", []))

        save_geojson(data, Path("sirenen.geojson"))
        save_csv(data, Path("sirenen.csv"))

        print(f"Quelle: {source}")
        print(f"Gefundene eindeutige Sirenen: {count}")
        print("Gespeichert: sirenen.geojson, sirenen.csv")

        if count != EXPECTED_COUNT:
            print(
                f"WARNUNG: Erwartet wurden {EXPECTED_COUNT}, erhalten wurden {count}.",
                file=sys.stderr,
            )
            return 2

        return 0

    except requests.RequestException as exc:
        print(f"HTTP-Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
