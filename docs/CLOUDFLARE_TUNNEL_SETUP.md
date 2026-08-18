# Getting a stable public URL (named Cloudflare Tunnel)

## Current interim setup: the redirect page (watchdog currently OFF)

The actual link to share is **https://marioromeo7.github.io/scenerai/** — a
static redirect page (branch `gh-pages` of this repo) that forwards to
whatever Quick Tunnel URL was last published to it. That publishing used to
happen automatically via a Windows Scheduled Task (`ScenaraiTunnelWatchdog`,
running `scripts/tunnel_watchdog.py` every 10 minutes) that detected a dead
tunnel, relaunched `cloudflared`, and pushed an updated redirect page with
the new URL — **this task was stopped and unregistered** (confirmed removed
via `Get-ScheduledTask`), so right now the redirect page only points at
whatever URL was live last time it ran, and nothing is auto-healing it.

To re-enable: re-register the task (same `schtasks`/`Register-ScheduledTask`
command used originally) or just run `python scripts/tunnel_watchdog.py` by
hand periodically. The script itself is untouched and still does the right
thing — only the schedule that was calling it automatically is gone. Doing
so will still correctly point the redirect page at whatever new Quick
Tunnel URL comes up, same as before. This only works while this machine is
on and logged in either way (the task runs as the current user) — it's a
stopgap, not a real always-on server. The named tunnel below doesn't remove
the need for this machine to be running either, but at least gives a
permanent hostname instead of a page that redirects.

## Why

The tunnel currently running (`cloudflared tunnel --url http://localhost:80`)
is a **Quick Tunnel** — anonymous, no login required, but Cloudflare hands
out a fresh random `*.trycloudflare.com` hostname every time it restarts
(and it does restart: these degrade after roughly a day unattended). A
**named tunnel** fixes both problems: same hostname forever, tied to your
own domain instead of a random one.

This requires two things only you can do (an account and a domain — see
below), plus a handful of commands, most of which can be copy-pasted as-is.

## What you need to do first

1. **A domain.** Any cheap one works — Namecheap, Porkbun, etc. run
   $3–12/yr for a `.com`/`.xyz`/whatever. You don't need anything fancy;
   `scenarai.xyz` or similar is fine.
2. **A free Cloudflare account** at [dash.cloudflare.com](https://dash.cloudflare.com/sign-up).
3. **Add the domain to Cloudflare** ("Add a site" in the dashboard). Cloudflare
   gives you two nameservers — set those at your domain registrar (wherever
   you bought the domain, in its DNS/nameserver settings). This can take a
   few minutes to a few hours to propagate; Cloudflare's dashboard shows
   "Active" once it's done. **Wait for Active before continuing.**

## Commands to run once the domain shows Active

All of this runs on this machine, in a terminal, using the `cloudflared`
already installed at `C:\Users\mario\bin\cloudflared.exe`.

```powershell
# 1. Log in -- opens a browser, pick the domain you just added to Cloudflare.
#    Creates C:\Users\<you>\.cloudflared\cert.pem, used by every command below.
cloudflared tunnel login

# 2. Create the named tunnel (pick any name; "scenarai" is fine).
#    Prints a Tunnel ID and writes credentials to
#    C:\Users\<you>\.cloudflared\<TUNNEL_ID>.json -- you'll need that ID next.
cloudflared tunnel create scenarai

# 3. Point a hostname at it. Use whatever subdomain you want --
#    e.g. app.yourdomain.com or scenarai.yourdomain.com.
cloudflared tunnel route dns scenarai app.yourdomain.com
```

Then copy `cloudflare/config.yml.example` in this repo to `cloudflare/config.yml`
and fill in the two placeholders with the Tunnel ID from step 2 and the
hostname from step 3.

## Running it

```powershell
cloudflared tunnel --config cloudflare\config.yml run scenarai
```

That's the same command that needs to be running for the site to be
reachable — same as the quick tunnel today, just pointed at a config
instead of `--url`. It'll print connection logs; leave it running (or ask
to have it set up as a background/auto-start task once this part works --
that's a separate, optional step, not needed just to get the stable URL).

## Verifying

Once running, `https://app.yourdomain.com` (whatever hostname you chose)
should serve the app immediately and identically every time you restart
the tunnel — no more new URLs to re-share.

## What to hand back

Once you've done steps 1–3 above and have a Tunnel ID + hostname, send
them over (or just say "done") and the rest — generating `config.yml`,
launching it, verifying it's live — can happen in the same session.
