"""Chart data extractor for simpleastro

This module provides a single high-level function `extract_chart_data(subject)`
that accepts either the legacy `AstrologicalSubject` wrapper or the
`AstrologicalSubjectModel` from kerykeion and returns a structured
dictionary with keys suitable for LLM analysis.

The implementation intentionally focuses on robust, defensive access to the
kerykeion models so it works with both legacy wrappers and the newer models.
"""
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kerykeion import AspectsFactory
from kerykeion.schemas.kr_models import (
    AstrologicalSubjectModel,
    KerykeionPointModel,
    SingleChartAspectsModel,
)

# Public API
__all__ = ["extract_chart_data", "build_point_dict"]


def _unwrap_subject(subject: Any) -> AstrologicalSubjectModel:
    """Return the underlying AstrologicalSubjectModel from different subject forms.

    Supports:
    - legacy AstrologicalSubject wrapper (has attribute _model)
    - object with .model() method returning AstrologicalSubjectModel
    - already an AstrologicalSubjectModel

    Raises TypeError if the provided subject cannot be unwrapped.
    """
    # Direct model instance
    if isinstance(subject, AstrologicalSubjectModel):
        return subject

    # Legacy wrapper with _model attribute
    model = getattr(subject, "_model", None)
    if model is not None:
        if isinstance(model, AstrologicalSubjectModel):
            return model

    # Some wrappers expose a .model() convenience method
    model_func = getattr(subject, "model", None)
    if callable(model_func):
        maybe = model_func()
        if isinstance(maybe, AstrologicalSubjectModel):
            return maybe

    raise TypeError("Unsupported subject type: unable to extract AstrologicalSubjectModel")


def build_point_dict(point: KerykeionPointModel) -> Dict[str, Optional[Any]]:
    """Convert a KerykeionPointModel into a plain dictionary.

    The returned dict contains the most useful numeric and labeled fields that
    the LLM prompt pipeline will expect.
    """
    if point is None:
        return {}

    # Try to handle Enum-like values gracefully
    def _s(v):
        try:
            return v.value
        except Exception:
            return str(v) if v is not None else None

    return {
        "name": getattr(point, "name", None),
        "sign": _s(getattr(point, "sign", None)),
        "sign_num": getattr(point, "sign_num", None),
        "position": getattr(point, "position", None),  # degrees inside the sign (0-30)
        "abs_pos": getattr(point, "abs_pos", None),  # degrees in the zodiac (0-360)
        "house": _s(getattr(point, "house", None)),
        "retrograde": getattr(point, "retrograde", None),
        "speed": getattr(point, "speed", None),
        "declination": getattr(point, "declination", None),
        "element": _s(getattr(point, "element", None)),
        "quality": _s(getattr(point, "quality", None)),
        "emoji": getattr(point, "emoji", None),
        "point_type": _s(getattr(point, "point_type", None)),
    }


def _collect_planets(model: AstrologicalSubjectModel) -> Dict[str, Dict[str, Any]]:
    """Collect main planetary points from the model into a dict keyed by name."""
    planet_fields = [
        "sun",
        "moon",
        "mercury",
        "venus",
        "mars",
        "jupiter",
        "saturn",
        "uranus",
        "neptune",
        "pluto",
        # Add commonly used extras
        "chiron",
        "ceres",
        "pallas",
        "juno",
        "vesta",
    ]

    planets = {}
    for f in planet_fields:
        point = getattr(model, f, None)
        if point is not None:
            planets[f.capitalize()] = build_point_dict(point)
    return planets


def _collect_angles(model: AstrologicalSubjectModel) -> Dict[str, Dict[str, Any]]:
    angles = {}
    for attr, label in (
        ("ascendant", "Ascendant"),
        ("medium_coeli", "MC"),
        ("imum_coeli", "IC"),
        ("descendant", "Descendant"),
    ):
        p = getattr(model, attr, None)
        if p is not None:
            angles[label] = build_point_dict(p)
    return angles


def _collect_houses(model: AstrologicalSubjectModel) -> Dict[str, Dict[str, Any]]:
    houses = {}
    for i in range(1, 13):
        attr = f"{["first","second","third","fourth","fifth","sixth","seventh","eighth","ninth","tenth","eleventh","twelfth"][i-1]}_house"
        p = getattr(model, attr, None)
        if p is not None:
            houses[f"House_{i}"] = build_point_dict(p)
    return houses


def _collect_nodes(model: AstrologicalSubjectModel) -> Dict[str, Dict[str, Any]]:
    nodes = {}
    for attr, label in (
        ("true_north_lunar_node", "True_North_Lunar_Node"),
        ("true_south_lunar_node", "True_South_Lunar_Node"),
        ("mean_north_lunar_node", "Mean_North_Lunar_Node"),
        ("mean_south_lunar_node", "Mean_South_Lunar_Node"),
    ):
        p = getattr(model, attr, None)
        if p is not None:
            nodes[label] = build_point_dict(p)
    return nodes


def _collect_aspects(model: AstrologicalSubjectModel) -> List[Dict[str, Any]]:
    """Use Kerykeion's AspectsFactory to compute single-chart aspects and
    return a serializable list of aspect dicts.
    """
    try:
        aspects_model: SingleChartAspectsModel = AspectsFactory.single_chart_aspects(model)
    except Exception:
        # If aspect calculation fails for any reason, return empty list (caller can still use other data)
        return []

    out = []
    for a in aspects_model.aspects:
        out.append({
            "p1_name": getattr(a, "p1_name", None),
            "p2_name": getattr(a, "p2_name", None),
            "aspect": getattr(a, "aspect", None),
            "orb": getattr(a, "orbit", None),
            "aspect_degrees": getattr(a, "aspect_degrees", None),
            "diff": getattr(a, "diff", None),
            "p1_abs_pos": getattr(a, "p1_abs_pos", None),
            "p2_abs_pos": getattr(a, "p2_abs_pos", None),
            "aspect_movement": getattr(a, "aspect_movement", None),
        })
    return out


def _element_and_quality_distributions(planets: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    elems = Counter()
    quals = Counter()
    for p in planets:
        e = p.get("element")
        q = p.get("quality")
        if e:
            elems[e] += 1
        if q:
            quals[q] += 1
    return dict(elems), dict(quals)


def _detect_stelliums(planets: Sequence[Dict[str, Any]], threshold: int = 3) -> List[Dict[str, Any]]:
    """Detect simple stelliums by sign or house (>= threshold planets in same sign/house)."""
    by_sign = defaultdict(list)
    by_house = defaultdict(list)

    for name, p in ((p.get("name"), p) for p in planets):
        if name is None:
            continue
        sign = p.get("sign")
        house = p.get("house")
        by_sign[sign].append(name)
        by_house[house].append(name)

    results = []
    for sign, names in by_sign.items():
        if sign and len(names) >= threshold:
            results.append({"type": "stellium_sign", "sign": sign, "planets": names})
    for house, names in by_house.items():
        if house and len(names) >= threshold:
            results.append({"type": "stellium_house", "house": house, "planets": names})
    return results


def _detect_grand_trines(aspects: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect simple Grand Trine patterns (three planets each trine to the others).

    This is a naive O(n^3) check using aspect names and planet pairs; it is
    purposely simple for an MVP.
    """
    tri_map = defaultdict(set)
    for a in aspects:
        if a.get("aspect") and a["aspect"].lower() == "trine":
            p1 = a.get("p1_name")
            p2 = a.get("p2_name")
            if p1 and p2:
                tri_map[p1].add(p2)
                tri_map[p2].add(p1)

    planets = list(tri_map.keys())
    found = []
    n = len(planets)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                a = planets[i]
                b = planets[j]
                c = planets[k]
                if b in tri_map[a] and c in tri_map[a] and a in tri_map[b] and c in tri_map[b] and a in tri_map[c] and b in tri_map[c]:
                    found.append({"type": "grand_trine", "planets": [a, b, c]})
    return found


def _detect_t_squares(aspects: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect simple T-square patterns: two planets in opposition both square to a third."""
    # Build lookups
    by_pair = {}
    squares = defaultdict(set)
    oppositions = set()
    for a in aspects:
        p1 = a.get("p1_name")
        p2 = a.get("p2_name")
        asp = (a.get("aspect") or "").lower()
        if p1 is None or p2 is None:
            continue
        key = tuple(sorted((p1, p2)))
        by_pair[key] = asp
        if asp == "square":
            squares[p1].add(p2)
            squares[p2].add(p1)
        if asp == "opposition":
            oppositions.add(key)

    results = []
    # For every opposition pair, check if both ends square the same planet
    for opp in oppositions:
        pA, pB = opp
        # find planet P such that P is square to both pA and pB
        common = squares[pA].intersection(squares[pB])
        for p in common:
            results.append({"type": "t_square", "opposition": [pA, pB], "apex": p})
    return results


def extract_chart_data(subject: Any) -> Dict[str, Any]:
    """Primary entry point.

    Returns a structured dictionary with the following high-level keys:
    - person_name
    - birth_data (best-effort fields found on the model)
    - planets
    - houses
    - angles
    - nodes
    - aspects
    - elemental_distribution
    - modality_distribution
    - aspect_patterns

    Raises TypeError if the provided subject cannot be unwrapped into a supported model.
    """
    model = _unwrap_subject(subject)

    # Basic identification and time data (best-effort)
    birth_data = {
        "name": getattr(model, "name", None),
        "city": getattr(model, "city", None),
        "nation": getattr(model, "nation", None),
        "iso_local": getattr(model, "iso_formatted_local_datetime", None),
        "iso_utc": getattr(model, "iso_formatted_utc_datetime", None),
        "year": getattr(model, "year", None),
        "month": getattr(model, "month", None),
        "day": getattr(model, "day", None),
        "hour": getattr(model, "hour", None),
        "minute": getattr(model, "minute", None),
    }

    planets = _collect_planets(model)
    houses = _collect_houses(model)
    angles = _collect_angles(model)
    nodes = _collect_nodes(model)
    aspects = _collect_aspects(model)

    # Build lists for distribution analysis
    planets_list = list(planets.values())

    elemental_distribution, modality_distribution = _element_and_quality_distributions(planets_list)

    # Detect simple aspect patterns
    aspect_patterns = []
    aspect_patterns.extend(_detect_stelliums(planets_list))
    aspect_patterns.extend(_detect_grand_trines(aspects))
    aspect_patterns.extend(_detect_t_squares(aspects))

    return {
        "person_name": birth_data.get("name"),
        "birth_data": birth_data,
        "planets": planets,
        "houses": houses,
        "angles": angles,
        "nodes": nodes,
        "aspects": aspects,
        "elemental_distribution": elemental_distribution,
        "modality_distribution": modality_distribution,
        "aspect_patterns": aspect_patterns,
    }
