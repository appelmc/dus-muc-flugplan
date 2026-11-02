#!/usr/bin/env python3
"""Selbsttest fuer build_dus_ics.py - laeuft ohne Netz.

Prueft: Zeitenableitung aus der DUS-API (Abflug und Ankunft), Kennzeichen im
Titel, Uebernahme des Vortags aus einem bestehenden Feed. Synthetische Daten,
kein Abruf der echten Quellen.
"""
from __future__ import annotations

import datetime as dt
import importlib.util

spec = importlib.util.spec_from_file_location("m", "build_dus_ics.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

HEUTE = dt.datetime.now(m.TZ).date()
ISO = HEUTE.isoformat()

# --- Zeitenableitung aus der API-Antwort ------------------------------------
abflug = {                       # DUS -> MUC
    "flightNumber": "LH 2004", "adFlag": "D", "flightDate": ISO,
    "scheduledTime": f"{ISO}T06:15:00+01:00",
    "destinationLocalTime": f"{ISO}T07:20:00+01:00",
    "flightDuration": 65,
    "airline": {"iataCode": "LH", "name": "Lufthansa"},
    "status": {"code": "P", "publicStatus": {"name": "pünktlich"}},
}
ankunft = {                      # MUC -> DUS
    "flightNumber": "VL 2001", "adFlag": "A", "flightDate": ISO,
    "scheduledTime": f"{ISO}T08:35:00+01:00",
    "destinationLocalTime": None,
    "flightDuration": 70,
    "airline": {"iataCode": "VL", "name": "Lufthansa City Airlines"},
    "status": {"code": "P", "publicStatus": {"name": "pünktlich"}},
}
e1, e2 = m.flug_eintrag(abflug), m.flug_eintrag(ankunft)
print("Abflug DUS:", {k: e1[k] for k in ("date", "dep", "arr", "from", "to", "airline")})
print("Ankunft DUS:", {k: e2[k] for k in ("date", "dep", "arr", "from", "to", "airline")})
assert (e1["from"], e1["to"], e1["dep"], e1["arr"]) == ("DUS", "MUC", "06:15", "07:20")
# Ankunftsflug: Abflug in Muenchen aus der Blockzeit abgeleitet (08:35 - 70 min)
assert (e2["from"], e2["to"], e2["dep"], e2["arr"]) == ("MUC", "DUS", "07:25", "08:35")
assert e1["airline"] == "Lufthansa" and e2["airline"] == "Lufthansa City"
assert m.flug_eintrag({"flightNumber": "kaputt", "adFlag": "D"}) is None
print("OK: beide Richtungen, beide Zeiten, Airline und Muellzeile stimmen.")

# --- ICS mit Kennzeichen ----------------------------------------------------
schluessel = {(ISO, "DUS", "MUC", m.flight_number_key("LH 2004"))}
zusatz = {(ISO, "DUS", "MUC", "2004"): {"aircraft": "A320", "times": ["07:20", "06:15"]}}
ics = m.build_ics([e1, e2], confirmed=schluessel, zusatz=zusatz, mark="✅")
titel = [z for z in ics.replace("\r\n ", "").splitlines() if z.startswith("SUMMARY:")]
print("\n--- Titelzeilen ---")
for z in titel:
    print("   ", z)
assert sum(1 for z in titel if "✅" in z) == 1, "Kennzeichen sitzt nicht auf genau einem Flug"
assert any("LH 2004 DUS→MUC ✅" in z for z in titel)
assert any("VL 2001 MUC→DUS" in z and "✅" not in z for z in titel)
assert "A320" in ics, "Flugzeugtyp fehlt"
# Ortszeit -> UTC: 06:15 Ortszeit muss im Feed als UTC-Zeit stehen
erwartet = dt.datetime.fromisoformat(abflug["scheduledTime"]).astimezone(dt.timezone.utc)
assert f"DTSTART:{erwartet.strftime('%Y%m%dT%H%M%SZ')}" in ics, "UTC-Umrechnung stimmt nicht"
assert "VL 2001 MUC→DUS" in ics and "DUS-MUC" in ics and "MUC-DUS" in ics
print("OK: Kennzeichen, Gegenprobe, UTC-Umrechnung und Kategorien stimmen.")

# --- Vortag uebernehmen -----------------------------------------------------
def _ics(*bloecke: str) -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", *bloecke, "END:VCALENDAR", ""])


def _ev(uid: str, start: str, titel: str) -> str:
    return "\r\n".join(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{start}",
                        f"SUMMARY:{titel}", "END:VEVENT"])


def _stamp(tag: dt.date) -> str:
    return tag.strftime("%Y%m%dT120000Z")


gestern, vorwoche = HEUTE - dt.timedelta(days=1), HEUTE - dt.timedelta(days=5)
alt = _ics(
    _ev(f"{gestern}-DUSMUC-LH2004@dus-muc-schedule", _stamp(gestern), "LH 2004 DUS→MUC ✅"),
    _ev(f"{HEUTE}-DUSMUC-LH2006@dus-muc-schedule", _stamp(HEUTE), "LH 2006 DUS→MUC"),
    _ev(f"{vorwoche}-DUSMUC-LH2008@dus-muc-schedule", _stamp(vorwoche), "LH 2008 DUS→MUC"),
)
neu = _ics(_ev(f"{HEUTE}-DUSMUC-LH2006@dus-muc-schedule", _stamp(HEUTE), "LH 2006 DUS→MUC"))
erg, anzahl = m.carry_previous(neu, alt, HEUTE - dt.timedelta(days=1))
print("\nUebernommene Termine:", anzahl)
assert anzahl == 1, "es haette genau der Vortag uebernommen werden muessen"
assert f"{gestern}-DUSMUC-LH2004" in erg and f"{vorwoche}-DUSMUC-LH2008" not in erg
assert erg.count(f"UID:{HEUTE}-DUSMUC-LH2006") == 1 and erg.count("END:VEVENT") == 2
print("OK: Vortag bleibt, Aelteres fliegt raus, nichts doppelt.")
print("\nAlle Tests bestanden.")
