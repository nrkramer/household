# Household

## Setup

Open **Household** in the sidebar. The setup has three steps:

1. **Connect Cloudflare.** Click the link to create an API token with the needed
   permissions pre-filled, then paste it in. Tip: under *Zone Resources*, choose
   only the domain you'll use.
2. **Choose the address.** Pick your domain and a subdomain (default `ha`).
3. **Set up.** The add-on then:
   - creates (or reuses) a Cloudflare Tunnel and runs it inside this add-on,
   - points the address at the tunnel,
   - requires a client certificate for the address (mTLS),
   - adds a firewall rule that blocks requests without a valid, unrevoked certificate,
   - creates a TURN relay key and installs a small integration so camera streams work remotely,
   - tells Home Assistant to trust the tunnel as a reverse proxy (Home Assistant restarts once),
   - tests from the internet that the address is blocked without a certificate and works with one.

Run setup again at any time. It repairs anything that's missing and changes nothing that's already correct.

If you used the separate **Cloudflared** add-on before, stop it after setup succeeds. This add-on runs the tunnel itself.

## Adding people

Have them join your Wi-Fi and scan the QR code on the Household page (or open
`http://<home-assistant-ip>:8099`).

- **New people** create an account. You get a notification to approve them (in
  Home Assistant, or on your phone if `notify_service` is set).
- **Existing users**, including you, choose *I already have an account* and get a
  certificate immediately.

The page walks them through installing the certificate on iOS or Android and
connecting the Home Assistant app.

## Removing people

**Remove from household** revokes every certificate that person has and disables their Home
Assistant account, which signs them out everywhere. **Revoke** on a single
device cuts off just that device.

## Options

| Option | |
|---|---|
| `cert_validity_days` | Lifetime of issued certificates (default 10 years; revoking is the real control) |
| `notify_service` | Optional, e.g. `notify.mobile_app_my_phone`, for Approve/Deny push notifications |

## Troubleshooting

- **"The token is missing: …"**: edit the token in Cloudflare (My Profile → API Tokens) and add the listed permission.
- **Self-test says Cloudflare is still issuing the HTTPS certificate**: brand-new domains take a few minutes. Run setup again.
- **Self-test returns 400**: Home Assistant didn't accept the proxy settings. Check Settings → System → Network.
- **Remote camera video is slow**: check the *Camera relay* step. The token needs the Realtime permission.
