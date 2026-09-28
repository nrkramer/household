"""HTML rendering. Plain strings, no template engine."""

import json
from datetime import datetime
from html import escape as e
from urllib.parse import quote

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--text:#1c1f24;--muted:#626a75;--line:#dde1e6;--accent:#03a9f4;--accent-text:#fff;--ok:#2e7d32;--bad:#c62828;--warn:#b26a00}
@media (prefers-color-scheme:dark){:root{--bg:#111315;--card:#1c1f23;--text:#e8eaed;--muted:#9aa1ab;--line:#30353b;--accent:#29b6f6;--accent-text:#04131b;--ok:#66bb6a;--bad:#ef5350;--warn:#ffb74d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:24px 16px 48px}
main.wide{max-width:960px}
h1{font-size:1.5rem;margin:8px 0 4px}
h2{font-size:1.1rem;margin:28px 0 10px}
p{margin:8px 0}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0}
label{display:block;font-weight:600;margin:14px 0 4px}
input,select{width:100%;padding:11px 12px;font:inherit;color:var(--text);background:var(--bg);border:1px solid var(--line);border-radius:8px}
.btn{display:inline-block;padding:11px 18px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);font:inherit;font-weight:600;text-decoration:none;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-text)}
.btn.danger{color:var(--bad)}
.btn.small{padding:6px 12px;font-size:.9rem}
.stack .btn{display:block;width:100%;text-align:center;margin:10px 0}
form.inline{display:inline}
.error{color:var(--bad);font-weight:600}
.ok{color:var(--ok)} .bad{color:var(--bad)} .warn{color:var(--warn)}
ol{padding-left:22px} li{margin:8px 0}
code,.mono{font-family:ui-monospace,Consolas,monospace}
.secret{font-family:ui-monospace,Consolas,monospace;font-size:1.4rem;letter-spacing:.06em;background:var(--bg);border:1px dashed var(--line);border-radius:8px;padding:10px 14px;display:inline-block}
.copyrow{display:flex;gap:8px;align-items:center}
.copyrow input{flex:1}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.85rem;color:var(--muted);font-weight:600}
.pill{font-size:.8rem;padding:2px 8px;border-radius:99px;border:1px solid currentColor}
.qr{background:#fff;padding:10px;border-radius:8px;display:inline-block}
.qr svg{display:block;width:180px;height:180px}
.spinner{width:22px;height:22px;border:3px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:s 1s linear infinite;display:inline-block;vertical-align:middle;margin-right:8px}
@keyframes s{to{transform:rotate(360deg)}}
"""

COPY_JS = """
function copyField(id){const el=document.getElementById(id);el.select();el.setSelectionRange(0,999);
let ok=false;try{ok=document.execCommand('copy')}catch(_){}
if(!ok&&navigator.clipboard){navigator.clipboard.writeText(el.value)}
const b=document.querySelector('[data-copy="'+id+'"]');if(b){b.textContent='Copied';setTimeout(()=>b.textContent='Copy',1500)}}
"""


def page(title: str, body: str, wide: bool = False, script: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head>
<body><main class="{'wide' if wide else ''}">{body}</main>
<script>{COPY_JS}{script}</script></body></html>"""


def when(ts: float | str | None) -> str:
    if not ts:
        return "—"
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return e(ts)
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# -- portal ------------------------------------------------------------------


def portal_home() -> str:
    return page(
        "Join this home",
        """<h1>Home Assistant</h1>
<p class="muted">You're on the home network. Set up this phone so the Home Assistant app works from anywhere.</p>
<div class="card stack">
  <a class="btn primary" href="join">I'm new here – create my account</a>
  <a class="btn" href="device">I already have an account – add this device</a>
</div>""",
    )


def _platform_select(platform: str) -> str:
    opts = [("ios", "iPhone / iPad"), ("android", "Android"), ("other", "Other")]
    return "".join(
        f'<option value="{v}"{" selected" if v == platform else ""}>{label}</option>' for v, label in opts
    )


def join_form(values: dict, error: str = "") -> str:
    v = {k: e(values.get(k, "")) for k in ("name", "username", "device")}
    return page(
        "Create account",
        f"""<p><a href="./" class="muted">← Back</a></p>
<h1>Create your account</h1>
<p class="muted">The home owner will get a notification to approve you.</p>
{f'<p class="error">{e(error)}</p>' if error else ''}
<form method="post" class="card">
  <label for="name">Your name</label>
  <input id="name" name="name" value="{v['name']}" autocomplete="name" required>
  <label for="username">Username</label>
  <input id="username" name="username" value="{v['username']}" autocapitalize="none" autocorrect="off" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
  <label for="device">Name this device</label>
  <input id="device" name="device" value="{v['device']}" placeholder="e.g. Alex's iPhone" required>
  <label for="platform">Device type</label>
  <select id="platform" name="platform">{_platform_select(values.get('platform', 'other'))}</select>
  <p><button class="btn primary" type="submit">Request access</button></p>
</form>""",
    )


def device_form(values: dict, error: str = "") -> str:
    v = {k: e(values.get(k, "")) for k in ("username", "device")}
    return page(
        "Add a device",
        f"""<p><a href="./" class="muted">← Back</a></p>
<h1>Add this device</h1>
<p class="muted">Sign in with your Home Assistant account to get a certificate for this device.</p>
{f'<p class="error">{e(error)}</p>' if error else ''}
<form method="post" class="card">
  <label for="username">Username</label>
  <input id="username" name="username" value="{v['username']}" autocapitalize="none" autocorrect="off" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <label for="device">Name this device</label>
  <input id="device" name="device" value="{v['device']}" placeholder="e.g. Alex's iPhone" required>
  <label for="platform">Device type</label>
  <select id="platform" name="platform">{_platform_select(values.get('platform', 'other'))}</select>
  <p><button class="btn primary" type="submit">Get certificate</button></p>
</form>""",
    )


def request_page(request: dict, external_url: str) -> str:
    status = request["status"]
    if status in ("pending", "approved"):
        body = f"""<h1>Waiting for approval</h1>
<div class="card"><p><span class="spinner"></span>Hi {e(request['name'])} – the home owner has been notified.</p>
<p class="muted">Keep this page open. It will continue automatically once you're approved.</p></div>"""
        poll = "setInterval(async()=>{try{const r=await fetch(location.pathname+'/status',{cache:'no-store'});const j=await r.json();if(j.status!=='pending'&&j.status!=='approved')location.reload()}catch(_){}},3000);"
        return page("Waiting for approval", body, script=poll)
    if status == "denied":
        return page("Request declined", "<h1>Request declined</h1><p>Your request wasn't approved. Talk to the home owner.</p>")
    if status == "expired":
        return page("Request expired", "<h1>Request expired</h1><p>Nobody approved this within a day. <a href='../join'>Try again</a>.</p>")
    if status == "error":
        return page(
            "Something went wrong",
            f"""<h1>Something went wrong</h1><p>Your account was approved, but the certificate couldn't be created.
The home owner can retry from the Household panel.</p><p class="muted mono">{e(request.get('error', ''))}</p>""",
        )
    if status == "collected":
        return page(
            "Link expired",
            "<h1>Download link expired</h1><p>For safety, certificate downloads only last a day. "
            "<a href='../device'>Get a new certificate</a> with your username and password.</p>",
        )
    return page("Your certificate is ready", issued_body(request, external_url))


def issued_body(request: dict, external_url: str) -> str:
    platform = request["platform"]
    pw = e(request["bundle_password"])
    url = e(external_url)
    download = f'<a class="btn primary" href="{e(request["id"])}/household.p12">Download certificate</a>'
    common_tail = f"""
<div class="card">
  <p><b>Server address</b> (use this in the app):</p>
  <div class="copyrow"><input id="exturl" value="{url}" readonly><button class="btn small" data-copy="exturl" onclick="copyField('exturl')">Copy</button></div>
  <p><b>Certificate password:</b></p>
  <div class="copyrow"><input id="p12pw" class="mono" value="{pw}" readonly><button class="btn small" data-copy="p12pw" onclick="copyField('p12pw')">Copy</button></div>
  <p class="muted">Then sign in with username <b>{e(request['username'])}</b> and your password.</p>
</div>"""

    if platform == "ios":
        steps = f"""<ol>
<li>{download}<br><span class="muted">Tap <b>Download</b> when Safari asks. It's saved in the Files app under Downloads.</span></li>
<li>Install <a href="https://apps.apple.com/app/home-assistant/id1099568401">Home Assistant</a> from the App Store (version 2026.9.1 or newer), and open it.</li>
<li>Choose to enter the address manually and paste the server address below.</li>
<li>When the app says the server needs a client certificate, tap <b>Import</b>, pick <code>household.p12</code> from Downloads and enter the certificate password.</li>
<li>Already set up the app at home? Go to <b>Settings → Companion App → your server</b>, set the <b>External URL</b> to the address below, and import the certificate under <b>Client Certificate</b>.</li>
</ol>"""
    elif platform == "android":
        steps = f"""<ol>
<li>{download}</li>
<li>Tap the downloaded file. If it doesn't open an installer, go to <b>Settings → Security &amp; privacy → More security settings → Encryption &amp; credentials → Install a certificate → VPN &amp; app user certificate</b> and pick <code>household.p12</code>.</li>
<li>Enter the certificate password and keep the suggested name.</li>
<li>Install <a href="https://play.google.com/store/apps/details?id=io.homeassistant.companion.android">Home Assistant</a>, open it and enter the server address below. When Android asks which certificate to use, choose the one you just installed.</li>
<li>Already set up the app at home? In <b>Settings → Companion app → your server</b>, set the <b>External URL</b> to the address below. You'll be asked for the certificate the first time you're away from home.</li>
</ol>"""
    else:
        steps = f"""<ol><li>{download}</li>
<li>Import <code>household.p12</code> into your device or browser's certificate store using the password below, then open the server address.</li></ol>"""

    return f"""<h1>You're in, {e(request['name'])}!</h1>
<p class="muted">This certificate lets <b>{e(request['device'])}</b> reach Home Assistant from anywhere. Download it now – the link works for 24 hours.</p>
<div class="card">{steps}</div>{common_tail}
<p><a class="btn" href="homeassistant://navigate/lovelace">Open the Home Assistant app</a></p>"""


# -- admin -------------------------------------------------------------------


def admin_page(base: str, pending: list[dict], members: list[dict], errors: list[dict], portal_urls: list[str],
               qr_svg: str, status: dict) -> str:
    def action(path: str, label: str, cls: str = "", confirm: str = "") -> str:
        onsubmit = f' onsubmit="return confirm(\'{e(confirm)}\')"' if confirm else ""
        return (f'<form class="inline" method="post" action="{e(base)}/{path}"{onsubmit}>'
                f'<button class="btn small {cls}">{label}</button></form>')

    rows = "".join(
        f"""<tr><td><b>{e(r['name'])}</b><br><span class="muted">{e(r['username'])}</span></td>
<td>{e(r['device'])}<br><span class="muted">{e(r['platform'])}</span></td><td>{when(r['created'])}</td>
<td>{action(f"approve/{r['id']}", "Approve", "primary")} {action(f"deny/{r['id']}", "Deny", "danger")}</td></tr>"""
        for r in pending
    )
    pending_html = (
        f"<table><tr><th>Person</th><th>Device</th><th>Requested</th><th></th></tr>{rows}</table>"
        if pending else '<p class="muted">No pending requests.</p>'
    )

    error_html = "".join(
        f"""<p class="bad">Certificate for <b>{e(r['name'])}</b> ({e(r['device'])}) failed: <span class="mono">{e(r.get('error', ''))}</span>
{action(f"approve/{r['id']}", "Retry", "primary")}</p>"""
        for r in errors
    )

    member_cards = []
    for m in members:
        devices = "".join(
            f"""<tr><td>{e(d['name'])}<br><span class="muted">{e(d['platform'])}</span></td>
<td>{when(d['issued'])}</td><td>{when(d.get('expires'))}</td>
<td><span class="pill {'ok' if d['status'] == 'active' else 'bad'}">{e(d['status'])}</span></td>
<td>{action(f"revoke/{m['user_id']}/{d['cert_id']}", "Revoke", "danger", f"Revoke {d['name']}? It will stop working immediately.") if d['status'] == 'active' else ''}</td></tr>"""
            for d in m["devices"]
        )
        if m["active"]:
            head_action = action(f"remove/{m['user_id']}", "Remove from household", "danger",
                                 f"Remove {m['name']}? This revokes all their devices and disables their account.")
            badge = '<span class="pill ok">active</span>'
        else:
            head_action = action(f"restore/{m['user_id']}", "Re-enable account")
            badge = '<span class="pill bad">removed</span>'
        member_cards.append(
            f"""<div class="card"><p><b>{e(m['name'])}</b> <span class="muted">{e(m['username'])}</span> {badge}
<span style="float:right">{head_action}</span></p>
<table><tr><th>Device</th><th>Issued</th><th>Expires</th><th>Status</th><th></th></tr>{devices or '<tr><td colspan=5 class="muted">No devices</td></tr>'}</table></div>"""
        )

    status_items = "".join(
        f'<li><span class="{"ok" if ok else "bad"}">{"✔" if ok else "✖"}</span> {e(label)}</li>'
        for label, ok in status.items()
    )
    links = "".join(f'<div><code>{e(u)}</code></div>' for u in portal_urls)

    return page(
        "Household",
        f"""<h1>Household</h1>
<div class="card" style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
  <div class="qr">{qr_svg}</div>
  <div style="flex:1;min-width:220px"><p><b>Invite someone</b></p>
  <p class="muted">Have them join your Wi-Fi, then scan this code or open:</p>{links}
  <ul style="list-style:none;padding:0;margin-top:14px">{status_items}</ul>
  <p><a class="btn small" href="{e(base)}/setup">Remote access setup</a></p></div>
</div>
<h2>Join requests</h2><div class="card">{error_html}{pending_html}</div>
<h2>Members</h2>{''.join(member_cards) or '<p class="muted">Nobody has joined yet.</p>'}""",
        wide=True,
    )


# -- setup wizard --------------------------------------------------------------

TOKEN_PERMISSIONS = [
    {"key": "zone", "type": "read"},
    {"key": "dns", "type": "edit"},
    {"key": "ssl_and_certificates", "type": "edit"},
    {"key": "firewall_services", "type": "edit"},
    {"key": "argo_tunnel", "type": "edit"},
    {"key": "calls", "type": "edit"},
]


def token_link() -> str:
    keys = quote(json.dumps(TOKEN_PERMISSIONS, separators=(",", ":")))
    return (f"https://dash.cloudflare.com/profile/api-tokens?permissionGroupKeys={keys}"
            f"&accountId=%2A&zoneId=all&name={quote('Home Assistant Household')}")


def _steps_header(current: int) -> str:
    names = ["Connect Cloudflare", "Choose address", "Set up"]
    return '<p class="muted">' + " → ".join(
        f"<b>{n}</b>" if i == current else e(n) for i, n in enumerate(names)) + "</p>"


def wizard_connect(base: str, error: str = "") -> str:
    return page(
        "Set up remote access",
        f"""<h1>Set up remote access</h1>{_steps_header(0)}
<div class="card">
<p>You need a domain on Cloudflare. If you don't have one yet, buy one at
<a href="https://dash.cloudflare.com/?to=/:account/domains/register" target="_blank" rel="noopener">Cloudflare Registrar</a>
(from about $10/year), or add a domain you already own to a free Cloudflare account.</p>
<ol>
<li><a class="btn primary" href="{e(token_link())}" target="_blank" rel="noopener">Create a Cloudflare API token</a><br>
<span class="muted">The permissions are pre-filled. Under <b>Zone Resources</b>, you can limit it to just the domain you'll use. Click <b>Continue to summary</b>, then <b>Create Token</b>.</span></li>
<li>Paste the token here:</li>
</ol>
{f'<p class="error">{e(error)}</p>' if error else ''}
<form method="post" action="{e(base)}/setup/token">
  <input name="token" type="password" autocomplete="off" placeholder="Cloudflare API token" required>
  <p><button class="btn primary" type="submit">Continue</button></p>
</form>
<p class="muted">The token is stored only in this add-on's private storage (and your Home Assistant backups).</p>
</div>""",
    )


def wizard_domain(base: str, zones: list[dict], subdomain: str = "ha", error: str = "") -> str:
    options = "".join(f'<option value="{e(z["id"])}">{e(z["name"])}</option>' for z in zones)
    return page(
        "Choose address",
        f"""<h1>Choose the address</h1>{_steps_header(1)}
{f'<p class="error">{e(error)}</p>' if error else ''}
<form method="post" action="{e(base)}/setup/domain" class="card">
  <label for="subdomain">Address</label>
  <div class="copyrow"><input id="subdomain" name="subdomain" value="{e(subdomain)}" style="max-width:10em"><span>.</span>
  <select name="zone_id">{options}</select></div>
  <p class="muted">Household members' phones will use <b>https://&lt;address&gt;</b> from outside your home.</p>
  <p><button class="btn primary" type="submit">Continue</button></p>
</form>
<form method="post" action="{e(base)}/setup/reset"><button class="btn small">Use a different token</button></form>""",
    )


_STATUS_ICON = {"ok": ("ok", "✔"), "warn": ("warn", "⚠"), "fail": ("bad", "✖"), "skip": ("muted", "–"),
                "running": ("", '<span class="spinner"></span>')}


def setup_page(base: str, hostname: str, steps: dict, results: dict, running: bool, complete: bool) -> str:
    rows = []
    for key, label in steps.items():
        result = results.get(key)
        cls, icon = _STATUS_ICON.get(result["status"], ("muted", "·")) if result else ("muted", "·")
        detail = f'<br><span class="muted">{e(result["detail"])}</span>' if result and result.get("detail") else ""
        rows.append(f'<li style="list-style:none;margin:10px 0"><span class="{cls}" style="display:inline-block;width:1.6em">{icon}</span>{e(label)}{detail}</li>')
    if running:
        button = '<p class="muted"><span class="spinner"></span>Setting up… Home Assistant may restart once; this page will catch up.</p>'
    else:
        button = (f'<form method="post" action="{e(base)}/setup/run"><button class="btn primary">'
                  f'{"Re-run setup (repair)" if results else "Set up"}</button></form>'
                  + ('' if results else '<p class="muted">Home Assistant may restart once during setup.</p>'))
    done = (f'<div class="card"><p class="ok"><b>Remote access is ready.</b></p><p>Add your own phone first: on Wi-Fi, open the join portal '
            f'(QR code on the <a href="{e(base)}/">Household page</a>) and choose “I already have an account”.</p></div>') if complete else ""
    poll = "setTimeout(()=>location.reload(),3000);" if running else ""
    return page(
        "Remote access setup",
        f"""<p><a href="{e(base)}/" class="muted">← Household</a></p>
<h1>Remote access setup</h1>{_steps_header(2)}
<p>Address: <b>https://{e(hostname)}</b></p>
{done}
<div class="card"><ul style="padding:0;margin:0">{''.join(rows)}</ul></div>
{button}
<form method="post" action="{e(base)}/setup/reset" onsubmit="return confirm('Forget the token and address? Nothing is deleted in Cloudflare.')">
<button class="btn small">Change token or address</button></form>""",
        script=poll,
    )
