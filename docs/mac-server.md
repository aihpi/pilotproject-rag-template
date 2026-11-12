# Running the Mac as a shared server

[Running on a Mac with Ollama](mac-ollama.md) gets the chat working for you on one Mac. This page keeps it running around the clock and lets colleagues use it. It assumes you have finished all five steps there.

## Keep the Mac awake

A Mac that goes to sleep cannot answer anyone. Turn sleep off, and let the Mac start again by itself after a power cut:

```bash
sudo pmset -a sleep 0
```

```bash
sudo pmset -a autorestart 1
```

The screen can still turn off; only the computer stays awake.

## Give the models the Mac's memory

macOS lets the graphics chip use only about three quarters of the Mac's memory and keeps the rest back, so on a Mac Studio with 256 GB roughly 64 GB sit unused by the models. Raise the limit so that only 16 GB stay with macOS and Docker. First work out the number, all of the memory minus 16 GB, in megabytes:

```bash
echo $(( $(sysctl -n hw.memsize) / 1048576 - 16384 ))
```

On a Mac Studio with 256 GB this prints `245760`. The limit is lost at every restart, so set it with a small system job that runs whenever the Mac starts. Put your own number in place of `245760` if it differs:

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

The second command also sets the limit right away. Check it, then quit and reopen Ollama so it sees the new limit:

```bash
sysctl iogpu.wired_limit_mb
```

The 16 GB left over have to cover macOS and Docker Desktop together. If you gave Docker Desktop more memory under Settings, Resources, subtract more. `ollama ps` shows whether a model fits: `100% GPU` in the `PROCESSOR` column means it does, and a split such as `40%/60% CPU/GPU` means part of it runs on the much slower processor.

To go back to the default, remove the job and reset the limit:

```bash
sudo launchctl bootout system /Library/LaunchDaemons/local.rag-template.gpu-memory.plist
```

```bash
sudo sysctl iogpu.wired_limit_mb=0
```

## Pin Gemma's window

Ollama chooses Gemma's context window by itself, and on a Mac with this much memory it takes 256K. An Ollama update or the slider in the Ollama app can change that without anyone noticing, and a smaller window only shows up as worse answers. A copy of Gemma with a fixed window keeps it at 256K:

```bash
printf 'FROM gemma4:31b\nPARAMETER num_ctx 262144\n' > Modelfile
```

```bash
ollama create gemma4-31b-256k -f Modelfile
```

In your settings file, replace `gemma4:31b` with `gemma4-31b-256k` everywhere it appears (see [step 4](mac-ollama.md#4-put-the-model-names-into-your-settings-file)), then run `make up`. After the first question, `ollama ps` shows the copy with `262144` in the `CONTEXT` column. The copy needs no extra disk space or memory, because 256K is what Gemma used before.

The copy points to the model files as they were when you made it. After `ollama pull gemma4:31b` brings a new version, run the `ollama create` command again.

## Keep Gemma loaded

Ollama unloads a model after five minutes without questions. The next person then waits while about 20 GB are read back into memory. This setting keeps the models loaded:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

Quit and reopen Ollama afterwards. Like every `launchctl setenv`, it is lost when the Mac restarts; the job in [Start everything after a restart](#start-everything-after-a-restart) sets it again.

## Many people at once

Ollama answers only a limited number of questions per model at the same time and queues the rest. `OLLAMA_NUM_PARALLEL` raises that number:

```bash
launchctl setenv OLLAMA_NUM_PARALLEL 4
```

Every question answered in parallel needs its own cache, so four at once with a 256K window need about 89 GB of cache on top of the model (see the table in [step 2](mac-ollama.md#2-check-gemmas-context-window)). Quit and reopen Ollama afterwards. If answers stay slow with several people, try one of the [faster servers](mac-ollama.md#when-answers-are-too-slow).

## Start everything after a restart

After a restart, Ollama comes back without your settings and the template does not come back at all. The memory limit already has its own job from above; the two below take care of the rest. They run when a user logs in, so on a Mac without a screen, turn on automatic login under System Settings, Users & Groups. FileVault does not allow automatic login.

### Ollama with its settings

The Ollama app starts itself at login, but before your settings exist. Remove Ollama under System Settings, General, Login Items, and let this job start it after setting them:

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

The job only sets `OLLAMA_KEEP_ALIVE`. If you use `OLLAMA_NUM_PARALLEL` or the two memory settings from [step 2](mac-ollama.md#2-check-gemmas-context-window), add a `launchctl setenv` for each in front of `open -a Ollama`, separated by semicolons, before you run the commands.

### The template

Add the autostart file to `.env`. If you already use another override such as `docker-compose.smb.yml`, list all of them, separated by colons:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.autostart.yml
```

In Docker Desktop, turn on Settings, General, "Start Docker Desktop when you sign in to your computer". Run `make up` once. From then on the containers start whenever Docker starts, and containers you stopped with `make down` stay stopped.

To remove the Ollama job again, unload it and delete its file:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.rag-template.ollama.plist
```

## Using the chat from other computers

Ollama does not need to be reachable from the network, because the template runs on the same Mac and talks to it directly. The template can be opened to the network: colleagues use the chat in their browser at `http://<name of the Mac>.local:8000`.

Before you share it, add these lines to `.env` and run `make up`:

```
QDRANT_PORT=127.0.0.1:6333
POSTGRES_PORT=127.0.0.1:5432
CHAINLIT_AUTH_USERNAME=<your user name>
CHAINLIT_AUTH_PASSWORD=<a strong password>
CHAINLIT_AUTH_SECRET=<a long random string>
```

The first two lines keep the search index and the chat database on the Mac itself; without them anyone on the network could read them. The other three replace the default `admin` / `admin` login.

??? info "Reaching the chat under its own name, such as rag.example.internal"

    `<name of the Mac>.local` only works where the network passes on Mac names, and it changes when someone renames the Mac. A fixed name in your internal DNS avoids both. Most of it is a request to your IT:

    1. Give the Mac a fixed IP address, either as a reservation in the router or DHCP server, or by hand under System Settings, Network.
    2. Ask IT for a DNS record (an A record) that points a name such as `rag.example.internal` at that address.
    3. To drop the `:8000` from the address, set `APP_PORT=80` in `.env` and run `make up`. The chat is then at `http://rag.example.internal`.

    The connection is still plain HTTP, so passwords cross the network unencrypted and browsers mark the page as not secure. For HTTPS, put a reverse proxy such as [Caddy](https://caddyserver.com) in front of the app, with a certificate from your IT. If you use GitHub login, register the new address as the callback URL of your GitHub app.

??? info "Reaching the chat from home or on the road"

    From outside, the Mac cannot be reached: it sits behind the office router on a private address. If your organization has a VPN, connect to it and open the chat as you would in the office. `.local` names usually do not work over a VPN, so use the Mac's IP address or a DNS name from your IT.

    Without a VPN, install a mesh VPN such as [NetBird](https://netbird.io), an open-source project from Germany, on the Mac and on every laptop that needs the chat. The devices then reach each other directly and encrypted, wherever they are. [How To Home Server: Accessing the server remotely](https://spark-hpi.de/how-to-home-server/#accessing-the-server-remotely) walks through the setup. Check with whoever is responsible for data protection before you use it for work.

    Do not forward port 8000 on the router to the internet. That would put a login over plain HTTP, and the documents behind it, on the open internet.
