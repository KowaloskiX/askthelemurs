# AskTheLemurs! frontend

Next.js 16, Tailwind 4, deck.gl 9 and MapLibre. It talks to the FastAPI server in `app/` over HTTP and server-sent events.

```bash
pnpm install          # also copies the MapLibre worker to public/maplibre
pnpm dev              # http://localhost:3000, expects the API on http://localhost:8000
```

Set `NEXT_PUBLIC_API` to point at another API (read at build time).

| Path | Contents |
|---|---|
| `src/app/page.tsx` | Start screen, layout, HUD, resident and district cards |
| `src/components/LiveMap.tsx` | The map: residents, answers, districts, venues, travel arcs |
| `src/components/Results.tsx` | Reports for events, city decisions and the data agent |
| `src/components/Chat.tsx` | Report panel and chat |
| `src/lib/useSim.ts`, `api.ts` | Conversation state, streaming client and types |
