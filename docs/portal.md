# The portal: reach Daxton from anywhere

The dashboard (`daxton ui`) normally lives on the Mac at `http://127.0.0.1:8765`. The portal publishes it on the
internet, at a random `trycloudflare.com` address in a minute or at a hostname you own, say
`https://daxton.example.com`, so a phone or a laptop anywhere can open the same HUD, talk to Daxton through its own
microphone, and hear the reply in the Daxton voice. The Mac keeps doing all the work (wake word, Whisper, the brain,
the skills, the voice); the portal is a window onto it.

## The one-minute version (no domain)

```bash
echo 'DASHBOARD_PASSWORD=correct horse battery staple' >> .env   # the lock; nothing is published without it
daxton service install          # `daxton ui` runs at login and restarts if it stops
daxton tunnel quick --service   # a Cloudflare quick tunnel; prints https://<four-words>.trycloudflare.com
```

That address is yours until the tunnel restarts (a reboot, a network drop), when Cloudflare hands out a new one:
`daxton tunnel status` prints the current address, and the HUD's Systems panel shows it with a **QR** button so you
can point a phone at the Mac's screen. Nothing else changes: the same login, the same voice both ways. What you do
not get is Cloudflare Access in front of the login page, so the dashboard password is the only lock; use a long one.
The rest of this document is the permanent version on your own domain.

Four pieces make it safe:

1. **A Cloudflare Tunnel.** `cloudflared` runs on the Mac and dials out to Cloudflare; Cloudflare forwards
   `https://daxton.example.com` back down that connection to `http://127.0.0.1:8765`. No port is opened on the Mac,
   no router configuration, no public IP, and the certificate is handled for you.
2. **Cloudflare Access.** A login in front of the hostname (email one-time code, Google, Apple, ...), so only the
   people you list ever see the dashboard's login page. Free for up to 50 users.
3. **The dashboard's own password** (`DASHBOARD_PASSWORD`). A second lock, enforced by Daxton itself on every page,
   API call and WebSocket. Without it the server refuses anything that did not come from the Mac's own browser, so a
   tunnel by itself cannot expose the assistant by accident.
4. **HTTPS end to end** from the browser to Cloudflare, which is also what browsers demand before they let a page use
   the microphone.

## Before you start

- A domain whose DNS is on Cloudflare (a free Cloudflare account, the domain's nameservers pointed at Cloudflare).
  Any domain you already have works; you do not need to move the website, only the DNS. `dig +short NS example.com`
  tells you where a domain's DNS lives today: `*.ns.cloudflare.com` means you are ready; `*.domaincontrol.com`
  (GoDaddy) or another registrar's servers means the DNS has to move first, see "Moving a domain to Cloudflare"
  at the end.
- Daxton installed and working on the Mac (`daxton doctor` clean, `daxton ui` shows the HUD locally).
- Homebrew (the setup installs `cloudflared` with it).

## Step 1: the password

In the repo's `.env` (the one `daxton ui` loads), add a long passphrase and the hostname you will use:

```
DASHBOARD_PASSWORD=correct horse battery staple
PUBLIC_HOSTNAME=daxton.example.com
```

Restart `daxton ui`. Opening `http://127.0.0.1:8765` now shows the login page first, even on the Mac. Sessions last
30 days per browser (`DASHBOARD_SESSION_DAYS`), and changing the password signs every device out at once.

## Step 2: the tunnel

```bash
cd ~/Documents/Claude/Projects/daxton-ai && source .venv/bin/activate
daxton tunnel setup daxton.example.com
```

What it does, printing every command before running it:

1. installs `cloudflared` if it is missing (`brew install cloudflared`);
2. `cloudflared tunnel login`: a browser window opens; sign in to Cloudflare and pick the domain. This writes
   `~/.cloudflared/cert.pem` once;
3. `cloudflared tunnel create daxton` (or reuses an existing tunnel of that name);
4. writes `~/.cloudflared/config.yml`: the hostname to `http://127.0.0.1:8765`, everything else 404;
5. `cloudflared tunnel route dns daxton daxton.example.com`: the DNS record (a proxied CNAME to the tunnel);
6. a launch agent (`ai.daxton.tunnel`, running `cloudflared tunnel run daxton`) so the tunnel starts at login and
   restarts if it drops. The same agent name is used for a quick tunnel, so switching from one to the other
   replaces it; `cloudflared service install` is not used because on macOS it writes an agent that runs bare
   `cloudflared`, which exits at once against a config file.

`daxton tunnel status` shows the state of each piece; `daxton tunnel run` runs the tunnel in the foreground if you
would rather watch it the first time. The setup refuses to run without `DASHBOARD_PASSWORD`, and it is safe to run
again (it reuses the tunnel, backs up the previous config and reinstalls the agent).

A domain registered minutes ago needs two more things to happen on their own before the address answers: the
registry has to publish the delegation (`dig +short NS daxton.example.com` empty until then) and Cloudflare has to
issue the edge certificate (a TLS handshake failure until then). Both usually take minutes, occasionally an hour.

## Step 3: keep the assistant running

`daxton ui` has to be running for the portal to answer. To have it start at login and come back if it stops:

```bash
daxton service install          # writes ~/Library/LaunchAgents/ai.daxton.dashboard.plist and loads it
daxton service status
tail -f ~/.daxton/logs/dashboard.log
```

Run it from the repo folder: the service loads `.env` from there. The first time Python uses the microphone from
the service macOS may ask for permission; allow it. The Mac has to stay awake (System Settings > Energy, or
`caffeinate`) and logged in; a Mac that sleeps takes the portal down with it.

`daxton service uninstall` removes it. Running `daxton ui` by hand in a terminal works just as well.

## Step 4: Cloudflare Access (recommended)

Right now anyone who guesses the hostname gets as far as Daxton's login page. Put Cloudflare's own login in front of it:

1. Open https://one.dash.cloudflare.com, then **Access > Applications > Add an application > Self-hosted**.
2. Application name `Daxton`, domain `daxton.example.com`, session duration as you like (a month is comfortable).
3. Add a policy: **Allow**, include **Emails**, your address. Save.
4. Login methods: **One-time PIN** is on by default (a code sent to that email). Add Google or Apple under
   Settings > Authentication if you prefer a tap.

From then on the hostname shows Cloudflare's login first, then Daxton's. Bots and scanners never reach the Mac at
all, and the WebSocket goes through the same door (Access sets its own cookie, which the socket carries).

## Using it

Open `https://daxton.example.com` on the phone. After the two logins the HUD appears, with `mic: here` selected
because the page is not on the Mac. Then:

- **Talk** (or hold `space` on a laptop): the phone's microphone streams to the Mac as 16 kHz PCM over the
  WebSocket; the Mac runs the same voice activity detector it uses for its own mic and stops when you go quiet
  (or when you tap Talk again). Whisper transcribes on the Mac, the brain answers, the reply is spoken.
- **Audio**: on by default when the page is remote; the reply's audio is streamed back and played in the browser.
  Browsers block sound until you have interacted with the page once, so the first time you may see "tap to enable
  audio playback".
- **Mac speakers**: whether the Mac also plays the reply. Switch it off when you are away and do not want the Mac
  talking to an empty room; everything still streams to you.
- **Mute** stops the Mac listening on its own microphone; the portal's Talk keeps working.
- Typing a request works exactly as at the Mac, and the log shows everything the assistant does, whoever asked.
- The power icon at the top right signs out.

Add the page to the phone's home screen (Share > Add to Home Screen) and it opens like an app, full screen.

## How it works

```
phone browser ──── wss ────► Cloudflare ──── tunnel ────► cloudflared on the Mac ──► daxton ui (127.0.0.1:8765)
   mic ──► AudioWorklet ──► 16 kHz PCM16 frames (binary WebSocket messages, 80 ms each)
   speaker ◄── AudioContext ◄── PCM16 frames + {"type": "speech_start"/"speech_end"} markers
```

- `daxton/ui/auth.py`: the policy (local-only without a password, cookie sessions with one), HMAC-signed cookies
  (stdlib only), the per-address login throttle, the same-origin check for sockets and POSTs.
- `daxton/ui/voice.py`: `VoiceCapture` (the server-side VAD for browser audio) and `SpeechRelay` (turns the
  assistant's `speech_chunk` events into binary frames for a page that asked for audio).
- `daxton/ui/server.py`: `/login`, `/logout`, `/healthz`, the guard middleware, the WebSocket that carries JSON both
  ways plus binary audio both ways, security headers.
- `daxton/assistant.py`: `remote_turn()` (transcribe an utterance that arrived from elsewhere, answer, speak),
  `local_audio` (mute the Mac's speakers while still streaming the voice), a transcriber built on first use when
  the session started without a microphone.
- `daxton/tts/macos_say.py`: when something is listening, the macOS voice is rendered to PCM (`say -o`) and played
  through the same player as ElevenLabs, so the free voice reaches the portal too.
- `daxton/tunnel.py`: `daxton tunnel` (`setup`, `run`, `quick`, `status`) and `daxton service`; the HUD reads the
  current portal address from the tunnel log and serves it as a QR code at `/portal.svg`.
- `daxton/cli.py`: the voice loop runs on a worker thread under a watchdog, so a microphone that cannot open
  (a permission prompt waiting on the Mac) never takes the portal down.

The audio path costs nothing extra: ElevenLabs is called once per reply as before; the same PCM is played on the
Mac and forwarded to the browsers that asked for it.

## Security notes

- Never publish the dashboard without `DASHBOARD_PASSWORD`; the server will not serve remote visitors without it, and
  `daxton tunnel setup` refuses to run.
- Use Cloudflare Access. The dashboard's login is throttled (five free attempts, then 30 s doubling per failure, per
  address), but a public login page is still a public login page.
- The session cookie is `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS, signed with a secret kept in
  `~/.daxton/dashboard.secret` (mode 600) or `DASHBOARD_SECRET`. The password is part of the signing key, so
  changing it invalidates every session.
- Requests carrying proxy headers (`Cf-Connecting-Ip`, `X-Forwarded-For`, ...) or coming from a non-loopback address
  are never treated as local. uvicorn trusts `X-Forwarded-*` from loopback only, which is where cloudflared runs.
- `/healthz` is public and says only that the server is up.
- Whatever you can do at the Mac's dashboard you can do from the portal: open apps, take screenshots, read notes.
  Treat the password accordingly.
- No credentials are stored by the portal. The `.env` file stays on the Mac and is git-ignored.

## Moving a domain to Cloudflare

Cloudflare Tunnel can only publish a hostname whose zone is on Cloudflare DNS (a free plan is enough; subdomain-only
delegation is an Enterprise feature). Moving a domain you use for a website or email is safe when done carefully,
and it is not something to do in a hurry:

1. In the Cloudflare dashboard, **Add a site**, free plan. Cloudflare scans and imports the domain's current records.
2. Compare the import against the registrar's DNS page **record by record**: MX and the mail provider's
   verification and DKIM records, SPF and DMARC TXT records, the website's A or CNAME records, redirect hosts,
   any CNAMEs a mail-sending service asked you to add. The scan misses some of these; add what it missed by hand.
   Set records that only need DNS (mail, verification) to "DNS only", not proxied.
3. At the registrar, replace the nameservers with the two Cloudflare gives you. Propagation takes minutes to hours;
   the old servers keep answering meanwhile, so nothing breaks if step 2 was complete.
4. When Cloudflare shows the zone as active, `daxton tunnel setup daxton.example.com`.

The alternative that avoids touching a business domain is a new domain registered on Cloudflare (Cloudflare Registrar
sells them at cost), which is on Cloudflare DNS from the first minute.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Local only" page on the phone | `DASHBOARD_PASSWORD` is empty in the `.env` that the running `daxton ui` loaded. Set it, restart. |
| Cloudflare error 1033 / 502 | The tunnel is not running (`daxton tunnel status`, `daxton tunnel run`) or `daxton ui` is not listening on the port in `~/.cloudflared/config.yml`. |
| Cloudflare error 1016 / DNS does not resolve | The DNS record is missing: `cloudflared tunnel route dns daxton daxton.example.com`, or check the DNS page (proxied CNAME to `<tunnel id>.cfargotunnel.com`). |
| Talk does nothing, or "microphone" error in the log | The page is not on HTTPS (use the tunnel hostname, not a LAN address), or the browser has no mic permission for the site. |
| Silence on the phone although the log shows the reply | Tap the page once (autoplay policy) or press `Audio`. With the macOS `say` voice, check the Mac's log for "could not render to PCM". |
| The Mac transcribes its own reply | Turn `Mac speakers` off while you are remote, or `Mute` the Mac's microphone. |
| Logged out on every visit | Cookies blocked for the site, or the clock on the Mac is off. |
| Slow first answer from the portal in `--no-voice` mode | Whisper loads on the first remote utterance (a few seconds, once). |
| The service log says the microphone has not opened | First run under launchd: macOS is asking whether Python may use the microphone. Click Allow on the Mac; the voice loop joins in by itself. Until then typed requests and browser voice work. |
| The quick tunnel address changed | Cloudflare gives a new one on every restart. `daxton tunnel status` shows it; the HUD's Portal row has a QR code. For a stable address use your own domain (`daxton tunnel setup`). |
