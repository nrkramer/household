# Changelog

## 0.2.2

- Setup: when the token check fails, show every Cloudflare call with its HTTP status and answer.
- Setup: the connect step lists exactly which token permission rows are needed.
- Setup: the tunnel permission check no longer passes on a 404.
- Token link: try more names for the Zone WAF and Cloudflare Tunnel permissions.

## 0.2.1

- Setup: fixed the address input being squeezed to zero width.
- Setup: permission check probes the exact firewall entry point it uses, and shows Cloudflare's error text when a permission is missing.

## 0.2.0

- Guided setup in the Household panel: one pre-filled Cloudflare token, then one click creates the tunnel, DNS, mTLS, firewall rule and TURN relay, configures Home Assistant's reverse proxy settings, and runs an end-to-end test.
- `cloudflared` is bundled and run by the add-on (no separate Cloudflared add-on needed).
- The camera relay integration is installed and configured automatically.
- Cloudflare settings moved from add-on options into the setup wizard.
- Pre-built images for amd64 and aarch64 (no more building on your device).
- AppArmor profile: `cloudflared` runs confined, without access to the add-on's data or your Home Assistant config.
- Icon and logo.

## 0.1.x

- Join portal, owner approval, per-device certificates, revocation.
