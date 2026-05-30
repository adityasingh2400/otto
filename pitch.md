# Otto — Pitch

The judging-room pitch for the Gemma 4 Voice Agents Hackathon (YC SF, 2026-05-30).
Co-hosted by Daily/Pipecat + Cekura. Theme: *Voice AI, open models, next-generation evals.*

**Win line:** *We're not selling a voice agent. We're selling the only one you'd actually
trust to answer your phone — because it earned it.*

Cross-refs: demo storyboard → `docs/DEMO.md` · architecture → `docs/ARCHITECTURE.md` ·
per-sponsor detail → `docs/TECH.md`.

---

## The whole pitch is one GAP

Explain the gap, not the feature. What the market believes → what's actually true → why
value is moving. Every number below is sourced; the spine is Tier-1 (un-attackable).

### Belief — "the hard part is building the agent"
The market priced and built around this: make deployment cheap and fast.
- Conversational AI: **$11.58B (2024) → $41.39B (2030), 23.7% CAGR** — Grand View Research.
- You can stand up an AI receptionist for **$49–399/mo** (Rosie, Goodcall, Slang.ai),
  **~$0.09/min** (Bland), live in **~20 min** (Goodcall) — public vendor pricing.

### Truth — "deployment got cheap; trust didn't"
- A 2024 study of **85 businesses across 58 industries found only 37.8% of calls reach a
  live person** (so ~62% don't) — 411 Locals. If building the agent were the bottleneck,
  cheap deployment would have moved this. It hasn't.
- The agent going live is when risk *starts*, and the risk is measured: **91% of ML models
  degrade after deployment, error rising ~35% within six months** — Vela et al., *Temporal
  quality degradation in AI models*, **Scientific Reports (Nature), 2022** (32 datasets, 4
  model families; Harvard/MIT/Cambridge/U. Monterrey).

### Why now — "when the agent is a commodity, value migrates to proving it's safe"
- The agent itself collapsed from a team-quarter to a weekend. The scarce thing is now
  *proof it's safe*, so an eval category is being born to fill it.
- Market signal you're standing in: **the hackathon theme is "next-generation evals," and
  the co-host is an eval company (Cekura).** Same arc software ran from cheap-deploy →
  observability/CI.

### Otto lives in the gap
Treats the agent as the commodity everyone agrees it is (paste a website — table stakes),
and sells the layer the market just realized it's missing: the agent **earns** the right
to go live by surviving a synthetic-caller swarm, and **re-earns it forever** via the
production loop. Not a cheaper receptionist — the trust layer that makes any of them safe
to turn on.

---

## Source quality tiers (know this before a judge pushes)

### Tier 1 — bulletproof (lead with these)
| Claim | Number | Source |
|---|---|---|
| US small businesses | 34.8M (6.27M employer firms) | SBA Office of Advocacy, 2024 Small Business Profile (govt) |
| Conversational AI market | $11.58B (2024) → $41.39B (2030), 23.7% CAGR | Grand View Research (PR Newswire release) |
| Models degrade after launch | 91% degrade; +35% error in 6 months | Vela et al., Scientific Reports (Nature), 2022 |
| GenAI pilots fail | 95% deliver no measurable P&L impact | MIT, Project NANDA — State of AI in Business 2025 (GenAI broadly, not voice-specific) |
| Agentic AI projects | >40% canceled by end of 2027 | Gartner press release, Jun 2025 |
| Agents fail office tasks | ~70% | Carnegie Mellon, TheAgentCompany benchmark |

### Tier 2 — real but commercial (attribute by name + method; never say "studies show")
| Claim | Number | Caveat |
|---|---|---|
| SMB voice-AI adoption | ~34% deployed → ~66% have not | Thoughtly, State of Voice AI 2025 — vendor survey. Say "a 2025 industry survey." |
| Calls answered live | 37.8% (≈62% not) | 411 Locals 2024 — real but small sample, marketing firm. Attribute. |
| Won't call back / voicemail dead | 85% / 80% | Blogs cite "BIA/Kelsey"; primary doc unconfirmed. Say "commonly cited" or drop. |
| Competitor pricing / setup | $49–399/mo, ~$0.09/min, ~20 min | Public vendor pages. Say "publicly listed pricing." |

### Tier 3 — DROP (vendor marketing; you'll get caught)
- **"$126K/yr, $1,200 per missed call"** — getaira.io blog's own calculation, not measured.
- **"Generic eval tools miss 40% of voice failure modes"** — Hamming AI, a voice-eval
  vendor marketing itself. Quoting a competitor's ad as fact.

### Verify-before-slide
Open the Nature DOI yourself and confirm the "91%" + "35%/6-month" wording. It's the one
number a sharp judge might challenge — read the primary source so you can defend it.

---

## Pitch variants

### Cold open (numbers-direct — preferred stage opener)
Go straight at the data; the gap reveals itself.
> You've all seen AI receptionists for local businesses. So here's the number that should
> stop you: **about two-thirds of small businesses still haven't deployed one.** It's not
> that they don't need it — they miss **62% of their calls.** It's that they can't afford
> to be wrong. A big brand shrugs off a bad interaction; for a local business the phone
> *is* the storefront, and a lost customer is a real hit. And they're right to be scared:
> **95% of generative-AI pilots never deliver measurable business impact (MIT). Gartner
> says 40% of agentic-AI projects will be scrapped by 2027. At Carnegie Mellon, AI agents
> failed routine office tasks ~70% of the time.** Agents don't crash — they confidently say
> the wrong thing, and you find out when a customer complains. So the question was never
> "can AI answer the phone." It's "can you trust it not to lose you the customer." That's
> Otto.

Honesty notes for live Q&A:
- "Two-thirds haven't deployed" = ~34% adoption from a 2025 industry survey (Thoughtly,
  vendor) — say "a 2025 industry survey," not "studies show."
- MIT 95% and Gartner 40% are AI agents/pilots *broadly*, not voice-specific. If pushed:
  "that's AI agents in general — voice is harder, because it fails live, out loud, to your
  customer." That answer strengthens the point.
- "A lost customer hurts a local business more" has **no clean citable stat** — keep it as
  plain logic ("the phone is the storefront"). Don't fabricate a number.

### One-liner (hallway / first sentence)
> Every other voice agent ships and hopes. Otto refuses to answer your phone until it's
> already failed a thousand calls in private and fixed itself — and it never stops.

### 10-second hook (open the demo)
> Raise your hand if you've seen a voice agent for a local business today. Right — a bunch.
> Now: would you actually let any of them answer *your* phone? Take the reservation, handle
> the food allergy, not lose you the customer? That's the problem. Anyone can build an
> agent. Nobody can prove it's safe to ship. That's what we built.

### 90-second pitch (cited; Tier-1 spine + attributed Tier-2)
> Conversational AI goes from 11 and a half billion today to 41 billion by 2030 — Grand
> View Research. The whole market believes the hard part is building the agent, so they
> made it cheap: 49 bucks a month, live in twenty minutes.
>
> But here's the gap. A 2024 study of 85 businesses across 58 industries found only **38%
> of calls reach a live person.** Deployment got cheap; that number didn't move. Because
> building the agent was never the bottleneck — trust is. And the risk is real and
> measured: a Nature study found **91% of AI models degrade after launch,** error climbing
> 35% in six months. The agent going live is when the danger *starts.*
>
> That's why this hackathon's theme is evals. When the agent is free, the value is in
> proving it's safe. **Otto is that layer.** Before the number ever goes live, a swarm of
> synthetic callers attacks it — the severe allergy, the 14-person party, "are you open
> now." It fails, we catch exactly how, it **rewrites its own policies** (a clean
> before/after diff, not a re-prompt), re-runs, and the pass rate climbs **58% → 100%.**
> Only then does the line activate. And it never stops: every real call is judged, and a
> real failure triggers a targeted swarm-heal on that one thing, forever. The whole loop
> runs on Cekura's swarm — we pushed it as far as it goes.
>
> Don't take my word for it. **Here's the number. Call it. Ask the exact allergy question
> the swarm caught an hour ago.** [judge calls — it handles it]
>
> We're not selling a voice agent. We're selling the only one you'd actually trust to
> answer your phone — because it earned it.

### 30-second version (judging-table round)
> You've all seen AI receptionists for local businesses. Here's the number nobody mentions:
> **about two-thirds of small businesses still haven't deployed one.** They miss **62% of
> their calls** — and they still won't risk it. Because **MIT just found 95% of GenAI
> pilots fail to deliver impact.** Agents don't crash — they confidently say the wrong
> thing. Otto is the trust layer: it fails a thousand synthetic calls in private and
> **fixes itself** before the line goes live. Here's the number. Call it.

~80 words, ~32 seconds spoken. Same honesty notes as the cold open — "a 2025 industry
survey" for the two-thirds (Thoughtly), MIT 95% is GenAI broadly not voice-specific
(strengthens the point if pushed).

---

## Differentiation — who you beat and how

The worry isn't being out-idea'd; it's getting lumped in. Lead from the peak, not the base.

- **Tier 1 field (most teams): a voice agent, no real eval.** You beat them on thesis —
  they're off-theme. Don't dwell on "we built an agent"; that's the crowded base.
- **Tier 2 field (some teams): an eval/red-team *tool*.** They show "here are the failures"
  — a report. You **close the loop**: find → patch → re-run → gate → go live. A report vs.
  a system.
- **Tier 3 field (0–2 teams: also close the loop, live).** Beat them on depth they can't
  reach in 4 days:
  - **Two loops** — pre-launch gate is obvious; the *perpetual production loop* (self-heal
    after launch, backed by the Nature degradation finding) is not.
  - **Legible patches** — edits `policies[]` only; prompt recompiles into a readable
    before/after diff. Others re-prompt and it looks like vibes.
  - **Vertical-archetyped swarm** — contractor gets dispatch/licensing callers, clinic gets
    "no medical advice." Eval *quality*, not generic red-teaming.
  - **Real + keyless** — half the eval demos are quietly staged; ours runs live.
  - **The closer** — judge dials the number, hits the exact patched failure, it works.
    Un-clonable on the day. A claim vs. a memory.

**Demo discipline:** spend seconds on the peak (gate, self-rewrite, forever-loop, the live
call), not the base (it's a voice agent, you paste a website, you run evals).

---

## Sources
- SBA Office of Advocacy — 2024 Small Business Profile (34.8M): https://advocacy.sba.gov/2024/11/19/new-advocacy-report-shows-small-business-total-reaches-34-8-million-accounting-for-2-6-million-net-new-jobs-in-latest-year-of-data/
- Grand View Research — Conversational AI Market ($11.58B→$41.39B, 23.7% CAGR): https://www.grandviewresearch.com/industry-analysis/conversational-ai-market-report — release: https://www.prnewswire.com/news-releases/conversational-ai-market-to-be-worth-41-39-billion-by-2030-at-cagr-23-7---grand-view-research-inc-302452404.html
- Vela et al., "Temporal quality degradation in AI models," Scientific Reports (Nature), 2022 — 91% degrade / +35% in 6mo: https://www.nature.com/articles/s41598-022-15245-z *(verify DOI/wording before slide)*
- 411 Locals — 2024 study, 37.8% answered live: https://411locals.us/small-business-owners-dont-answer-62-of-phone-calls/
- MIT / Project NANDA — State of AI in Business 2025 (95% of GenAI pilots fail): https://mlq.ai/media/quarterly_decks/v0.1_State_of_AI_in_Business_2025_Report.pdf *(confirm the 95% wording in the primary PDF before slide)*
- Gartner — Over 40% of agentic AI projects canceled by end of 2027 (press release, Jun 2025): https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027
- Carnegie Mellon — TheAgentCompany benchmark (~70% task failure): https://the-agent-company.com/
- Thoughtly — State of Voice AI 2025 (SMB adoption ~34%): https://www.thoughtly.com/blog/the-state-of-voice-ai-in-2025-an-industry-report-and-survey/
