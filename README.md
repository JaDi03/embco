# Embco

Micro-task platform (data labeling) with instant USDC payouts on Arc Testnet. Mobile-first and installable as a PWA.

## Structure

| Folder       | Contents                                   |
|--------------|--------------------------------------------|
| `frontend/`  | Next.js app (UI + API routes)              |
| `contracts/` | Solidity contracts (Hardhat) — coming soon |

## Development

```bash
cd frontend
cp .env.example .env.local   # fill in the values
npm install
npm run dev                  # http://localhost:3000
```

| Script          | What it does                                             |
|-----------------|----------------------------------------------------------|
| `npm run dev`   | Dev server with hot reload (service worker disabled)    |
| `npm run build` | Production build                                        |
| `npm start`     | Serves the last build (does not pick up code changes)  |
| `npm run lint`  | ESLint                                                   |
| `npm run icons` | Regenerates app/PWA icons from `public/brand/*.svg`     |

## App routes

| Route      | For       | Navigation                                     |
|------------|-----------|------------------------------------------------|
| `/`        | Everyone  | Landing page                                   |
| `/worker`  | Workers   | Bottom tab bar on phones, sidebar on desktop   |
| `/agency`  | Agencies  | Sidebar on desktop, slide-in menu on phones    |

> Public documentation will be published later on GitHub Pages.
