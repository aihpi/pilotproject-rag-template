# RAG-Template

Ein Chat-Assistent, der Fragen zu deinen eigenen Dokumenten beantwortet und zu
jeder Antwort die genaue Seite zeigt, aus der sie stammt.

Du richtest ihn auf einen Ordner mit Dateien. Er liest sie ein, und danach können
Leute in normaler Sprache Fragen stellen. Zum Einrichten bearbeitest du **eine
einzige Einstellungsdatei**. Programmieren musst du nicht.

Dahinter stecken: **Chainlit** für das Chat-Fenster, **LiteLLM** für die
Verbindung zu den KI-Modellen und **Qdrant** als Speicher für deinen
durchsuchbaren Text.

## Was per Konfiguration änderbar ist

Alles Folgende ist eine Einstellung in dieser einen Datei. Code musst du nie
anfassen.

| Was | Einstellung | Deine Möglichkeiten |
|---|---|---|
| **Deine Dokumente** | `data_sources[]` | PDFs, einfacher Text und Markdown, oder tabellenartige `json`/`csv`-Dateien |
| **Wie Text zerteilt wird** | `chunking.strategy` | nach Größe, nach Überschrift, ein Stück pro Datensatz, nach Bedeutung oder auf Doclings eigene Art. Pro Dokumentart unterschiedlich möglich |
| **Welches Modell antwortet** | `models.chat_model` | jedes Modell, das dein KI-Dienst anbietet |
| **Welches Modell Text durchsuchbar macht** | `models.embed_model` | jedes Suchmodell, das dein KI-Dienst anbietet |
| **Wie Quellen aussehen** | `citation.*` | Formulierung und Aufbau der Angabe unter einer Antwort |
| **Was dem Assistenten gesagt wird** | `prompt`, `profiles` | seine Anweisungen, die Beispielfragen und optionale Rollen, die seine Suche einschränken |
| **Was der Assistent tun darf** | `tools.enabled` | suchen, alle Dokumente auflisten, ein ganzes Dokument lesen, Text drumherum holen, eine Aussage gegenprüfen |
| **Bilder und Diagramme** | `images.mode` | ignorieren, in Worten beschreiben (damit auffindbar) oder einem Modell zeigen, das Bilder sehen kann |

## Wie alles zusammenspielt

Deine Dokumente werden gelesen, in Stücke geteilt und durchsuchbar gespeichert.
Kommt eine Frage, werden die passenden Stücke herausgesucht und dem KI-Modell
gegeben, das daraus die Antwort samt Quellen schreibt.

```
data_sources ─► Parser (nach Format) ─► Chunker (nach Strategie) ─► Embedding ─► Qdrant
                                                                                 │
Nutzerfrage ─► Retrieval (top_k, optionale Filter) ◄─────────────────────────────┘
           └─► LLM-Tool-Schleife ─► Antwort + konfigurationsgesteuerte Zitate
```

- **[Erste Schritte](getting-started.md)**: installieren, Dokumente einlesen, starten.
- **[Lokal auf dem Mac mit Ollama](mac-ollama.md)**: die Modelle auf dem eigenen Mac betreiben.
- **[Den Mac als gemeinsamen Server betreiben](mac-server.md)**: am Laufen halten und mit Kollegen teilen.
- **[Beispielkorpus](example-corpus.md)**: die mitgelieferten Paper und wie du sie austauschst.
- **[Daten hinzufügen](adding-data.md)**: eigene Dateien verwenden.
- **[Agentische Tools](tools.md)**: was der Assistent tun darf.
- **[Abbildungen](images.md)**: wie Bilder und Diagramme behandelt werden.
- **[System-Prompts](prompts.md)**: die Anweisungen selbst schreiben oder schreiben lassen.
- **[Konfigurationsreferenz](configuration.md)**: jede Einstellung im Detail (technisch).
- **[Field-Mapping-DSL](field-mapping.md)**: JSON/CSV in Text umwandeln (technisch).
- **[Erweitern](extending.md)**: ein neues Dateiformat unterstützen (mit Python).
- **[Feedback-Export](feedback-export.md)**: Nutzerbewertungen sammeln und herunterladen.
- **[Antwortqualität prüfen](evaluation.md)**: Antworten bewerten und Konfigurationen vergleichen.
