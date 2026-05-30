# Demo + video storyboard

The framing the judges hear first: **"Paste your website. Get a working phone line in 30
seconds — then watch it attack and fix itself before it goes live."**

Win line at the end: **"We're not selling a voice agent. We're selling confidence that
your business can safely let AI answer the phone."**

## The 90-second cut (record this)

| t | On screen | Voiceover |
|---|---|---|
| 0:00–0:08 | Dashboard, paste Piccino URL, hit **Build it** (CTA pulses) | "Every local business runs on phone calls — and misses them. AI can answer, but one bad answer loses money or creates liability." |
| 0:08–0:18 | Extraction facts stream; agent greeting + policies render | "Paste a website. We extract the business rules into a voice agent in seconds." |
| 0:18–0:30 | Swarm arena fills; cards flip **red** (allergy, 14-top, guessed availability) | "Before it goes live, a swarm of synthetic callers attacks it — archetyped to the business. Watch it fail, on purpose." |
| 0:30–0:42 | Failure map clusters; **auto-patch diff** before/after; pass-rate foil number counts **58% → 100%** | "It finds the failure modes, rewrites its own policies, and re-runs the swarm. This is the eval loop the hackathon is about — built on Cekura." |
| 0:42–0:52 | ACTIVE banner lands with the phone number | "Only once it clears the safety gate does the line go live." |
| 0:52–1:15 | **Pick up a phone, call the number on speaker.** Ask the allergy question. Agent hedges + offers to connect. Live console updates. | "Now you call it — and ask the exact thing the swarm caught. It handles it correctly. The loop closed, live." |
| 1:15–1:30 | Click a **production edge-case** button → targeted heal → re-verify | "And it never stops: every real call is evaluated, and a new failure triggers a focused swarm-heal. We're not selling a voice agent — we're selling confidence that your business can safely let AI answer the phone." |

## Recording setup

1. `./scripts/dev.sh` → `http://localhost:8000` (full-screen, clean browser).
2. Have the **Piccino** chip ready; do a dry run so fonts/animation are warm.
3. For the live call: agent running (`uv run bot.py --transport twilio --proxy <ngrok>`),
   Twilio number on speaker, quiet room. If wifi is risky, pre-record this segment.
4. Screen-record at 60fps; the foil pass-rate count-up and arena red→green are the hero shots.

## Show the breadth (verbally, not built)

After the restaurant run, click the contractor, clinic, salon, and law-firm chips: same
flow, totally different synthetic callers (emergency dispatch / licensing for the
contractor; "no medical advice" / Rx / identity-verify for the clinic; specific-stylist /
dye-allergy for the salon; "no legal advice" / conflict-check for the law firm). One line:
**"Different business, same launch flow. Website in, safe phone line out."**

## Sponsor name-drops (work them in)

Pipecat/Daily (the agent), Cekura (the eval loop — co-host, in the room), Twilio (the
number you just called), NVIDIA (Parakeet/Magpie for fast speech), AWS (Bedrock AgentCore
scales it to thousands of simultaneous callers). See `docs/TECH.md`.

## Backup

If anything is flaky live, the dashboard run is fully deterministic with the cached specs
(`piccino` / `contractor` / `clinic`) — record that as the safety net.
