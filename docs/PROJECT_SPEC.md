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

Wenn LinkedIn den öffentlichen Zugriff technisch blockiert, muss der Lauf kontrolliert fehlschlagen und einen verständlichen Fehlerstatus liefern. Es soll keine Umgehung implementiert werden.

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
