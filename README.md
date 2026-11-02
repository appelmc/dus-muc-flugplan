# DUS ⇄ MUC Flugplan als Kalender-Abo

Öffentlicher iCalendar-Feed (`.ics`) mit den planmäßigen Flugverbindungen
zwischen **Düsseldorf (DUS)** und **München (MUC)**, beide Richtungen.
Der Feed wird täglich automatisch neu erzeugt und über GitHub Pages ausgeliefert.

**Abo-URL (in der Kalender-App eintragen):**

```
https://<benutzer>.github.io/dus-muc-flugplan/dus-muc.ics
```

## Inhalt

- **Flugnummer, Betreiber, Abflug- und Ankunftszeit** in Ortszeit (Europe/Berlin),
  Verkehrstag, Blockzeit
- **Beide Richtungen** DUS → MUC und MUC → DUS in einem Feed, je Termin eine
  Kalenderkategorie (`DUS-MUC` / `MUC-DUS`)
- **Reichweite:** ab heute bis zum Ende des veröffentlichten Flugplans
  (derzeit bis **27.03.2027**, Ende der Wintersaison). Der Sommerflugplan
  erscheint in der Quelle, sobald die Fluggesellschaften ihn veröffentlichen –
  der Feed wächst dann automatisch mit. `--weeks N` begrenzt die Abfrage,
  Standard 52 Wochen.
- **Heute ist enthalten**, einschließlich bereits abgeflogener oder gelandeter
  Flüge des Tages. Der Vortag bleibt zusätzlich einen Tag im Feed stehen, damit
  beim morgendlichen Neuerzeugen kein Loch entsteht.
- **Kennzeichen ✅:** Der Flug steht zusätzlich auf der Flugtafel des
  **Flughafens München** (Zweitquelle, geprüft für die ersten 14 Tage).
  Das ist eine zusätzliche Listung – **keine Buchung, keine Sitzverfügbarkeit,
  keine Durchführungsgarantie.** Alle anderen Termine stammen allein aus dem
  Düsseldorfer Flugplan und sind unmarkiert.

## Quellen

1. **Flug-API des Düsseldorfer Airports** (Hauptquelle)
   `https://www.dus.com/api/sitecore/flightapi/SearchFlightsWithOutParams`
   Öffentlich lesbar, ohne Konto oder Schlüssel. Parameter: `destination=1206`
   (Flughafen München in der DUS-API), `arrival=true|false` für die Richtung,
   `flightStartTime`/`flightEndTime`, `count`/`offset` zum Blättern.
   Je Flug liefert sie `flightNumber`, `adFlag` (`D`/`A`), `flightDate`,
   `scheduledTime`, `destinationLocalTime` und `flightDuration` – daraus ergeben
   sich **beide** Zeiten eines Fluges eindeutig.
2. **Flugtafel des Flughafens München** (Zweitquelle für ✅)
   `https://www.munich-airport.com/flightsearch/arrivals|departures`
   Nur für die Gegenprobe der nahen 14 Tage. Ein Tagesabruf je Richtung;
   die Startseite muss einmal je Sitzung geöffnet werden (Cookiejar), sonst
   antwortet der Endpunkt mit HTTP 500.

## Automatischer Lauf

`.github/workflows/update-feed.yml` baut den Feed täglich (04:23 UTC) und
schreibt `dus-muc.ics` zurück ins Repository; GitHub Pages liefert ihn aus.
Der Workflow prüft vor dem Schreiben die Mindestzahl an Terminen und bricht bei
zu wenigen Events ab, ohne den letzten guten Stand zu überschreiben.

## Selbst ausführen

```bash
python3 test_dus_muc.py                       # Selbsttest, ohne Netz
python3 build_dus_ics.py --out dus-muc.ics    # echter Abruf und Aufbau
```

Optionen: `--weeks N`, `--confirm-days N`, `--marker ZEICHEN`, `--keep-past`,
`--out PFAD`. Nur die Python-Standardbibliothek, keine Installation nötig.

## Kalender abonnieren (iPhone)

Einstellungen → Apps → Kalender → Accounts → Account hinzufügen →
Andere → **Kalenderabo hinzufügen** → die Abo-URL oben eintragen.
Aktualisierung: Einstellungen → Kalender → Accounts → Abo → „Aktualisieren".

## Grenzen

- Die DUS-API ist eine interne Schnittstelle der Website ohne Zusage auf
  Beständigkeit. Ändert sie ihr Format, bricht der Abruf – der Workflow
  überschreibt dann nichts, der letzte gute Feed bleibt stehen.
- Planzeiten können sich ändern; der Feed ist keine Buchungs- oder
  Verfügbarkeitsauskunft.
- Die ✅-Gegenprobe belegt nur, dass ein Flug auf zwei Tafeln erscheint.
  Ob beide Tafeln letztlich dieselbe vorgelagerte Datenquelle nutzen, ist nicht
  geprüft.
- Der Sommerflugplan und alles jenseits des Saisonendes sind erst verfügbar,
  wenn die Quelle sie veröffentlicht.
