# Beispielkorpus

Dem Template liegen ein paar Dokumente bei, damit es direkt nach dem Clonen
läuft. Du bekommst zuerst einen funktionierenden Chatbot und tauschst später
deine eigenen Dokumente ein, wenn du gesehen hast, was er kann.

## Was enthalten ist

Drei frei verfügbare Forschungsartikel in `apps/chainlit/data/documents/`, alle
in *Scientific Reports* unter der Lizenz **CC BY 4.0** veröffentlicht:

| Datei | Artikel | Referenz |
|---|---|---|
| `Kage_2018_SciReports.pdf` | Luminescence lifetime encoding in time-domain flow cytometry | *Sci Rep* **8**, 16715 (2018), DOI [10.1038/s41598-018-35137-5](https://doi.org/10.1038/s41598-018-35137-5) |
| `Schmidt_2022_SciReports.pdf` | A multiparametric fluorescence assay for screening aptamer–protein interactions based on microbeads | *Sci Rep* **12**, 2961 (2022), DOI [10.1038/s41598-022-06817-0](https://doi.org/10.1038/s41598-022-06817-0) |
| `Lin_2024_SciReports.pdf` | Coupling a recurrent neural network to SPAD TCSPC systems for real-time fluorescence lifetime imaging | *Sci Rep* **14**, 3286 (2024), DOI [10.1038/s41598-024-52966-9](https://doi.org/10.1038/s41598-024-52966-9) |

Die vollständige Quellenangabe (Titel, Autor:innen, Lizenzlinks) steht in
`apps/chainlit/data/documents/SOURCES.md`. Diese Datei ist die maßgebliche und
wird hier deshalb nicht wiederholt.

## Die zugehörige Instanz

Diese Paper gehören zur Einstellungsdatei `examples/papers/rag.config.yaml`, die
sie unter dem Namen `papers` ablegt. Das ist die Vorzeige-Einrichtung mit allem
eingeschaltet:

- der Assistent darf alle fünf [Fähigkeiten](tools.md) nutzen: suchen, Dokumente
  auflisten, ein ganzes Dokument lesen, Text drumherum holen und eine Aussage
  gegenprüfen;
- Dokumente werden dort geteilt, wo das Thema wechselt, statt nach fester Länge;
- Bilder und Diagramme werden in Worten beschrieben, damit man sie findet, und
  über dem Absatz gezeigt, der sie erwähnt (siehe [Abbildungen](images.md)).

`.env.example` und `docker-compose.yml` zeigen bereits auf diese Datei, `make up`
startet also unverändert genau diese Einrichtung.

**Warum diese Paper:** Sie enthalten echte Tabellen und Abbildungen. Damit lässt
sich gut zeigen, dass Tabellen erhalten bleiben, Abbildungen beschrieben werden
und über mehrere Dokumente hinweg gesucht wird. Ein reiner Textkorpus würde nichts
davon sichtbar machen.

## Eigenen Korpus einsetzen

1. **Eigene PDFs nach `apps/chainlit/data/documents/` legen.** Deine Dateien
   bleiben auf deinem Rechner und werden nie ins Projekt hochgeladen. Die drei
   Beispiele darfst du löschen, nichts hängt an ihnen.

    ```bash
    cp ~/my-papers/*.pdf apps/chainlit/data/documents/
    ```

2. **Einstellungsdatei kopieren und einen neuen Collection-Namen vergeben.**
   `vector_store.collection` zu ändern ist **Pflicht**. Sonst landen deine
   Dokumente im selben Topf wie die Beispiele. Schreib bei der Gelegenheit auch
   `prompt.starter_questions` um, denn die mitgelieferten fragen nach den
   Beispiel-Papern.

    ```bash
    sed 's#\.\./\.\./data/documents#data/documents#' examples/papers/rag.config.yaml > my-rag.yaml
    ```

    Ein einfaches `cp` reicht nicht: Pfade in der Datei gelten relativ zur Datei,
    und das `sed` macht für die Kopie aus `../../data/documents` ein
    `data/documents`.

    ```yaml
    vector_store:
      collection: my_papers        # neuer Name, `papers` nicht wiederverwenden

    prompt:
      starter_questions:
        - "Welche Dokumente sind in der Wissensbasis?"
    ```

3. **Dokumente einlesen.**

    ```bash
    RAG_CONFIG=my-rag.yaml python -m kb.ingest --recreate
    ```

    Führe das erneut aus, wann immer du Dokumente hinzufügst, entfernst oder
    änderst.

Danach die App wie gewohnt starten. Andere Dateiformate und Arten, Text zu
zerteilen, stehen in [Daten hinzufügen](adding-data.md), den vollständigen
Durchlauf für den ersten Start findest du in
[Erste Schritte](getting-started.md).

!!! note "Wechsel des Embedding-Modells"
    Jede Collection merkt sich, welches Modell ihren Text durchsuchbar gemacht
    hat. Wenn du `models.embed_model` änderst, lies mit `--recreate` alles neu ein
    oder nimm einen neuen Collection-Namen. Eine Collection, die mit einem anderen
    Modell gebaut wurde, weist die App ab, weil sich die beiden Datenarten nicht
    vergleichen lassen.
