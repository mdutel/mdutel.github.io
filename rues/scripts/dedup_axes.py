#!/usr/bin/env python3
"""
dedup_axes.py (v2) — Deduplique les segments OSM par nom de voie.

Entree  : GeoJSON exporte d'Overpass Turbo (Export -> GeoJSON).
Sortie  : - resume trie par longueur (console)
          - GeoJSON dedupliqué : 1 MultiLineString par voie, props {name, length_m, n_segments}

Flags console :
  !!            -> voie spatialement eclatee (bbox >> longueur) : a verifier (peut etre un coude)
  (~ Autre Nom) -> quasi-doublon : nom presque identique ET geographiquement proche
                   (typiquement variante d'orthographe OSM, ex. Claude / Claudius)

Filtres P0 optionnels :
  --min-length 250        -> ignore les voies sous 250 m
  --exclude-prefix Quai   -> ignore les voies dont le nom commence par "Quai" (repetable)

Aucune dependance externe (stdlib). Longueurs en metres (haversine).
Usage : python3 dedup_axes.py <export.geojson> [sortie.geojson] [--min-length N] [--exclude-prefix P]
"""

import json
import math
import sys
import difflib
from collections import defaultdict

R_TERRE = 6_371_000

SIM_THRESHOLD = 0.82
CENTROID_MAX_M = 400


def haversine(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R_TERRE * math.asin(math.sqrt(a))


def longueur_ligne(coords):
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        total += haversine(lon1, lat1, lon2, lat2)
    return total


def iter_linestrings(geom):
    if not geom:
        return []
    t = geom.get("type")
    if t == "LineString":
        return [geom["coordinates"]]
    if t == "MultiLineString":
        return list(geom["coordinates"])
    return []


def bbox_diag(lines):
    xs = [c[0] for ln in lines for c in ln]
    ys = [c[1] for ln in lines for c in ln]
    if not xs:
        return 0.0
    return haversine(min(xs), min(ys), max(xs), max(ys))


def centroid(lines):
    xs = [c[0] for ln in lines for c in ln]
    ys = [c[1] for ln in lines for c in ln]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def centroid_dist(la, lb):
    ax, ay = centroid(la)
    bx, by = centroid(lb)
    return haversine(ax, ay, bx, by)


def parse_args(argv):
    chemin_out = "axes_dedup.geojson"
    min_length = 0.0
    exclude_prefixes = []
    positionnels = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--min-length":
            min_length = float(argv[i + 1]); i += 2
        elif a == "--exclude-prefix":
            exclude_prefixes.append(argv[i + 1]); i += 2
        else:
            positionnels.append(a); i += 1
    chemin_in = positionnels[0] if positionnels else None
    if len(positionnels) > 1:
        chemin_out = positionnels[1]
    return chemin_in, chemin_out, min_length, exclude_prefixes


def main():
    chemin_in, chemin_out, min_length, exclude_prefixes = parse_args(sys.argv)
    if not chemin_in:
        print("Usage : python3 dedup_axes.py <export.geojson> [sortie.geojson] "
              "[--min-length N] [--exclude-prefix P]")
        sys.exit(1)

    with open(chemin_in, encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features", [])

    groupes = defaultdict(list)
    sans_nom = 0
    for feat in features:
        nom = (feat.get("properties") or {}).get("name")
        lignes = iter_linestrings(feat.get("geometry"))
        if not nom:
            sans_nom += 1
            continue
        groupes[nom].extend(lignes)

    voies = []
    for nom, lignes in groupes.items():
        longueur = sum(longueur_ligne(ln) for ln in lignes)
        diag = bbox_diag(lignes)
        ratio = (diag / longueur) if longueur > 0 else 0.0
        voies.append({"name": nom, "length_m": round(longueur), "n_segments": len(lignes),
                      "lignes": lignes, "ratio_diag": round(ratio, 2)})

    avant = len(voies)
    if min_length > 0:
        voies = [v for v in voies if v["length_m"] >= min_length]
    if exclude_prefixes:
        voies = [v for v in voies
                 if not any(v["name"].startswith(p) for p in exclude_prefixes)]
    filtres_actifs = (avant != len(voies))

    partenaire = defaultdict(list)
    for i in range(len(voies)):
        for j in range(i + 1, len(voies)):
            a, b = voies[i], voies[j]
            if difflib.SequenceMatcher(None, a["name"], b["name"]).ratio() >= SIM_THRESHOLD:
                if centroid_dist(a["lignes"], b["lignes"]) <= CENTROID_MAX_M:
                    partenaire[a["name"]].append(b["name"])
                    partenaire[b["name"]].append(a["name"])

    voies.sort(key=lambda v: v["length_m"], reverse=True)

    msg_filtre = " (filtres P0 appliques)" if filtres_actifs else ""
    print(f"\n{len(features)} segments en entree -> {len(voies)} voies retenues{msg_filtre} "
          f"({sans_nom} sans nom ignore(s))\n")
    print(f"{'#':>3}  {'long. (m)':>9}  {'seg':>4}  {'?':>2}  nom")
    print("-" * 78)
    for i, v in enumerate(voies, 1):
        flag = "!!" if v["ratio_diag"] > 1.6 else ""
        suffixe = ""
        if partenaire.get(v["name"]):
            suffixe = "  (~ " + " / ".join(sorted(set(partenaire[v["name"]]))) + ")"
        print(f"{i:>3}  {v['length_m']:>9}  {v['n_segments']:>4}  {flag:>2}  {v['name']}{suffixe}")
    print("\n  !!         = voie spatialement eclatee (a verifier, peut etre un coude)")
    print("  (~ Autre)  = quasi-doublon : nom proche + geographiquement adjacent\n")

    out = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"name": v["name"], "length_m": v["length_m"], "n_segments": v["n_segments"]},
         "geometry": {"type": "MultiLineString", "coordinates": v["lignes"]}}
        for v in voies]}
    with open(chemin_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"GeoJSON ecrit : {chemin_out}  ({len(voies)} voies)")


if __name__ == "__main__":
    main()
