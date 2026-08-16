# Getting a stable public URL (named Cloudflare Tunnel)

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
