# Changelog

## 0.2.0

- Guided setup in the Household panel: one pre-filled Cloudflare token, then one click creates the tunnel, DNS, mTLS, firewall rule and TURN relay, configures Home Assistant's reverse proxy settings, and runs an end-to-end test.
- `cloudflared` is bundled and run by the add-on (no separate Cloudflared add-on needed).
- The camera relay integration is installed and configured automatically.
- Cloudflare settings moved from add-on options into the setup wizard.

## 0.1.x

- Join portal, owner approval, per-device certificates, revocation.
