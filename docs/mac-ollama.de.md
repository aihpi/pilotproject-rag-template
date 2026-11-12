# Lokal auf dem Mac mit Ollama

Diese Seite richtet das Template und seine KI-Modelle zusammen auf einem Mac mit Apple-Chip ein, zum Beispiel einem Mac Studio. Die Modelle laufen in [Ollama](https://ollama.com), deine Dokumente und Fragen verlassen den Rechner also nicht. Das Template spricht mit Ollama genauso wie mit einem gehosteten KI-Dienst, deshalb muss am Code nichts geändert werden.

Das Beispiel nimmt Gemma 4 31B für Antworten, Bildbeschreibungen und die Bewertung der Antworten und Octen-Embedding-8B für die Suche. Wenn der Chat bei dir läuft, hält [Den Mac als gemeinsamen Server betreiben](mac-server.md) ihn am Laufen und öffnet ihn für Kolleginnen und Kollegen.

## Was du brauchst

- Einen Mac mit Apple-Chip und macOS 14 (Sonoma) oder neuer.
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) und Git.
- [Ollama](https://ollama.com/download), installiert als normale Mac-App.

Installiere Ollama als Mac-App und nicht in Docker. Docker kann unter macOS den Grafikchip des Mac nicht nutzen, ein Modell im Container rechnet also nur auf dem Prozessor und antwortet um ein Vielfaches langsamer.

## 1. Modelle herunterladen

```bash
ollama pull gemma4:31b
```

```bash
ollama pull benjaminj/octen-embedding-8b
```

```bash
ollama cp benjaminj/octen-embedding-8b octen-embedding-8b
```

Der letzte Befehl gibt dem Embedding-Modell einen zweiten Namen ohne Schrägstrich. Das Template liest einen Namen mit Schrägstrich als `anbieter/modell`, hielte `benjaminj` also für den Anbieter, und der Aufruf schlüge fehl. Der neue Name ist außerdem derselbe wie beim gehosteten Dienst, die Zeile `embed_model` in der Einstellungsdatei bleibt also, wie sie ist.

Das Octen-Modell ist ein Build aus der Community und nicht von Octen selbst veröffentlicht. Es ist komprimiert (quantisiert), seine Ergebnisse weichen deshalb leicht vom vollen Modell auf einem gehosteten Dienst ab. Wenn deine Dokumente eingelesen sind, lass [die Antwortbewertung](evaluation.md) einmal laufen und vergleiche die Werte mit den bisherigen.

Prüfe, ob Gemma kann, was das Template braucht:

```bash
ollama show gemma4:31b
```

Unter `Capabilities` braucht das Template `tools`, weil der Assistent deine Dokumente über Tool-Aufrufe durchsucht, und `vision` für die Bildbeschreibungen. Fehlt `tools`, nimm ein anderes Chat-Modell, das es mitbringt, zum Beispiel ein Qwen3-Modell.

## 2. Gemmas Kontextfenster prüfen

Das Kontextfenster ist die Textmenge, die ein Modell auf einmal liest. Eine einzige Antwort kann ganze Dokumente an das Modell schicken: Das Template erlaubt etwa 30.000 Tokens pro Tool-Ergebnis (`tools.max_context_chars`, 120.000 Zeichen) und ruft pro Antwort mehrere Tools auf, es braucht also ein Fenster von mindestens 128K Tokens. Ist ein Prompt länger als das Fenster, lässt Ollama einen Teil weg, und die App zeigt keinen Fehler.

Ollama wählt das Fenster danach, wie viel Speicher der Grafikchip nutzen darf, und ab 48 GB nimmt es 256K, das Maximum von Gemma 4 31B. Lade das Modell einmal und prüf, was es gewählt hat:

```bash
ollama run gemma4:31b "Hallo"
```

```bash
ollama ps
```

Die Spalte `CONTEXT` zeigt das Fenster. `262144` sind 256K, der Normalfall auf einem Mac mit viel Arbeitsspeicher, dann musst du nichts ändern. Auf einem Mac, auf den sich andere verlassen, [leg das Fenster fest](mac-server.md#gemmas-fenster-festlegen), damit es so bleibt.

??? info "Wenn das Fenster kleiner als 128K ist"

    Ollama wählt ein kleineres Fenster auf einem Mac mit weniger Arbeitsspeicher, in älteren Versionen oder wenn der Regler in der Ollama-App niedriger steht. Leg eine Kopie des Modells mit festem Fenster an und nimm in Schritt 4 überall den Namen der Kopie statt `gemma4:31b`. Für Gemma mit 128K:

    ```bash
    printf 'FROM gemma4:31b\nPARAMETER num_ctx 131072\n' > Modelfile
    ```

    ```bash
    ollama create gemma4-31b-128k -f Modelfile
    ```

    Die Kopie nutzt die schon heruntergeladenen Dateien des Modells und behält ihr Fenster, wenn Ollama oder der Mac neu startet. Für ein anderes Modell schreibst du dessen Namen hinter `FROM` und wählst ein Fenster, das es unterstützt.

    Liest dein Chat-Modell weniger als 128K, setz in deiner Einstellungsdatei auch `tools.max_context_chars` herunter, auf etwa so viele Zeichen, wie das Fenster des Modells Tokens hat, zum Beispiel `32000` für ein 32K-Modell. Sonst kann ein einziges Tool-Ergebnis das ganze Fenster füllen.

??? info "Wie viel Arbeitsspeicher jedes Fenster braucht"

    Das Modell selbst belegt etwa 20 GB. Dazu kommt ein Cache, der mit dem Fenster wächst. Ollama reserviert ihn vollständig, sobald es das Modell lädt, auch bei kurzen Fragen.

    | Fenster | Cache | Cache mit den zwei Einstellungen unten | Modell und Cache zusammen |
    |---|---|---|---|
    | 4K, Ollamas Standard unter 24 GB | 1,2 GB | 0,6 GB | etwa 21 GB |
    | 32K, Standard von 24 bis 48 GB | 3,5 GB | 1,8 GB | etwa 24 GB |
    | 128K, was das Template braucht | 11,6 GB | 5,8 GB | etwa 32 GB |
    | 256K, Standard ab 48 GB und Gemmas Maximum | 22,3 GB | 11,2 GB | etwa 42 GB |

    Die Cache-Größen sind aus der veröffentlichten Architektur von Gemma 4 31B berechnet, für eine Frage zur selben Zeit. Jede Frage, die parallel beantwortet wird, braucht ihren eigenen Cache. Ollama braucht außerdem ein paar GB Arbeitsspeicher, und Octen kommt mit seiner eigenen Größe dazu, die `ollama list` zeigt.

    Auf einem Mac mit weniger Arbeitsspeicher halbieren diese zwei Einstellungen den Cache, bei etwas weniger Genauigkeit:

    ```bash
    launchctl setenv OLLAMA_FLASH_ATTENTION 1
    ```

    ```bash
    launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
    ```

    Beende Ollama danach und öffne es wieder. Nach einem Neustart des Mac sind beide Einstellungen weg, außer du [startest Ollama mit ihnen](mac-server.md#alles-nach-einem-neustart-wieder-starten). Passt das Modell dann immer noch nicht, [gib den Modellen mehr vom Arbeitsspeicher](mac-server.md#den-modellen-den-arbeitsspeicher-geben).

Ein großes Fenster macht kurze Fragen nicht langsamer, aber der Mac braucht eine Weile, um einen sehr langen Prompt zu lesen, bevor er antwortet. Fragen, die ganze Dokumente heranziehen, dauern deshalb länger.

## 3. Das Template auf Ollama zeigen lassen

Hol das Projekt wie in [Erste Schritte](getting-started.md) beschrieben:

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template/apps/chainlit
cp .env.example .env
```

Trag in `.env` diese Adresse und diesen Schlüssel ein:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
LITELLM_API_KEY=ollama
```

Ollama prüft den Schlüssel nicht, die App erwartet aber einen, also geht jedes Wort. Die Adresse nutzt `host.docker.internal`, weil `localhost` im Container den Container selbst meint und nicht deinen Mac.

## 4. Modellnamen in die Einstellungsdatei schreiben

Leg neben dem Beispiel eine eigene Einstellungsdatei an und lass die App sie mit `RAG_CONFIG=examples/papers/my-rag.yaml` in `.env` benutzen:

```bash
cp examples/papers/rag.config.yaml examples/papers/my-rag.yaml
```

Lass die Kopie in `examples/papers/`. Pfade in einer Einstellungsdatei gelten relativ zur Datei, dort stimmen die Dokumentpfade des Beispiels also weiterhin. Eine Kopie in einem anderen Ordner findet keine Dokumente.

Ersetze in `my-rag.yaml` die Modellnamen des gehosteten Dienstes durch die aus Ollama:

```yaml
models:
  chat_model: gemma4:31b
  fallback_chat_model: null
  embed_model: octen-embedding-8b
  selectable_chat_models: [gemma4:31b]

images:
  vision_model: gemma4:31b
  vision_capable_models: [gemma4:31b]

evaluation:
  judge_model: gemma4:31b
```

`fallback_chat_model` ist das Modell, mit dem die App es erneut versucht, wenn das erste ausfällt. Mit nur einem Chat-Modell auf dem Mac gibt es nichts, worauf sie ausweichen könnte.

`selectable_chat_models` füllt die Modellauswahl im Einstellungsfeld. Bei einem gehosteten Dienst fragt die App die Liste selbst ab, mit einer Adresse, die schon auf `/v1` endet, landet diese Anfrage aber an der falschen Stelle. Deshalb steht die Liste hier ausgeschrieben. Trag jedes Chat-Modell ein, das du mit Ollama geladen hast und das zur Auswahl stehen soll.

Mit nur einem Modell bewertet der Prüfer Antworten, die sein eigenes Modell geschrieben hat. Für den Vergleich von Einstellungen wie dem Chunking reicht das, Werte zweier verschiedener Chat-Modelle lassen sich so aber nicht vergleichen. Siehe [Antwortqualität prüfen](evaluation.md).

Wechselst du später `embed_model`, passen die gespeicherten Dokumente nicht mehr zum Suchmodell. Nimm dann einen neuen Collection-Namen oder lies alles mit `--recreate` neu ein, wie in [Dokumente ändern](managing-documents.md) beschrieben.

## 5. Prüfen und starten

```bash
make check
```

```bash
make up
```

`make check` testet die Modelladresse, bevor irgendetwas anderes läuft. `make up` baut und startet alles, liest die Dokumente ein und öffnet den Chat unter <http://localhost:8000>. Mit `images.mode: describe` geht beim ersten Einlesen jede Abbildung einmal an Gemma, bei vielen Dokumenten dauert das eine Weile.

Melde dich mit `admin` / `admin` an, stell eine Frage zu deinen Dokumenten und klick auf eine Quelle unter der Antwort. Öffnet sich das Dokument, funktioniert alles.

## Wenn die Antworten zu langsam sind

Ollama rechnet die Modelle über llama.cpp. Server, die auf Apples eigenem Framework MLX aufbauen, sind auf dem Mac oft schneller, und zwei davon passen zum Template:

- [LM Studio](https://lmstudio.ai) mit Modellen im MLX-Format. Sein Server lauscht auf Port 1234, die Adresse wird also `http://host.docker.internal:1234/v1`. Modellnamen in LM Studio enthalten einen Schrägstrich, gib jedem Modell beim Laden deshalb einen Namen ohne.
- [vllm-mlx](https://vllm-mlx.is-a.dev/), das auch Anfragen mehrerer Personen gleichzeitig effizient abarbeitet. Es ist ein junges Community-Projekt, teste Tool-Aufrufe und Bildbeschreibungen also, bevor du dich darauf verlässt.

Was du auch nimmst, es muss Antworten mit Tool-Aufrufen, Bildbeschreibungen und Embeddings unter einer Adresse anbieten, unter Namen ohne Schrägstrich. Schafft ein Server nicht alles, setz einen [LiteLLM-Proxy](https://docs.litellm.ai/docs/simple_proxy) davor: Er gibt jedem Modell den Namen, den das Template erwartet, und leitet es an den Server weiter, auf dem es läuft. Das Template ist dafür gebaut, mit so einem Proxy zu sprechen.

Bevor du endgültig wechselst, lass den [Benchmark](evaluation.md) auf beiden Einrichtungen laufen. Er zeigt, ob der neue Server schneller ist und ob die Antworten so gut bleiben.
