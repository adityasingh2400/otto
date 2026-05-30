# Otto — Design System

**Warm Editorial.** Chosen via `/design-shotgun` (direction B) and implemented across the
whole site. This supersedes the earlier "Sunset Arcade" direction — arcade was rejected.

Source DNA: the founder's **ReRoute** project (warm cream + maroon, tight token system) +
an editorial **Fraunces** display face. Warm and human, but grown-up: it fits the product's
core promise, "trust me to answer your phone," better than anything playful or neon.

## Tokens

```
bg #FAF6F1 · card #FFFFFF · cream2 #F4ECE2 · paper #FBF7F1 · cream-muted #E7D5BC
ink #2A0A10 · ink-soft #6B4A3A · ink-fade #9A8575
brand: maroon #7A1B2D / maroon-l #9A3040 / maroon-d #4A0F18
semantics: pass #4A7A2E (bg #E8F2E0) · fail/danger #9A2020 (bg #F6E5E5) · gold #9A7020 (bg #F3E8CE)
hairlines: line #E4D7C8 · line2 #D8C7B4
soft shadows: sm 0 1px 3px / md 0 10px 28px -14px / lg 0 30px 70px -40px  rgba(42,10,16,…)
```

## Type
- **Display** `Fraunces` (600/700) — wordmark, big numbers, banner. Editorial, warm.
- **Display italic** `Fraunces` italic 500 — taglines, greetings, muted captions.
- **Body** `DM Sans` (400–700).
- **Mono** `JetBrains Mono` — data, codes, transcripts, status lines.

Loaded via Google Fonts; system fallbacks if offline.

## Motifs (the "feel")
- **Soft shadows, not hard.** Rounded cards (13–22px), gentle `sh-md` lift, hover raises 1–2px.
  (The arcade hard-offset shadow is gone.)
- **Maroon Fraunces numbers.** The hero pass-rate is a big maroon Fraunces figure that counts
  up — no holographic rainbow.
- **Faint maroon paper grain** over the whole canvas (a 7px dot multiply at .5 opacity) for warmth.
- **Gentle motion.** `rise` (fade-up) on cards/diffs/banner; a soft green `flash` when a swarm
  card flips fail→pass; pulsing dots on "live." No stamp/wiggle.
- **Pass = warm green, fail = danger red.** Consistent across the arena, diffs, metrics.
- **Tactile, custom components** (hand-built, no UI lib): the launch input, maroon CTA, demo
  chips, stepper, foil→maroon metric numbers, swarm caller cards, before/after diff, production
  buttons, owner-alert feed, the activated banner. On the landing: an animated hero that cycles
  fail→heal→pass per vertical, a vertical segmented control, a how-it-works timeline.

## Where it lives
- `apps/web/index.html` — the **landing** (served at `/`). Animated hero, vertical selector, timeline.
- `apps/web/app/index.html` — the **mission-control dashboard** (served at `/app/`), re-skinned to match.
- `apps/web/landing/` — the three explored directions (A Quiet Swiss, B Warm editorial, C Dark technical) kept for reference.

## Voice
Warm, direct, trust-forward. "Let AI answer your phone. Safely." Confident verbs, no hype.
Mono for anything machine-truthful (pass rates, policy ids, phone numbers); Fraunces italic for the human asides.

## Porting to Next.js (later)
Drop these tokens into `tailwind.config.ts` (colors + the `rise`/`flash`/`pulse` keyframes +
the three soft `boxShadow` levels) and the look transfers 1:1. The two single-file pages are
the source of truth for the look today.
