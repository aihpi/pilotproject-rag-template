# Abbildungen & Bilder

Standardmäßig **wirft die App die Bilder weg**, wenn sie ein PDF liest, und
behält nur die Bildunterschriften als Text. Bei Papern und Handbüchern, deren
Argumentation in einem Diagramm steckt, geht damit viel verloren. Der
`images:`-Block macht aus Bildern und Diagrammen Inhalte, die der Assistent
wirklich finden und zeigen kann.

```yaml
images:
  mode: none            # none | describe | attach
  vision_model: gemma-4-31b
  images_scale: 2.0     # Docling-Renderskalierung (2.0 = höher aufgelöste Ausschnitte)
  inline_figures: true
  show_unmarked_figures: true
  inline_figure_caption: false
  max_attach_images: 3
  attach_image_max_px: 768
  vision_capable_models: [gemma-4-31b]
```

Den Modus kannst du ohne Bearbeiten der Datei über die Einstellung `IMAGES_MODE`
umschalten. So lassen sich zwei Modi bequem am selben Datenbestand vergleichen.

## Drei Modi

| Modus | Beim Einlesen der Dokumente | Wenn jemand fragt |
|---|---|---|
| `none` | Bilder werden ignoriert | Nichts (die billigste Variante) |
| `describe` | Jedes Bild wird ausgeschnitten und von `vision_model` in Worten beschrieben | Die Beschreibung wird wie jeder andere Text durchsucht und zitiert |
| `attach` | Wie `describe` | Zusätzlich wird **das Bild selbst** einem Modell gezeigt, das sehen kann |

Mit `describe` wird jedes Bild zu einem ganz normalen, durchsuchbaren Eintrag,
dessen Text die vom Modell geschriebene Beschreibung ist, in der Sprache deiner
Einrichtung. Dazu gespeichert werden der Hinweis, dass es eine Abbildung ist, die
Seitenzahl und der Ort der Bilddatei.

Das hat einen praktischen Nebeneffekt: Weil die Suche danach nur noch Text
berührt, funktioniert `describe` anschließend mit **jedem** Chat-Modell. Das
Modell, das Bilder sehen kann, wird nur einmal gebraucht, beim Einlesen.

`attach` nutzt genau denselben Einlese-Schritt und legt das Bild bei der Antwort
noch obendrauf.

## Kosten und wann ein Re-Ingest nötig ist

Die Regel ist einfach: **ein Aufruf des Bildmodells pro Abbildung, einmalig beim
Einlesen.** Das Beantworten von Fragen kostet in `describe` nichts extra.

!!! warning "Moduswechsel"
    Der Wechsel von `none` zu `describe` oder `attach` bedeutet, dass **alle
    Dokumente neu eingelesen** werden müssen, denn die Bilder wurden beim ersten
    Mal nie ausgeschnitten oder beschrieben. Der Wechsel zwischen `describe` und
    `attach` **nicht**: Bilder und Beschreibungen liegen bereits vor, es ändert
    sich nur das Verhalten beim Antworten. Die nötigen Flags stehen unter
    [Daten hinzufügen](adding-data.md).

Dokumente neu einzulesen heißt nicht, die Bilder neu zu beschreiben. Jede
Beschreibung wird in eine Datei neben deinen Dokumenten geschrieben und
wiederverwendet. Ein Neuaufbau, eine Änderung an der Aufteilung in Textstücke oder
ein Einlesen, das auf halbem Weg abgebrochen ist, kosten für schon beschriebene
Bilder also nichts.

Es sind einfache Markdown-Dateien, eine pro Bild, nach Dokument gruppiert. Du
kannst also nachlesen, was das Modell geschrieben hat:

```
data/documents/
├── Kage_2018_SciReports.pdf
├── figures/
│   └── Kage_2018_SciReports__fig0.png
└── descriptions/
    └── Kage_2018_SciReports/
        ├── fig0.md
        └── fig1.md
```

Jede Datei beginnt mit einem kurzen Kopf, einem Fingerabdruck von Bild, Prompt und
Bildmodell, darunter steht die Beschreibung selbst. Wiederverwendet wird nur,
solange dieser Fingerabdruck passt. Änderst du `describe_prompt`, `vision_model`
oder `describe_image_max_px`, werden korrekterweise neue Beschreibungen angefragt.

Du darfst eine Beschreibung von Hand korrigieren, und weil der Kopf über das
Behalten entscheidet, bleibt dein Text erhalten. Beim Assistenten kommt er aber
nicht sofort an: Das automatische Update reagiert nur auf Änderungen an den
Dokumenten selbst. Eine handgeschriebene Beschreibung wird deshalb erst beim
nächsten **vollständigen Neuaufbau** übernommen.

Willst du für ein Dokument neue Beschreibungen erzwingen, lösche seinen Ordner
unter `descriptions/` und lies die Dokumente mit `--recreate` neu ein. `--recreate`
ist nötig, weil ein unverändertes PDF sonst übersprungen wird, siehe
[Dokumente ändern](managing-documents.de.md).

## Abbildungen in der Antwort anzeigen

Zwei getrennte Schalter entscheiden, was Leute tatsächlich sehen:

| `inline_figures` | `show_unmarked_figures` | Ergebnis |
|---|---|---|
| `true` | `true` | Das passende Bild erscheint **über** dem Absatz, um den es geht, weitere gefundene Bilder als kleine Vorschau unter der Antwort (Standard) |
| `true` | `false` | Nur Bilder, auf die das Modell ausdrücklich gezeigt hat. Am aufgeräumtesten, aber es erscheint nichts, wenn das Modell das Zeigen vergisst |
| `false` | `true` | Keine Bilder im Text, aber jedes gefundene Bild als kleine Vorschau unter der Antwort |
| `false` | `false` | Überhaupt keine Bildanzeige. Die Beschreibungen bleiben durchsuchbar und zitierbar |

Mit `inline_figure_caption: true` wird zusätzlich die Originalunterschrift kursiv
unter das Bild gesetzt.

## Wie der Abbildungs-Marker funktioniert

Lohnt sich zu verstehen, weil es sowohl die guten als auch die merkwürdigen Fälle
erklärt:

1. Zusammen mit dem Text bekommt das Modell pro Bild eine zusätzliche Zeile, eine
   Art Platzhalter: `Abbildungs-Marker: {{ABB:<dateiname>}}`.
2. Es wird gebeten, diesen Platzhalter unverändert und in einer eigenen Zeile
   direkt vor den Absatz zu setzen, der das Bild behandelt.
3. Danach tauscht die App den Platzhalter gegen das echte Bild.

Lässt sich ein Platzhalter nicht zuordnen, wird er stillschweigend entfernt
(niemand sieht je ein rohes `{{ABB:…}}`) und das Bild erscheint einfach unter der
Antwort. Die Anweisung an das Modell kannst du über
`images.figure_marker_prompt` umformulieren.

## Wenn Abbildungen keine Beschreibung haben

Manchmal scheitert das Beschreiben einer Abbildung, meist weil der KI-Dienst
kurzzeitig ausgelastet ist oder die Abbildung ungewöhnlich groß. Die App versucht
es einige Male erneut, und wenn es dann noch fehlschlägt, wird die Abbildung
weggelassen statt ohne Beschreibung gespeichert.

**Woran du es merkst.** Achte auf die Ausgabe, während deine Dokumente eingelesen
werden. Jedes Dokument meldet seine Fehlschläge:

```
[ingest] Alam_2026_SciReports.pdf: 2 of 17 figure descriptions failed
```

Fehlt so eine Zeile, ist nichts fehlgeschlagen. Beurteile es nicht anhand der
gespeicherten Einträge: Eine beschriebene Abbildung wird oft in mehrere Stücke
zerlegt, von denen eines nur `Abbildung 7 (Seite 3)` lauten kann. Das ist normal
und bedeutet nicht, dass die Beschreibung fehlt.

**Wie du es behebst.** Nur nötig, wenn du tatsächlich Fehlschläge gesehen hast.
Einzelne Abbildungen zu reparieren geht nicht, es wird also der ganze Bestand neu
aufgebaut:

```bash
make reingest        # mit Docker: baut neu und startet die App neu
```

Oder ausgeschrieben:

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
docker compose up -d
```

Ohne Docker:

```bash
RAG_CONFIG=examples/papers/my-rag.yaml uv run python -m kb.ingest --recreate
```

!!! warning "Das kostet erneut Geld"
    Jede Abbildung wird noch einmal beschrieben, der komplette Bestand also erneut
    berechnet. Der Beispielkorpus hat 170 Abbildungen. Prüfe mit
    `images.mode: none` vorab, wenn du nur testen willst, ob das Einlesen
    überhaupt funktioniert.

## Wo die Bilder liegen

Bilder werden als PNG-Dateien unter `<sources.data_dir>/figures/` gespeichert
(anderer Ort über `images.figure_store_dir`). Herausgegeben werden sie nur an
angemeldete Nutzer, also nach derselben Regel wie deine Quelldokumente. Der
Ordner ist bewusst von der Versionsverwaltung ausgenommen: Er wird bei jedem
Einlesen neu erzeugt und gehört nicht ins Projekt.

## `attach` braucht ein vision-fähiges Chat-Modell

Nicht jedes Chat-Modell kann Bilder ansehen, und der KI-Dienst verrät nicht,
welche es können. Deshalb musst du sie selbst unter
`images.vision_capable_models` auflisten. Steht das gerade genutzte Modell nicht
auf deiner Liste, fällt die App still auf eine normale Textantwort zurück und
notiert einen Hinweis. Die Antwort ist weiterhin korrekt, nur ohne Bild.

Vor dem Senden wird jedes Bild so verkleinert, dass seine längste Seite höchstens
`attach_image_max_px` beträgt, und in JPEG umgewandelt. Diesen Schritt nicht
entfernen: Bilder in Originalauflösung lassen KI-Dienste die Anfrage rundheraus
ablehnen (Fehler **413**). `max_attach_images` begrenzt, wie viele Bilder eine
einzelne Antwort mitführen darf.

!!! note "Eine kosmetische Eigenheit"
    Das Chat-Fenster zeigt Bilder in einem breiten Rahmen mit festem
    Seitenverhältnis. Hohe Bilder, etwa Hochformat-Diagramme oder gestapelte
    Kurven, bekommen deshalb links und rechts leere Ränder. Es wird nichts
    abgeschnitten; ein Klick öffnet das Bild vollständig.
