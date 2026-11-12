# Den Mac als gemeinsamen Server betreiben

[Lokal auf dem Mac mit Ollama](mac-ollama.md) bringt den Chat bei dir auf einem Mac zum Laufen. Diese Seite hält ihn rund um die Uhr am Laufen und lässt Kolleginnen und Kollegen ihn nutzen. Sie setzt voraus, dass du dort alle fünf Schritte erledigt hast.

## Den Mac wach halten

Ein Mac im Ruhezustand antwortet niemandem. Schalte den Ruhezustand aus und lass den Mac nach einem Stromausfall von selbst wieder starten:

```bash
sudo pmset -a sleep 0
```

```bash
sudo pmset -a autorestart 1
```

Der Bildschirm darf trotzdem ausgehen, nur der Rechner bleibt wach.

## Den Modellen den Arbeitsspeicher geben

macOS lässt den Grafikchip nur etwa drei Viertel des Arbeitsspeichers nutzen und hält den Rest zurück. Auf einem Mac Studio mit 256 GB bleiben so rund 64 GB für die Modelle ungenutzt. Setz die Grenze so hoch, dass nur 16 GB für macOS und Docker bleiben. Rechne zuerst die Zahl aus, der gesamte Arbeitsspeicher minus 16 GB, in Megabyte:

```bash
echo $(( $(sysctl -n hw.memsize) / 1048576 - 16384 ))
```

Auf einem Mac Studio mit 256 GB kommt `245760` heraus. Die Grenze geht bei jedem Neustart verloren, setz sie deshalb mit einem kleinen Systemauftrag, der bei jedem Start des Mac läuft. Weicht deine Zahl ab, setz sie statt `245760` ein:

```bash
sudo tee /Library/LaunchDaemons/local.rag-template.gpu-memory.plist > /dev/null <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.rag-template.gpu-memory</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/sbin/sysctl</string>
    <string>iogpu.wired_limit_mb=245760</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF
```

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/local.rag-template.gpu-memory.plist
```

Der zweite Befehl setzt die Grenze auch sofort. Prüf sie, dann beende Ollama und öffne es wieder, damit es die neue Grenze sieht:

```bash
sysctl iogpu.wired_limit_mb
```

Die übrigen 16 GB müssen für macOS und Docker Desktop zusammen reichen. Hast du Docker Desktop unter Settings, Resources mehr Speicher gegeben, zieh entsprechend mehr ab. `ollama ps` zeigt, ob ein Modell hineinpasst: `100% GPU` in der Spalte `PROCESSOR` heißt ja, eine Aufteilung wie `40%/60% CPU/GPU` heißt, ein Teil läuft auf dem viel langsameren Prozessor.

Zurück zum Standard geht es, indem du den Auftrag entfernst und die Grenze zurücksetzt:

```bash
sudo launchctl bootout system /Library/LaunchDaemons/local.rag-template.gpu-memory.plist
```

```bash
sudo sysctl iogpu.wired_limit_mb=0
```

## Gemmas Fenster festlegen

Ollama wählt Gemmas Kontextfenster selbst, und auf einem Mac mit so viel Arbeitsspeicher nimmt es 256K. Ein Ollama-Update oder der Regler in der Ollama-App kann das ändern, ohne dass es jemand merkt, und ein kleineres Fenster zeigt sich nur an schlechteren Antworten. Eine Kopie von Gemma mit festem Fenster hält es bei 256K:

```bash
printf 'FROM gemma4:31b\nPARAMETER num_ctx 262144\n' > Modelfile
```

```bash
ollama create gemma4-31b-256k -f Modelfile
```

Ersetze in deiner Einstellungsdatei `gemma4:31b` überall durch `gemma4-31b-256k` (siehe [Schritt 4](mac-ollama.md#4-modellnamen-in-die-einstellungsdatei-schreiben)) und führ `make up` aus. Nach der ersten Frage zeigt `ollama ps` die Kopie mit `262144` in der Spalte `CONTEXT`. Die Kopie braucht weder zusätzlichen Platz auf der Festplatte noch mehr Arbeitsspeicher, denn mit 256K lief Gemma schon vorher.

Die Kopie zeigt auf die Modelldateien, wie sie beim Anlegen waren. Holt `ollama pull gemma4:31b` eine neue Version, führ den Befehl `ollama create` erneut aus.

## Gemma geladen halten

Ollama entlädt ein Modell nach fünf Minuten ohne Fragen. Die nächste Person wartet dann, bis etwa 20 GB wieder im Speicher sind. Diese Einstellung hält die Modelle geladen:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

Beende Ollama danach und öffne es wieder. Wie jedes `launchctl setenv` ist die Einstellung nach einem Neustart weg, der Auftrag unter [Alles nach einem Neustart wieder starten](#alles-nach-einem-neustart-wieder-starten) setzt sie erneut.

## Viele Personen gleichzeitig

Ollama beantwortet pro Modell nur eine begrenzte Zahl von Fragen gleichzeitig und stellt den Rest in eine Warteschlange. `OLLAMA_NUM_PARALLEL` erhöht diese Zahl:

```bash
launchctl setenv OLLAMA_NUM_PARALLEL 4
```

Jede parallel beantwortete Frage braucht ihren eigenen Cache, vier gleichzeitig mit einem 256K-Fenster brauchen also etwa 89 GB Cache zusätzlich zum Modell (siehe die Tabelle in [Schritt 2](mac-ollama.md#2-gemmas-kontextfenster-prufen)). Beende Ollama danach und öffne es wieder. Bleiben die Antworten bei mehreren Personen langsam, probier einen der [schnelleren Server](mac-ollama.md#wenn-die-antworten-zu-langsam-sind).

## Alles nach einem Neustart wieder starten

Nach einem Neustart kommt Ollama ohne deine Einstellungen zurück, und das Template kommt gar nicht zurück. Die Speichergrenze hat schon ihren eigenen Auftrag von oben, die zwei folgenden kümmern sich um den Rest. Sie laufen, wenn sich jemand anmeldet. Auf einem Mac ohne Bildschirm schaltest du deshalb unter Systemeinstellungen, Benutzer:innen & Gruppen die automatische Anmeldung ein. Mit FileVault ist die automatische Anmeldung nicht möglich.

### Ollama mit seinen Einstellungen

Die Ollama-App startet sich bei der Anmeldung selbst, aber bevor deine Einstellungen gesetzt sind. Entferne Ollama unter Systemeinstellungen, Allgemein, Anmeldeobjekte und lass diesen Auftrag es starten, nachdem er sie gesetzt hat:

```bash
tee ~/Library/LaunchAgents/local.rag-template.ollama.plist > /dev/null <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.rag-template.ollama</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>launchctl setenv OLLAMA_KEEP_ALIVE -1; open -a Ollama</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.rag-template.ollama.plist
```

Der Auftrag setzt nur `OLLAMA_KEEP_ALIVE`. Nutzt du `OLLAMA_NUM_PARALLEL` oder die zwei Speicher-Einstellungen aus [Schritt 2](mac-ollama.md#2-gemmas-kontextfenster-prufen), setz vor dem Ausführen für jede ein `launchctl setenv` vor `open -a Ollama`, getrennt durch Semikolons.

### Das Template

Trag die Autostart-Datei in `.env` ein. Nutzt du schon eine andere Ergänzung wie `docker-compose.smb.yml`, liste alle auf, getrennt durch Doppelpunkte:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.autostart.yml
```

Schalte in Docker Desktop unter Settings, General "Start Docker Desktop when you sign in to your computer" ein. Führ einmal `make up` aus. Danach starten die Container immer mit Docker, und Container, die du mit `make down` angehalten hast, bleiben aus.

Um den Ollama-Auftrag wieder zu entfernen, entlade ihn und lösch seine Datei:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.rag-template.ollama.plist
```

## Den Chat von anderen Rechnern aus nutzen

Ollama muss im Netzwerk nicht erreichbar sein, denn das Template läuft auf demselben Mac und spricht direkt mit ihm. Das Template selbst kannst du im Netzwerk freigeben: Kolleginnen und Kollegen öffnen den Chat im Browser unter `http://<Name des Mac>.local:8000`.

Bevor du ihn freigibst, trag diese Zeilen in `.env` ein und führ `make up` aus:

```
QDRANT_PORT=127.0.0.1:6333
POSTGRES_PORT=127.0.0.1:5432
CHAINLIT_AUTH_USERNAME=<dein Benutzername>
CHAINLIT_AUTH_PASSWORD=<ein sicheres Passwort>
CHAINLIT_AUTH_SECRET=<eine lange Zufallszeichenfolge>
```

Die ersten zwei Zeilen halten den Suchindex und die Chat-Datenbank auf dem Mac selbst, ohne sie könnte jeder im Netzwerk darin lesen. Die anderen drei ersetzen die Standardanmeldung `admin` / `admin`.

??? info "Den Chat unter einem eigenen Namen erreichen, etwa rag.example.internal"

    `<Name des Mac>.local` funktioniert nur, wo das Netzwerk Mac-Namen weiterreicht, und ändert sich, wenn jemand den Mac umbenennt. Ein fester Name im internen DNS vermeidet beides. Das meiste davon ist eine Bitte an deine IT:

    1. Gib dem Mac eine feste IP-Adresse, entweder als Reservierung im Router oder DHCP-Server oder von Hand unter Systemeinstellungen, Netzwerk.
    2. Bitte die IT um einen DNS-Eintrag (einen A-Record), der einen Namen wie `rag.example.internal` auf diese Adresse zeigen lässt.
    3. Damit das `:8000` aus der Adresse verschwindet, setz `APP_PORT=80` in `.env` und führ `make up` aus. Der Chat liegt dann unter `http://rag.example.internal`.

    Die Verbindung ist weiterhin reines HTTP, Passwörter gehen also unverschlüsselt durchs Netz, und Browser markieren die Seite als nicht sicher. Für HTTPS setzt du einen Reverse Proxy wie [Caddy](https://caddyserver.com) vor die App, mit einem Zertifikat von deiner IT. Nutzt du die Anmeldung über GitHub, trag die neue Adresse als Callback-URL deiner GitHub-App ein.

??? info "Den Chat von zu Hause oder unterwegs erreichen"

    Von außen ist der Mac nicht erreichbar: Er steht hinter dem Router des Büros auf einer privaten Adresse. Hat deine Organisation ein VPN, verbinde dich damit und öffne den Chat wie im Büro. `.local`-Namen funktionieren über ein VPN meist nicht, nimm also die IP-Adresse des Mac oder einen DNS-Namen von deiner IT.

    Ohne VPN installierst du ein Mesh-VPN wie [NetBird](https://netbird.io), ein Open-Source-Projekt aus Deutschland, auf dem Mac und auf jedem Laptop, das den Chat braucht. Die Geräte erreichen sich dann direkt und verschlüsselt, egal wo sie sind. [How To Home Server: Accessing the server remotely](https://spark-hpi.de/how-to-home-server/#accessing-the-server-remotely) zeigt die Einrichtung (auf Englisch). Kläre vor dem Einsatz für die Arbeit mit der zuständigen Person für Datenschutz, ob ihr den Dienst nutzen dürft.

    Leite Port 8000 nicht im Router ins Internet weiter. Damit stünde eine Anmeldung über reines HTTP, samt den Dokumenten dahinter, offen im Internet.
