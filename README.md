# Household: remote access for Home Assistant

Use the Home Assistant app away from home without a VPN, without opening router
ports, and without a Nabu Casa subscription. The only cost is your own domain.

- **Nothing exposed to the internet.** A Cloudflare Tunnel connects out from your
  Home Assistant box; your router stays closed.
- **Only your devices get in.** Cloudflare refuses any connection that doesn't
  present a client certificate you issued (mTLS), before a single request reaches
  Home Assistant. Password-guessing bots and Home Assistant exploits never get
  to Home Assistant.
- **Easy for the people you live with.** On your Wi-Fi they open a page (or scan
  a QR code), create an account and get a certificate for their phone. You get
  a notification with Approve/Deny.
- **Easy to remove people.** One click revokes all of someone's devices and
  disables their account. It takes effect within seconds.
- **Cameras work remotely.** WebRTC camera streams are relayed through Cloudflare's
  TURN service (1,000 GB/month free), so live video is fast even on cellular.

```
Phone ──TLS + client cert──▶ Cloudflare ──no/revoked cert? blocked──▶ Tunnel ──▶ Home Assistant
Camera video (WebRTC) ◀──────── Cloudflare TURN relay ────────▶ go2rtc
```

## Requirements

- Home Assistant OS or Supervised (add-ons are required), on amd64 or aarch64.
- A domain on Cloudflare (free plan). If you don't have one, [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/) sells them at cost, from about $10/year.
- Home Assistant Companion app **2026.9.1+ on iOS**. Android supports client certificates natively.

## Install

1. [![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fnrkramer%2Fhousehold)
   (or add `https://github.com/nrkramer/household` under Settings → Add-ons → Add-on Store → ⋮ → Repositories).
2. Install **Household** and start it. Turn on **Show in sidebar**.
3. Open **Household** in the sidebar and follow the setup:
   1. Click **Create a Cloudflare API token** (the permissions are pre-filled), create it, and paste it in.
   2. Pick your domain and an address (default `ha.<your-domain>`).
   3. Click **Set up**. The add-on creates the tunnel, DNS record, mTLS
      requirement, firewall rule and camera relay, configures Home Assistant to trust
      the tunnel (Home Assistant restarts once), and then tests the whole path from the
      internet.
4. Add your own phone: on your Wi-Fi, scan the QR code in the panel, choose
   **I already have an account**, and follow the steps on the page.

Running setup again is safe. It checks every piece and repairs anything that's missing.

## What the token can do

The token needs Zone Read, DNS Edit, SSL and Certificates Edit, Zone WAF Edit,
Cloudflare Tunnel Edit and (for cameras) Realtime Edit. It's stored only in
the add-on's private storage, which is included in your Home Assistant backups.
When creating it, limit **Zone Resources** to the domain you use.

## Repository layout

| Path | |
|---|---|
| `household/` | The add-on: setup wizard, bundled `cloudflared`, LAN join portal, admin panel |
| `household/integration/cloudflare_turn/` | Small integration the add-on installs into Home Assistant to supply TURN relay credentials to WebRTC streams |
| `.github/workflows/` | Builds and publishes multi-arch images to `ghcr.io/nrkramer/household` on every push to `main` (HA's official builder actions), plus the app linter |
| `deploy.ps1` | Developer helper: copy the add-on to a test box over the Samba add-on. It removes `image:` from the copy so the box builds it locally |

Releasing: bump `version` in `household/config.yaml`, add a `CHANGELOG.md` entry, and push to `main`. CI publishes the image; Home Assistant then offers the update.

## Security notes

- The join portal (port 8099) is meant for your LAN only. Never forward it.
- New members need owner approval. Existing members add devices with their password.
- Certificates are issued by your Cloudflare account's private CA and are valid only for your domain.

## License

MIT, see [LICENSE](LICENSE).
