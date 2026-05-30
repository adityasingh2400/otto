# Demo + video storyboard

The framing the judges hear first: **"Paste your website. Get a working phone line in 30
seconds — then watch it attack and fix itself before it goes live."**

Win line at the end: **"We're not selling a voice agent. We're selling confidence that
your business can safely let AI answer the phone."**

## The submission cut — a hybrid (record this)

Two halves, ~2 minutes. **Part A is us on camera** — walking around, talking like humans,
B-roll energy. It carries the *why*: the problem, the stakes, the market, the thesis. **Part B
is the screen + a real phone call** — it carries the *proof*. Keep every spoken number direct
and easy; flash the hard ones on screen. Numbers are tagged **[T1]** bulletproof · **[T2]**
attribute by name · **[logic]** no stat, don't invent one.

### Part A — on camera (≈0:00–1:00): the why

| beat | on camera | what you say (direct) | flash on screen |
|---|---|---|---|
| **Hook** | One of us, mid-stride, phone in hand | "Every local business runs on its phone. And they miss most of the calls — a 2024 study found only **38% reach a live person.**" | **62% of calls go unanswered** [T2 — "a 2024 study," 411 Locals] |
| **Stakes** | Other person picks it up, talking to camera | "For Google a bad call is a rounding error. For the restaurant down the street the phone *is* the storefront — one botched call isn't one lost order, it's a regular you never get back." | *(no number — this is [logic], and it's stronger for it)* |
| **Why they won't fix it** | Back to first person, walking | "They *could* deploy an AI receptionist in twenty minutes for fifty bucks. They don't — because a wrong answer is worse than a missed one. And they're right: **91% of AI models degrade after launch. 95% of GenAI pilots deliver zero impact.** Agents don't crash — they confidently say the wrong thing." | **91% degrade** [T1, Nature] · **95% of pilots fail** [T1, MIT] |
| **Market + thesis** | Both, casual | "There are **34.8 million** small businesses in the US. Conversational AI hits **$41 billion by 2030.** Everyone's racing to build a cheaper agent. Nobody's selling the thing that actually unlocks it: proof it's safe to turn on. That's Otto." | **34.8M businesses** [T1, SBA] · **$41B by 2030** [T1, Grand View] |
| **Scale tease** | Gesture at a laptop / wave it off | "And it's not just restaurants — anything that answers a phone and takes an action. Contractors, clinics, salons, law firms. Same flow, totally different callers." | *(chips: restaurant · contractor · clinic · salon · law firm)* |

### Part B — screen + live call (≈1:00–2:00): the proof

| beat | on screen | voiceover | flash |
|---|---|---|---|
| **Build** | Paste Piccino URL → **Build it**; facts stream; greeting + policies render | "Paste a website. Thirty seconds later, a working phone agent." | **~30s** |
| **Attack** | Swarm arena fills; cards flip **red** (allergy, 14-top, guessed availability) | "Before it ever goes live, a swarm of synthetic callers attacks it — archetyped to *this* business. Watch it fail, on purpose." | |
| **Self-heal** *(hero viz)* | Failures cluster → **before/after policy diff** animates in → re-run → pass-rate counts **58% → 100%** | "It finds the failure, **rewrites its own policy** — a clean before/after, not a re-prompt — and re-runs the swarm. Fifty-eight to a hundred percent. And it **can't make itself worse**: any fix that breaks something already passing gets rolled back." | **58% → 100%** [real, verified] |
| **Go live** | ACTIVE banner lands with the phone number | "Only once it clears the safety gate does the line go live." | |
| **Call it** *(live recording)* | **Phone on speaker — we actually dial it.** Ask the exact allergy question the swarm caught. Agent hedges + offers to connect. Live console updates. | "So we call it. And we ask the exact thing it failed an hour ago. It handles it. The loop closed — live, on camera." | |
| **Killer case** *(taxonomy)* | Click a **"voice was fine, action was wrong"** replay — *confirmed a sold-out table.* Taxonomy panel: **OUTCOME × critical**, event-stream evidence; auto-heals red→green | "Here's the part other evals miss. The voice sounded **perfect** — but the table was never booked. We don't grade the transcript, we grade the whole call: what it said, what it *did*, what actually happened. We catch it, it writes its own fix, it re-verifies." | **14 detectors · 4 dimensions** [real] |
| **Certificate** | Click **Evaluation report ↗** → printable safety certificate (v1→vN, every scenario, every patch) | "Out the other side: a safety certificate, every number from a real run. We're not selling a voice agent — we're selling confidence that a business can safely let AI answer the phone." | |

### The one killer case, in one breath
If you only land one technical idea, land this: **"The voice was perfect. The table was never
booked. Everyone else grades the words — we grade what it actually did."** That single
said-vs-did flip is the whole taxonomy, made legible in five seconds.

## Recording setup

**Part A (on camera).** Shoot loose — walking, real, two people. Phone-in-hand is good B-roll.
The numbers above are the script spine, not a teleprompter; say them in your own words but keep
the tags honest (don't upgrade a [T2] to "studies show", don't put a dollar figure on the
[logic] stakes line). Record clean audio — this half lives or dies on it.

**Part B (screen + call).**
1. `./scripts/dev.sh` → `http://localhost:8000` (full-screen, clean browser).
2. Have the **Piccino** chip ready; do a dry run so fonts/animation are warm.
3. For the live call: agent running (`uv run bot.py --transport twilio --proxy <ngrok>`),
   Twilio number on speaker, quiet room. If wifi is risky, pre-record this segment.
4. Screen-record at 60fps; the **self-heal 58→100 count-up** and the **OUTCOME×critical
   red→green** are the hero shots — give each a beat to breathe.

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
