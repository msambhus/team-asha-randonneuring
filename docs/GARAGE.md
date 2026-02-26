# 🚲 Garage — Feature Idea Board

**Purpose:** This is a collaborative space to propose and discuss new features for Team Asha Randonneuring. Add your ideas freely, focus on the "what" and "why," and let the team figure out the "how" later.

---

## How to Contribute

1. Add your idea under the appropriate section below
2. Use a descriptive title and brief explanation
3. Keep it simple — no technical details or implementation plans
4. Tag ideas with status labels (see legend at bottom)
5. Use blockquotes (>) for notes, questions, or considerations

---

## 🎯 Feature Ideas

### Personal Portal & Profile

**Vision:** Transform the rider profile into a comprehensive personal portal with multiple sub-pages for tracking everything related to randonneuring and biking.

#### Profile Enhancement
- **Strava Integration** `[NEW]`
  - Display connected Strava account information
  - Show Strava ride calendar with sync status
  - Include key metrics currently shown on profile page
  
- **Unified Rider Information** `[NEW]`
  - Consolidate all rider data from the current rider page (e.g., `/rider/14832`)
  - Create a single source of truth for rider stats and history

---

### Gear Tracker

**Vision:** A comprehensive system to track cycling gear, monitor usage, plan maintenance, and facilitate gear sharing within the community.

#### Core Features

##### Gear Inventory Management `[NEW]`
- Add and categorize all cycling equipment
- Record purchase date (or "used since" date) and cost
- Track vendor information
- Upload photos and attach receipts or warranty documents
- Add star ratings (1-5) and personal notes/reviews for each item
- Mark items as active, retired, or lost

##### Lending & Borrowing System `[NEW]`
- **For Lenders:**
  - Track items loaned to other riders
  - Record who borrowed what and when
  - Set expected return dates
  - View borrowing history
  
- **For Borrowers:**
  - See what items you've borrowed and from whom
  - Track when items were borrowed and returned
  - Add feedback after using borrowed gear

##### Gear Categories `[NEW]`
- **Bikes** (complete bikes with frame details)
- **Components** (groupset, wheels, tires, saddle, handlebars, pedals, chain, cassette)
- **Electronics** (bike computers, lights, power meters, heart rate monitors)
- **Clothing** (jerseys, bibs, jackets, gloves, shoes)
- **Nutrition** (bars, gels, supplements, bottles)
- **Accessories** (bags, pumps, tools, locks, spare parts)

##### Mileage & Usage Tracking `[IN DISCUSSION]`
> **Note:** Strava already tracks bike-level mileage via their API. Consider if component-level tracking adds enough value.

- Sync bike mileage from Strava automatically
- Track component-level mileage separately (chains, tires, cassettes)
- Set expected lifespan thresholds (e.g., replace chain every 3,000 km)
- Get alerts when gear reaches usage limits

##### Maintenance Reminders `[NEW]`
- Schedule regular maintenance tasks (chain cleaning, tire pressure checks, etc.)
- Track maintenance history for each item
- Set custom reminder intervals (time-based or mileage-based)
- Mark tasks as complete with notes and costs

##### Multi-Bike Support `[NEW]`
- Manage multiple bikes with separate component tracking
- Assign names/nicknames to bikes (e.g., "The Tank", "Speed Machine")
- Track which bike is used for which type of riding

##### Retirement & Archive `[NEW]`
- Mark gear as retired when replaced
- View historical gear timeline
- Compare performance/longevity across similar items
- Export gear history reports

#### Why This Matters

- **Personal:** Budget for gear replacement and prevent unexpected failures on long rides
- **Financial:** Track total cost of cycling over time
- **Community:** Share and borrow gear, build trust within the team
- **Knowledge:** Collect real-world gear longevity data and reviews

---

### Ride Checklist System

**Idea:** A customizable checklist system to help riders prepare for brevets and long rides.

#### Core Features `[NEW]`
- **Personalized Checklists**
  - Create a master checklist template (one per user)
  - Make deep clones for specific rides
  - Keep all checklists private by default
  
- **Sharing & Templates** `[NEW]`
  - Share checklist templates with other riders via invitation
  - Privacy controls — checklists are private unless explicitly shared

- **Automatic Generation** `[NEW]`
  - Option to create a checklist for upcoming brevet
  - Pre-populate with items from the user's master template

- **Tracking Columns** `[NEW]`
  - **Packing Status** (checkbox): Did I pack this?
  - **Pre-ride Status** (checkbox): Is it ready to go?
  - **Where to Carry** (text field): On bike, drop bag, car, etc.

- **Checklist Comparison** `[NEW]`
  - View past checklists from previous rides
  - Compare current checklist against an older one
  - Learn from past experiences

#### Suggested Checklist Categories

Users should be able to customize these, but here are starter suggestions:

- **Bike Prep**
- **Electronics**
  - Bike-related devices
  - Non-bike devices
  - Pre-ride prep tasks
  - Chargers for base/home
  
- **Bike Spares & Tools**
  - Tools carried on bike
  - Tools in drop bag
  
- **Bike Assembly Tools**
  
- **Lighting Devices**
  
- **Medical Kit & Personal Care**
  
- **Clothing**
  - On-bike clothing
  - Off-bike clothing
  
- **Nutrition**
  
- **Vehicle/Car Items**

---

## 💡 More Ideas?

_Add new sections below as needed. Keep it organized and easy to scan!_

---

## 📋 Idea Status Legend

- `[NEW]` — Fresh idea, needs discussion
- `[IN DISCUSSION]` — Being evaluated by the team
- `[APPROVED]` — Green-lit for development
- `[PARKING LOT]` — Good idea, but not now
- `[TBD]` — To be decided, needs more research or clarification 
