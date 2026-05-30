# Landing redesign — minimal layout explorations (2026-05-25)

Generated via `/design-shotgun`. Three **minimal** landing-page layout variations for
**Otto.** (the LineForge product). Status: **awaiting the user's pick — no variant promoted yet.**

## The ask
"Redesign the landing page layout. Give me a few variations. Landing page only."
Then refined to: **"all concepts should be minimalism similar to the existing landing page,
just designed 10x better."**

## Hard constraint (do not violate)
Brand is **locked** by `/Users/aditya/Desktop/callski/DESIGN.md` — "Warm Editorial":
cream `#FAF6F1` + maroon `#7A1B2D`, Fraunces display, DM Sans body, JetBrains Mono for
data/captions, faint maroon dot-grain, soft shadows. The user wanted **layout** variety,
**not** a rebrand. All three variants share the identical brand/tokens; only the
**composition** differs. Keep it that way unless the user says otherwise.

Also kept minimal per the user: same tiny element set as the current page (wordmark,
headline, one URL input, one caption). No product cards, no proof strips, no metrics,
no dashboard screenshots.

## The three directions
| File | Name | Composition |
|------|------|-------------|
| `variant-A.html` | **Off-Center Editorial** | Left-anchored, asymmetric. Big left-rag Fraunces headline, input below it, wide empty cream field on the right. Boldest craft jump, still same brand. |
| `variant-B.html` | **Refined Center** | The current centered shape, elevated: tighter vertical rhythm + a `● answering calls now` live pill (green pulsing dot). Safest upgrade, most conversion-proven CTA placement. |
| `variant-C.html` | **Poster** | Full-bleed. Huge upper-left headline as type-architecture, `YOUR AI FRONT DESK` corner mark, big void, footer hairline grounding the input bottom-left + caption bottom-right. Most dramatic; CTA sits lower. |

Screenshots (1440×900, animations disabled): `shot-A.png`, `shot-B.png`, `shot-C.png`.
Side-by-side board: `board.html` (open in a browser; it iframes the three at scale).

## What each file is
- Standalone, single-file HTML. Self-contained CSS + a 3-line JS form handler.
- Uses the exact DESIGN.md tokens (pixel-accurate, not AI-guessed).
- Fonts load from Google Fonts (system serif/mono fallback offline).
- **Form behavior is identical to the current page**: submit → `location.href='/app/'+(url?('?url='+encodeURIComponent(url)):'')`.
- Responsive: each has a `max-width:560–600px` mobile breakpoint.

## Decisions already made this session
1. Image-gen mockups (`design generate`) were **unavailable** — no OpenAI API key on this
   machine (`~/.gstack/openai.json` missing, `OPENAI_API_KEY` unset). User chose
   **hand-built real HTML** instead (better fidelity for a locked brand, droppable into the repo).
2. Variant A's headline was refined: each tagline sentence now holds its own line
   (`white-space:nowrap` desktop, wraps on mobile) instead of breaking mid-phrase.

## Next step (where to resume)
1. **User picks A / B / C** (or asks to mix elements — e.g. C's poster type with B's centered
   input + live pill). The pick question was about to be asked when the session was handed off.
2. **Promote the chosen variant** → copy it to `/Users/aditya/Desktop/callski/apps/web/index.html`
   (the live landing, served at `/`). It's a drop-in replacement; the current file is also
   49 lines of the same brand, so no other wiring is needed.
3. Optional tweaks the user may want:
   - Variant **C** has one extra element beyond the strict minimal set: the
     `YOUR AI FRONT DESK` top-right corner mark. Remove it for strict minimalism.
   - Variant **B**'s `answering calls now` pill is the one added "refined detail" — easy to drop.
   - Input currently shows placeholder `https://www.your-business.com`. The previous live page
     hard-coded `value="https://www.piccino.com/"` (a demo URL) — restore if desired.

## Canonical copies (both kept in sync as of handoff)
- Repo (this folder): `apps/web/landing/minimal-redesign-2026-05-25/`
- gstack artifacts: `~/.gstack/projects/callski/designs/landing-minimal-20260525/`

## Untouched (intentionally)
- `apps/web/index.html` — the live landing (no variant promoted yet).
- `apps/web/landing/variant-a.html` / `variant-b.html` / `variant-c.html` — an **earlier,
  different** exploration (Quiet Swiss / Warm Editorial / Dark Technical per DESIGN.md). Not related to these.
