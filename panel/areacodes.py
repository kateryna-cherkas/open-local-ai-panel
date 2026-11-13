"""Telephone area codes per panel city (metro core), used to place service-area businesses.

Service-area businesses (SABs) have no public address or coordinates in Google Places, so
the metro-radius check (protocol 4.4) cannot be applied. For those candidates the phone's
area code must belong to the city's metro; toll-free or missing phones -> not matched
(left for the manual step). Codes from the NANPA area-code maps; overlays included.
"""
from typing import Dict, FrozenSet

from panel.names import digits_only

AREA_CODES = {
    "Houston_TX": "713 281 832 346",
    "Phoenix_AZ": "602 480 623",
    "Atlanta_GA": "404 470 678 770 943",
    "Seattle_WA": "206 425 253 360 564",
    "Denver_CO": "303 720 983",
    "Chicago_IL": "312 773 872 630 331 847 224 708 464 815 779",
    "Philadelphia_PA": "215 267 445 610 484 835 856",
    "Tacoma_WA": "253 206 425 360 564",
    "Boise_ID": "208 986",
    "Knoxville_TN": "865",
    "Greenville_SC": "864 821",
    "Reno_NV": "775",
    "Bend_OR": "541 458",
    "Los_Angeles_CA": "213 323 310 424 818 747 626 562",
    "Jacksonville_FL": "904",
    "Boston_MA": "617 857 781 339 508 774 978 351",
    "Tampa_FL": "813 656 727",
    "Bakersfield_CA": "661",
    "Newark_NJ": "973 862 201 551 908",
    "Worcester_MA": "508 774",
    "Lakeland_FL": "863",
    "Redding_CA": "530",
    "Bellingham_WA": "360 564",
    "Trenton_NJ": "609 640",
    "Flagstaff_AZ": "928",
    "Asheville_NC": "828",
}  # type: Dict[str, str]

_CODES = {k: frozenset(v.split()) for k, v in AREA_CODES.items()}  # type: Dict[str, FrozenSet[str]]


def area_code(phone: str) -> str:
    d = digits_only(phone or "")
    return d[:3] if len(d) == 10 else ""


def in_metro(phone: str, city_key: str) -> bool:
    codes = _CODES.get(city_key)
    if codes is None:
        return True  # client-check cities without a mapping: fall back to name match only
    return area_code(phone) in codes
