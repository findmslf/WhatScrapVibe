"""Enrichissement de numéros de téléphone — « OSINT maison », version propre.

Uniquement des métadonnées déduites du numéro lui-même (hors-ligne, via la lib
`phonenumbers` de Google) : validité, format E.164, pays, région, opérateur,
type de ligne. Aucune requête à un service tiers, aucune base de fuites, aucune
énumération de comptes — ces techniques sont volontairement exclues (abus de
service / données illégales ; en vrai judiciaire ça passe par réquisition).
"""
from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberType, carrier, geocoder, number_type
from phonenumbers.phonenumberutil import NumberParseException

_TYPES = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "fixe",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixe/mobile",
    PhoneNumberType.VOIP: "VoIP",
    PhoneNumberType.TOLL_FREE: "numéro vert",
    PhoneNumberType.PREMIUM_RATE: "surtaxé",
}


def _parse(raw: str, region: str = "FR"):
    try:
        return phonenumbers.parse((raw or "").strip(), region)
    except NumberParseException:
        return None


def enrich_phone(raw: str, region: str = "FR") -> dict:
    """Métadonnées d'un numéro. `region` = pays supposé pour un numéro national."""
    n = _parse(raw, region)
    if n is None:
        return {"numero": raw, "valide": False}
    valide = phonenumbers.is_valid_number(n)
    return {
        "numero": raw,
        "valide": valide,
        "e164": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164) if valide else "",
        "pays": geocoder.description_for_number(n, "fr"),
        "operateur": carrier.name_for_number(n, "fr"),
        "type": _TYPES.get(number_type(n), "inconnu"),
    }


def describe_phone(raw: str, region: str = "FR") -> str:
    """Rend un numéro enrichi lisible : « 06 12 34 56 78 (France · mobile · SFR) »."""
    e = enrich_phone(raw, region)
    if not e["valide"]:
        return f"{raw} (format non valide)"
    bits = [b for b in (e["pays"], e["type"], e["operateur"]) if b]
    return f"{raw} (" + " · ".join(bits) + ")" if bits else raw


def describe_phones(nums, region: str = "FR") -> list[str]:
    return [describe_phone(n, region) for n in (nums or [])]
