<p align="center">
  <img src="00_aisc/img/logo_aisc_bmftr.jpg" alt="AISC / BMFTR">
</p>

# Modulares RAG-Template

**🇬🇧 [English version](README.en.md)** · 📖 **[Dokumentation](https://aihpi.github.io/pilotproject-rag-template/de/)**

Ein Chat-Assistent, der Fragen zu deinen eigenen Dokumenten beantwortet und dir
zu jeder Antwort die genaue Seite zeigt, aus der sie stammt.

Du gibst ihm einen Ordner mit PDFs. Er liest sie ein, und danach kannst du in
normaler Sprache Fragen stellen. Jede Antwort verlinkt auf die Quelle, sodass du
selbst nachprüfen kannst.

Zum Einrichten bearbeitest du eine einzige Einstellungsdatei. Programmieren musst
du nicht.

> Drei wissenschaftliche Paper liegen dem Projekt bei
> ([Quellen & Lizenz](apps/chainlit/data/documents/SOURCES.md)). Du kannst also
> einen funktionierenden Assistenten ausprobieren, bevor du irgendetwas änderst.

<details>
<summary><b>Worauf das Ganze aufbaut</b></summary>

[Chainlit](https://chainlit.io) für das Chat-Fenster, [LiteLLM](https://litellm.ai)
für die Verbindung zu den KI-Modellen, [Qdrant](https://qdrant.tech) als Speicher
für den durchsuchbaren Text und [Docling](https://github.com/DS4SD/docling) zum
Lesen der PDFs.
</details>

---

## Was du brauchst

Drei Dinge musst du installieren, dazu brauchst du eine Netzverbindung:

| | |
|---|---|
| **[Docker](https://docs.docker.com/get-docker/)** | Führt alles aus. Unter Windows läuft es über WSL2, das Docker Desktop selbst einrichtet. |
| **Git** | Um das Projekt herunterzuladen. Wer das nicht installieren möchte, nimmt auf GitHub den grünen *Code*-Knopf und *Download ZIP*. |
| **Einen Texteditor** | Um eine Einstellungsdatei auszufüllen. Jeder Editor genügt. |
| **Eine stabile Internetverbindung** | Mit Zugang zu einem KI-Dienst. Das Einlesen von Dokumenten macht hunderte Aufrufe, eine Verbindung mit gelegentlichen Abbrüchen scheitert also häufig. Ein VPN oder ein stark genutztes Netz ist die übliche Fehlerquelle. |

Nicht nötig sind Python, uv, pip, Node, Qdrant oder PostgreSQL. Das steckt alles
im Container.

**Eines ist nicht dabei: das KI-Modell.** Mitgeliefert werden die Chat-Anwendung,
die Datenbank und der Suchindex, aber kein Modell. Du verweist auf einen
bestehenden Dienst, indem du Adresse und Schlüssel in die Datei `.env` einträgst.
Das kann ein Dienst deiner Einrichtung sein oder ein Modellserver auf deinem
eigenen Rechner.

Ob dein Dienst erreichbar ist und dein Schlüssel stimmt, prüft `make check` im
[Schnellstart](#schnellstart), bevor irgendetwas eingelesen wird.

## Schnellstart

Kopiere diese Zeilen blockweise in ein Terminal:

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template/apps/chainlit

cp .env.example .env            # legt die Vorlage an, jetzt .env ausfüllen
```

Öffne die neue Datei `.env` und trage dort Adresse und Zugangsschlüssel deines
KI-Dienstes ein. Prüfe sie dann, bevor du startest:

```bash
make check                      # erreichbar? Schlüssel richtig?
docker compose up -d --build    # startet alles
```

Beim ersten Mal baut die Prüfung den Container mit, das dauert ein paar Minuten,
danach läuft sie in Sekunden. Gebaut wird ohnehin, und so erfährst du von einem
falschen Schlüssel, bevor die Dokumente eingelesen werden, statt mittendrin. Bei
einem Problem sagt dir die Ausgabe, was zu prüfen ist.

Danach <http://localhost:8000> öffnen und mit `admin` / `admin` anmelden. Ändere
dieses Passwort, bevor andere auf die App zugreifen können.

Die drei Beispiel-Paper sind von Anfang an geladen, alle Funktionen sind aktiv.
Die zugehörigen Einstellungen stehen in
[`examples/papers/rag.config.yaml`](apps/chainlit/examples/papers/rag.config.yaml),
in diese Datei lohnt sich der erste Blick.

<details>
<summary><b>Ohne Docker</b></summary>

```bash
cd apps/chainlit
uv sync                                   # uv nutzen, nicht pip (siehe „Aktualisieren“)
docker run -p 6333:6333 qdrant/qdrant     # Speicher für den durchsuchbaren Text

export RAG_CONFIG=examples/papers/rag.config.yaml
uv run python -m kb.ingest                # Dokumente einlesen
uv run chainlit run app.py                # http://localhost:8000
```
</details>

> Welche KI-Modelle du nutzt: das Beispiel verwendet frei verfügbare Modelle
> (`gpt-oss-120b`, `octen-embedding-8b` und `gemma-4-31b` zum Lesen von Bildern).
> Nichts davon bindet dich an einen kostenpflichtigen Anbieter.
>
> Die Modellnamen unterscheiden sich je nach Dienst, die obigen funktionieren bei
> dir also womöglich nicht. Frag deinen Dienst, welche Modelle er anbietet, und
> trage diese Namen in die Einstellungsdatei ein. In der Datei stehen als
> Kommentar weitere offene Alternativen (Qwen3, Llama 3.x, Mistral, BGE-M3,
> multilingual-E5).

## Auf eine neuere Version aktualisieren

Läuft die App schon und du möchtest die neuesten Änderungen, sind es drei
Befehle.

Mit Docker, der übliche Weg:

```bash
git pull
cd apps/chainlit
docker compose up -d --build
```

Das `--build` am Ende ist wichtig. Ohne `--build` startet Docker wieder deine alte
App und du siehst keine der Änderungen, ohne dass eine Fehlermeldung dich warnt. Der
Neubau dauert etwa eine halbe Minute. (`make up` macht dasselbe.)

Der Ingest läuft dabei mit und ist normalerweise in Sekunden fertig, weil er nur
geänderte Dateien anfasst. Zwei Fälle sind teurer, und sie verhalten sich absichtlich
unterschiedlich. Ändert ein Update den lexikalischen Tokenizer, baut der Ingest die
Collection von selbst neu auf: er nennt den Grund und die Zahl der betroffenen Punkte
und embeddet den Korpus neu, was bei vielen Dokumenten Zeit und abgerechnete
Gateway-Aufrufe kostet. Wechselst du dagegen das Embedding-Modell, verweigert er den
Lauf und verlangt ein ausdrückliches `--recreate`. Das ist deine Entscheidung, nicht
die Nebenwirkung eines Updates.

Ohne Docker:

```bash
git pull
cd apps/chainlit
uv sync
```

Bitte `uv sync` benutzen und nicht `pip install -e .`. Pip installiert bei einigen
Paketen andere Versionen, und die App startet dann möglicherweise nicht.

Danach <http://localhost:8000> öffnen und eine Frage stellen. Klick auf eine der Quellen
unter der Antwort, dann sollte sich das PDF rechts öffnen. So siehst du am schnellsten, dass
alles funktioniert hat.

### Gut zu wissen

- Deine Dokumente, die indexierten Daten und der Chat-Verlauf bleiben beim
  Aktualisieren erhalten. Neu einlesen musst du normalerweise nicht. Dateien, die du
  im Dokumentenordner ergänzt, änderst oder löschst, übernimmt die App innerhalb von
  Sekunden von selbst.
- Eine Ausnahme: hat ein früherer Ingest fehlgeschlagene Abbildungs-Beschreibungen
  gemeldet, lies deine Dokumente einmal neu ein, damit diese Abbildungen beschrieben
  werden. Nur dann lohnt es sich, siehe [Abbildungen](docs/images.de.md).
- Das allererste Einlesen dauert länger, weil die App die Modelle zum Lesen von PDFs
  herunterlädt (etwa 500 MB). Sie bleiben danach gespeichert, das passiert also einmal
  und nicht bei jedem Update.
- Alte Versionen belegen Speicherplatz, freigeben lässt er sich mit
  `docker image prune`.
- Zickt etwas, hilft meistens alles stoppen und neu starten:
  `docker compose down && docker compose up -d --build`. Bleibt der Fehler, steht er
  vermutlich in der [Fehlerbehebung](docs/troubleshooting.de.md).

## Eigene Dokumente verwenden

Lege zuerst eine eigene Kopie der Einstellungsdatei an, damit die Beispiele
unangetastet bleiben:

```bash
cp apps/chainlit/examples/papers/rag.config.yaml apps/chainlit/my-rag.yaml
```

1. Lege deine PDFs in `apps/chainlit/data/documents/`. Sie bleiben auf deinem
   Rechner. Nur die Beispiel-Paper gehören zum Projekt und dürfen weg.
2. Öffne `my-rag.yaml` und gib `vector_store.collection` einen neuen Namen. So
   bleiben deine Dokumente von den Beispielen getrennt.
3. Lass die App deine Dokumente einlesen:
   `RAG_CONFIG=my-rag.yaml docker compose up -d`

Markdown-, JSON- und CSV-Dateien gehen ebenfalls. Siehe
[Daten hinzufügen](docs/adding-data.de.md).

Später Dokumente ändern braucht gar keine Befehle, denn die App beobachtet den
Ordner. Legst du eine Datei dazu, ist sie in Sekunden eingelesen. Korrigierst du
eine, wird sie erneut gelesen, und löschst du eine, taucht sie nicht mehr in
Antworten auf. Alles andere bleibt unberührt, du zahlst also nicht zweimal für
dieselbe Datei, und neu starten musst du nichts. Wer das lieber selbst anstößt,
schaltet es mit `DOCUMENT_WATCH=false` ab.
→ [Dokumente ändern](docs/managing-documents.de.md)

## Was du konfigurierst

Alles steht in einer Einstellungsdatei. Das sind ihre Abschnitte:

| Abschnitt | Wofür |
|---|---|
| `models` | welche KI-Modelle genutzt werden und welche Nutzer auswählen dürfen |
| `vector_store` | wo der durchsuchbare Text gespeichert wird |
| `data_sources[]` | wo deine Dokumente liegen und welcher Art sie sind |
| `chunking` | wie Dokumente vor der Suche in Stücke geteilt werden |
| `retrieval` | wie viele Textstücke pro Frage nachgeschlagen werden |
| `tools` | was der Assistent außer Suchen darf → [mehr](docs/tools.de.md) |
| `images` | was mit Bildern und Diagrammen in den PDFs passiert → [mehr](docs/images.de.md) |
| `citation` | wie eine Quellenangabe unter einer Antwort aussieht |
| `prompt` | die Anweisungen für den Assistenten und die Beispielfragen → [mehr](docs/prompts.de.md) |
| `evaluation` | Antworten automatisch bewerten, um Konfigurationen zu vergleichen (Schema-Vorgabe aus, in `examples/papers` an) → [mehr](docs/evaluation.de.md) |
| `app` | Aussehen und Verhalten des Chat-Fensters |

Jede einzelne Option steht in der [Konfiguration](docs/configuration.de.md).

## Funktionen

- Der Assistent entscheidet selbst, wie er nachschlägt. Neben der reinen Suche
  kann er alle Dokumente auflisten, ein ganzes Dokument lesen (nötig für
  Zusammenfassungen), mehr Text rund um einen Treffer holen oder eine Aussage
  gegenprüfen. Du legst fest, was davon erlaubt ist. → [Doku](docs/tools.de.md)
- Bilder und Diagramme fallen nicht unter den Tisch. Sie werden in Worten
  beschrieben und damit auffindbar, und eine passende Abbildung erscheint direkt
  über dem Absatz, der sie behandelt. → [Doku](docs/images.de.md)
- Tabellen bleiben erhalten statt verworfen zu werden.
- Jede Aussage hat eine Quelle. Ein Klick öffnet das Original-PDF auf der
  richtigen Seite.
- Gibst du dem Assistenten seine Anweisungen nicht vor, schreibt er sie beim
  Start selbst aus deinen Dokumenten. → [Doku](docs/prompts.de.md)
- Nutzer können Modelle wechseln und Anweisungen bearbeiten, die Auswahl wird
  gespeichert.
- Chat-Verlauf, Daumen hoch/runter und Export, mit GitHub- oder lokalem Login.
- **Antwortqualität messen.** Jede Antwort bekommt zwei Werte: *Treue* (sagt der
  Assistent nur, was seine Quellen hergeben?) und *Relevanz* (beantwortet er die
  gestellte Frage?). Ein kleines Abzeichen über dem Eingabefeld zeigt den
  laufenden Mittelwert. Ein Klick darauf zeigt die Einzelbewertung je Aussage:
  welche bestanden haben, welche nicht, und warum. Daneben vergleicht ein zweiter Tab
  verschiedene Konfigurationen, so dass „hat diese Änderung geholfen?" etwas
  wird, das man anschauen kann. → [Doku](docs/evaluation.de.md)

### Evaluation einschalten

Die mitgelieferte `examples/papers`-Instanz hat die Evaluation **an**, damit ein
frischer Clone das Abzeichen auch sieht. In der Schema-Vorgabe ist sie aus, eine
eigene Instanz bewertet also nichts, bis du es einschaltest. Der Evaluations-Dienst
startet mit dem übrigen Stack, eine Einstellung in deiner Konfigurationsdatei ist
der ganze Schalter:

```yaml
evaluation:
  enabled: true
```

App neu starten (`docker compose restart chainlit`), eine Frage stellen und etwa
fünfzehn Sekunden warten. Über dem Eingabefeld erscheint ein Abzeichen mit den
Werten. Ein Klick darauf zeigt die ganze Erklärung: welche Aussagen bestanden
haben, welche nicht, und warum.

## Wie alles zusammenspielt

Deine Dokumente werden gelesen, in Stücke geteilt und durchsuchbar gespeichert.
Kommt eine Frage, werden die passenden Stücke herausgesucht und dem KI-Modell
gegeben, das daraus eine Antwort mit Quellen schreibt.

```
                        rag.config.yaml
                               │
  Dokumente ──► kb/parsers ──► kb/chunkers ──► Embeddings ──► Qdrant
  (pdf/md/json/csv/custom)                                      │
                                                                ▼
  Chainlit-UI ◄── Zitate ◄── Antwort ◄── LLM + Tools ◄── Retrieval
```

Jeder Schritt ist ein kleiner Ordner, den du mit einer einzigen neuen Datei
erweiterst: Dateiformate in `kb/parsers/`, Arten des Textteilens in
`kb/chunkers/` und Fähigkeiten in `tools/`.
→ [Erweitern](docs/extending.de.md)

## Dokumentation

| Seite | |
|---|---|
| [Erste Schritte](docs/getting-started.de.md) | Installation, Ingest, Start |
| [Dokumente ändern](docs/managing-documents.de.md) | ergänzen, korrigieren, entfernen, neu aufbauen |
| [Fehlerbehebung](docs/troubleshooting.de.md) | häufige Fehler und was zu tun ist |
| [Beispielkorpus](docs/example-corpus.de.md) | was mitgeliefert wird und wie man es tauscht |
| [Daten hinzufügen](docs/adding-data.de.md) | Formate, Chunking, Zitate |
| [Agentische Tools](docs/tools.de.md) | die fünf Tools, eigene schreiben |
| [Abbildungen](docs/images.de.md) | `images.mode`, Inline-Platzierung |
| [System-Prompts](docs/prompts.de.md) | Generierung, Bearbeitung, Modell-Selektor |
| [Antwortqualität prüfen](docs/evaluation.de.md) | Bewertung, Abzeichen, Vergleich |
| [Konfiguration](docs/configuration.de.md) | vollständige Schema-Referenz |
| [Field-Mapping-DSL](docs/field-mapping.de.md) | JSON/CSV → Chunks |
| [Erweitern](docs/extending.de.md) | eigene Parser, Chunker, Tools |

Veröffentlicht auf Deutsch und Englisch unter
**<https://aihpi.github.io/pilotproject-rag-template/>**, lokal via
`uv run --only-group docs mkdocs serve`.

## Einschränkungen

- Das hier ist ein Prototyp und wurde nicht auf Sicherheit geprüft. Sieh ihn dir
  an, bevor du ihn mit sensiblen Daten oder echten Nutzern einsetzt.
- Quellen und Anschlussfragen funktionieren nur auf Deutsch. Die App sucht nach
  deutschen Formulierungen (`Quelle N: … (S.x)`, `Anschlussfragen:`), dafür also
  `language: de` setzen. Die Dokumente selbst dürfen jede Sprache haben.
- Bilder beschreiben kostet Geld: mit `images.mode: describe` wird jede Abbildung
  beim Einlesen einmal an ein KI-Modell geschickt.
- Wechselst du das Modell, das deinen Text durchsuchbar macht, müssen alle
  Dokumente neu eingelesen werden (`--recreate`).

## Mitwirken

Siehe [CONTRIBUTING.md](CONTRIBUTING.md). Frühere Projektstände (der
IT-Grundschutz-Assistent, Forschungs-Notebooks und Evaluations-Skripte) liegen im
Branch `backup/pre-template-cleanup`.

## Referenzen

- [AI Service Centre Berlin Brandenburg (KI-Servicezentrum)](https://hpi.de/ki-servicezentrum/)
- [fghgsd.de](https://fghgsd.de)

## Lizenz

Der Code steht unter der [MIT-Lizenz](LICENSE). Die Beispiel-Paper sind CC BY 4.0,
siehe [SOURCES.md](apps/chainlit/data/documents/SOURCES.md).

---

## Danksagung
<img src="00_aisc/img/logo_bmftr_de.png" alt="BMFTR" style="width:170px;"/>

Das [AI Service Centre Berlin Brandenburg](http://hpi.de/kisz) wird vom
[Bundesministerium für Forschung, Technologie und Raumfahrt](https://www.bmbf.de/)
unter dem Förderkennzeichen 01IS22092 gefördert.
