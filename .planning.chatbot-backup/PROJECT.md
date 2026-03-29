# Team Asha Randonneuring Chatbot

## What This Is

An AI-powered chatbot embedded as a floating widget across the Team Asha randonneuring web application. It serves as a personalized cycling coach and randonneuring knowledge base for logged-in users, drawing on their Strava training data, brevet history, team ride plans, and general cycling/randonneuring expertise. Powered by OpenAI GPT-5.4.

## Core Value

Personalized, data-grounded cycling coaching and randonneuring information — answering "Am I ready for my next brevet?" with actual training data, not generic advice.

## Requirements

### Validated

- ✓ Flask app with blueprint-based routing — existing
- ✓ Google OAuth user authentication — existing
- ✓ Strava OAuth integration with activity sync — existing
- ✓ Rider profiles with career stats, SR/R-12 awards — existing
- ✓ Fitness scoring (0-100) with frequency/volume/intensity/recency — existing
- ✓ Eddington number calculation with multi-day ride splitting — existing
- ✓ Ride plans with control stops, distances, elevation — existing
- ✓ Upcoming brevet calendar with readiness assessment — existing
- ✓ OpenAI-powered coaching advice (openai_coach.py) — existing
- ✓ Brevet history with finish times across seasons — existing

### Active

- [ ] Floating chat widget accessible on every page
- [ ] Chat API endpoint for message exchange
- [ ] System prompt with cycling/randonneuring guardrails
- [ ] Personalized responses using logged-in user's Strava data (if connected)
- [ ] General cycling/randonneuring knowledge mode (no Strava)
- [ ] Team Asha context: upcoming brevets, ride plans, routes, team stats
- [ ] Randonneuring knowledge: rules, distances, cutoffs, SR/R-12 awards, PBP
- [ ] Off-topic query handling: polite redirect with cycling topic suggestion
- [ ] Persistent chat history stored in database
- [ ] Chat history UI: view/continue previous conversations
- [ ] Bike repair, maintenance, and gear guidance
- [ ] Nutrition advice for long-distance cycling
- [ ] Training plan suggestions based on upcoming rides and current fitness

### Out of Scope

- Voice input/output — text-only chat for v1
- Multi-user chat or forums — this is a personal coaching assistant
- Integration with other fitness platforms (Garmin, Wahoo) — Strava only
- Ride GPS tracking or live location — use Strava/RideWithGPS for that
- Medical advice — always defer to healthcare professionals
- Non-cycling topics — strict guardrails, redirect to cycling

## Context

This is a brownfield addition to an existing Flask app deployed on Vercel. The app already has:
- A rich data layer in `models.py` (2300+ lines) with functions for riders, activities, stats, ride plans
- An OpenAI coaching service (`services/openai_coach.py`) with a detailed cycling system prompt — this is the foundation for the chatbot's system prompt
- Fitness scoring (`services/fitness.py`) and Eddington calculation (`services/eddington.py`)
- Strava analysis (`services/strava_analysis.py`) for ride performance comparison
- Google OAuth and Strava OAuth authentication flows
- PostgreSQL database on Supabase with rider, strava_activity, ride, ride_plan tables
- In-memory caching via Flask-Caching (SimpleCache for Vercel serverless)

The existing `openai_coach.py` SYSTEM_PROMPT (150+ lines) is an excellent starting point — it covers brevet cutoff times, nutrition guidance, training philosophy, fitness interpretation, and coaching tone. The chatbot system prompt should extend this with:
- General randonneuring knowledge (rules, ACP/RUSA regulations, SR/R-12 definitions)
- Bike maintenance and repair guidance
- Route/ride plan discussion capabilities
- Guardrails against off-topic queries

## Constraints

- **AI Model**: OpenAI GPT-5.4 (user specified)
- **Deployment**: Vercel serverless — each request is stateless, no persistent processes
- **Database**: PostgreSQL on Supabase — chat history must be stored here
- **Auth**: Requires Google OAuth login (any logged-in user, not just riders with profiles)
- **Cost**: Token usage should be monitored — consider message length limits and conversation depth
- **Privacy**: Strava data is personal — only show a user's own data, respect `strava_data_private` flag
- **Latency**: Streaming responses preferred for good UX on longer answers

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Floating widget on all pages | Maximum accessibility, doesn't require navigation | — Pending |
| OpenAI GPT-5.4 | User preference, existing OpenAI integration in codebase | — Pending |
| Persistent chat history in DB | User wants to continue conversations across sessions | — Pending |
| Personalized + general modes | Serves both Strava-connected and non-connected users | — Pending |
| Extend existing coaching system prompt | Reuse proven cycling coaching context from openai_coach.py | — Pending |

---
*Last updated: 2026-03-14 after initialization*
