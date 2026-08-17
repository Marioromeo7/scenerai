"""
Keeps the Cloudflare Quick Tunnel alive and keeps the public GitHub Pages
redirect (https://marioromeo7.github.io/scenerai/) pointed at whatever
tunnel URL is currently live.

Quick Tunnels don't have a fixed hostname -- every restart gets a fresh
random *.trycloudflare.com URL, and they degrade on their own roughly
daily (observed live, not assumed: "control stream encountered a
failure" in the tunnel log, followed by the hostname going unreachable).
This script is meant to be run periodically (see
docs/CLOUDFLARE_TUNNEL_SETUP.md for the Windows Scheduled Task command)
so that failure gets noticed and fixed without a human watching for it.

Each run is a single idempotent check:
  - current URL still healthy -> do nothing
  - not healthy (or none recorded yet) -> kill any stale cloudflared,
    launch a fresh tunnel, capture its new URL, rewrite the gh-pages
    redirect page, commit, push.

Intentionally NOT a long-running daemon loop: a process that has to stay
alive to do its job is exactly the kind of single point of failure this
script exists to route around. Re-invoked by the OS scheduler instead.
"""
import re
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

CLOUDFLARED    = r"C:\Users\mario\bin\cloudflared.exe"
LOCAL_TARGET   = "http://localhost:80"
GH_PAGES_DIR   = Path(r"D:\tmp_gh_pages_build")   # git worktree, branch gh-pages
REDIRECT_FILE  = GH_PAGES_DIR / "index.html"
LAUNCH_LOG     = Path(r"D:\scenarai_project\cloudflare_tunnel_watchdog.log")
HEALTH_PATH    = "/api/health"
URL_RE         = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
LAUNCH_TIMEOUT_S = 30
# Separate from LAUNCH_TIMEOUT_S -- cloudflared can print the assigned URL
# in its log before Cloudflare's edge has actually finished propagating the
# route, or before the local backend is reachable through it yet. Found via
# code review: publishing on log-output alone (no health check) risked
# pushing a redirect to a URL that isn't actually serving traffic.
#
# 60s, not a smaller guess -- measured live: a fresh Quick Tunnel hostname
# took over a minute to become DNS-resolvable even via 1.1.1.1 directly
# (ruling out local resolver caching), confirmed by watching two real
# restarts (one manual, one via the actual Scheduled Task) both correctly
# decline to publish at a 20s cutoff. Waiting longer here, still well
# inside the Scheduled Task's 5-minute execution limit, resolves within
# the same run far more often than deferring to the next 10-minute cycle.
PUBLISH_HEALTH_TIMEOUT_S = 60


def _current_url() -> str | None:
    """Whatever URL the redirect page currently points at, if any."""
    if not REDIRECT_FILE.exists():
        return None
    text = REDIRECT_FILE.read_text(encoding="utf-8")
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}{HEALTH_PATH}", timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _kill_existing_cloudflared():
    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)


def _launch_and_capture_url() -> str | None:
    with open(LAUNCH_LOG, "w", encoding="utf-8") as logf:
        subprocess.Popen(
            [CLOUDFLARED, "tunnel", "--url", LOCAL_TARGET],
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    # cloudflared prints the assigned URL within a few seconds of startup --
    # poll the log rather than a fixed sleep, so a slow start doesn't
    # produce a false "no URL" result.
    deadline = time.time() + LAUNCH_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1.5)
        if LAUNCH_LOG.exists():
            text = LAUNCH_LOG.read_text(encoding="utf-8", errors="ignore")
            m = URL_RE.search(text)
            if m:
                return m.group(0)
    return None


def _redirect_html(url: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={url}">
<title>Scenarai</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f0f10; color:#e8e6e1;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  a {{ color:#c9a96e; }}
</style>
<script>
  // Kept up to date by scripts/tunnel_watchdog.py -- this file's only job
  // is to always point at whatever the current Cloudflare Quick Tunnel
  // URL is, since that URL itself has no fixed hostname.
  location.replace("{url}");
</script>
</head>
<body>
  <p>Redirecting to Scenarai&hellip; if nothing happens,
    <a href="{url}">click here</a>.</p>
</body>
</html>
"""


def _update_gh_pages(new_url: str):
    REDIRECT_FILE.write_text(_redirect_html(new_url), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=GH_PAGES_DIR, check=True)
    result = subprocess.run(["git", "commit", "-m", f"Update redirect target to {new_url}"],
                             cwd=GH_PAGES_DIR, capture_output=True, text=True)
    if result.returncode != 0 and "nothing to commit" not in result.stdout:
        raise RuntimeError(f"git commit failed: {result.stdout}\n{result.stderr}")
    subprocess.run(["git", "push", "origin", "gh-pages"], cwd=GH_PAGES_DIR, check=True)


def main():
    current = _current_url()
    if current and _is_healthy(current):
        print(f"OK: {current} is healthy, nothing to do.")
        return

    print(f"Tunnel down or unknown (last recorded: {current}) -- restarting.")
    _kill_existing_cloudflared()
    new_url = _launch_and_capture_url()
    if not new_url:
        print("ERROR: cloudflared did not report a new URL within the timeout. "
              f"Check {LAUNCH_LOG} manually.")
        raise SystemExit(1)

    print(f"New tunnel URL: {new_url} -- verifying it's actually serving traffic before publishing.")
    deadline = time.time() + PUBLISH_HEALTH_TIMEOUT_S
    healthy = False
    while time.time() < deadline:
        if _is_healthy(new_url):
            healthy = True
            break
        time.sleep(2)
    if not healthy:
        # Deliberately don't touch the gh-pages redirect here -- it's better
        # left pointing at the old (confirmed-dead) URL than to be updated
        # to a new URL that was never confirmed to work either. The next
        # scheduled run repeats the whole kill/relaunch/verify cycle fresh.
        print(f"ERROR: {new_url} did not become healthy within {PUBLISH_HEALTH_TIMEOUT_S}s "
              "-- not publishing. Will retry on the next scheduled run.")
        raise SystemExit(1)

    _update_gh_pages(new_url)
    print("gh-pages redirect updated and pushed.")


if __name__ == "__main__":
    main()
