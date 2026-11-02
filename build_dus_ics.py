#!/usr/bin/env python3
"""Baut einen iCalendar-Feed (ICS) für die Flugverbindungen DUS <-> MUC.

Quelle: Flug-API des Düsseldorfer Airports (dus.com), ungeschuetzt lesbar:
    https://www.dus.com/api/sitecore/flightapi/SearchFlightsWithOutParams
Gefiltert auf das Ziel-/Herkunftsflugziel München (Airport-Id 1206). Die API
liefert je Flug unter anderem:
    flightNumber      "LH 2004"
    adFlag            "D" = Abflug DUS, "A" = Ankunft DUS
    flightDate        "2027-01-15"
    scheduledTime     ISO-Zeit der jeweiligen Tafel (Abflug bzw. Ankunft)
    destinationLocalTime  Ankunftszeit am Zielort (bei Abfluegen)
    flightDuration    Blockzeit in Minuten
    airline.name      Betreiber
Daraus lassen sich beide Zeiten eines Fluges eindeutig bestimmen - anders als
bei der FMO-Seite, die je Tafel nur eine Zeit herausgibt. Der heutige Tag ist
enthalten, Vorlauf bis zum Ende des veroeffentlichten Flugplans.

Zweitquelle fuer das Kennzeichen ✅: die Flugtafel des Flughafens München
(munich-airport.com). Fluege, die dort fuer denselben Tag stehen, werden
markiert. Das ist eine zusaetzliche Listung, keine Buchung und keine
Durchfuehrungsgarantie.

Aufruf:
    python3 build_dus_ics.py [--out PATH] [--weeks N] [--confirm-days N] [--marker ZEICHEN]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import http.cookiejar
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://www.dus.com/api/sitecore/flightapi/SearchFlightsWithOutParams"
MUC_AIRPORT_ID = 1206          # Flughafen "München" in der DUS-API
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TZ = ZoneInfo("Europe/Berlin")
DEFAULT_WEEKS = 52             # so weit, wie die Quelle veroeffentlicht hat
CONFIRM_DAYS = 14              # gegen die Muenchner Tafel gepruefter Nahbereich
CONFIRMED_MARK = "✅"
SEITE = 1000                   # Datensätze je API-Abruf
MAX_SEITEN = 40                # Sicherung gegen Endlosschleifen beim Blaettern

# Flugtafel des Flughafens Muenchen (Zweitquelle, kein Schluessel noetig).
MUC_BOARD = "https://www.munich-airport.com/flightsearch/{direction}"
MUC_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": "https://www.munich-airport.com/",
    "X-Requested-With": "XMLHttpRequest",
}
MUC_ROW_RE = re.compile(r'<tr[^>]*class="fp-flight-item"[^>]*>(.*?)</tr>', re.S)
MUC_CELL_RE = re.compile(
    r'<td[^>]*class="fp-flight-(airline|airport|number|status|time-muc|time-other|area)"[^>]*>(.*?)</td>',
    re.S)
TAG_RE = re.compile(r"<[^>]+>")
TIME_RE = re.compile(r"(\d{2}:\d{2})")
FLIGHT_RE = re.compile(r"([A-Z0-9]{2})\s?(\d{3,4})")
TAG_NAMEN = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", fragment)).strip()


def flight_number_key(flight: str) -> str:
    """'LH 2004' / 'VL 2004' -> '2004' (Marke ist saisonal austauschbar)."""
    digits = re.sub(r"\D", "", flight or "")
    return digits.lstrip("0") or digits


def airline_kurz(name: str) -> str:
    """Anzeigename der Airline kurz und einheitlich."""
    return (name or "").replace("Lufthansa City Airlines", "Lufthansa City").strip()


def _zeit(iso: str | None) -> str:
    """ISO-Zeitstempel -> 'HH:MM' in Ortszeit (Europe/Berlin)."""
    if not iso:
        return ""
    try:
        return dt.datetime.fromisoformat(iso).astimezone(TZ).strftime("%H:%M")
    except ValueError:
        return ""


def _tag(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return dt.datetime.fromisoformat(iso).astimezone(TZ).date().isoformat()
    except ValueError:
        return ""


def api_seite(start: dt.date, ende: dt.date, arrival: bool, offset: int) -> dict:
    """Ein Seitenabruf der DUS-Flug-API fuer den Zeitraum (je Richtung)."""
    params = {
        "lang": "de",
        "count": SEITE,
        "codeshare": "true",
        "arrival": "true" if arrival else "false",
        "destination": MUC_AIRPORT_ID,
        "flightStartTime": f"{start.isoformat()}T00:00:00",
        "flightEndTime": f"{ende.isoformat()}T23:59:59",
        "showDetails": "false",
        "offset": offset,
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.dus.com/de-de/fliegen/abflug",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        antwort = json.loads(r.read().decode("utf-8", "replace"))
    if antwort.get("hasError"):
        raise RuntimeError(str(antwort.get("data", {}).get("errorMessage") or "API-Fehler"))
    return antwort.get("data") or {}


def zeitraum_abschnitte(start: dt.date, ende: dt.date, tage: int = 60):
    """Zeitraum in handliche Abschnitte teilen (weniger Last je Antwort)."""
    cur = start
    while cur <= ende:
        last = min(cur + dt.timedelta(days=tage - 1), ende)
        yield cur, last
        cur = last + dt.timedelta(days=1)


def fluege_zeitraum(start: dt.date, ende: dt.date) -> list[dict]:
    """Alle FMO/MUC-Fluege im Zeitraum, beide Richtungen, geblaettert."""
    fluege: list[dict] = []
    for a, b in zeitraum_abschnitte(start, ende):
        for arrival in (False, True):
            offset = 0
            for _ in range(MAX_SEITEN):
                daten = api_seite(a, b, arrival, offset)
                blatt = daten.get("flights") or []
                fluege.extend(blatt)
                if not daten.get("more") or not blatt:
                    break
                offset += len(blatt)
                time.sleep(0.4)
            time.sleep(0.4)
    return fluege


def flug_eintrag(f: dict) -> dict | None:
    """API-Datensatz -> Termin-Datensatz mit beiden Ortszeiten."""
    nummer = (f.get("flightNumber") or "").strip()
    if not FLIGHT_RE.match(nummer.replace("  ", " ")):
        return None
    tag = f.get("flightDate") or _tag(f.get("scheduledTime"))
    if not tag:
        return None
    abflug_dus = f.get("adFlag") == "D"
    zeit = _zeit(f.get("scheduledTime"))
    if not zeit:
        return None
    dauer = f.get("flightDuration")
    dauer = int(dauer) if isinstance(dauer, (int, float)) and 20 <= int(dauer) <= 300 else None

    if abflug_dus:
        dep, arr = zeit, _zeit(f.get("destinationLocalTime"))
        if not arr and dauer:
            arr = (dt.datetime.strptime(zeit, "%H:%M") + dt.timedelta(minutes=dauer)).strftime("%H:%M")
    else:
        arr, dep = zeit, ""
        if dauer:
            dep = (dt.datetime.strptime(zeit, "%H:%M") - dt.timedelta(minutes=dauer)).strftime("%H:%M")
    return {
        "date": tag,
        "dow": TAG_NAMEN[dt.date.fromisoformat(tag).weekday()],
        "dep": dep,
        "arr": arr,
        "airline": airline_kurz((f.get("airline") or {}).get("name", "")),
        "flight": nummer,
        "from": "DUS" if abflug_dus else "MUC",
        "to": "MUC" if abflug_dus else "DUS",
    }


def collect_flights(weeks: float = DEFAULT_WEEKS) -> list[dict]:
    """Flugplan ab heute fuer die angegebene Reichweite."""
    heute = dt.datetime.now(TZ).date()
    ende = heute + dt.timedelta(days=int(round(weeks * 7)))
    roh = fluege_zeitraum(heute, ende)
    fluege = [e for e in (flug_eintrag(f) for f in roh) if e]
    if not fluege:
        raise SystemExit("FEHLER: keine Flugdaten von der DUS-API erhalten.")
    # Dubletten (Blaettern/Abschnittsgrenzen) entfernen
    return list({(f["date"], f["from"], f["to"], f["flight"]): f for f in fluege}.values())


# --- Gegenprobe gegen die Flugtafel des Flughafens Muenchen -----------------

def muc_opener() -> urllib.request.OpenerDirector:
    """Sitzung fuer die Muenchner Tafel: ohne Cookie der Startseite antwortet
    der Tages-Endpunkt mit HTTP 500."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(urllib.request.Request("https://www.munich-airport.com/", headers=MUC_HEADERS),
                 timeout=60):
        pass
    return op


def muc_day(direction: str, day: dt.date, opener: urllib.request.OpenerDirector) -> str:
    """Eine Tagesansicht der Muenchner Flugtafel ('arrivals'/'departures')."""
    tag = day.isoformat()
    url = (MUC_BOARD.format(direction=direction)
           + f"?from={tag}T00:00:00&allow_scroll_back=1&allow_pagination=1"
           + f"&min_date={tag}T00:00:00&max_date={tag}T23:59:59&per_page=2000&page=0")
    req = urllib.request.Request(url, headers=MUC_HEADERS)
    try:
        with opener.open(req, timeout=120) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:                                   # noqa: BLE001
        with muc_opener().open(req, timeout=120) as r:   # Sitzung neu aufbauen
            return r.read().decode("utf-8", "replace")


def parse_muc_board(seite: str, farbcode: str = "DUS") -> list[dict]:
    """Fluege mit Ziel/Herkunft des anderen Flughafens aus der Muenchner Tafel."""
    out = []
    for block in MUC_ROW_RE.findall(seite):
        if f"({farbcode})" not in block:
            continue
        zellen: dict[str, str] = {}
        for name, fragment in MUC_CELL_RE.findall(block):
            zellen.setdefault(name, _text(fragment))
        nummer_text = zellen.get("number", "")
        nummer = FLIGHT_RE.search(nummer_text)
        muster = re.search(r"\(([A-Z0-9]{3,4})\)", nummer_text)
        zeiten = TIME_RE.findall(zellen.get("time-muc", "")) + TIME_RE.findall(zellen.get("time-other", ""))
        out.append({
            "flight": f"{nummer.group(1)} {nummer.group(2)}" if nummer else "",
            "aircraft": muster.group(1) if muster else "",
            "times": zeiten,
        })
    return out


def confirm_against_muc(flights: list[dict], days: int) -> tuple[set, dict, str]:
    """Gegenprobe: Fluege, die auch auf der Muenchner Tafel stehen.

    Gibt (Schluessel, Zusatzinfos, Hinweis) zurueck. Bei Fehlern bleibt die
    Menge leer - es wird nie ein Flug faelschlich markiert.
    """
    if days <= 0:
        return set(), {}, "Stufe abgeschaltet"
    heute = dt.datetime.now(TZ).date()
    grenze = heute + dt.timedelta(days=days - 1)     # genau `days` Kalendertage
    tage = sorted({f["date"] for f in flights})
    tage = [t for t in tage if heute <= dt.date.fromisoformat(t) <= grenze]
    bestaetigt: set = set()
    zusatz: dict = {}
    fehler: list[str] = []
    try:
        opener = muc_opener()
    except Exception as exc:                            # noqa: BLE001
        return set(), {}, f"Muenchner Tafel nicht erreichbar ({exc})"
    for tag in tage:
        d = dt.date.fromisoformat(tag)
        for direction, von, nach in (("arrivals", "DUS", "MUC"), ("departures", "MUC", "DUS")):
            try:
                for z in parse_muc_board(muc_day(direction, d, opener)):
                    num = flight_number_key(z["flight"])
                    if not num:
                        continue
                    key = (tag, von, nach, num)
                    bestaetigt.add(key)
                    zusatz[key] = {"aircraft": z["aircraft"], "times": z["times"]}
            except Exception as exc:                    # noqa: BLE001
                fehler.append(f"{direction} {tag}: {exc}")
            time.sleep(0.4)
    return bestaetigt, zusatz, ("; ".join(fehler) if fehler else "")


# --- ICS-Aufbau -------------------------------------------------------------

def local_to_utc(date_str: str, hhmm: str) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in date_str.split("-"))
    return dt.datetime(y, mo, d, h, m, tzinfo=TZ).astimezone(dt.timezone.utc)


def ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            chunks.append(cur)
            cur = b""
        cur += b
    chunks.append(cur)
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def _zeitspanne(f: dict, block: int) -> tuple[dt.datetime, dt.datetime]:
    if f["dep"]:
        start = local_to_utc(f["date"], f["dep"])
    else:
        start = local_to_utc(f["date"], f["arr"]) - dt.timedelta(minutes=block)
    if f["arr"]:
        ende = local_to_utc(f["date"], f["arr"])
    else:
        ende = start + dt.timedelta(minutes=block)
    if ende <= start:                      # Ankunft nach Mitternacht
        ende += dt.timedelta(days=1)
    return start, ende


def build_ics(flights: list[dict], keep_past: bool = False,
              confirmed: set | None = None, zusatz: dict | None = None,
              mark: str = CONFIRMED_MARK) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(TZ).date()
    events: list[str] = []
    seen: set = set()

    # uebliche Blockzeit als Rueckfall, falls eine Quelle keine Dauer liefert
    dauern = []
    for f in flights:
        if f.get("dep") and f.get("arr"):
            m = int((local_to_utc(f["date"], f["arr"]) - local_to_utc(f["date"], f["dep"])).total_seconds() // 60)
            if 20 <= m <= 240:
                dauern.append(m)
    dauern.sort()
    block = dauern[len(dauern) // 2] if dauern else 75

    for f in sorted(flights, key=lambda x: (x["date"], x["dep"] or x["arr"], x["flight"])):
        if not keep_past and dt.date.fromisoformat(f["date"]) < today:
            continue
        uid_key = f'{f["date"]}-{f["from"]}{f["to"]}-{f["flight"].replace(" ", "")}'
        if uid_key in seen:
            continue
        seen.add(uid_key)
        key = (f["date"], f["from"], f["to"], flight_number_key(f["flight"]))
        ist_bestaetigt = bool(confirmed) and key in confirmed
        info = (zusatz or {}).get(key, {})

        start, ende = _zeitspanne(f, block)
        abflug = (f'{f["dep"]} Uhr' if f["dep"] else
                  f'ca. {start.astimezone(TZ).strftime("%H:%M")} Uhr (abgeleitet)')
        ankunft = (f'{f["arr"]} Uhr' if f["arr"] else
                   f'ca. {ende.astimezone(TZ).strftime("%H:%M")} Uhr (abgeleitet)')
        dauer = int((ende - start).total_seconds() // 60)
        uid = f"{uid_key}@dus-muc-schedule"
        summary = f'{f["flight"]} {f["from"]}→{f["to"]}' + (f" {mark}" if ist_bestaetigt else "")
        bestaetigung = ""
        if ist_bestaetigt:
            muster = (f', Flugzeugtyp laut Münchener Tafel: {info["aircraft"]}'
                      if info.get("aircraft") else "")
            bestaetigung = (f"Gegenprobe: steht auch auf der Flugtafel des Flughafens München "
                            f"(Stand {now.strftime('%d.%m.%Y')}){muster}.\n")
        desc = (
            f'{f["airline"]} · {f["flight"]} · {f["from"]} → {f["to"]}\n'
            f'Abflug {abflug}, Ankunft {ankunft} (jeweils Ortszeit, Blockzeit {dauer} Minuten)\n'
            f'Verkehrstag: {f["dow"]}\n'
            f"{bestaetigung}"
            f"Quelle: Flug-API des Düsseldorfer Airports (dus.com), Stand {now.strftime('%d.%m.%Y %H:%M')} UTC.\n"
            "Planmäßiger Flug – Zeiten und Durchführung können sich ändern. Keine Buchung, keine Verfügbarkeit."
        )
        events.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{ende.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{ics_escape(desc)}",
            f"LOCATION:{ics_escape('Flughafen Düsseldorf (DUS)' if f['from'] == 'DUS' else 'Flughafen München (MUC)')}",
            f"CATEGORIES:{'DUS-MUC' if f['from'] == 'DUS' else 'MUC-DUS'}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hermes-agent//dus-muc-flight-schedule//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:DUS ⇄ MUC Flugplan",
        "X-WR-CALDESC:Planmäßige Flugverbindungen Düsseldorf (DUS) ⇄ München (MUC), beide Richtungen. Quelle: Flug-API des Düsseldorfer Airports, täglich neu erzeugt. Termine mit ✅ stehen zusätzlich auf der Flugtafel des Flughafens München. Planzeiten ohne Gewähr – keine Buchung, keine Verfügbarkeit.",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    body = [fold(line) for line in events]
    return "\r\n".join(header + body + ["END:VCALENDAR", ""])


def _unfold(ics: str) -> str:
    return ics.replace("\r\n ", "").replace("\n ", "")


def vevent_blocks(ics: str) -> list[str]:
    return re.findall(r"BEGIN:VEVENT.*?END:VEVENT", _unfold(ics), re.S)


def carry_previous(neu: str, alt: str, ab: dt.date) -> tuple[str, int]:
    """Termine des Vortags aus dem bisherigen Feed uebernehmen.

    Ohne diesen Schritt faellt der heutige Tag beim taeglichen Neuaufbau aus dem
    Kalender, wenn die Quelle ihn (noch) nicht fuehrt. Der alte Stand bleibt so
    lange stehen, bis er aelter als einen Tag ist.
    """
    vorhanden = {m for block in vevent_blocks(neu) for m in re.findall(r"UID:(\S+)", block)}
    uebernommen: list[str] = []
    for block in vevent_blocks(alt):
        uid = re.search(r"UID:(\S+)", block)
        if not uid or uid.group(1) in vorhanden:
            continue
        kennung = uid.group(1)
        treffer = re.match(r"(\d{4}-\d{2}-\d{2})-", kennung)
        if treffer:
            tag = dt.date.fromisoformat(treffer.group(1))
        else:
            start = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})", block)
            if not start:
                continue
            tag = dt.date(int(start.group(1)[:4]), int(start.group(1)[4:6]), int(start.group(1)[6:8]))
        if tag < ab:
            continue
        vorhanden.add(kennung)
        uebernommen.append(block)
    if not uebernommen:
        return neu, 0
    zeilen = [fold(zeile) for block in uebernommen for zeile in block.splitlines()]
    return neu.replace("END:VCALENDAR", "\r\n".join(zeilen) + "\r\nEND:VCALENDAR", 1), len(uebernommen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dus-muc.ics")
    ap.add_argument("--weeks", type=float, default=DEFAULT_WEEKS,
                    help=f"gewuenschte Reichweite ab heute in Wochen (Standard {DEFAULT_WEEKS})")
    ap.add_argument("--confirm-days", type=int, default=CONFIRM_DAYS,
                    help=f"Tage gegen die Muenchener Tafel pruefen (Standard {CONFIRM_DAYS})")
    ap.add_argument("--marker", default=CONFIRMED_MARK,
                    help=f"Kennzeichen fuer bestaetigte Fluege (Standard {CONFIRMED_MARK!r})")
    ap.add_argument("--keep-past", action="store_true")
    args = ap.parse_args()

    flights = collect_flights(args.weeks)
    tage = sorted({f["date"] for f in flights})
    airlines = collections.Counter(f["airline"] for f in flights)
    print(f"Fluege von der DUS-API: {len(flights)}  Zeitraum: {tage[0]} .. {tage[-1]}")
    print("  Richtungen:", dict(collections.Counter(f"{f['from']}->{f['to']}" for f in flights)))
    print("  Betreiber:", dict(airlines))

    confirmed, zusatz, hinweis = confirm_against_muc(flights, args.confirm_days)
    print(f"Gegenprobe Muenchen: {len(confirmed)} Fluege der Muenchner Tafel im "
          f"{args.confirm_days}-Tage-Fenster." + (f" Hinweis: {hinweis}" if hinweis else ""))

    ics = build_ics(flights, keep_past=args.keep_past, confirmed=confirmed, zusatz=zusatz,
                    mark=args.marker)

    ziel = pathlib.Path(args.out)
    if ziel.exists():
        ics, uebernommen = carry_previous(ics, ziel.read_text(encoding="utf-8"),
                                          dt.datetime.now(TZ).date() - dt.timedelta(days=1))
        if uebernommen:
            print(f"Vortag uebernommen: {uebernommen} Termine aus dem bisherigen Feed "
                  "(aeltere werden verworfen).")

    ziel.write_text(ics, encoding="utf-8", newline="")
    markiert = sum(1 for z in ics.replace("\r\n ", "").splitlines() if z.startswith("SUMMARY:") and args.marker in z)
    print(f"Events im Feed: {ics.count('BEGIN:VEVENT')}  davon markiert: {markiert}  -> {args.out} "
          f"({len(ics.encode())} bytes, sha256 {hashlib.sha256(ics.encode()).hexdigest()[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
