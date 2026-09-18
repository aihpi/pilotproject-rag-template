# Erste Schritte

Von null zu einem laufenden Assistenten, der Fragen zu deinen eigenen Dokumenten
beantwortet. Kopiere die Blöcke der Reihe nach in ein Terminal.

## Was du brauchst

- **[Docker](https://docs.docker.com/get-docker/)**. Unter Windows läuft es über
  WSL2, das Docker Desktop selbst einrichtet.
- **Git**, um das Projekt herunterzuladen. Oder den Knopf *Download ZIP* auf
  GitHub.
- **Zugang zu einem KI-Dienst**: eine Adresse und einen Zugangsschlüssel.
- **Eine stabile Internetverbindung.** Das Einlesen von Dokumenten macht hunderte
  Aufrufe an diesen Dienst, eine Verbindung mit gelegentlichen Abbrüchen scheitert
  also häufig. Ein VPN oder ein stark genutztes Netz ist die übliche Ursache.

Python, uv, pip, Qdrant oder PostgreSQL brauchst du nicht auf dem Rechner. Das
steckt alles im Container.

!!! note "Kein Modell ist dabei"
    Mitgeliefert werden die Chat-Anwendung, die Datenbank und der Suchindex, aber
    kein KI-Modell. Die App spricht immer mit einem Dienst woanders, dafür sind
    Adresse und Schlüssel in Schritt 1 da.

    Ein Modellserver auf dem eigenen Rechner geht auch, aber im Container bedeutet
    `localhost` den Container selbst, nicht deinen Rechner. Nimm deshalb
    `http://host.docker.internal:11434/v1` statt `http://localhost:11434/v1`
    (11434 ist der übliche Port für einen lokalen Modellserver). Außerdem musst du
    die Modellnamen deines Servers in die Einstellungsdatei schreiben, denn die
    voreingestellten Namen kommen von einem gehosteten Dienst.

## 1. Zum Laufen bringen

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template/apps/chainlit
cp .env.example .env
```

Öffne die neue Datei `.env` und trage Adresse und Schlüssel deines KI-Dienstes
ein. Ohne diese beiden Angaben funktioniert nichts.

Prüfe die Angaben, bevor du sonst etwas startest:

```bash
make check
```

Der Befehl probiert jedes Modell mehrmals und sagt dir, ob ein Problem an deinen
Einstellungen, an deiner Verbindung oder am Dienst liegt, mit den passenden
Schritten dazu.

Der erste Durchlauf baut den Container mit und dauert deshalb ein paar Minuten,
spätere Durchläufe dauern Sekunden. Gebaut wird ohnehin, und hier fällt eine
falsche Adresse oder ein falscher Schlüssel sofort auf statt mitten beim Einlesen
der Dokumente. Wenn die Prüfung grün zurückkommt, alles starten:

```bash
docker compose up -d --build
```

Der erste Start dauert ein paar Minuten: Die Bestandteile werden heruntergeladen,
die drei Beispiel-Paper eingelesen und das Chat-Fenster gestartet. Zuschauen
kannst du mit `docker compose logs -f chainlit`.

Danach <http://localhost:8000> öffnen und mit `admin` / `admin` anmelden. Stell
eine Frage zu den Beispiel-Papern und klick auf eine Quelle unter der Antwort.
Wenn sich das PDF öffnet, läuft alles.

## 2. Eigene Dokumente verwenden

Lege deine PDFs in `data/documents/` und erstelle dann eine eigene
Einstellungsdatei, damit das Beispiel unangetastet bleibt:

```bash
cp examples/papers/rag.config.yaml my-rag.yaml
```

Öffne `my-rag.yaml` und ändere drei Dinge:

```yaml
models:
  chat_model: gpt-oss-120b        # ein Modell, das dein KI-Dienst anbietet
  embed_model: octen-embedding-8b # ein Suchmodell deines KI-Dienstes

vector_store:
  collection: my_docs             # ein neuer Name, den du dir ausdenkst
```

- Die beiden **Modelle** müssen Namen sein, die dein Dienst wirklich anbietet.
  Wenn du unsicher bist, frag dort die Liste ab.
- Die **collection** ist ein selbst ausgedachter Name. Er hält deine Dokumente
  von den Beispielen getrennt. Nimm immer einen neuen, sonst vermischen sich
  beide.

Zum Schluss sagst du der App, dass sie deine Datei statt des Beispiels nutzen
soll: Setze dafür `RAG_CONFIG=my-rag.yaml` in `.env`.

Alle verfügbaren Einstellungen stehen in der
[Konfigurationsreferenz](configuration.md).

## 3. Dokumente einlesen

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
```

Damit wird jedes Dokument gelesen und durchsuchbar gespeichert. Je nach Menge
dauert das von einer Minute bis deutlich länger.

Diesen Befehl brauchst du nur für das *erste* Einlesen oder nach Einstellungen, die
alle Dokumente betreffen. Ein Dokument später hinzuzufügen, zu ändern oder zu löschen
braucht nichts weiter: Die App beobachtet den Ordner und übernimmt Änderungen
innerhalb von Sekunden. Siehe [Dokumente ändern](managing-documents.de.md).

!!! warning "Bilder beschreiben kostet Geld"
    Mit `images.mode: describe` (Standard im Beispiel) wird jedes Bild einmal an
    ein KI-Modell geschickt, um beschrieben zu werden. Bei vielen Dokumenten
    summiert sich das. Setze `images.mode: none`, wenn Bilder nicht durchsuchbar
    sein müssen.

    Lässt sich ein Bild nicht beschreiben, wird es weggelassen statt ohne
    Beschreibung gespeichert. Der Durchlauf meldet pro Dokument, wie viele
    betroffen waren, achte also auf diese Zeile. Siehe
    [Abbildungen](images.de.md).

## 4. Neu starten und ausprobieren

```bash
docker compose up -d
```

Nimm `up -d`, nicht `restart`. Ein Neustart verwendet die alten Einstellungen
weiter, deine Änderung an `RAG_CONFIG` aus Schritt 2 würde also ignoriert und der
Assistent würde weiter aus den Beispiel-Papern antworten.

Öffne <http://localhost:8000> erneut und frag etwas, das nur deine Dokumente
beantworten können. Wenn eine sinnvolle Antwort mit funktionierendem
Quellen-Link kommt, bist du fertig.

Sagt der Assistent, er finde nichts, wurde in Schritt 3 vermutlich nichts
eingelesen. Prüfe, ob deine PDFs wirklich in `data/documents/` liegen und ob
`RAG_CONFIG` auf deine Datei zeigt.

## 5. Sehen, wie gut die Antworten sind (optional)

Der Assistent läuft, aber woher weißt du, ob er *gut* antwortet? Die Evaluation
bewertet jede Antwort in zwei Punkten und zeigt das Ergebnis in einem kleinen
Abzeichen über dem Eingabefeld:

- **Treue (Faithfulness)** prüft, ob der Assistent nur Dinge gesagt hat, die seine Quellen
  auch hergeben, oder hat er eigene Behauptungen hinzugefügt? Der Wert ist der
  Anteil gedeckter Aussagen: 1,0 heißt alles geprüft, 0,5 heißt die Hälfte ist
  nicht belegt.
- **Relevanz (Relevance)** prüft, ob die Antwort die gestellte Frage beantwortet, statt
  zwar korrekt, aber am Thema vorbei zu sein?

Beides wird von einem zweiten KI-Modell (dem *Judge*) geprüft, das die Antwort
und die Quellen liest und entscheidet. Von Hand geschriebene „richtige Antworten"
braucht es nicht, deshalb funktioniert es auf den Fragen, die sowieso schon
gestellt werden.

Die mitgelieferte `examples/papers`-Instanz hat die Evaluation an, du solltest das
Abzeichen also schon bei der ersten Frage sehen. Sie kostet einen Judge-Aufruf pro
Antwort, deshalb ist die Schema-Vorgabe für eigene Instanzen aus. Der Dienst läuft
in beiden Fällen; eine Einstellung entscheidet, ob bewertet wird:

```yaml
evaluation:
  enabled: true
```

App neu starten (`docker compose restart chainlit`), eine Frage stellen und etwa
fünfzehn Sekunden warten. Über dem Eingabefeld erscheint ein Abzeichen mit den
Werten. Ein Klick darauf zeigt die ganze Erklärung: welche Aussagen bestanden
haben, welche nicht, und warum.

Die vollständige Anleitung steht unter
[Antwortqualität prüfen](evaluation.de.md).

## Ohne Docker

Nur nötig, wenn du Docker nicht nutzen kannst oder an der App selbst
entwickelst. Du brauchst Python 3.12 oder neuer und ein laufendes Qdrant.

```bash
cd apps/chainlit
uv sync                                   # uv nutzen, nicht pip
docker run -p 6333:6333 qdrant/qdrant     # Speicher für den durchsuchbaren Text

export RAG_CONFIG=my-rag.yaml
uv run python -m kb.ingest --recreate     # Dokumente einlesen
uv run chainlit run app.py                # http://localhost:8000
```

Die `export`-Zeile musst du in jedem neuen Terminal-Fenster wiederholen.

## Doku lokal

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
