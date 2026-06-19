# Tactical Trainer PWA

A standalone, installable Progressive Web App for the 12-week Elite Tactical Athlete program. No build step. No external dependencies. Works fully offline after first visit.

## Run locally

Service workers require a secure context (HTTPS or `localhost`).

```bash
# Option 1 — Node (npx, no install)
npx serve tactical-trainer

# Option 2 — Python
cd tactical-trainer
python3 -m http.server 8080
# then open http://localhost:8080

# Option 3 — VS Code Live Server extension
# Right-click index.html → Open with Live Server
```

Open `http://localhost:PORT` in Chrome or Safari. On first load the service worker installs and caches the app shell. After that it works in airplane mode.

## Icons

The repository ships with pre-generated `icon-192.png` and `icon-512.png` (teal `#0E7490` background, white **T**). To regenerate:

```bash
cd tactical-trainer
npm install canvas
node generate-icons.js
```

## Deploy to a static host

Installability and service workers **require HTTPS**.

### Netlify (drag & drop)
1. Go to [app.netlify.com](https://app.netlify.com) → "Add new site" → "Deploy manually"
2. Drag the `tactical-trainer/` folder onto the drop zone.
3. Done — Netlify provides HTTPS automatically.

### Vercel
```bash
npx vercel tactical-trainer
```

### GitHub Pages
1. Push this repo to GitHub (already done).
2. Go to repo Settings → Pages → Source: branch `claude/dazzling-bell-6j44z1`, folder `/tactical-trainer`.
3. Save. GitHub Pages serves over HTTPS.

## Data & privacy

All training data lives in `localStorage` on your device. Nothing is sent to any server. Export a backup regularly via the Progress tab → Backup & restore.

### localStorage keys
- `tactical:log:w{W}:d{D}` — session log per week/day
- `tactical:tests` — test battery entries

### Backup file format
```json
{ "app": "tactical-trainer", "version": 1, "exportedAt": "ISO", "data": { "tactical:log:...": "..." } }
```
