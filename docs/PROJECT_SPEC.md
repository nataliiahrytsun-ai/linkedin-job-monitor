# Task: LinkedIn Job Monitoring für Kunden und Supplier

## Ziel

Implementiere eine kompakte interne Anwendung, mit der öffentlich sichtbare LinkedIn-Stellenausschreibungen unserer Kunden und Supplier automatisiert abgerufen, strukturiert gespeichert und über eine einfache Benutzeroberfläche angezeigt werden können.

Als primäres Scraping-Framework muss das folgende Repository verwendet werden:

https://github.com/D4Vinci/Scrapling

Scrapling unterstützt unter anderem asynchrone Crawls, Browser-basierte Fetcher, adaptive Selektoren, parallele Requests und Streaming von Ergebnissen. Nutze diese Funktionen gezielt, ohne unnötige Komplexität einzuführen.

## Beispielquelle

Als erste Referenz und Testquelle soll folgende öffentliche LinkedIn-Jobseite verwendet werden:

https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691%2C30242966&trk=job-results_see-all-jobs-link&currentJobId=4434981246&position=39&pageNum=0

Die Implementierung darf jedoch nicht fest auf Acuity Analytics zugeschnitten sein. Weitere Kunden und Supplier müssen über ihre jeweilige LinkedIn-Job-URL ergänzt werden können.

## Aktueller Implementierungsstand (2026-08-11)

Diese Spezifikation bewahrt das ursprüngliche LinkedIn-Ziel und die damaligen
Akzeptanzkriterien. Der aktuell abgeschlossene Produktionsumfang ist jedoch
source-neutral und verwendet **Lever** als ersten freigegebenen externen
Source:

- `lever` ist als Production Adapter registriert und in den Company-Formularen
  auswählbar;
- `fixture` bleibt als interner Offline-/Test-Adapter registriert, ist aber
  keine auswählbare Production Source;
- Normalisierung, Persistenz, Reconciliation, `ScrapeRun`, Background
  Execution und UI sind source-neutral;
- Company Management, Jobs UI, Dashboard, ScrapeRun History und read-only
  Status-Polling sind für den aktuellen Lever-Umfang abgeschlossen;
- die mehrseitige Lever-Verarbeitung ist offline über den vollständigen
  Production Flow mit zwei Requests, drei persistierten Jobs und einem
  erfolgreichen `ScrapeRun` verifiziert.

**LinkedIn ist nicht als Production Source implementiert.** Es gibt keinen
Production Adapter, keinen Registry Key und keine auswählbare LinkedIn-Option
im Company-Formular. Die nachfolgenden LinkedIn-Anforderungen und
Diagnoseabschnitte bleiben als ursprüngliches Produktziel und historische
Machbarkeitsnachweise erhalten. Eine Production Integration benötigt eine
separate Entscheidung zu Zugriff, Zulässigkeit und technischer Machbarkeit.

Der verbindliche aktuelle Status steht in
[`docs/MILESTONES.md`](MILESTONES.md); reproduzierbare Prüfungen stehen in
[`docs/BACKEND_VERIFICATION.md`](BACKEND_VERIFICATION.md).

# Funktionale Anforderungen

## 1. Unternehmen verwalten

In der Anwendung muss eine Liste der zu überwachenden Unternehmen gepflegt werden können.

Pro Unternehmen werden mindestens folgende Informationen gespeichert:

- Unternehmensname
- LinkedIn-Job-URL
- Unternehmenstyp:
  - Kunde
  - Supplier
  - Sonstige
- Aktiv oder inaktiv
- Zeitpunkt des letzten Scraping-Laufs
- Status des letzten Scraping-Laufs
- Anzahl der zuletzt gefundenen Jobs

Folgende Aktionen müssen möglich sein:

- Unternehmen hinzufügen
- Unternehmen bearbeiten
- Unternehmen deaktivieren
- Scraping für ein einzelnes Unternehmen starten
- Scraping für alle aktiven Unternehmen starten

Eine physische Löschung ist für den ersten Stand nicht zwingend erforderlich. Eine Deaktivierung reicht aus.

## 2. LinkedIn-Jobs abrufen

Für jedes aktive Unternehmen sollen alle öffentlich erreichbaren Stellenausschreibungen erfasst werden.

Pro Stellenausschreibung sollen, soweit auf der öffentlichen Seite verfügbar, mindestens folgende Attribute extrahiert werden:

- LinkedIn Job ID
- Position beziehungsweise Jobtitel
- Unternehmen
- Land
- Stadt oder Standort
- Vollständiger Standorttext
- Remote-, Hybrid- oder Onsite-Kennzeichnung
- Beschäftigungsart
- Senioritätslevel
- Tätigkeitsbereich beziehungsweise Job Function
- Branche
- Veröffentlichungsdatum
- Zeitpunkt der Erfassung
- Beschreibung
- Direkter Link zur Stellenausschreibung
- Status:
  - Aktiv
  - Nicht mehr gefunden
  - Geschlossen, falls erkennbar
- Hash oder vergleichbarer technischer Fingerprint zur Änderungserkennung

Nicht jedes Feld ist bei jeder Stellenausschreibung verfügbar. Fehlende Werte müssen als `null` gespeichert werden und dürfen keinen Fehler verursachen.

## 3. Pagination und vollständige Ergebnismenge

Der Scraper darf nicht nur die erste sichtbare Seite auslesen.

Er muss:

- alle öffentlich erreichbaren Ergebnisse durchlaufen
- Pagination beziehungsweise Lazy Loading berücksichtigen
- Jobdetailseiten abrufen, wenn die Jobbeschreibung nicht vollständig in der Ergebnisliste enthalten ist
- doppelte Jobs über die LinkedIn Job ID oder einen stabilen Fallback-Schlüssel erkennen
- den Lauf beenden, wenn keine weiteren neuen Ergebnisse vorhanden sind
- Endlosschleifen durch eine maximale Seiten- beziehungsweise Request-Grenze verhindern

Die maximale Anzahl von Seiten und Requests muss konfigurierbar sein.

## 4. Speicherung und Aktualisierung

Die Daten sollen zunächst in einer lokalen SQLite-Datenbank gespeichert werden.

Beim erneuten Scraping:

- bestehende Jobs dürfen nicht doppelt angelegt werden
- geänderte Informationen müssen aktualisiert werden
- `last_seen_at` muss aktualisiert werden
- neue Jobs müssen angelegt werden
- nicht mehr gefundene Jobs dürfen nicht sofort gelöscht werden
- Jobs, die über mehrere erfolgreiche Läufe nicht mehr gefunden wurden, sollen als inaktiv markiert werden

Empfohlene Kernfelder:

### Company

- `id`
- `name`
- `linkedin_jobs_url`
- `company_type`
- `is_active`
- `created_at`
- `updated_at`
- `last_scraped_at`
- `last_scrape_status`

### JobPosting

- `id`
- `company_id`
- `linkedin_job_id`
- `title`
- `country`
- `city`
- `location`
- `workplace_type`
- `employment_type`
- `seniority_level`
- `job_function`
- `industry`
- `published_at`
- `description`
- `job_url`
- `content_hash`
- `status`
- `first_seen_at`
- `last_seen_at`
- `created_at`
- `updated_at`

### ScrapeRun

- `id`
- `company_id`
- `started_at`
- `finished_at`
- `status`
- `jobs_found`
- `jobs_created`
- `jobs_updated`
- `requests_made`
- `duration_seconds`
- `error_message`

## 5. Einfache Benutzeroberfläche

Implementiere eine funktionale, übersichtliche interne UI.

### Dashboard

Das Dashboard zeigt:

- Anzahl überwachter Unternehmen
- Anzahl aktiver Jobs
- Anzahl neu gefundener Jobs im letzten Lauf
- Zeitpunkt des letzten erfolgreichen Laufs
- aktuell laufende Scraping-Prozesse
- fehlgeschlagene Läufe

### Unternehmensansicht

Pro Unternehmen sollen angezeigt werden:

- Unternehmensinformationen
- LinkedIn-Job-URL
- letzter Scraping-Status
- Zeitpunkt des letzten Laufs
- Anzahl aktiver Jobs
- Schaltfläche `Jobs aktualisieren`
- Tabelle der gefundenen Jobs

### Jobübersicht

Die Jobübersicht benötigt mindestens folgende Filter:

- Unternehmen
- Unternehmenstyp
- Land
- Standort
- Jobtitel beziehungsweise Freitextsuche
- Status
- Veröffentlichungszeitraum
- Remote, Hybrid oder Onsite

Die Tabelle zeigt mindestens:

- Position
- Unternehmen
- Standort
- Land
- Veröffentlichungsdatum
- Erfassungsdatum
- Status
- Link zur LinkedIn-Stellenausschreibung

### Jobdetailansicht

Die Detailansicht zeigt alle gespeicherten Informationen und die vollständige Jobbeschreibung.

## 6. Scraping-Performance

Der Scraping-Prozess soll möglichst schnell sein, ohne unkontrolliert Requests an LinkedIn zu senden.

Verwende nach Möglichkeit:

- asynchrone Verarbeitung
- begrenzte Parallelität
- Connection- und Browser-Session-Wiederverwendung
- konfigurierbare Timeouts
- konfigurierbare Request Delays
- Retry-Mechanismus mit exponentiellem Backoff
- Request-Deduplizierung
- Batch-Schreibvorgänge in die Datenbank
- Wiederverwendung bereits geladener Jobdaten, sofern keine Änderung erkennbar ist

Scrapling stellt hierfür unter anderem Spider-basierte Crawls, konfigurierbare Parallelität, Domain-Throttling, Sessions, Pause-und-Resume-Funktionalität sowie detaillierte Laufstatistiken bereit.

Das Scraping darf die UI nicht blockieren. Der aktuelle Status muss über Polling oder eine vergleichbar einfache Lösung angezeigt werden.

Für den ersten Stand ist kein komplexes Queue-System erforderlich. Eine saubere Hintergrundausführung über einen Worker-Thread oder separaten Prozess ist ausreichend, sofern parallele Läufe kontrolliert werden.

## 7. Fehlerbehandlung

Folgende Fehlerfälle müssen sauber behandelt werden:

- LinkedIn-Seite nicht erreichbar
- HTTP-Timeout
- geänderte HTML-Struktur
- keine Jobs gefunden
- unvollständige Jobdetailseite
- ungültige LinkedIn-URL
- doppelter Scraping-Lauf für dasselbe Unternehmen
- einzelne Jobseite schlägt fehl
- gesamter Lauf schlägt fehl
- Datenbankfehler

Ein einzelner fehlerhafter Job darf nicht den gesamten Lauf abbrechen.

Fehler müssen in `ScrapeRun` gespeichert und über die UI nachvollziehbar angezeigt werden.

## 8. Rechtliche und technische Einschränkungen

Die Implementierung darf ausschließlich öffentlich zugängliche Inhalte verarbeiten.

Nicht implementieren:

- LinkedIn-Login-Automatisierung
- Verwendung privater LinkedIn-Accounts
- Speicherung von LinkedIn-Cookies oder Sessions angemeldeter Benutzer
- CAPTCHA-Umgehung
- bewusste Umgehung von Zugriffsbeschränkungen
- Scraping persönlicher Profile
- Erfassung nicht öffentlich sichtbarer Daten
- aggressive Proxy-Rotation zur Umgehung von Sperren

Robots.txt, Nutzungsbedingungen, Datenschutzanforderungen und angemessene Request-Raten müssen berücksichtigt werden. Auch das Scrapling-Projekt weist ausdrücklich darauf hin, geltende Gesetze, Website-Nutzungsbedingungen und robots.txt zu respektieren.

### Begrenzte Ausnahme für den Pagination-Spike

Nur für den lokalen technischen Pagination-Spike darf ein manuell bestätigter
Diagnoselauf wenige gewöhnliche, nicht authentifizierte Requests an öffentlich
sichtbare LinkedIn-Joblistenseiten senden. Der Lauf ist ausschließlich mit dem
expliziten Flag `--confirm-live-test` zulässig.

`robots.txt` muss weiterhin vor dem Test geprüft und das Ergebnis im
Diagnosebericht aufgezeichnet werden. Ein Ergebnis `Disallow: /` ist eine
Warnung und Einschränkung für den gewöhnlichen Betrieb, blockiert aber diesen
einzelnen, ausdrücklich bestätigten Diagnosetest nicht.

Für diesen Test gelten zwingend folgende Grenzen:

- maximal 4 Joblistenseiten und maximal 4 Target-Page-Requests;
- sequenzielle Ausführung mit mindestens 2 Sekunden Pause zwischen Requests;
- keine Anmeldung, Cookies, Proxies, IP-Wechsel, Stealth-Funktionen,
  Browser-Fetcher, Impersonation oder Retries;
- keine Requests an Jobdetailseiten;
- keine Speicherung vollständiger LinkedIn-HTML-Antworten;
- Abbruch, sobald die Pagination ausreichend bestätigt ist, kein bestätigter
  Next-Page-Link oder keine neue Job-ID vorliegt oder URL beziehungsweise Inhalt
  wiederholt wird.

Bei HTTP 401, 403 oder 429, Redirect auf Login, Authwall oder Checkpoint,
CAPTCHA, Access Denied, Consent/Interstitial oder jeder anderen technischen
Blockierung muss der Test sofort und ohne weiteren Request oder alternativen
Fortsetzungsweg enden.

Diese Ausnahme gilt nur für den begrenzten lokalen Pagination-Spike. Sie erlaubt
weder vollständiges oder serverseitiges Scraping noch einen Production-Betrieb
oder die Umgehung von LinkedIn-Beschränkungen. Production erfordert eine eigene
Entscheidung des Teams.

#### Pagination-Diagnoseläufe, Parser-Fix und finaler Validierungslauf

Der erste begrenzte Live-Pagination-Lauf wurde mit exakt dieser URL ausgeführt:
`https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691%2C30242966`.
Der aktuelle Robots-Preflight stellte genau einen Request an `robots.txt`,
erhielt HTTP 200, zeichnete `target_allowed=false` auf und rief die Target-Seite
nicht ab. Der mit `--confirm-live-test` bestätigte Live-Runner stellte danach
genau einen Target-Request, erhielt HTTP 200 ohne Redirects, fand keine Job-IDs,
stellte keinen weiteren Request und zeichnete `stop_reason="captcha"` auf.

Das Ergebnis ist **Inconclusive** — the response was classified as CAPTCHA by
the previous broad raw-HTML marker check, but the saved report does not contain
enough evidence to confirm that a CAPTCHA was actually presented. Der frühere
fehlerhafte Klassifikator wertete `captcha` oder `security verification` an
beliebiger Stelle im Raw HTML als CAPTCHA-Signal; das konnte auch JavaScript,
Metadaten, Resource-URLs oder versteckten Text betreffen. Commit
`7613ef9d8bdcc8ac252047d61d7aa46edd2d4318` ersetzte die
Raw-Substring-Suche durch eine strukturelle CAPTCHA-Diagnostik und ergänzte die
sicheren Berichtsfelder `block_reason` und `block_evidence`. Die reale
LinkedIn-Pagination bleibt **Not verified**.

Der korrigierende Live-Lauf ist abgeschlossen. Er stellte genau einen
Target-Request, erhielt HTTP 200 ohne Redirects und zeichnete `pages=1`,
`requests=1`, `found_job_ids=[]`, `stop_reason="no_new_job_ids"`,
`block_reason=null` und `block_evidence=null` auf. Ein weiterer Target-Request
wurde nicht gestellt. Dieser Lauf bestätigte die Pagination nicht.

Die anschließende Offline-Diagnose eines realen rendered DOM-Fragments fand
einen konkreten Parser-Fehler. Das frühere `extract_job_cards` begann nur bei
`li.jobs-search-results__list-item` oder `li.job-card-container`. Das untersuchte
Fragment enthielt stattdessen einen
`a.base-card__full-link[href*="/jobs/view/"]`. Die URL lieferte korrekt die
Job-ID `4447661197`, und der Titel `Delivery Manager` stand in `span.sr-only`;
der alte äußere Selektor ließ den Code diese Verlinkung jedoch nie verarbeiten.

Commit `b852de18d195df795bbfcc28c7b573b164702853` ergänzte einen validierten
Fallback für LinkedIn-Job-Links, Unterstützung regionaler LinkedIn-Subdomains,
Titel-Fallbacks über `sr-only`, `aria-label` und Link-Text sowie Deduplizierung
nach Job-ID. Das reale manuelle DOM-Fragment wird offline jetzt als genau eine
Karte mit Job-ID `4447661197` und Titel `Delivery Manager` extrahiert. Die
vollständige Testsuite bestand mit 60 Tests; Ruff und MyPy strict bestanden
ebenfalls.

Der finale begrenzte Plain-HTTP-Extraktionslauf nach diesem Fix ist
abgeschlossen. Er erhielt HTTP 200 ohne Redirects und extrahierte 60 eindeutige
LinkedIn-Job-IDs einschließlich `4447661197`; der Lauf endete mit
`stop_reason="no_next_page"`. Die Live-Extraktion war **Verified**, die
vollständige Live-Pagination blieb zu diesem Zeitpunkt **Not verified**. Der kanonische Abschluss
steht in
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](diagnostics/linkedin-pagination-2026-08-05.md).

Commit `5096e5a901220149916685660fdf1cba50c1231d` implementiert validierte
`seeMoreJobPostings`-Continuation-URLs und die mit synthetischen Offline-Tests
geprüfte Behandlung überlappender Batches. Zwei aufeinanderfolgende reine
Overlap-Batches sind zulässig, ein Batch mit einer neuen Job-ID setzt den
Zähler zurück, und der dritte aufeinanderfolgende Overlap beendet den Lauf mit
`overlap_limit`; die harte Grenze bleibt bei 4 Target-Requests und 4 Seiten.

Im Rahmen der bestehenden Teamanweisung, die Pagination lokal mit nur wenigen
Requests zu testen, wurde genau ein begrenzter Post-Fix-Validierungslauf für
diese Implementierung abgeschlossen. Nach einem frischen Robots-Preflight
verwendete er dieselbe exakte Target-URL, `--confirm-live-test`,
`--continuation-start 25` und `--continuation-step 25` sowie alle bestehenden
Sicherheitsgrenzen. Der Lauf stellte 4 Target-Requests für 4 Seiten, verwendete
die Offsets 25, 50 und 75, erhielt jeweils HTTP 200 ohne Redirects und erweiterte
die gespeicherte 60-ID-Ausgangsbasis zu einer global deduplizierten Menge von 82
IDs. Er endete mit `page_limit`, `block_reason=null` und
`block_evidence=null`.

Die vollständige Live-Pagination ist für diesen begrenzten Diagnoselauf
**Verified**, weil der Guest-Endpoint 22 IDs außerhalb der gespeicherten
Ausgangsbasis lieferte, ohne Blockierung oder Überschreitung der Grenzen. Daraus
folgt nicht, dass alle angezeigten Stellen erfasst wurden. Weitere Live-Läufe,
Production Scraping und ein vollständiger serverseitiger Scrape sind nicht
zulässig.

Außerhalb dieser Ausnahme gilt weiterhin: Wenn LinkedIn den öffentlichen Zugriff
technisch blockiert, muss der Lauf kontrolliert fehlschlagen und einen
verständlichen Fehlerstatus liefern. Es soll keine Umgehung implementiert werden.

# Technische Anforderungen

## Empfohlener Stack

- Python 3.12
- Scrapling
- Django
- Django Templates
- HTMX für kleinere dynamische UI-Interaktionen
- SQLite
- Pytest oder Django Test Framework
- Ruff
- MyPy oder Pyright, soweit sinnvoll
- Docker optional, aber empfohlen

FastAPI und ein separates JavaScript-Frontend sind für diesen Umfang nicht notwendig.

## Architektur

Trenne die Anwendung mindestens in folgende Bereiche:

    app/
    ├── companies/
    │   ├── models.py
    │   ├── forms.py
    │   ├── views.py
    │   └── services.py
    ├── jobs/
    │   ├── models.py
    │   ├── views.py
    │   ├── filters.py
    │   └── services.py
    ├── scraping/
    │   ├── linkedin_spider.py
    │   ├── extractors.py
    │   ├── normalizers.py
    │   ├── persistence.py
    │   ├── runner.py
    │   ├── exceptions.py
    │   └── selectors.py
    ├── scrape_runs/
    │   ├── models.py
    │   └── views.py
    └── templates/

Die Selektoren dürfen nicht unstrukturiert über den Code verteilt sein. Lege sie zentral ab und dokumentiere ihre Bedeutung.

Scraping, Normalisierung, Persistierung und UI müssen voneinander getrennt sein.

## Scrapling-Verwendung

Bevor die eigentliche Implementierung beginnt, muss das Scrapling Repository analysiert werden.

Prüfe insbesondere:

- verfügbare Fetcher
- Unterschiede zwischen HTTP-, Dynamic- und Stealth-Fetching
- Spider-API
- asynchrone Verarbeitung
- Session-Wiederverwendung
- Concurrency und Throttling
- Request- und Response-Objekte
- CSS- und XPath-Selektion
- adaptive Selektoren
- Retry- und Blocking-Verhalten
- Laufstatistiken
- Streaming
- Pause und Resume
- Testmöglichkeiten
- Browser-Abhängigkeiten
- Docker-Unterstützung

Scrapling bietet mehrere Fetcher-Typen für normale HTTP-Anfragen und Browser-basierte Seiten, sowie CSS-, XPath- und textbasierte Selektionsmöglichkeiten. Wähle den einfachsten Fetcher, der für die öffentliche LinkedIn-Seite zuverlässig funktioniert.

Verwende keine Funktionen ausschließlich deshalb, weil sie verfügbar sind. Jede technische Entscheidung muss zum Anwendungsfall passen.

# Pflichtdokumentation

Erstelle vor oder parallel zur Implementierung folgende Datei:

    docs/SCRAPLING_GUIDE.md

Diese Dokumentation muss speziell für dieses Projekt geschrieben werden und mindestens folgende Punkte enthalten:

## 1. Überblick

- Was ist Scrapling?
- Warum verwenden wir es für diesen Anwendungsfall?
- Welche Teile von Scrapling werden tatsächlich eingesetzt?
- Welche Teile werden bewusst nicht eingesetzt?

## 2. Installation

- Python-Abhängigkeiten
- optionale Extras
- Browser-Abhängigkeiten
- Installationsbefehle
- lokale Einrichtung
- Docker-Einrichtung, falls verwendet

Nach der Installation bestimmter Scrapling-Extras müssen die benötigten Browser-Komponenten separat über den vorgesehenen Installationsbefehl installiert werden.

## 3. Architektur von Scrapling

Erkläre:

- Fetcher
- Spider
- Request
- Response
- Selector
- Sessions
- Concurrency
- adaptive Selektoren
- Crawling-Statistiken

## 4. Entscheidung für den verwendeten Fetcher

Dokumentiere:

- welcher Fetcher verwendet wird
- weshalb dieser Fetcher gewählt wurde
- welche Alternativen getestet wurden
- welche Performance- und Stabilitätsunterschiede festgestellt wurden

## 5. LinkedIn-spezifische Extraktion

Dokumentiere:

- Einstiegspunkt
- Pagination
- Jobkarten
- Jobdetailseiten
- verwendete Selektoren
- Fallback-Selektoren
- Erkennung der LinkedIn Job ID
- Behandlung fehlender Felder
- Erkennung von Seitenänderungen

## 6. Performance-Konfiguration

Dokumentiere:

- Parallelität
- Timeouts
- Delays
- Retries
- maximale Seitenzahl
- maximale Requests
- Session-Wiederverwendung
- erwartete Laufzeit

## 7. Fehleranalyse

Dokumentiere typische Fehler und deren Diagnose:

- keine Jobs gefunden
- Selektor funktioniert nicht mehr
- HTTP-Fehler
- Blockierung
- Browser startet nicht
- Timeout
- einzelne Detailseiten fehlen

## 8. Erweiterung

Erkläre, wie später:

- ein weiteres Unternehmen hinzugefügt wird
- weitere Jobplattformen ergänzt werden können
- Selektoren aktualisiert werden
- ein periodischer Scheduler ergänzt wird
- von SQLite auf PostgreSQL migriert werden kann

Die Dokumentation darf nicht nur Inhalte aus dem Scrapling README kopieren. Sie muss die Funktionsweise in eigenen Worten erklären und mit konkreten Beispielen aus dieser Anwendung verbinden.

# Tests

Jede wesentliche Funktion muss durch automatisierte Tests abgedeckt werden.

Mindestens erforderlich:

## Unit Tests

- Extraktion einer Jobkarte
- Extraktion einer Jobdetailseite
- Normalisierung von Standort und Land
- Extraktion der LinkedIn Job ID
- Behandlung fehlender Felder
- Deduplizierung
- Hash- beziehungsweise Änderungserkennung
- Statusaktualisierung bestehender Jobs
- Fehlerbehandlung
- URL-Validierung
- Pagination-Abbruch

## Integration Tests

- vollständiger Scraping-Lauf mit gespeicherten HTML-Fixtures
- neuer Job wird angelegt
- bestehender Job wird aktualisiert
- Job wird nicht doppelt angelegt
- fehlgeschlagene Detailseite stoppt den Lauf nicht
- ScrapeRun wird korrekt abgeschlossen
- UI zeigt gespeicherte Jobs korrekt an
- Filter funktionieren

## Test-Fixtures

Speichere anonymisierte beziehungsweise öffentlich abrufbare HTML-Beispiele als Test-Fixtures.

Tests dürfen nicht bei jedem Lauf live auf LinkedIn zugreifen. Live-Tests müssen separat markiert und standardmäßig deaktiviert sein.

## Qualitätsanforderungen

- keine ungetestete Parsing-Logik
- keine hart codierte Logik für ausschließlich ein Unternehmen
- keine still geschluckten Exceptions
- keine Speicherung vollständiger HTML-Seiten in der Datenbank
- keine LinkedIn-Zugangsdaten
- keine Secrets im Repository
- klare Type Hints
- verständliche Logs
- reproduzierbare lokale Einrichtung

# Akzeptanzkriterien

Der Task ist abgeschlossen, wenn:

1.  Acuity Analytics als Unternehmen angelegt werden kann.
2.  Ein Scraping-Lauf über die UI gestartet werden kann.
3.  Alle öffentlich erreichbaren Jobseiten der konfigurierten Unternehmens-URL verarbeitet werden.
4.  Jeder gefundene Job strukturiert in SQLite gespeichert wird.
5.  Jobtitel, Unternehmen, Land, Standort, Beschreibung und URL dargestellt werden, sofern diese Werte öffentlich verfügbar sind.
6.  Fehlende Felder keinen Abbruch verursachen.
7.  Doppelte Jobs nicht mehrfach gespeichert werden.
8.  Wiederholte Läufe bestehende Jobs aktualisieren.
9.  Nicht mehr gefundene Jobs nachvollziehbar als inaktiv markiert werden können.
10. Die UI nach Unternehmen, Land, Status und Freitext gefiltert werden kann.
11. Scraping-Läufe und Fehler in der UI nachvollziehbar sind.
12. Der Scraping-Prozess die UI nicht blockiert.
13. Ein einzelner fehlerhafter Job den gesamten Lauf nicht abbricht.
14. Unit- und Integrationstests erfolgreich durchlaufen.
15. `docs/SCRAPLING_GUIDE.md` vollständig vorhanden ist.
16. Das Projekt über eine dokumentierte lokale Installation gestartet werden kann.
17. Keine LinkedIn-Logins, privaten Cookies oder Mechanismen zur Umgehung von Zugriffsbeschränkungen verwendet werden.

# Vorgehensweise für Codex

Arbeite in dieser Reihenfolge:

1.  Bestehendes Projekt und aktuelle Architektur analysieren.
2.  Scrapling Repository und offizielle Dokumentation untersuchen.
3.  `docs/SCRAPLING_GUIDE.md` als projektspezifische technische Grundlage erstellen.
4.  Einen kleinen technischen Spike für die Acuity-Analytics-URL implementieren.
5.  Prüfen, ob ein HTTP-basierter Fetcher ausreicht oder ein Browser-Fetcher erforderlich ist.
6.  Extraktionslogik anhand gespeicherter HTML-Fixtures absichern.
7.  Datenmodell und Migrationen implementieren.
8.  Scraping-Service und Persistierung implementieren.
9.  Hintergrundausführung implementieren.
10. UI implementieren.
11. Unit- und Integrationstests ergänzen.
12. Gesamten Flow mit Acuity Analytics verifizieren.
13. Dokumentation mit den tatsächlichen Erkenntnissen aktualisieren.

Beginne nicht direkt mit der vollständigen UI. Verifiziere zuerst in einem isolierten Spike, welche öffentlich zugänglichen LinkedIn-Daten zuverlässig und regelkonform extrahiert werden können.

## Erwartetes Ergebnis des technischen Spikes

Der Spike muss dokumentieren:

- ob die Beispiel-URL ohne Anmeldung erreichbar ist
- welcher Scrapling Fetcher funktioniert
- welche Felder zuverlässig extrahiert werden können
- wie Pagination funktioniert
- ob Jobdetailseiten separat geladen werden müssen
- welche Request-Laufzeit erreicht wird
- welche Einschränkungen oder Blockierungen auftreten
- ob die geplante Lösung technisch und regelkonform umsetzbar ist

Falls die öffentlichen LinkedIn-Seiten nicht stabil oder regelkonform automatisiert verarbeitet werden können, darf keine Umgehung implementiert werden. Dokumentiere stattdessen die Einschränkung und schlage als Alternative eine offizielle Datenquelle, eine freigegebene API oder einen anderen zulässigen Importprozess vor.
