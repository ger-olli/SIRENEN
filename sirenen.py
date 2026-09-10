#!/usr/bin/env python3
"""Düsseldorfer Sirenen per WMS GetFeatureInfo abrufen und speichern."""

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
TARGET_COUNT = 101

# Erweiterter Suchraum um Düsseldorf in EPSG:25832.
SEARCH_BBOX = (320000.0, 5650000.0, 380000.0, 5710000.0)
GRID_SIZE = 4
OVERLAP = 0.15
BUFFER_PX = 90
WIDTH = 101
HEIGHT = 101


def get_authkey() -> str:
    key = os.environ.get(AUTH_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"Umgebungsvariable {AUTH_ENV} fehlt. "
            "Den AuthKey niemals direkt in den Quellcode schreiben."
        )
    return key


def normalize_authkey(key: str) -> str:
    """Key auf Rohwert normalisieren und einmal vorkodieren."""
    raw = key
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    return quote(raw, safe="")


def request_json(params: dict[str, object], session: requests.Session) -> dict:
    response = session.get(
        WMS_URL,
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": "SIRENEN/4.0 (+https://github.com/ger-olli/SIRENEN)",
        },
        timeout=60,
    )
    print(f"HTTP-Status: {response.status_code}")
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        preview = response.text[:1000]
        raise RuntimeError(f"Server lieferte kein JSON: {preview}") from exc

    if not isinstance(data, dict) or "features" not in data:
        raise RuntimeError(
            "Antwort ist keine GeoJSON FeatureCollection: "
            + json.dumps(data, ensure_ascii=False)[:1000]
        )
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
            x1 = minx + col * step_x - pad_x
            y1 = miny + row * step_y - pad_y
            x2 = minx + (col + 1) * step_x + pad_x
            y2 = miny + (row + 1) * step_y + pad_y
            tiles.append((x1, y1, x2, y2))
    return tiles


def fetch_tile(authkey: str, bbox: tuple[float, float, float, float], session: requests.Session) -> dict:
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
        "BUFFER": BUFFER_PX,
        "authkey": normalize_authkey(authkey),
    }
    return request_json(params, session)


def fetch_all(authkey: str) -> dict:
    all_features: list[dict] = []
    tiles = make_tiles()

    with requests.Session() as session:
        for index, bbox in enumerate(tiles, start=1):
            print(
                f"Kachel {index}/{len(tiles)}: "
                f"{bbox[0]:.0f},{bbox[1]:.0f},{bbox[2]:.0f},{bbox[3]:.0f}"
            )
            data = fetch_tile(authkey, bbox, session)
            features = data.get("features", [])
            print(f"  Treffer in Kachel: {len(features)}")
            all_features.extend(features)

    return {
        "type": "FeatureCollection",
        "features": all_features,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25832"}},
    }


def deduplicate(data: dict) -> dict:
    features = data.get("features", [])
    seen: set[str] = set()
    unique = []

    for feature in features:
        props = feature.get("properties") or {}
        key = str(
            props.get("nummer")
            or props.get("_uuid")
            or feature.get("id")
            or json.dumps(feature, sort_keys=True, ensure_ascii=False)
        )
        if key not in seen:
            seen.add(key)
            unique.append(feature)

    unique.sort(key=lambda f: str((f.get("properties") or {}).get("nummer", "")))

    result = dict(data)
    result["features"] = unique
    result["numberReturned"] = len(unique)
    return result


def save_geojson(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(data: dict, path: Path) -> None:
    rows = [(feature.get("properties") or {}) for feature in data.get("features", [])]
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    preferred = [
        "nummer",
        "adresse",
        "plz",
        "stadtteil",
        "stadt",
        "beschallungsradius",
        "_last_update",
        "_uuid",
    ]
    all_fields = {key for row in rows for key in row.keys()}
    fields = [field for field in preferred if field in all_fields]
    fields += sorted(all_fields - set(fields))

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report_number_gaps(data: dict) -> None:
    numbers = {
        str((feature.get("properties") or {}).get("nummer", ""))
        for feature in data.get("features", [])
    }
    expected_labels = {f"S{i:03d}" for i in range(1, TARGET_COUNT + 1)}
    missing = sorted(expected_labels - numbers)
    if missing:
        print("Fehlende Nummern: " + ", ".join(missing), file=sys.stderr)
    else:
        print("Alle Nummern S001-S101 gefunden.")


def main() -> int:
    try:
        authkey = get_authkey()
        print(
            f"Scanne erweiterten Düsseldorfer Suchraum in {GRID_SIZE}x{GRID_SIZE} Kacheln, "
            f"BUFFER={BUFFER_PX}px, Überlappung={int(OVERLAP * 100)}% …"
        )
        data = deduplicate(fetch_all(authkey))
        count = len(data.get("features", []))

        if count == 0:
            raise RuntimeError("Der Karten-Layer hat keine Sirenen geliefert.")

        save_geojson(data, Path("sirenen.geojson"))
        save_csv(data, Path("sirenen.csv"))

        print(f"Gefundene eindeutige Sirenen: {count}")
        print("Gespeichert: sirenen.geojson, sirenen.csv")
        report_number_gaps(data)

        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            print(
                f"- {props.get('nummer', '?')}: "
                f"{props.get('adresse', '?')} | {props.get('stadtteil', '?')}"
            )

        return 0

    except requests.RequestException as exc:
        print(f"HTTP-Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
