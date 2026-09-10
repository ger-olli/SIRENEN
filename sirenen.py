#!/usr/bin/env python3
"""Düsseldorfer Sirenen per WMS GetFeatureInfo abrufen und zusammenführen."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import requests

AUTH_ENV = "DUESSELDORF_AUTHKEY"
LAYER = "feuerwehr37:fwr_sirenen"
WMS_URL = "https://maps.duesseldorf.de/services/feuerwehr37/wms"

# Die alte Vollabfrage lieferte stabil 95 Treffer. Sie bleibt deshalb die Basis.
BASE_BBOX = (330000.0, 5660000.0, 370000.0, 5700000.0)
BASE_BUFFER_PX = 100

# Ergänzende Suche für Randlagen, insbesondere im Süden/Norden der Stadt.
SEARCH_BBOX = (320000.0, 5650000.0, 380000.0, 5710000.0)
GRID_SIZE = 4
OVERLAP = 0.15
TILE_BUFFER_PX = 90
WIDTH = 101
HEIGHT = 101


def get_authkey() -> str:
    key = os.environ.get(AUTH_ENV, "").strip()
    if not key:
        raise RuntimeError(f"Umgebungsvariable {AUTH_ENV} fehlt.")
    return key


def normalize_authkey(key: str) -> str:
    """AuthKey für den Düsseldorfer Dienst einmal vorkodieren."""
    raw = key
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    return quote(raw, safe="")


def request_json(
    authkey: str,
    bbox: tuple[float, float, float, float],
    buffer_px: int,
    session: requests.Session,
) -> dict:
    bbox_value = ",".join(f"{v:.3f}" for v in bbox)
    params = {
        "QUERY_LAYERS": LAYER,
        "INFO_FORMAT": "application/json",
        "REQUEST": "GetFeatureInfo",
        "SERVICE": "wms",
        "VERSION": "1.3.0",
        "FORMAT": "image/png",
        "STYLES": "",
        "TRANSPARENT": "TRUE",
        "CRS": "EPSG:25832",
        "LAYERS": LAYER,
        "FEATURE_COUNT": 500,
        "I": WIDTH // 2,
        "J": HEIGHT // 2,
        "WIDTH": WIDTH,
        "HEIGHT": HEIGHT,
        "BBOX": bbox_value,
        "BUFFER": buffer_px,
        "authkey": normalize_authkey(authkey),
    }

    response = session.get(
        WMS_URL,
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": "SIRENEN/5.0 (+https://github.com/ger-olli/SIRENEN)",
        },
        timeout=60,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Server lieferte kein JSON: {response.text[:500]}") from exc

    if not isinstance(data, dict) or "features" not in data:
        raise RuntimeError("Antwort ist keine GeoJSON FeatureCollection.")
    return data


def make_tiles() -> list[tuple[float, float, float, float]]:
    minx, miny, maxx, maxy = SEARCH_BBOX
    step_x = (maxx - minx) / GRID_SIZE
    step_y = (maxy - miny) / GRID_SIZE
    pad_x = step_x * OVERLAP
    pad_y = step_y * OVERLAP

    tiles = []
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            tiles.append((
                minx + col * step_x - pad_x,
                miny + row * step_y - pad_y,
                minx + (col + 1) * step_x + pad_x,
                miny + (row + 1) * step_y + pad_y,
            ))
    return tiles


def feature_key(feature: dict) -> str:
    props = feature.get("properties") or {}
    return str(
        props.get("nummer")
        or props.get("_uuid")
        or feature.get("id")
        or json.dumps(feature, sort_keys=True, ensure_ascii=False)
    )


def merge_features(*collections: dict) -> dict:
    by_key: dict[str, dict] = {}
    for collection in collections:
        for feature in collection.get("features", []):
            by_key.setdefault(feature_key(feature), feature)

    features = sorted(
        by_key.values(),
        key=lambda f: str((f.get("properties") or {}).get("nummer", "")),
    )
    return {
        "type": "FeatureCollection",
        "features": features,
        "numberReturned": len(features),
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:EPSG::25832"},
        },
    }


def fetch_all(authkey: str) -> dict:
    with requests.Session() as session:
        print("1/2 Basisabfrage über ganz Düsseldorf …")
        base = request_json(authkey, BASE_BBOX, BASE_BUFFER_PX, session)
        print(f"Basis-Treffer: {len(base.get('features', []))}")

        supplemental_features: list[dict] = []
        tiles = make_tiles()
        print(f"2/2 Ergänzungsscan über {len(tiles)} überlappende Kacheln …")
        for index, bbox in enumerate(tiles, start=1):
            data = request_json(authkey, bbox, TILE_BUFFER_PX, session)
            features = data.get("features", [])
            print(f"Kachel {index:02d}/{len(tiles)}: {len(features)} Treffer")
            supplemental_features.extend(features)

        supplemental = {"type": "FeatureCollection", "features": supplemental_features}
        merged = merge_features(base, supplemental)

        base_keys = {feature_key(f) for f in base.get("features", [])}
        merged_keys = {feature_key(f) for f in merged.get("features", [])}
        new_keys = sorted(merged_keys - base_keys)
        print(f"Zusätzliche eindeutige Treffer durch Ergänzungsscan: {len(new_keys)}")
        if new_keys:
            print("Neu ergänzt: " + ", ".join(new_keys))

        return merged


def save_geojson(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(data: dict, path: Path) -> None:
    rows = [(feature.get("properties") or {}) for feature in data.get("features", [])]
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    preferred = [
        "nummer", "adresse", "plz", "stadtteil", "stadt",
        "beschallungsradius", "_last_update", "_uuid",
    ]
    all_fields = {key for row in rows for key in row.keys()}
    fields = [field for field in preferred if field in all_fields]
    fields += sorted(all_fields - set(fields))

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(data: dict) -> None:
    numbers = [
        str((feature.get("properties") or {}).get("nummer", ""))
        for feature in data.get("features", [])
    ]
    numeric = {n for n in numbers if n.startswith("S") and n[1:].isdigit()}
    if numeric:
        max_no = max(int(n[1:]) for n in numeric)
        expected = {f"S{i:03d}" for i in range(1, max_no + 1)}
        missing = sorted(expected - numeric)
        if missing:
            print("Nummernlücken bis zur höchsten gefundenen Nummer: " + ", ".join(missing))

    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        print(f"- {props.get('nummer', '?')}: {props.get('adresse', '?')} | {props.get('stadtteil', '?')}")


def main() -> int:
    try:
        data = fetch_all(get_authkey())
        count = len(data.get("features", []))
        if count == 0:
            raise RuntimeError("Der Karten-Layer hat keine Sirenen geliefert.")

        save_geojson(data, Path("sirenen.geojson"))
        save_csv(data, Path("sirenen.csv"))
        print(f"Gefundene eindeutige Sirenen gesamt: {count}")
        print("Gespeichert: sirenen.geojson, sirenen.csv")
        report(data)
        return 0
    except requests.RequestException as exc:
        print(f"HTTP-Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
