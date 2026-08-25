---
name: agent-cost-capacity-planner
description: Forecast Copilot Credit consumption and monthly running cost for a Microsoft Copilot Studio agent by reading its actual design, then recommend concrete redesigns that cut spend. Use when someone asks what an agent will cost to run, how many Copilot Credits or messages it will burn, how to size capacity for a rollout, whether to buy prepaid Copilot Credit packs, pay-as-you-go or Credit Commit Units, how to reduce an agent's running cost, or needs a budget sign-off before shipping an agent.
---

# Agent Cost & Capacity Planner

Turn an agent's design into a defensible cost forecast, then turn the forecast into
design changes. Most cost surprises in Copilot Studio are not volume surprises — they
are design surprises: an agent that grounds on the tenant graph when a scoped knowledge
source would do, or calls an agent flow inside a retry loop, costs several times what
its owner budgeted at the same traffic.

## Scope

**In scope:** credit consumption modelling, monthly cost forecast, purchasing-model
comparison, capacity headroom, cost hotspot analysis, optimization recommendations,
budget one-pager.

**Out of scope:** provisioning or changing anything in the tenant, negotiating pricing,
per-seat Microsoft 365 Copilot licence entitlement decisions, Azure infrastructure cost
for custom connectors or external APIs (flag these as "out of model" line items rather
than guessing).

**Never do this:** never state a currency amount from memory. Credit *rates* are stable
and listed below; the *price per credit* varies by purchasing motion, region, agreement
and time. Always ask the user for their effective price per credit, or have them confirm
it from their licensing guide, before converting credits into money.

## Step 1 — Gather inputs

Ask for what is missing. Do not proceed on assumptions without labelling them.

**Design inputs** (best case, the exported solution; worst case, a description):

- exported agent solution `.zip` or unpacked solution folder, or the agent's topic list
- knowledge sources configured, and their scope (SharePoint site, specific files, public
  website, Dataverse, tenant graph / Microsoft Graph grounding)
- tools and actions: connectors, agent flows, custom MCP or REST tools, child agents
- orchestration mode (classic / generative), and whether a reasoning model is enabled
- channels, and whether voice is enabled (voice changes the model completely — it bills
  per minute, not per interaction)
- autonomous triggers and their expected firing frequency

**Volume inputs:**

| Input | Notes |
|---|---|
| Monthly active users | distinct humans, not sessions |
| Sessions per user per month | a session = one conversation |
| Turns per session | user messages, not agent messages; ask for p50 and p90 |
| % of users holding a Microsoft 365 Copilot licence | drives the zero-rating below; usually the single largest lever |
| Employee-facing or customer-facing | zero-rating only applies to employee-facing, authenticated M365 Copilot USL identities |
| Growth assumption | % month over month, and any launch step-change |

If the user cannot give turns per session, use 4 for a task agent and 8 for a Q&A agent,
and label it clearly as an assumption to be replaced with real telemetry after two weeks
of production traffic.

## Step 2 — Billing rates

These are the Copilot Credit rates. **Re-verify them** against
`https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-messages-management`
before publishing a forecast, and record the verification date in the report.

| Agent feature | Copilot Credits | M365 Copilot licensed user |
|---|---|---|
| Classic answer | 1 | no charge |
| Generative answer | 2 | no charge |
| Agent action | 5 | no charge |
| Tenant graph grounding for messages | 10 | no charge |
| Agent flow actions (per 100 actions) | 13 | no charge |
| Text and generative AI tools — basic (per 10 responses) | 1, or 0.1 per 1K tokens | no charge |
| Text and generative AI tools — standard (per 10 responses) | 15, or 1.5 per 1K tokens | no charge |
| Text and generative AI tools — premium (per 10 responses) | 100, or 10 per 1K tokens | no charge |
| Content processing tools (per page) | 8 | no charge |

Voice bills per minute and *includes* the core agent activity for that minute:

| Voice tier | Credits per minute |
|---|---|
| Classic voice, classic orchestration | 10 |
| GenAI voice | 35 |
| Premium GenAI voice | 75 |

Two rules that catch people out:

1. **Features stack within one interaction.** A generative answer that also grounds on
   the tenant graph is 2 + 10 = 12 credits, not 10.
2. **Reasoning models bill twice.** The feature rate applies *plus* the premium
   generative AI tools rate at 10 credits per 1K tokens. An agent with reasoning enabled
   on a chatty topic is the most expensive shape available — model it explicitly.

## Step 3 — Derive the interaction mix from the design

This is the step that separates this skill from a spreadsheet. Do not ask the user how
many generative answers they will have per month — they do not know. Read it out of the
design.

For each topic and each tool, classify what a single execution consumes:

- a topic that answers from static message nodes → **classic answer**, 1
- a topic routed through generative answers over a knowledge source → **generative
  answer**, 2
- a topic or orchestration step that invokes a connector, custom tool or child agent →
  **agent action**, 5 per invocation
- any path where tenant graph grounding is enabled → **+10 on every turn that uses it**
- an agent flow → 13 per 100 actions; count the *actions inside the flow*, not the flow
  invocations, and check for loops
- a prompt/AI Builder tool → the matching text-and-generative-AI-tools tier
- document ingestion at runtime → content processing, 8 per page — multiply by realistic
  document length, this line item is routinely underestimated by an order of magnitude

Then build a weighted mix: estimate what share of turns lands on each path. Use topic
trigger breadth as the proxy when there is no telemetry — a broadly triggered generative
topic absorbs most traffic. State the weighting and mark it as the forecast's biggest
sensitivity.

**Credits per session** = Σ (turns on path *i* × credits per execution on path *i*)
+ any once-per-session costs (greeting, authentication, initial grounding).

## Step 4 — Forecast

Use `scripts/forecast.py` rather than doing this arithmetic by hand — hand-rolled credit
maths goes wrong quietly, and the script keeps the rate table, the feature stacking and
the zero-rating consistent across scenarios.

```bash
python3 ./scripts/forecast.py --schema > model.json   # annotated starting point
# edit model.json to describe the agent and its volumes (run from the skill folder)
python3 ./scripts/forecast.py model.json --json results.json
```

The config describes one or more **populations** (employee-facing and customer-facing
traffic behave differently and must be modelled separately) and, within each, the
conversation **paths** with their share of turns. Per-scenario overrides let a path turn
expensive only in the worst case, e.g. `"scenarios": {"worst": {"tenant_graph_grounding":
true}}`. `assets/example-config.json` is a worked example.

The script emits the scenario table, hotspots, per-feature breakdown, purchasing
comparison and sensitivity analysis as markdown — fold it into the report rather than
re-deriving it. It prints no currency figures unless the user supplied prices.

Compute three scenarios. Never give a single number.

- **Base** — p50 turns per session, stated adoption, current design
- **Peak** — p90 turns per session, launch-month adoption spike, retries included
- **Worst case** — p90 turns, full adoption, every optional expensive path taken
  (grounding on, reasoning on, longest documents)

For each: monthly credits = active users × sessions/user × credits/session, then split
into billable and zero-rated by the M365 Copilot licence share.

```
billable_credits = monthly_credits × (1 − m365_copilot_licensed_share)
```

Only apply the zero-rating to employee-facing traffic from users authenticated with
their Microsoft 365 Copilot identity. Customer-facing and unauthenticated traffic is
always billable — if the agent serves both, model them as two separate populations.

Present as a table: scenario × monthly credits × billable credits × cost (once the user
has supplied price per credit) × cost per session × cost per active user.

## Step 5 — Purchasing model

Compare, using the user's own quoted rates:

- **Pay-as-you-go** via an Azure subscription — no commitment, highest unit rate, right
  answer for pilots and spiky traffic
- **Prepaid Copilot Credit packs** — monthly commitment, unused credits do not roll over
  indefinitely; find the utilization break-even
- **Credit Commit Units**, 1-year prepurchase — lowest unit rate, requires a defensible
  annual forecast

Give the break-even volume between each pair, and a recommendation tied to the
confidence in the forecast, not just the cheapest unit rate. A cheap commitment on a
forecast built from assumed turns-per-session is a bad trade; say so.

Add a capacity headroom check: at what monthly volume does the chosen pack run out
mid-month, and what happens then.

## Step 6 — Cost hotspots and optimization levers

Rank the top five design elements by total credits consumed per month. For each, give
the lever, the estimated saving, and what it costs in quality or effort.

Standard levers, roughly in order of typical impact:

1. **Route employee-facing traffic through M365 Copilot identities.** Zero-rating turns
   the largest line item to zero. Frequently missed because the agent was designed
   unauthenticated for convenience.
2. **Scope the grounding.** Tenant graph grounding at 10 credits per turn is the single
   most expensive routine feature. If the answers come from three SharePoint sites, point
   at three SharePoint sites. Reserve tenant-wide grounding for topics that genuinely
   need cross-tenant reach, and gate it behind an explicit path rather than leaving it on
   for every turn.
3. **Deflect deterministic answers to classic answers.** Opening hours, policy lookups,
   status codes and menu navigation cost 1 instead of 2, and are more reliable. Look for
   generative topics whose knowledge source is a single short document.
4. **Collapse agent actions.** Five credits each, and orchestrators often call three
   tools where one composite tool would do. Chatty tool loops are a common 3–5x
   multiplier.
5. **Turn reasoning off where it is not earning its keep.** Enable it per topic, not
   globally.
6. **Cap agent flow actions.** Check loops and per-item processing — 13 credits per 100
   actions looks cheap until a flow iterates 5,000 rows per session.
7. **Trim the turn count.** Fewer clarifying round-trips is both cheaper and better UX;
   better trigger phrases and slot filling pay for themselves.

For each recommendation give: current credits/month → projected credits/month → % saved
→ implementation effort (S/M/L) → risk to answer quality.

## Step 7 — Deliverables

Produce two artifacts:

**1. Cost model report (HTML, self-contained).** Sections: assumptions and their
sources; rate table with verification date; per-topic and per-tool consumption breakdown;
three-scenario forecast; purchasing recommendation with break-evens; ranked hotspots;
optimization plan with projected savings; sensitivity analysis showing which two
assumptions move the answer most; open questions.

**2. Budget sign-off one-pager.** For the budget holder, not the architect: expected
monthly cost with a range, what drives it, what has already been optimized out, what
would change the number, and a single recommended purchasing action.

Charts help: a stacked bar of credits by feature type, and a line of forecast cost across
the three scenarios over twelve months.

## Guardrails

- Label every assumption inline. A forecast whose assumptions are invisible is worse than
  no forecast.
- Give ranges, never false precision. "€X–Y per month, driven mainly by turns per
  session" beats a single figure to the cent.
- If the design is not available and the user can only describe the agent, say clearly
  that the mix is estimated rather than derived, and widen the range.
- Re-baseline after two weeks of production telemetry. Say this in the report — the first
  forecast is a planning instrument, not a promise.
- External API, Azure and third-party connector costs are out of model. List them as
  named line items to be priced separately rather than silently omitting them.
- This skill reads and calculates. It never modifies the tenant, the solution or any
  licensing configuration.

## Related

Microsoft publishes an official form-based
[Copilot Studio agent usage estimator](https://microsoft.github.io/copilot-studio-estimator/).
Use it to sanity-check the arithmetic. This skill differs in that it derives the
interaction mix from the agent's own design and closes the loop back into redesign — point
users at both.
