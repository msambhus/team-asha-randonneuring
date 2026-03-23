# Feature Research

**Domain:** Wind forecast integration for cycling / randonneuring ride planning apps
**Researched:** 2026-03-23
**Confidence:** MEDIUM (ecosystem surveyed via web; no authoritative spec exists for this niche domain)

## Feature Landscape

### Table Stakes (Users Expect These)

Features a wind-aware cycling tool must have or it feels broken / incomplete to riders.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Headwind / tailwind classification per route segment | Every wind cycling tool (myWindsock, Headwind, MyRacewind, windgpx) shows this; it's the core value proposition | MEDIUM | Requires bearing math + wind direction → component projection. Already partially implemented in `services/weather.py` |
| Color-coded wind cells / route sections | Users scan, not read; color is the primary signal in every competing tool | LOW | Red = headwind, Green = tailwind, Blue = crosswind. PROJECT.md already specifies this scheme. Inline styles needed since intensity is dynamic |
| Wind speed displayed as text value | Riders want exact numbers to calibrate effort and clothing decisions | LOW | Must accompany color — color alone is insufficient for planning |
| Wind intensity scaling (color + font size) | Stronger wind must be visually louder — every heatmap tool does this | LOW | Opacity or saturation scales with km/h. PROJECT.md specifies font size + shading both scale |
| Wind data at each control / stop | Brevets are planned stop-by-stop; riders make decisions at controls | HIGH | Requires stop-to-coordinate interpolation via RWGPS track points — the hardest table-stakes item |
| Forecast wind for upcoming events | Riders check conditions 3–7 days out before committing to a start strategy | MEDIUM | Open-Meteo forecast, 1-hour cache. PROJECT.md specifies 3–4 week window |
| Heavy wind warning / alert banner | Riders need a quick "is this brevet going to be brutal?" answer without reading every cell | LOW | Threshold detection (>30 km/h max or >15 km/h avg headwind) feeds a banner on the brevets page |
| Historical / actual wind for completed rides | Post-ride analysis is core to Strava-integrated tools; riders want to know if their slow time was wind-caused | MEDIUM | Requires Open-Meteo archive API fetch + persistence. Only reliable for recent years |

### Differentiators (Competitive Advantage)

Features that go beyond generic cycling weather tools and serve the specific randonneuring context.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Per-stop wind column in structured ride plan table | No generic tool shows wind in a brevet control sheet format; riders get wind and timing in one view | HIGH | The integration point between the existing ride plan UI and wind data. This is the core differentiator of this milestone |
| Custom plan wind integration (rider-overridden stops) | Riders who adjust timing or add stops get personalized wind context, not just the base plan | MEDIUM | Requires merging base stop wind data with custom plan stop list |
| Wind persistence in DB (no repeated archive calls) | Historical wind is slow to fetch; persisting it means the second view is instant | MEDIUM | `ride_wind_data` table. Prevents hammering the archive API and provides consistent data |
| Consistent color scheme across forecast and historical views | Riders can directly compare "what I expected" vs "what actually happened" | LOW | Green/Red/Blue scheme applied identically to both forecast columns and historical columns |
| Clickable ride headers linking to detail pages | Turns the seasons summary from a read-only table into a navigation hub for wind-enhanced detail views | LOW | 2025/2026 season only; older seasons have no wind data |
| Crosswind classification with 45-degree threshold | Crosswind is the most physically dangerous condition; calling it out explicitly is safety-relevant | LOW | `|headwind| > |crosswind|` → head/tailwind classification; else → crosswind. PROJECT.md has decided this |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Real-time wind updates during a ride | Riders would love live conditions while riding | Requires push infrastructure, mobile-first UI redesign, and adds latency/complexity with minimal planning value (you're already riding) | Pre-ride forecast is sufficient; riders can check a weather app if conditions change mid-ride |
| Wind integration in the chat agent | Chat is a natural interface for "will it be windy Saturday?" | Significant scope expansion; agent needs to understand routes, controls, and timing context to give useful answers — easy to get wrong and erode trust | Phase this as a follow-on milestone once the data model is proven |
| Precipitation and temperature columns | Riders ask "can you add rain too?" | Existing weather button already covers this; duplicating it in the table creates visual noise and dilutes the wind-focus value proposition | Point riders to the existing per-brevet weather link button |
| Wind data for pre-2025 rides | Completeness instinct — "show all historical rides" | Open-Meteo archive is reliable for recent years but accuracy degrades; fetching old data would require validating coverage and raises questions about what to do with missing data | Clearly scope to 2025/2026 seasons; document the boundary |
| Animated wind arrows on a map | Looks impressive; myWindsock does this | Requires a mapping library (Leaflet or Mapbox), a JS build step, and significant UI work — all incompatible with the Flask + Jinja2 + no-JS-framework constraint | Color-coded table cells in a structured plan convey the same decision-relevant information in the existing stack |
| User-configurable wind thresholds | Power users want to set their own "heavy wind" level | Adds settings UI, per-user preferences storage, and logic branches throughout; the 30 km/h / 15 km/h thresholds are well-calibrated for the domain | The fixed thresholds match community norms (75% of cyclists avoid >30 km/h winds per myWindsock data) |

## Feature Dependencies

```
[Stop-to-coordinate interpolation (RWGPS track points)]
    └──requires──> [Wind columns in base ride plan]
                       └──requires──> [Wind columns in custom ride plan]

[Open-Meteo forecast fetch]
    └──requires──> [Wind columns in base ride plan]
    └──requires──> [Heavy wind warning banner]

[Open-Meteo archive fetch]
    └──requires──> [Historical wind for completed rides]
    └──requires──> [Wind persistence in DB]

[Wind type classification (head/tail/cross + color)]
    └──requires──> [All visual wind columns] (both forecast and historical)

[Wind persistence in DB]
    └──enhances──> [Historical wind for completed rides] (makes repeat views fast)

[Clickable ride headers]
    └──requires──> [Historical wind columns exist on detail page]
```

### Dependency Notes

- **Stop-to-coordinate interpolation requires track point fetching:** Ride plan stops don't store lat/lng — they must be resolved against RWGPS track points using cumulative distance. This must be built before any wind column can be rendered.
- **Wind type classification underlies everything visual:** The color scheme (green/red/blue), intensity scaling, and warning thresholds all depend on classifying each stop's wind as headwind, tailwind, or crosswind. This is a prerequisite for every other feature.
- **Historical fetch requires the archive API:** Different endpoint, different parameters (`start_date`/`end_date`), same response shape as forecast. Must be validated before building the historical columns.
- **Custom plan wind depends on base plan wind:** Custom plans merge base stops with overrides; wind data flows from the base stop interpolation and must be re-resolved for any added stops.
- **Clickable headers depend on detail pages having wind:** Adding links before the destination has wind data would be confusing; headers should only link after the wind column feature is complete.

## MVP Definition

### Launch With (v1)

Minimum needed to deliver the core value: "see wind at each control before and after a brevet."

- [ ] Stop-to-coordinate interpolation via RWGPS track points — without this, no per-stop wind is possible
- [ ] Wind type classification with crosswind component (sine projection) — prerequisite for all visual display
- [ ] Wind columns in base ride plan (color-coded, speed text, intensity scaling) — the primary planning surface
- [ ] Heavy wind warning banner on upcoming brevets page — the quick-scan safety signal
- [ ] Historical wind for completed 2026 rides in Strava analysis — post-ride analysis closes the loop

### Add After Validation (v1.x)

- [ ] Wind columns in custom ride plan — add once base plan wind is confirmed correct; custom plans are less frequently viewed
- [ ] Wind persistence in DB — add when archive API latency becomes a friction point (first ride view will be slow without this)
- [ ] Clickable 2025/2026 season ride headers — add once detail pages have wind data to show

### Future Consideration (v2+)

- [ ] Wind in chat agent — defer until data model is stable and chat agent has route/timing context understanding
- [ ] Wind data for 2025 season (historical only) — defer; validate 2026 coverage first, then backfill if warranted

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Stop-to-coordinate interpolation | HIGH | HIGH | P1 |
| Wind type classification + color scheme | HIGH | MEDIUM | P1 |
| Wind columns in base ride plan | HIGH | MEDIUM | P1 |
| Heavy wind warning banner | HIGH | LOW | P1 |
| Historical wind for completed 2026 rides | HIGH | MEDIUM | P1 |
| Wind persistence in DB | MEDIUM | MEDIUM | P2 |
| Wind columns in custom ride plan | MEDIUM | LOW (after base) | P2 |
| Clickable 2025/2026 ride headers | LOW | LOW | P2 |
| Wind in chat agent | HIGH | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | myWindsock | Headwind App | Epic Ride Weather | Our Approach |
|---------|------------|--------------|-------------------|--------------|
| Wind color coding | Color-coded lines on map (head/tail) | Route color changes by wind direction | Wind vectors on map | Color-coded table cells (green/red/blue) in existing ride plan UI — no map required |
| Wind intensity scaling | Darker = stronger (visual weight) | Bright pink = strong headwind | Minute-by-minute forecast | Cell shading opacity + font size scale with wind speed |
| Historical wind analysis | Yes — Strava segment weather history | Yes — past rides with difficulty rating | No | Yes — Open-Meteo archive for completed 2026 rides |
| Per-point granularity | Yes — tap route point for data | No — start-of-ride location only | Minute-by-minute along route | Per-control granularity (matches randonneuring decision points) |
| Wind warning / threshold | 75th percentile thresholds in blog post | Difficulty score 1–5 | Not mentioned | Binary heavy-wind banner (>30 km/h max or >15 km/h avg headwind) |
| Crosswind | Cross-wind warn setting (user-configured) | Orange = crosswind | Not mentioned | Classified + blue-coded; 45-degree projection threshold |
| Brevet / control sheet format | Not applicable (generic cycling) | Not applicable | Not applicable | Native — wind data lives inside the existing control table |

## Sources

- [myWindsock — Cycling Weather Forecast](https://mywindsock.com/plot/) — wind visualization, per-point data, crosswind settings, Strava integration
- [Headwind App](https://headwindapp.com/) — route color coding, difficulty rating, historical and predictive modes
- [Epic Ride Weather](https://www.epicrideweather.com/) — wind vectors, minute-by-minute route forecast
- [MyRacewind / Tractebel](https://digital.tractebel-engie.com/solutions/myracewind/) — red/blue headwind/tailwind color convention (pro racing context)
- [GPX Wind Analyzer (windgpx)](https://windgpx.netlify.app/) — historical weather + GPX overlay, blue-to-red color scale
- [road.cc — Headwind App Review](https://road.cc/content/tech-news/free-headwind-app-provides-visualisation-wind-conditions-273403) — color convention details (pink = strong headwind, orange = crosswind)
- [myWindsock — 75th percentile wind data](https://mywindsock.com/page/coffee-shop-chat/25-of-riders-dont-ride-above-this-windspeed/) — community wind threshold norms supporting the 30 km/h warning level
- [RoadBikeRider — How much wind is too much](https://www.roadbikerider.com/too-much-wind-cycling/) — wind speed danger thresholds for cycling

---
*Feature research for: wind forecast integration in cycling / randonneuring ride planning*
*Researched: 2026-03-23*
