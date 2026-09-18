# Daten hinzufügen

Jeder Satz Dokumente ist ein `data_sources[]`-Eintrag in deiner
Einstellungsdatei. Ein Eintrag sagt, **wo** die Dateien liegen, **welcher Art**
sie sind und optional, wie sie zerteilt und beschriftet werden. Du kannst mehrere
Sätze angeben und gemeinsam durchsuchen.

```yaml
data_sources:
  - name: handbook          # eindeutiges Label (für --only und Fallback-IDs)
    path: ./data/handbook   # Datei oder Verzeichnis, RELATIV ZUR KONFIGURATIONSDATEI
    format: pdf             # pdf | txt | md | json | csv | custom
    glob: "*.pdf"           # für Verzeichnisse
    chunking: {strategy: heading}      # optionale Überschreibung pro Quelle
    extra_metadata: {topic: security}  # optionale statische Metadaten auf jedem Chunk
```

!!! note "Pfade sind relativ zur Konfigurationsdatei"
    Ein `path` zählt **ab dem Ordner, in dem die Einstellungsdatei liegt**, nicht
    ab dem Ort, an dem du gerade im Terminal stehst. Darüber stolpern viele.
    Pfade, die ganz vorne bei der Festplattenwurzel beginnen, werden unverändert
    genommen. In Docker nimmst du die gemounteten Pfade (`/data/...`) oder die
    Einstellung `INGEST_DOCLING_JSON_DIR`.

## Mehrere Ordner gleichzeitig

Zwei Ordner, nichts Optionales, zum Kopieren. Pro Ordner ein `- name:`-Block:

```yaml
data_sources:
  - name: handbook
    path: ./data/handbook
    format: pdf
    glob: "*.pdf"

  - name: notes
    path: ./data/notes
    format: pdf
    glob: "*.pdf"

sources:
  data_dir: ./data/handbook
  served_extensions: [.pdf]
```

Beide Ordner werden eingelesen, gemeinsam durchsucht, und ein Klick auf ein Zitat
öffnet die Datei aus dem Ordner, aus dem sie stammt.

**Deine Ordner gehören nicht unter `sources:`.** Dieser Block regelt nur das
Anklicken von Zitaten: `served_extensions` sagt, welche Dateitypen sich öffnen
lassen, und `data_dir` ist nur der Ordner, der zuerst geprüft wird, wenn derselbe
Dateiname zweimal vorkommt (dort landen außerdem die Abbildungen). Zeig damit auf
deinen Hauptordner und lass ihn in Ruhe.

Ein dritter Ordner ist ein weiterer Block:

```yaml
  - name: contracts
    path: ./data/contracts
    format: pdf
    glob: "*.pdf"
```

Die Ordner dürfen unterschiedliche Dateitypen enthalten. Einer kann PDFs führen
und ein anderer Markdown; setze dafür einfach im jeweiligen Block das eigene
`format`.

## 1. Dateien ablegen

Lege deine Dokumente irgendwo auf deinem Rechner ab und richte `path` darauf,
zum Beispiel auf einen `data/`-Ordner neben dem Projekt:

```
pilotproject-rag-template/
  data/
    handbook/*.pdf
    notes/*.md
    faq.csv
  apps/chainlit/
    my-rag.yaml        # path: ../../data/handbook  (relativ zu dieser Datei)
```

## Dokumente auf einer Windows-Freigabe (SMB)

Liegen die Dokumente auf einem Windows-Dateiserver, kopierst du sie nicht.
Docker **hängt** den freigegebenen Ordner schreibgeschützt in die Container
**ein**, und die App liest ihn direkt unter `/data/documents`. Aus Sicht der App
ist das einfach ein Ordner mit Dateien, genau wie das lokale `data/documents`:
gleiches Parsen, gleicher inkrementeller Ingest, gleiche Zitate, Unterordner
eingeschlossen. Die Dateien bleiben auf dem Server, und die App kann sie nie
verändern. Am Code ändert sich nichts, der `path` der Quelle ist einfach
`/data/documents`, der Pfad im Container, nie ein Windows-Pfad. Das ist die
Checkliste für die Person, die die App innerhalb dieses Netzwerks betreibt.

1. **Ordner auf dem Windows Server freigeben.** Rechtsklick auf den Ordner,
   *Freigeben*, oder in PowerShell:

    ```powershell
    New-SmbShare -Name documents -Path D:\Docs -ReadAccess DOMAIN\rag-reader
    ```

    Nimm ein eigenes Lesekonto wie `rag-reader`, kein persönliches Login. Ein
    persönliches Login hört auf zu funktionieren, wenn das Passwort wechselt,
    und es landet in einer Konfigurationsdatei auf dem Docker-Host.

    **Das Passwort darf kein Komma und kein `$` enthalten.** Beide brechen die
    Verbindung, und keines von beiden erzeugt eine brauchbare Fehlermeldung: es
    sieht hinterher nach einem falschen Passwort aus. Bei einem neu angelegten
    Konto kostet die Regel nichts.

    `-ReadAccess` setzt die Freigabeberechtigung. Setze die NTFS-Berechtigungen
    des Ordners für dasselbe Konto ebenfalls auf Lesen. Windows nimmt von beiden
    die strengere, und welche das ist, überrascht regelmäßig. Sonst sollte das
    Konto auf dem Server nichts dürfen, auch keine Anmeldung an der Konsole.

    Zwei weitere Punkte auf dem Server, beide nicht Sache dieser App:

    - **Port 445 auf den Rechner begrenzen, der die App betreibt.** Genau ein
      Host verbindet sich mit dieser Freigabe. Eine Firewall-Regel auf dessen
      Adresse lässt die Freigabe für den Rest des Netzes so dicht wie zuvor.
    - **SMB 1.0 kann aus bleiben.** Client und Server handeln den höchsten
      Dialekt aus, den beide können, gegen einen aktuellen Windows Server ist
      das 3.1.1. Einen stillen Rückfall auf 1.0 gibt es nicht, dafür bräuchte es
      ein ausdrückliches `vers=1.0`. Womit ein laufender Mount zustande kam,
      zeigt `docker compose exec ingest mount | grep cifs`.

2. **Prüfen, dass die Freigabe vom Docker-Host erreichbar ist** (irgendeine
   Maschine im Netzwerk, auf der Docker läuft):

    ```bash
    smbclient -L //fileserver -U rag-reader
    ```

    Auf einem Windows-Docker-Host tut `net view \\fileserver` dasselbe.

3. **`.env` ausfüllen**:

    ```
    COMPOSE_FILE=docker-compose.yml:docker-compose.smb.yml
    RAG_CONFIG=examples/smb/rag.config.yaml
    SMB_SHARE=//fileserver/documents
    SMB_USER=rag-reader
    SMB_PASSWORD='...'
    ```

    Zwei Dinge an dieser Datei gehen leise schief, weil beide keinen Fehler
    erzeugen:

    **Auf einem Windows-Docker-Host braucht die erste Zeile ein Semikolon**,
    `COMPOSE_FILE=docker-compose.yml;docker-compose.smb.yml`. Mit einem
    Doppelpunkt liest Compose den ganzen Wert als einen Dateinamen und bricht mit
    `no such file or directory` ab, wobei beide Pfade aneinandergehängt in der
    Meldung stehen. Das ist der Hinweis.

    **Die einfachen Anführungszeichen um das Passwort gehören dazu.** Sie sind
    das Netz unter der Regel aus Schritt 1: ohne sie ersetzt Compose ein `$VAR`
    im Wert, und am Server kommt ein anderes Passwort an als das getippte.

4. **Probelauf.** Das hängt die Freigabe ein und listet, was der Ingest lesen
   würde, ohne den Suchindex anzufassen:

    ```bash
    docker compose run --rm ingest python -m kb.ingest --dry-run --config "$RAG_CONFIG"
    ```

    Es müssen die Dateien der Freigabe erscheinen, Unterordner eingeschlossen:
    die Beispielkonfiguration nutzt `**/*.[pP][dD][fF]`, das jeden Ordner
    durchläuft und `.pdf` wie `.PDF` nimmt. Andere Formate (`md`, `txt`, `csv`,
    `json`) sind weitere Quellen auf demselben Pfad, siehe den auskommentierten
    Block im Beispiel. Ist die Freigabe nicht
    erreichbar oder stimmen die Zugangsdaten nicht, startet der Container gar
    nicht und meldet `error while mounting volume ... connection refused` (oder
    `permission denied`). Dann Freigabename, Konto und CIFS-Unterstützung des
    Docker-Hosts prüfen (`apt install cifs-utils` auf einer Linux-VM). Der
    Suchindex bleibt in dem Fall unberührt.

5. **App starten**: `make up`. Dann im Chat eine Frage stellen, deren
   Antwort in einem der Dokumente steht, und prüfen, dass das Zitat es öffnet.

Fünf Dinge, die man über den Betrieb von einer Freigabe wissen sollte:

- Ist die Freigabe beim Start nicht erreichbar, startet der Container nicht,
  siehe oben. Nutzt du stattdessen den Bind-Mount-Ausweg aus
  `docker-compose.smb.yml`, erscheint ein fehlender Mount als leerer Ordner. Der
  Ingest löscht dann nichts aus der Collection, siehe die Warnung in
  [Schritt 4 unten](#4-dokumente-einlesen). Mount reparieren und erneut laufen
  lassen.
- Die Freigabe ist schreibgeschützt eingehängt. Abbildungen und Beschreibungen
  werden unter `sources.data_dir` neben der Konfiguration geschrieben, nie auf
  die Freigabe.
- Änderungen auf der Freigabe werden alle `DOCUMENT_WATCH_INTERVAL` Sekunden per
  Abfrage bemerkt, nicht sofort. SMB liefert Linux keine Änderungsereignisse.
- **Wo das Passwort landet.** `.env` ist von Git ausgenommen, wird aber für
  alle lesbar angelegt, `chmod 600 .env` ist den einen Befehl wert. Docker
  übernimmt die Mount-Optionen anschließend in die Metadaten des Volumes: dort
  zeigt `docker volume inspect` das Passwort im Klartext, und im Klartext liegt
  es auch auf der Platte des Docker-Daemons. Auf diesem Weg lässt sich das nicht
  vermeiden. Docker-Secrets gelten für Container, nicht für Volume-Optionen, und
  `credentials=` ist eine Option des Helfers `mount.cifs`, den der Volume-Treiber
  nicht aufruft (nachgemessen: der Mount scheitert mit `permission denied`).
  Der Schutz ist also das Konto, nicht das Geheimnis: nur Lesen, keine weiteren
  Rechte, und ein Wechsel kostet eine Zeile in `.env`. Soll das Passwort auf
  diesem Rechner gar nicht lesbar sein, hänge die Freigabe stattdessen auf dem
  Host ein und reiche sie per Bind-Mount herein, wie in `docker-compose.smb.yml`
  beschrieben: dann liest `mount.cifs` eine root-eigene Datei und nichts davon
  erreicht Docker.
- Die Freigabe landet unter `/data/documents`, und genau von dort liest
  `examples/smb`. Eine Konfiguration, die ihre Dokumente woanders liest, braucht
  stattdessen `SMB_MOUNT_TARGET` in der `.env` mit diesem Pfad. Das ist der
  Normalfall für ein Tool außerhalb des Templates, das seine eigene
  Konfigurationsdatei und seinen eigenen Ordneraufbau mitbringt. Der Ordner muss
  im Container bereits vorhanden sein, sonst startet der Container nicht und
  nennt den Pfad, den er nicht anlegen konnte.

## 2. Quelle deklarieren (nach Format)

=== "PDF"

    Auf einen Ordner mit PDFs richten. **Docling** liest sie und erkennt dabei
    die Struktur, weiß also, wo die Überschriften sind, welcher Text zu welchem
    Abschnitt gehört und auf welcher Seite er stand. Schalte OCR ein, wenn deine
    PDFs Scans sind, der Text also in Wirklichkeit ein Foto ist und sich nicht
    markieren lässt.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # ein Chunk pro Abschnitt
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **PDFs einmal lesen und das Ergebnis wiederverwenden.** PDFs zu lesen ist
    langsam, mit OCR besonders, und beim Abstimmen der Einstellungen wiederholst
    du es vermutlich mehrfach. Benenne einen Ordner für die umgewandelten
    Dokumente, dann passiert der langsame Schritt einmal pro PDF: Ein PDF ohne
    umgewandelte Datei dort wird beim nächsten Einlesen umgewandelt und
    gespeichert, jedes spätere Einlesen liest nur noch. Text und Tabellen sind
    dieselben wie bei einer direkten Umwandlung; Abbildungen sind nicht Teil der
    umgewandelten Dateien, `images.mode` hat auf eine solche Quelle also keine
    Wirkung. Es geht rein um Geschwindigkeit:

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      pdf_options: {docling_json_dir: ../../data/handbook_json}
      chunking: {strategy: passthrough}   # Abschnitte sind bereits überschriftenbasiert
    ```

    Neben jede umgewandelte Datei schreibt das Einlesen einen kleinen `.stamp` mit
    der Docling-Version, den Umwandlungseinstellungen und einer Prüfsumme des PDFs.
    Ändert sich eines davon, wird das Dokument erneut umgewandelt: ein ersetztes
    PDF, eine geänderte `ocr`-Einstellung oder ein Docling-Update wirken beim
    nächsten Einlesen. Lösche eine umgewandelte Datei, um die Umwandlung zu
    erzwingen, oder den ganzen Ordner für alle. Um ein Dokument zu entfernen,
    lösche sein PDF und seine JSON-Datei. Das Protokoll des Einlesens zählt
    Dateien, nicht Dokumente: Jedes PDF erscheint zweimal, einmal als PDF und
    einmal als umgewandelte Datei.

    Du kannst den Ordner auch selbst mit Doclings eigenem Befehl füllen, zum
    Beispiel für ein einzelnes gescanntes PDF, das OCR braucht, während der Rest es
    nicht braucht:

    ```bash
    docling --to json --ocr --output ../../data/handbook_json ../../data/handbook/scan.pdf
    ```

    Eine selbst exportierte Datei hat keinen Stamp und bleibt, wie sie ist, egal
    was sich an den Einstellungen oder an Docling ändert. Dasselbe gilt für Ordner,
    die vor den Stamps umgewandelt wurden. Lösche diese Dateien nach einem
    Docling-Update, damit sie erneut umgewandelt werden.

=== "Text / Markdown"

    Jede Datei wird ein Abschnitt. Passt gut zur Aufteilung nach `fixed_size`.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # oder txt
      glob: "*.md"
    ```

=== "CSV"

    Ein Stück pro Zeile. Welche Spalten verwendet werden, beschreibst du mit
    einem [Field-Mapping](field-mapping.md). Nimm `passthrough`, damit jede Zeile
    ganz bleibt.

    ```yaml
    - name: faq
      path: ../../data/faq.csv
      format: csv
      chunking: {strategy: passthrough}
      field_mapping:
        delimiter: ";"
        text_template: "F: {question}\n\nA: {answer}"
        metadata: {title: question}
    ```

=== "JSON"

    Einfache Listen ebenso wie tief verschachtelte Dateien. Die vollständige
    Anleitung steht in der [Field-Mapping-DSL](field-mapping.md).

    ```yaml
    - name: articles
      path: ../../data/articles.json
      format: json
      field_mapping:
        record_path: items
        text_fields: [title, body]
        metadata: {title: title}
    ```

=== "Custom"

    Wenn deine Dateien eine ungewöhnliche Struktur haben, auf die nichts davon
    passt, kann jemand ein kleines Stück Python dafür schreiben. Siehe
    [Erweitern](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

### Welche Dateien eine Quelle einliest: `glob`

Zeigt `path` auf einen Ordner, entscheidet `glob`, welche Dateien darin zu dieser
Quelle gehören. Die Muster sind die von Pythons `pathlib`:

| Muster | Passt auf | Beispiel gegen die neun mitgelieferten Paper |
|---|---|---|
| `*` | beliebig viele Zeichen, auch keine | `*.pdf` nimmt alle neun |
| `?` | genau ein Zeichen | `*_202?_*.pdf` nimmt die sechs ab 2020 |
| `[seq]` | ein Zeichen aus der Menge | `Kage_20[12]*` nimmt Kage_2018 und Kage_2020 |
| `[!seq]` | ein Zeichen, das nicht in der Menge ist | `[!K]*.pdf` nimmt die sieben ohne K am Anfang |
| `**/` | in Unterordner absteigen | `**/*.pdf` |

Zwei Dinge, die Zeit kosten, wenn man sie nicht weiß:

- **Groß- und Kleinschreibung zählt.** `*.pdf` überspringt eine Datei namens
  `bericht.PDF`.
- **Klammer-Expansion gibt es nicht.** `{a,b}*.pdf` ist kein Fehler, es passt
  einfach auf nichts. Die Quelle liest dann null Dateien ein und der Lauf sieht
  erfolgreich aus. Nimm zwei Quellen oder eine Zeichenklasse.

Zeigt `path` auf eine einzelne Datei statt auf einen Ordner, wird `glob` ignoriert.

## 3. Chunking-Strategie wählen

Dokumente werden vor dem Speichern in Stücke zerteilt, weil sich kleine Stücke
besser durchsuchen lassen als ganze Dokumente. Dafür gibt es mehrere Wege:

| Strategie | Was sie tut | Wofür |
|---|---|---|
| `fixed_size` | Schneidet alle paar Zeichen, mit etwas Überlappung, damit an der Nahtstelle keine Sätze verlorengehen | Einfache Dokumente ohne klare Struktur |
| `heading` | Ein Stück pro Abschnitt, teilt nur zu lange Abschnitte | Dokumente mit ordentlichen Überschriften |
| `passthrough` | Ein Stück pro Datensatz, nie geteilt | Zeilen aus JSON/CSV-Dateien |
| `semantic` | Schneidet dort, wo das Thema wechselt, statt nach fester Länge. Genauer, kostet aber extra, weil der Text beim Einlesen analysiert wird | Lange Fließtexte ohne brauchbare Überschriften |
| `docling_hybrid` | Doclings eigene Methode. Kümmert sich selbst um Tabellen und Abbildungen und bemisst die Stücke so, dass sie immer ins Modell passen | Nur PDFs |

Setze unter `chunking:` einen Standard für alles und überschreibe ihn für einen
einzelnen Satz Dokumente mit einem `chunking:`-Block in dessen Eintrag.

## 4. Dokumente einlesen

!!! tip "Nur ein Dokument ergänzen oder entfernen?"
    [Dokumente ändern](managing-documents.de.md) ist die kurze, allgemein
    verständliche Fassung dieses Schritts. Der Rest dieser Seite dreht sich um
    Formate und Chunking.

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest                         # embedden + in die Collection upserten
python -m kb.ingest --only faq              # nur ein Satz Dokumente
```

Ein normaler Lauf hält die Collection im Gleichstand mit dem Ordner. Zu jeder
Datei wird ein Fingerabdruck ihres Inhalts gespeichert, deshalb gilt:

- eine **neue** Datei wird eingelesen,
- eine **geänderte** Datei wird erneut eingelesen, weil sich der Fingerabdruck
  geändert hat,
- eine **unveränderte** Datei wird übersprungen und kostet nichts,
- eine **gelöschte** Datei verliert ihre Einträge und taucht damit nicht mehr in
  Antworten auf.

Deine Dokumente zu verwalten heißt also einfach, den Ordner zu verwalten: Dateien
hinzufügen, austauschen oder löschen und denselben Befehl noch einmal ausführen.
Auch der komplette Austausch aller Dokumente funktioniert in einem Lauf: die alten
Einträge werden entfernt und die neuen Dateien eingelesen. Die App macht das auch von selbst: sie beobachtet die
Ordner und führt innerhalb von Sekunden nach einer Änderung dasselbe aus, im
Normalbetrieb rufst du das also nie manuell auf. Mit `DOCUMENT_WATCH=false`
schaltest du das ab.

!!! warning "Eine bewusste Ausnahme"
    Ist der Ordner **völlig leer**, während die Collection Dateien kennt, wird
    nichts gelöscht. Ein leerer Ordner liegt fast immer daran, dass eine
    Einbindung nicht hochkam oder `path` falsch ist, und die Collection deswegen
    stillschweigend zu leeren wäre schlimmer als nichts zu tun. Du bekommst einen
    entsprechenden Hinweis. Um eine Collection absichtlich zu leeren, nimm
    `--recreate`.

    Zum Löschen muss klar sein, welche Einträge zu der Datei gehören. Bei PDF-,
    Markdown- und Textquellen ist das der Fall. Eine `json`- oder `csv`-Quelle,
    deren `field_mapping` kein `source_file` schreibt, lässt sich nicht zuordnen;
    diese Einträge bleiben erhalten und werden gemeldet, `--recreate` räumt sie
    weg.

!!! danger "Jedes Dokument braucht einen eindeutigen Dateinamen"
    Dokumente werden **allein über den Dateinamen** erkannt, nicht über den Ordner,
    in dem sie liegen. Zwei Dateien, die beide `intro.pdf` heißen und in
    verschiedenen Ordnern derselben Collection liegen, sind für die App also
    dasselbe Dokument: nur eines davon wird durchsuchbar, das andere geht verloren.
    Auch das Löschen eines der beiden wird abgelehnt, weil sich seine Einträge nicht
    von denen des anderen unterscheiden lassen.

    Ein Lauf warnt dich jetzt, wenn ein Name doppelt vorkommt. Wenn diese Warnung
    erscheint, benenne die Dateien eindeutig um und lies sie mit `--recreate` neu
    ein.

!!! warning "Wann `--recreate` wirklich nötig ist"
    Fingerabdrücke betreffen nur die Dateien. Änderst du etwas, das sich auf die
    Zerlegung oder Suche **aller** Dokumente auswirkt, sind die vorhandenen
    Einträge veraltet und der ganze Bestand muss neu aufgebaut werden:

    ```bash
    python -m kb.ingest --recreate
    ```

    Das betrifft eine andere `chunking`-Strategie, andere Chunk-Größen oder den
    Wechsel von `images.mode: none` auf etwas anderes. Ein Wechsel des
    `embed_model` wird rundheraus abgelehnt, weil sich alte und neue Vektoren nicht
    vergleichen lassen; nimm `--recreate` oder eine neue
    `vector_store.collection`.

    Das alte `--skip-if-exists` gibt es weiterhin, nützt aber nichts mehr: es
    bricht den Lauf ab, sobald die Collection existiert, und verhindert damit genau,
    dass hinzugefügte, geänderte und gelöschte Dateien bemerkt werden.

## 5. Zitate sollen die Quelldatei öffnen

Damit ein Klick auf eine Quelle das Dokument wirklich öffnet, muss ihr Dateityp
als erlaubt aufgeführt sein:

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

**Deine Ordner musst du hier nicht auflisten.** Ausgeliefert wird aus `data_dir`
*und* aus jedem Ordner in `data_sources[]`. Ein Korpus, der über mehrere Ordner
verteilt ist, hat also ohne weitere Konfiguration klickbare Zitate. `data_dir`
wird zuerst durchsucht: Liegt derselbe Dateiname in zwei Ordnern, öffnet die Kopie
aus `data_dir`.

Für zwei andere Dinge bleibt `data_dir` maßgeblich: Dort werden Abbildungen und
ihre Beschreibungen abgelegt (`<data_dir>/figures`, `<data_dir>/descriptions`).

Die Angabe unter einer Antwort wird aus dem zusammengebaut, was sich die App beim
Lesen notiert hat: Dateiname, Titel und Seite. Die eingebauten Leseroutinen
füllen das automatisch aus. Wenn jemand einen [eigenen Parser](extending.md)
schreibt, sollte er dasselbe tun. Eigene Zusatzfelder zeigst du in Zitaten über
`citation.extra_fields` an.

## 6. Eine Instanz in Teile eines Korpus aufteilen

Ein Korpus besteht oft aus Teilen, die man getrennt durchsuchen will, weil sie
unterschiedlich lang sind, unterschiedliche Leser haben oder einfach nicht dieselbe
Frage beantworten. Statt mehrere Instanzen zu betreiben, kannst du sie in einer halten
und die Nutzenden wählen lassen, welcher Teil gesucht wird.

Drei Stellen müssen zusammenpassen: Jeder Teil bekommt eine eigene Datenquelle mit
einem Etikett, das Etikett wird zum Filtern freigegeben, und eine Rolle filtert
darauf.

```yaml
data_sources:
  - name: handbuecher
    path: docs/handbuecher
    format: pdf
    extra_metadata: { kategorie: handbuch }
  - name: merkblaetter
    path: docs/merkblaetter
    format: pdf
    extra_metadata: { kategorie: merkblatt }
    chunking: { strategy: heading }   # kurze Dokumente, anderes Chunking

retrieval:
  payload_indexes: [kategorie]                  # Qdrant-Index für das Feld
  filterable_fields: [source_file, kategorie]   # Freigabeliste

profiles:
  - id: handbuecher
    name: "Handbücher"
    retrieval_filters: { kategorie: handbuch }
  - id: alles
    name: "Alle Dokumente"                      # ohne Filter: sucht überall
```

`extra_metadata` wird auf jeden Chunk der Quelle kopiert, das Etikett reist also mit
dem Text mit. Es ist nicht das Einzige, worauf sich filtern lässt: Die Parser legen
ohnehin `source_file` (den Dateinamen), `page_start`, `page_end`, `section_title` und
`section_index` auf jeden Chunk, und all das darf ebenfalls in `filterable_fields`
stehen. Wegen `source_file` kann der Assistent eine Suche auf ein Dokument
einschränken. `filterable_fields` ist eine Freigabeliste: Ein Filter auf ein Feld, das
nicht darin steht, wird **stillschweigend ignoriert** — der übliche Grund, warum eine
Rolle scheinbar nichts tut. `payload_indexes` legt den Qdrant-Index dafür an; ohne ihn
funktioniert das Filtern weiterhin, scannt aber.

Ein Profil kann auf eine Kategorie filtern oder auf mehrere gleichzeitig:

```yaml
profiles:
  # eine Kategorie
  - id: handbuecher
    name: "Handbücher"
    retrieval_filters: { kategorie: handbuch }

  # mehrere Kategorien, ODER-verknüpft
  - id: bis-2023
    name: "Bis 2023"
    retrieval_filters: { zeitraum: [bis_2019, "2020_2023"] }

  # mehrere Felder, UND-verknüpft
  - id: alte-handbuecher
    name: "Ältere Handbücher"
    retrieval_filters: { zeitraum: bis_2019, kategorie: handbuch }

  # ohne Filter: sucht alles
  - id: alles
    name: "Alle Dokumente"
```

`kategorie` und `zeitraum` sind hier nur Beispielnamen: Es sind die Etiketten, die du
selbst in `extra_metadata` gesetzt hast.

Die beiden Formen ziehen in verschiedene Richtungen. Eine Liste in einem Feld ist ein
ODER und macht die Suche **größer** — ein Chunk passt, wenn einer der Werte zutrifft.
Mehrere Felder sind ein UND und machen sie **kleiner**, denn ein Chunk muss dann alle
Bedingungen erfüllen.

Deshalb ist UND nur über *verschiedene* Felder sinnvoll. Ein Chunk hat genau einen
Zeitraum, also wäre „zeitraum bis_2019 und zeitraum 2020_2023" immer leer: Kein Chunk
kann beides sein. Zwei Werte desselben Feldes brauchen die Listenform. Zweimal
denselben Schlüssel zu schreiben hilft nicht, in YAML gewinnt schlicht der zweite.

Jeder Teil kann außerdem anders gechunkt werden, und das ist oft der eigentliche
Gewinn: Ein zweiseitiges Merkblatt und ein zwanzigseitiges Handbuch wollen nicht
dieselbe Strategie.

!!! warning "Ein Filter ist kein Zugriffsrecht"
    `retrieval_filters` begrenzt, *was gesucht wird*. Wer die App benutzen kann, kann
    eine Rolle ohne Filter wählen und damit jeden Teil erreichen, und nichts in diesem
    Template vergibt oder verweigert Rechte pro Dokument. Hat ein Teil einen anderen
    Adressatenkreis als die übrigen, gib ihm eine eigene Collection und eine eigene
    Instanz. Ein Filter ist keine Grenze.

Eine Einschränkung noch: Der Assistent kann von sich aus auf ein einzelnes Dokument
einschränken (das `search`-Tool nimmt ein `document`-Argument, wenn `source_file`
freigegeben ist), aber keine Kategorie wählen. Die Kategorie kommt über die Rolle, die
der Nutzer ausgewählt hat.

Genau das in lauffähiger Form steht in
`examples/papers/rag.config.multi-source.yaml`. Die Datei teilt die neun
mitgelieferten Paper nach Erscheinungszeitraum in drei Teile, mit einer Rolle pro Teil
und einer, die alles durchsucht.

Zum Starten `RAG_CONFIG` in `apps/chainlit/.env` darauf zeigen lassen:

```bash
RAG_CONFIG=examples/papers/rag.config.multi-source.yaml
```

dann `docker compose up -d --build`. App und Ingest-Dienst lesen dieselbe Variable, ein
Eintrag schaltet also beide um, und der Ingest baut die Collection, bevor die App
startet.

Sie schreibt eine eigene Collection, liegt also neben dem kommentierten Beispiel statt
es zu ersetzen: Wer `RAG_CONFIG` zurückstellt, hat beide weiterhin.
