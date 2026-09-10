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
EXPECTED_COUNT = 101

# Großzügige Düsseldorf-BBOX in EPSG:25832.
BBOX = "330000,5660000,370000,5700000"


def get_authkey() -> str:
    key = os.environ.get(AUTH_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"Umgebungsvariable {AUTH_ENV} fehlt. "
            "Den AuthKey niemals direkt in den Quellcode schreiben."
        )
    return key


def normalize_authkey(key: str) -> str:
    """Key auf Rohwert normalisieren und einmal vorkodieren.

    Der Düsseldorfer Endpunkt erwartet den authkey in der tatsächlich
    gesendeten URL doppelt URL-kodiert. requests kodiert Parameter selbst;
    deshalb wird der Rohwert hier genau einmal vor-kodiert.

    Funktioniert sowohl wenn das GitHub-Secret als Rohwert als auch bereits
    URL-kodiert eingetragen wurde.
    """
    raw = key
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    return quote(raw, safe="")


def request_json(params: dict[str, object]) -> dict:
    response = requests.get(
        WMS_URL,
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": "SIRENEN/2.0 (+https://github.com/ger-olli/SIRENEN)",
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


def fetch_all(authkey: str) -> dict:
    # WIDTH/HEIGHT bilden ganz Düsseldorf ab. BUFFER wird in Pixeln gemessen.
    # Bei dieser BBOX entspricht BUFFER=100 deutlich mehr als der benötigten
    # Distanz vom Mittelpunkt bis zum Kartenrand und erfasst damit den Layer.
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
        "I": 50,
        "J": 50,
        "WIDTH": 101,
        "HEIGHT": 101,
        "BBOX": BBOX,
        "BUFFER": 100,
        # absichtlich einmal vorkodiert; requests kodiert '%' erneut zu '%25'
        "authkey": normalize_authkey(authkey),
    }
    return request_json(params)


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

    # Nach Sirenennummer sortieren, falls vorhanden.
    unique.sort(key=lambda f: str((f.get("properties") or {}).get("nummer", "")))

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
    ]
    all_fields = {key for row in rows for key in row.keys()}
    fields = [field for field in preferred if field in all_fields]
    fields += sorted(all_fields - set(fields))

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    try:
        authkey = get_authkey()
        print("Rufe Düsseldorfer WMS GetFeatureInfo ab …")
        data = deduplicate(fetch_all(authkey))
        count = len(data.get("features", []))

        save_geojson(data, Path("sirenen.geojson"))
        save_csv(data, Path("sirenen.csv"))

        print(f"Gefundene eindeutige Sirenen: {count}")
        print("Gespeichert: sirenen.geojson, sirenen.csv")

        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            print(
                f"- {props.get('nummer', '?')}: "
                f"{props.get('adresse', '?')} | {props.get('stadtteil', '?')}"
            )

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
