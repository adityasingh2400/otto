# LineForge — Design System

The product's visual language, drawn from the founder's own projects so it feels like
their work, not a template. Source DNA:

- **cracked** ("Sunset Arcade") — warm paper→mango gradient, ink `#3C1F15`, cherry +
  marigold + amber accents, **hard offset "arcade" shadows**, holographic/foil text,
  riso-dot texture, stamp-in / count-up / CTA-pulse motion. Bowlby One / Inter / JetBrains Mono.
- **ReRoute & swarma** — warm cream `#FAF6F1` + maroon `#7A1B2D`, tight CSS-variable token
  system, DM Sans / Outfit / JetBrains Mono, soft shadows, generous radii.

LineForge fuses cracked's tactile arcade energy with ReRoute's editorial structure into a
**"Sunset Mission Control"**: warm and confident, but dense enough for live phone-line ops.

## Tokens

```
paper #FFF6EC · cream #FFFAF2 · cream-dim #F5EBD9 · cream-muted #E7D5BC
ink #3C1F15 · ink-soft #6E3F2E · ink-fade #9C7560
sunset gradient: blush #FFE4D6 → peach #FFCFA8 → apricot #FFB07A → mango #FFA532
accents: cherry #FF6B5C / #E03E2D · marigold #FFC53D / #E0A41F · amber #E8B547 · maroon #7A1B2D
semantics: pass #2FA45E / #1E7A43 · fail = cherry · warn = marigold
hairlines: rgba(60,31,21,.16) / .28
```

## Type

- **Display** `Bowlby One` — wordmark, big metric numbers, banner. Chunky, retro-arcade.
- **Body** `Inter` (400–700).
- **Mono** `JetBrains Mono` — all data, codes, transcripts, status lines.
- **Serif accent** `Fraunces` italic — taglines, greetings, muted captions (editorial flavor).

Loaded via Google Fonts; system fallbacks if offline.

## Signature motifs (the "feel")

- **Arcade hard shadows** — solid offset shadows (`5px 5px 0 ink`), not blur. Cards,
  buttons, chips. Buttons press *into* the shadow on `:active` (tactile, UIverse-spirit).
- **Foil hero number** — the pass-rate animates a holographic gradient with an ink stroke
  (`holoPan`), and counts up. It's the hero stat; make it sing.
- **Stamp-in** — cards/diffs/banner arrive with a rotated overshoot (like a stamp landing).
- **Flip-to-green** — when the swarm re-run flips a card fail→pass, it pulses a green inset.
- **Riso dot texture** — a faint multiplied dot grid over the whole canvas (cracked's print feel).
- **CTA pulse** — the primary build button breathes a cherry ring until pressed.

## Components (custom, in `apps/web/index.html`)

Hand-built in the arcade idiom (no component-lib dependency): tactile input, arcade
button, pill chips, stepper, foil metric cards, arena caller cards (stamp/flip),
before/after patch diff, production replay buttons, and the activated banner. The spirit
of UIverse / 21st.dev (tactile micro-interactions, distinctive components) is honored by
building these custom rather than importing.

## Adapting to a Next.js migration (later)

Port these tokens into `tailwind.config.ts` exactly as cracked does (colors + `boxShadow`
arcade-* + the `holoPan`/`stampIn`/`ctaPulse` keyframes), and the single-file dashboard's
look transfers 1:1. The current single-file build is the source of truth for the look.

## Voice

Warm, direct, a little playful. "Build it." "website → a phone line that heals itself."
Confident verbs, no corporate hedging. Mono for anything machine-truthful (pass rates,
policy ids, phone numbers); serif italic for the human asides.
