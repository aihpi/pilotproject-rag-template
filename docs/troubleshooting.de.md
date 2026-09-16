# Wenn etwas nicht funktioniert

Fehler, die wirklich vorgekommen sind, mit Ursache und Lösung.

## Hier anfangen

Die meisten Probleme haben eine von drei Ursachen: deine Einstellungen, deine
Verbindung oder den KI-Dienst. Das hier sagt dir, welche davon, aus `apps/chainlit`:

```bash
make check
```

Der Befehl probiert jedes Modell mehrmals und meldet das Ergebnis. Alles außer
durchgehend grün ist unten erklärt.

## Der Build bricht mit „network timeout" ab

`docker compose up -d --build` installiert rund 470 MB an Paketen, und uv lädt viele
davon gleichzeitig. Auf einer langsamen oder geteilten Leitung kann einer dieser
Downloads so lange stillstehen, dass uv aufgibt und der ganze Build abbricht:

```
× Failed to download `tokenizers==0.22.2`
╰─▶ Failed to download distribution due to network timeout.
```

Jedes Mal wird ein anderes Paket genannt. Genau das ist der Hinweis: Die Pakete sind in
Ordnung, die Leitung ist das Problem.

Starte den Build noch einmal. Docker behält jeden Schritt, der schon fertig war, aber
der abgebrochene Schritt lädt von vorn, deshalb reicht Wiederholen auf einer wirklich
langsamen Verbindung nicht. Was hilft, sind weniger gleichzeitige Downloads, denn dann
bekommt jeder mehr von der Leitung und bleibt seltener stehen:

```
UV_HTTP_TIMEOUT=1800 UV_CONCURRENT_DOWNLOADS=2 docker compose build
```

Ein Kabel statt WLAN, oder das VPN ausgeschaltet zu lassen, hilft aus demselben Grund.

## Manche Aufrufe gehen, andere nicht

Die Prüfung meldet etwa `only 3 of 5 attempts worked`, oder beim Einlesen erscheinen
viele `Connection error`-Meldungen, während einzelne Abbildungen durchkommen.

Dieses Muster heißt: an deinen Einstellungen liegt es nicht. Eine falsche Adresse
oder ein falscher Schlüssel scheitern immer, nicht manchmal. Die Verbindung zwischen
dir und dem Dienst verliert Anfragen.

Beim Einlesen fällt das besonders auf, weil dabei hunderte Aufrufe stattfinden: Eine
Verbindung, die jede dritte Anfrage verliert, scheitert dutzende Male, während eine
einzelne Chat-Nachricht problemlos klappt und das Problem verdeckt.

Was du der Reihe nach probieren kannst:

1. **VPN ausschalten**, falls du eines nutzt. Das ist die häufigste Ursache.
2. **Ein anderes Netz nehmen.** Ein stark genutztes Netz in einem vollen Raum macht
   genau das.
3. **Warten und erneut versuchen.** Auch der Dienst selbst kann überlastet sein.
4. **Erst ohne Bilder einlesen**, mit `images.mode: none`. Das entfernt die meisten
   Aufrufe, du kannst also prüfen, ob alles andere funktioniert, bevor du für
   Bildbeschreibungen zahlst.

## „database disk image is malformed"

Die Datei mit deinem **Chat-Verlauf** ist beschädigt. Sonst ist nichts betroffen:
Deine Dokumente und alles, was der Assistent daraus gelernt hat, liegen getrennt
davon.

**Warum das passiert ist.** Diese Datei lag früher in einem Ordner, den dein Rechner
und die App gemeinsam nutzen. Das ist praktisch, aber diese Art von Datei verträgt es
nicht: Wird die App genau im falschen Moment gestoppt, während sie schreibt, kann die
Datei zerbrechen. Mehrere Neustarts hintereinander machen es wahrscheinlicher.

**Wen es betrifft.** Nur Mac und Windows. Unter Linux verhält sich der gemeinsame
Ordner anders, dort kann das nicht auftreten.

**Schon behoben.** Die Datei liegt jetzt an einem Ort, den die App für sich allein
hat, und dein vorhandener Verlauf wurde automatisch übernommen. Du solltest das nicht
wieder sehen.

**Wenn du es jetzt siehst**, rette die alten Nachrichten so, aus `apps/chainlit`:

```bash
docker compose stop chainlit
mv .chainlit/chat_history.sqlite3 .chainlit/chat_history.broken
sqlite3 .chainlit/chat_history.broken ".recover" | sqlite3 .chainlit/chat_history.sqlite3
docker compose up -d
```

Die dritte Zeile baut aus allem, was noch lesbar ist, eine gesunde Datei. Behalte die
`.broken`-Datei, bis du geprüft hast, dass dein Verlauf stimmt.

## Ein neues PDF erscheint nicht

Lege die Datei in `apps/chainlit/data/documents/` und warte einige Sekunden. Die App
beobachtet den Ordner und liest neue Dateien von selbst ein.

Wenn nichts passiert, prüfe der Reihe nach:

1. **Läuft die App?** `docker compose ps`
2. **Was sagt sie?** `docker compose logs -f chainlit` und nach `[watch]`-Zeilen
   schauen. Sie nennen jede Datei, die hinzugefügt, geändert oder entfernt wurde.
3. **Ist der Name eindeutig?** Dokumente werden allein über den Dateinamen erkannt.
   Eine zweite Datei namens `intro.pdf` gilt als dasselbe Dokument, und der Lauf warnt
   davor.
4. **Ist es der richtige Ordner?** Bei einer eigenen Einstellungsdatei entscheidet
   `data_sources[]`, welcher Ordner beobachtet wird, und Pfade zählen ab dem Ordner,
   in dem die Einstellungsdatei liegt.

Siehe [Dokumente ändern](managing-documents.de.md).

## „the 'tesseract' binary was not found"

Du hast `ocr: true` eingeschaltet. Damit wird Text aus Seiten gelesen, die
eigentlich Bilder sind. Das Programm dafür ist absichtlich nicht mitgeliefert, damit
der Download klein bleibt.

Die meisten PDFs brauchen das nicht. Ein normales PDF enthält schon echten Text, den
du markieren und kopieren kannst, und die Standardeinstellung liest solche Dateien
korrekt. Nötig ist es nur bei Scans, bei denen jemand eine Papierseite fotografiert
oder eingescannt hat.

Sind deine Dokumente wirklich Scans, nennt die Fehlermeldung die genauen drei Zeilen,
mit denen das Programm nachinstalliert wird.

## Die App erreicht kein Modell auf meinem eigenen Rechner

Im Container bedeutet `localhost` den Container selbst, nicht deinen Rechner. Nimm
stattdessen `host.docker.internal`:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
```

Denk daran, dass die Modellnamen in der Einstellungsdatei von einem gehosteten Dienst
kommen und auf deinem eigenen Server nicht existieren. Die musst du also auch ändern.

## Port 8000 ist schon belegt

Etwas anderes auf deinem Rechner benutzt ihn. Wähle in `.env` einen anderen:

```
APP_PORT=8080
```

Dasselbe geht mit `QDRANT_PORT` und `POSTGRES_PORT`. Es ändert sich nur der Port auf
deinem Rechner, innerhalb der App musst du nichts anpassen.

## Der Assistent antwortet, aber es erscheinen keine Quellen

Quellenangaben und Anschlussfragen werden über deutsche Formulierungen erkannt und
funktionieren deshalb nur mit `language: de` in der Einstellungsdatei. Deine Dokumente
selbst können in jeder Sprache sein. Das ist eine bekannte Einschränkung.

## Eine Quelle wird genannt, aber der Klick tut nichts

Die Angabe erscheint unter der Antwort als reiner Text statt als Link, oder das
Quellen-Panel überspringt eine Nummer (Quelle 2, 3, 5 ohne 4).

Die App nennt die Datei, die sie nicht verlinken konnte. Suche im Protokoll nach:

```bash
docker compose logs --tail 100 chainlit | grep citation_unlinkable
```

Jede Zeile nennt die zitierte Datei und die Nummer, unter der sie zitiert wurde.
Drei Ursachen kommen infrage:

1. **Der Dateityp wird nicht ausgeliefert.** Öffnen lassen sich nur Endungen, die
   unter `sources.served_extensions` stehen. Eine `.docx`-Quelle wird eingelesen
   und beantwortet, ist aber erst klickbar, wenn `.docx` dort ergänzt wird.
2. **Die Datei liegt nicht mehr auf der Platte.** Suchindex und Dokumentenordner
   stimmen nicht überein: Die Textstücke stammen aus einer Datei, die seither
   umbenannt, verschoben oder gelöscht wurde. Ein erneutes Einlesen behebt es.
3. **Zwei Dateien heißen gleich.** Dokumente werden allein über den Dateinamen
   erkannt. Gibt es denselben Namen zweimal, lässt sich nur eine davon öffnen:
   eine Kopie oben im Ordner gewinnt gegen eine in einem Unterordner, zwischen
   zwei Kopien in Unterordnern ist der Gewinner unbestimmt. Benenne eine davon um.

Deine Ordner musst du **nicht** unter `sources:` auflisten. Wenn ein Korpus über
mehrere `data_sources[]`-Ordner verteilt ist und nur die Dateien aus `data_dir`
verlinkt werden, ist das ein Fehler aus der Zeit vor diesem Verhalten und keine
falsche Konfiguration.

Die übersprungene Nummer im Panel ist Absicht. Die Nummer in der Antwort ist die
Position, die das Modell wirklich gelesen hat. Eine nicht verlinkbare Quelle lässt
deshalb eine Lücke, statt alle folgenden Nummern um eins zu verschieben. Die Lücke
ist eine wahre Aussage darüber, was abgerufen wurde.

## Das Einlesen bricht mit einem Fehler ab

Der Lauf endet jetzt mit einer Erklärung statt mit einer Textwand: was der Fehler war,
was er bedeutet, und nummerierte Schritte. Genannt wird auch `make check`, das
Verbindung und Einstellungen für sich prüft.

Eines solltest du wissen: Dokumente aus früheren Läufen bleiben erhalten, aber was beim
Abbruch gerade gelesen wurde, ist nicht gespeichert. Beim nächsten Lauf werden diese
Dateien noch einmal gelesen.

## Das Bewertungs-Abzeichen erscheint nicht

Das Abzeichen über dem Eingabefeld zeigt, wie gut jede Antwort abgeschnitten hat.
Wenn es nie auftaucht, prüfe der Reihe nach:

1. **Ist die Evaluation eingeschaltet?** Die Schema-Vorgabe ist aus, die
   mitgelieferte Instanz `examples/papers` hat sie aber an — prüfe also zuerst,
   welche Config du fährst. In einer eigenen Config muss
   `evaluation: {enabled: true}` stehen, und die App muss danach neu gestartet
   werden. Der Evaluations-Dienst startet mit dem übrigen Stack — prüfe mit
   `docker compose ps`, ob er läuft, falls nie Werte erscheinen.
2. **Hast du eine Frage gestellt, die die Wissensbasis nutzt?** Die Evaluation
   bewertet nur Antworten, bei denen etwas nachgeschlagen wurde. Hat der Assistent
   aus allgemeinem Wissen geantwortet oder gesagt „steht nicht in den Dokumenten",
   gibt es nichts zu bewerten.
3. **Warte etwa fünfzehn Sekunden.** Die Bewertung läuft im Hintergrund, nachdem
   die Antwort schon angezeigt wird. Das Abzeichen erscheint, sobald das Ergebnis
   da ist.

Hilft das alles nicht, schau ins App-Protokoll nach Zeilen, die mit `[WARN]
eval_status_unavailable` beginnen. Das heißt, die App erreicht den
Evaluations-Dienst nicht.

## Werte sind alle leer (NULL)

Das Abzeichen erscheint, aber die Zahlen fehlen. Das bedeutet, dass das
Bewertungsmodell (der *Judge*) versucht hat, die Antwort zu benoten, aber
gescheitert ist.

Die häufigste Ursache: das Judge-Modell ist beim KI-Dienst gerade nicht
erreichbar. Prüfe:

```bash
docker compose logs --tail 20 eval
```

Stehen dort `500 Internal Server Error` oder `Connection error`, ist das
Judge-Modell beim Dienst ausgefallen. Warte und versuch es nochmal, oder setze
`evaluation.judge_model` in der Einstellungsdatei auf ein anderes Modell, das dein
Dienst anbietet.

Ein fehlgeschlagener Wert wird als leer gespeichert, nie als Null. So zieht ein
vorübergehender Ausfall die Mittelwerte nicht nach unten.

## Das Abzeichen zeigt Werte, denen ich nicht traue

Zwei Dinge zum Prüfen:

**Vergleichst du über verschiedene Modelle hinweg?** Wenn `evaluation.judge_model`
nicht gesetzt ist, benotet das Modell, das *geantwortet* hat, auch seine eigene
Antwort. Das macht Werte unzuverlässig, wenn du im Einstellungs-Panel zwischen
Modellen wechselst, weil jedes Modell sich selbst anders beurteilt. Setze
`judge_model` auf ein festes Modell, das nicht das bewertete ist.

**Was messen die Kennzahlen eigentlich?**

- **Treue (Faithfulness)** prüft, ob die Aussagen der Antwort von den
  abgerufenen Textstellen gedeckt sind. Sie zerlegt die Antwort in einzelne
  Behauptungen und prüft jede gegen die Quellen. Die Formel ist schlicht:
  gedeckte Aussagen geteilt durch alle Aussagen. Ein Wert von 0,5 heißt nicht
  „mittelmäßig". Er heißt, die Hälfte der Aussagen dieser Antwort war nicht
  durch die Quellen belegt. Das Abzeichen-Panel listet jede Aussage mit Häkchen
  oder Kreuz und der Begründung des Judges auf, du siehst also genau, welche
  durchgefallen ist.

- **Relevanz (Relevance)** prüft, ob die Antwort die gestellte Frage tatsächlich
  beantwortet. Dazu werden aus der Antwort Fragen erzeugt und mit der echten
  Frage über Embedding-Ähnlichkeit verglichen. Liegen die erzeugten Fragen nahe
  an der echten, ist die Antwort relevant. Eine Relevanz von 0 % heißt meistens
  nicht, dass die Antwort am Thema vorbeiging. Meistens heißt es, dass der Assistent die
  Antwort verweigert hat („steht nicht in den Dokumenten"), was das richtige
  Verhalten ist, wenn die Wissensbasis die Frage nicht abdeckt.

Beide Werte kommen aus dem Evaluations-Framework
[RAGAS](https://docs.ragas.io/). Sie sind *referenzfrei*: von Hand geschriebene
richtige Antworten braucht es nicht. Der Preis dafür: sie messen, was das
Bewertungsmodell *denkt*, und das trägt dessen Meinungen und Rauschen mit.
Vergleiche Veränderungen zwischen Durchläufen („hat diese Konfigurationsänderung
geholfen?"), lies eine einzelne Zahl nicht als Urteil.

## Hier steht nichts, was passt

Am schnellsten kommst du über das Protokoll weiter:

```bash
docker compose logs --tail 100 chainlit
```

Fehler beim Einlesen von Dokumenten beginnen mit `[ingest]`, Meldungen des
Ordner-Beobachters mit `[watch]`.
