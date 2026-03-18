# Team Asha Randonneuring — Personality-Driven Coaching

## What This Is

An AI-powered coaching platform for Team Asha's randonneuring group (15-40 riders). Enhances the existing chatbot with deep personality profiling of riders and coaches, extracted from WhatsApp group chats and personal blogs. Coaches get AI personas that mirror their real communication styles (Venki's tongue-in-cheek wisdom, Shriram's bike snobbery). Riders get responses tuned to how they communicate. An admin interface manages personality traits, gear preferences, coaching guardrails, and knowledge base expansion — with Braintrust evals validating that the chatbot respects the rules.

## Core Value

Coaching that feels like it comes from a real teammate who knows you — matching each rider's communication style and each coach's authentic personality, grounded in actual conversation data.

## Requirements

### Validated

<!-- Shipped and confirmed valuable from v1.0 milestone -->

- ✓ Flask app with blueprint-based routing — existing
- ✓ Google OAuth user authentication — existing
- ✓ Strava OAuth integration with activity sync — existing
- ✓ Rider profiles with career stats, SR/R-12 awards — existing
- ✓ Fitness scoring (0-100) with frequency/volume/intensity/recency — existing
- ✓ Floating chat widget accessible on every page — existing
- ✓ Agentic chat loop with intent classification, tool use, streaming SSE — existing
- ✓ Personalized responses using Strava data and brevet history — existing
- ✓ WhatsApp knowledge base with pgvector RAG — existing
- ✓ Persistent chat history with conversation management — existing
- ✓ Braintrust observability with spans and token tracking — existing
- ✓ Admin panel for ride management, Strava status, route generation — existing
- ✓ Hardcoded coach personas (Shriram for bikes, Venki for general) — existing
- ✓ Off-topic query redirection with cycling guardrails — existing

### Active

<!-- Current scope — personality-driven coaching milestone -->

- [ ] Personality trait extraction from WhatsApp exported chat logs per individual
- [ ] Personality trait extraction from blog posts (Mihir's WordPress, Venki's Google Drive doc)
- [ ] Personality profiles stored in database with communication style, tone patterns, humor type
- [ ] Admin page to view and edit personality traits for each team member
- [ ] Admin page to capture gear preferences per rider (bike, wheels, lights, bags, accessories, kit)
- [ ] Admin page to track preference patterns (premium vs value orientation)
- [ ] Admin page for coach assignment and configuration (who coaches what topics)
- [ ] Admin page for coaching tone settings per coach persona
- [ ] Admin page for topic guardrails (what topics each coach can/cannot answer)
- [ ] Coaching guardrails stored as structured config, not hardcoded in prompts
- [ ] Braintrust eval suite validating chatbot respects coaching guardrails
- [ ] Knowledge base expansion: crawl and embed content from resources spreadsheet links
- [ ] Knowledge base embeddings from external cycling/randonneuring sites
- [ ] Chatbot uses personality traits to match response tone to each rider (future phase)
- [ ] Coach personas dynamically generated from personality profile data (future phase)

### Out of Scope

- Voice input/output — text-only
- Multi-user chat or forums — personal coaching assistant
- Integration with Garmin/Wahoo — Strava only
- Medical advice — defer to healthcare professionals
- Real-time WhatsApp integration — uses exported chat files only
- Automated blog scraping on schedule — one-time extraction, manually refreshed
- Building a custom LLM — uses OpenAI with persona prompts
- Mobile app — web-first

## Context

This is the second milestone for an existing Flask app deployed on Vercel. The v1.0 milestone built:
- Full agentic chatbot with intent classification, tool use, and streaming SSE
- WhatsApp RAG pipeline (parse → chunk → embed → retrieve)
- Braintrust observability and eval framework
- Strava integration with fitness scoring
- Admin panel for ride management

The chatbot currently has **hardcoded** coach personas in `services/openai_coach.py`:
- **Coach Shriram**: Routed to when message contains bike-specific keywords (tire, derailleur, frame fit, etc.)
- **Coach Venki**: Default for everything else
- Personality described in static SYSTEM_PROMPT text

This milestone replaces that with **data-driven** personality profiles extracted from actual WhatsApp conversations and blog posts, managed through an admin interface.

Key personality examples from the user:
- **Venki**: Guide figure, can be serious about right vs wrong, fun-loving, tongue-in-cheek, sarcastic
- **Shriram**: Direct, loves bikes and accessories, subtly sells people on buying more/better gear, bike snob, recognizes people by their bikes before their names
- Other team members use mind games, fun/sarcastic tones — all should be captured

### Three User Roles
1. **Riders (athletes)**: Ask questions, get personalized coaching, view their profiles
2. **Coaches/mentors**: Review rider profiles, fine-tune AI behavior
3. **Admin (Mihir)**: Manage everything — personality data, coach assignments, knowledge base, guardrails

### External Data Sources
- WhatsApp group chat exports (.txt files)
- Mihir's blog: https://unexpectedathlete.wordpress.com/2023/09/06/a-bucket-list-item-checked-off-pbp2023-done-and-dusted/
- Venki's blog: Google Drive PDF
- Resources spreadsheet: https://docs.google.com/spreadsheets/d/1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU/edit?gid=856968589#gid=856968589

## Constraints

- **AI Model**: OpenAI GPT-4o-mini (current) — personality extraction may need GPT-4o for quality
- **Deployment**: Vercel serverless — stateless requests, no background jobs
- **Database**: PostgreSQL on Supabase with pgvector for embeddings
- **Existing patterns**: Must follow Flask blueprint structure, Jinja2 templates, Tailwind CSS
- **Privacy**: Personality traits derived from group chats are semi-public within team context
- **Cost**: Embedding external resources will have one-time OpenAI cost — monitor token usage
- **Admin auth**: Existing password-based admin auth (ADMIN_PASSWORD env var)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Admin page defines guardrails, Braintrust evals validate them | Complementary layers — UI for config, evals for verification | — Pending |
| Personality extraction from WhatsApp + blogs | Real conversation data captures authentic communication styles | — Pending |
| Store personality traits in database, not hardcoded prompts | Dynamic, admin-editable, can evolve as more data is analyzed | — Pending |
| Include resource crawling in scope but prioritize personality/admin first | Knowledge expansion is valuable but personality is the core differentiator | — Pending |
| Existing tech stack (Flask, PostgreSQL, OpenAI, Tailwind) | No reason to change — brownfield enhancement | ✓ Good |

---
*Last updated: 2026-03-17 after milestone 2 initialization*
