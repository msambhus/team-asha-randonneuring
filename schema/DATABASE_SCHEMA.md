# Team Asha Randonneuring - Database Schema

## Entity Relationship Diagram

```mermaid
erDiagram
    app_user ||--o| rider : "links to"
    rider ||--o| rider_profile : "has"
    rider ||--o{ rider_ride : "participates"
    rider ||--o{ custom_ride_plan : "creates"
    rider ||--o| strava_connection : "connects"
    rider ||--o{ strava_activity : "tracks"
    
    season ||--o{ ride : "contains"
    club ||--o{ ride : "organizes"
    ride_plan ||--o{ ride : "plans"
    ride_plan ||--o{ ride_plan_stop : "has stops"
    ride_plan ||--o{ custom_ride_plan : "base for"
    
    custom_ride_plan ||--o{ custom_ride_plan_stop : "has overrides"
    ride_plan_stop ||--o{ custom_ride_plan_stop : "can be overridden"
    
    ride ||--o{ rider_ride : "includes"
    
    app_user {
        int id PK
        varchar email UK
        varchar google_id UK
        boolean profile_completed
        int rider_id FK
        timestamp created_at
        timestamp last_login
    }
    
    rider {
        int id PK
        int rusa_id UK
        text first_name
        text last_name
    }
    
    rider_profile {
        int rider_id PK,FK
        text photo_filename
        text bio
        boolean pbp_2023_registered
        text pbp_2023_status
    }
    
    season {
        int id PK
        text name
        date start_date
        date end_date
        boolean is_current
    }
    
    club {
        int id PK
        text code UK
        text name
        text region
    }
    
    ride_plan {
        int id PK
        text name
        text slug UK
        numeric total_distance_miles
        int total_elevation_ft
        text rwgps_url
        text rwgps_url_team
        int distance_km
        numeric cutoff_hours
        text start_time
        numeric avg_moving_speed
        numeric avg_elapsed_speed
        int total_moving_time_min
        int total_elapsed_time_min
        int total_break_time_min
        numeric overall_ft_per_mile
        text rwgps_route_id
        timestamp created_at
    }
    
    ride_plan_stop {
        int id PK
        int ride_plan_id FK
        int stop_order
        text location
        text stop_type
        numeric distance_miles
        int elevation_gain
        int segment_time_min
        int stop_duration_min
        text stop_name
        text notes
        numeric seg_dist
        int ft_per_mi
        numeric avg_speed
        int cum_time_min
        int bookend_time_min
        int time_bank_min
        numeric difficulty_score
    }
    
    ride {
        int id PK
        int season_id FK
        int club_id FK
        text name
        text ride_type
        text date
        int distance_km
        int elevation_ft
        real distance_miles
        real ft_per_mile
        text rwgps_url
        text rusa_event_id
        int ride_plan_id FK
        text event_status
        text start_location
        text start_time
        real time_limit_hours
    }
    
    rider_ride {
        int id PK
        int rider_id FK
        int ride_id FK
        text status
        text finish_time
        timestamp signed_up_at
    }
    
    custom_ride_plan {
        int id PK
        int rider_id FK
        int base_plan_id FK
        text name
        text description
        boolean is_public
        numeric avg_moving_speed
        timestamp created_at
        timestamp updated_at
    }
    
    custom_ride_plan_stop {
        int id PK
        int custom_plan_id FK
        int base_stop_id FK
        int stop_order
        text location
        text stop_type
        numeric distance_miles
        int elevation_gain
        int segment_time_min
        int stop_duration_min
        text stop_name
        text notes
        boolean is_custom_stop
        boolean is_hidden
    }
    
    strava_connection {
        int rider_id PK_FK
        bigint strava_athlete_id
        text access_token
        text refresh_token
        bigint expires_at
        text scope
        timestamp connected_at
        timestamp last_sync_at
    }
    
    strava_activity {
        int id PK
        int rider_id FK
        bigint strava_activity_id
        text name
        text activity_type
        real distance
        int moving_time
        int elapsed_time
        real total_elevation_gain
        timestamp start_date
        real average_speed
        text strava_url
        timestamp fetched_at
    }
```

## Detailed Table Descriptions

### Authentication & User Management

#### `app_user`
Manages user authentication and links to rider profiles.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| email | varchar(255) | NOT NULL, UNIQUE | User's email from Google OAuth |
| google_id | varchar(255) | NOT NULL, UNIQUE | Google OAuth identifier |
| profile_completed | boolean | DEFAULT false | Whether profile setup is complete |
| rider_id | integer | FK → rider(id) | Links to rider profile |
| created_at | timestamp | DEFAULT CURRENT_TIMESTAMP | Account creation time |
| last_login | timestamp | DEFAULT CURRENT_TIMESTAMP | Last login timestamp |

**Foreign Keys:**
- `rider_id` → `rider(id)`

---

### Rider Information

#### `rider`
Core rider information tied to RUSA membership.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| rusa_id | integer | NOT NULL, UNIQUE | RUSA membership number |
| first_name | text | NOT NULL | Rider's first name |
| last_name | text | NOT NULL | Rider's last name |

#### `rider_profile`
Extended rider profile information.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| rider_id | integer | PK, FK → rider(id) | Primary key and foreign key |
| photo_filename | text | NULL | Profile photo filename |
| bio | text | NULL | Rider biography |
| pbp_2023_registered | boolean | DEFAULT false | Paris-Brest-Paris 2023 registration |
| pbp_2023_status | text | NULL | PBP 2023 status |

**Foreign Keys:**
- `rider_id` → `rider(id)` (CASCADE on delete)

#### `strava_connection`
Stores Strava OAuth connection and tokens for a rider.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| rider_id | integer | PK, FK → rider(id) | Primary key and foreign key to rider |
| strava_athlete_id | bigint | NOT NULL | Strava athlete ID |
| access_token | text | NOT NULL | OAuth access token (encrypted) |
| refresh_token | text | NOT NULL | OAuth refresh token (encrypted) |
| expires_at | bigint | NOT NULL | Token expiration timestamp |
| scope | text | NULL | OAuth scopes granted |
| connected_at | timestamp | DEFAULT CURRENT_TIMESTAMP | Initial connection timestamp |
| last_sync_at | timestamp | NULL | Last activity sync timestamp |

**Foreign Keys:**
- `rider_id` → `rider(id)` (CASCADE on delete)

**Note:** One-to-one relationship. One Strava connection per rider.

#### `strava_activity`
Cached Strava activities for fitness scoring and calendar display.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| rider_id | integer | NOT NULL, FK → rider(id) | Rider who performed activity |
| strava_activity_id | bigint | NOT NULL, UNIQUE | Strava's activity ID |
| name | text | NULL | Activity name |
| activity_type | text | NULL | Type (Ride, VirtualRide, etc.) |
| distance | real | NULL | Distance in meters |
| moving_time | integer | NULL | Moving time in seconds |
| elapsed_time | integer | NULL | Elapsed time in seconds |
| total_elevation_gain | real | NULL | Elevation gain in meters |
| start_date | timestamp | NULL | Activity start date/time |
| average_speed | real | NULL | Average speed in m/s |
| strava_url | text | NULL | Link to activity on Strava |
| fetched_at | timestamp | DEFAULT CURRENT_TIMESTAMP | When activity was cached |

**Foreign Keys:**
- `rider_id` → `rider(id)` (CASCADE on delete)

**Note:** Activities cached for 1 year rolling window. Used for fitness scoring and training calendar.

---

### Ride Planning & Management

#### `season`
Defines cycling seasons for organizing rides.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| name | text | NOT NULL | Season name (e.g., "2025-2026") |
| start_date | date | NULL | Season start date |
| end_date | date | NULL | Season end date |
| is_current | boolean | DEFAULT false | Active season flag |

#### `club`
Cycling clubs/organizations hosting rides.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| code | text | NOT NULL, UNIQUE | Short club code |
| name | text | NOT NULL | Full club name |
| region | text | NOT NULL | Geographic region |

#### `ride_plan`
Detailed ride route plans with timing and elevation data.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| name | text | NOT NULL | Ride plan name |
| slug | text | NOT NULL, UNIQUE | URL-friendly identifier |
| total_distance_miles | numeric | NULL | Total distance in miles |
| total_elevation_ft | integer | NULL | Total elevation gain in feet |
| rwgps_url | text | NULL | RideWithGPS route URL |
| rwgps_url_team | text | NULL | Team-specific RWGPS URL |
| distance_km | integer | NULL | Total distance in kilometers |
| cutoff_hours | numeric | NULL | Time limit in hours |
| start_time | text | DEFAULT '07:00' | Default start time |
| avg_moving_speed | numeric | NULL | Average moving speed (mph) |
| avg_elapsed_speed | numeric | NULL | Average elapsed speed (mph) |
| total_moving_time_min | integer | NULL | Total moving time in minutes |
| total_elapsed_time_min | integer | NULL | Total elapsed time in minutes |
| total_break_time_min | integer | NULL | Total break time in minutes |
| overall_ft_per_mile | numeric | NULL | Average feet per mile |
| rwgps_route_id | text | NULL | RWGPS route identifier |
| created_at | timestamp | DEFAULT now() | Creation timestamp |

#### `ride_plan_stop`
Individual stops/waypoints along a ride plan.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| ride_plan_id | integer | NOT NULL, FK → ride_plan(id) | Parent ride plan |
| stop_order | integer | NOT NULL | Sequential stop number |
| location | text | NOT NULL | Stop location name |
| stop_type | text | DEFAULT 'waypoint' | Type: waypoint/control/rest |
| distance_miles | numeric | NULL | Cumulative distance to stop |
| elevation_gain | integer | NULL | Elevation gain to this stop |
| segment_time_min | integer | NULL | Time for this segment (riding time, excludes stop) |
| stop_duration_min | integer | NULL | Break/rest duration at this stop (minutes) |
| stop_name | text | NULL | Break/rest stop name (e.g., "Lunch Break", "Coffee Stop") |
| notes | text | NULL | Additional notes |
| seg_dist | numeric | NULL | Segment distance |
| ft_per_mi | integer | NULL | Feet per mile for segment |
| avg_speed | numeric | NULL | Average speed for segment |
| cum_time_min | integer | NULL | Cumulative time |
| bookend_time_min | integer | NULL | Time buffer |
| time_bank_min | integer | NULL | Time bank remaining |
| difficulty_score | numeric | NULL | Segment difficulty rating |

**Foreign Keys:**
- `ride_plan_id` → `ride_plan(id)` (CASCADE on delete)

#### `ride`
All ride events (Team Asha and external RUSA events).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| season_id | integer | NOT NULL, FK → season(id) | Season this ride belongs to |
| club_id | integer | NOT NULL, FK → club(id) | Organizing club (TA=Team Asha, DBC/SFR/SCR/SRR=external) |
| name | text | NOT NULL | Ride/event name |
| ride_type | text | NULL | Type (brevet, populaire, PBP, etc.) |
| date | date | NOT NULL | Ride date (DATE type for proper date handling) |
| distance_km | integer | NOT NULL | Distance in kilometers |
| elevation_ft | integer | NULL | Total elevation gain |
| distance_miles | real | NULL | Distance in miles |
| ft_per_mile | real | NULL | Feet per mile ratio |
| rwgps_url | text | NULL | RideWithGPS URL |
| rusa_event_id | text | NULL | RUSA event identifier |
| ride_plan_id | integer | FK → ride_plan(id) | Associated ride plan (Team Asha rides) |
| event_status | text | DEFAULT 'UPCOMING' | Event status: UPCOMING/COMPLETED |
| start_location | text | NULL | Starting location/address |
| start_time | text | NULL | Event start time |
| time_limit_hours | real | NULL | Official time limit in hours |

**Foreign Keys:**
- `season_id` → `season(id)`
- `club_id` → `club(id)` (Required: use club_id to differentiate Team Asha vs external events)
- `ride_plan_id` → `ride_plan(id)`

**Note:** Team Asha rides have `club_id` pointing to 'TA' club. External events have `club_id` pointing to DBC, SFR, SCR, or SRR.

---

### Ride Participation

#### `rider_ride`
Tracks rider signups, participation and completion status (consolidated lifecycle).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| rider_id | integer | NOT NULL, FK → rider(id) | Participating rider |
| ride_id | integer | NOT NULL, FK → ride(id) | The ride |
| status | text | NOT NULL | Status: SIGNED_UP/FINISHED/DNF/DNS |
| finish_time | text | NULL | Completion time (set when FINISHED) |
| signed_up_at | timestamp | NULL | Signup timestamp (NULL for historical rides) |

**Foreign Keys:**
- `rider_id` → `rider(id)` (CASCADE on delete)
- `ride_id` → `ride(id)` (CASCADE on delete)

**Lifecycle States:**
- `SIGNED_UP`: Rider registered, ride hasn't occurred yet
- `FINISHED`: Rider completed the ride successfully
- `DNF`: Did Not Finish
- `DNS`: Did Not Start

---

### Custom Ride Plans

#### `custom_ride_plan`
User-customized versions of base ride plans with personalized pacing and timing.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| rider_id | integer | NOT NULL, FK → rider(id) | Owner of this custom plan |
| base_plan_id | integer | NOT NULL, FK → ride_plan(id) | Base plan being customized |
| name | text | NULL | Custom plan name |
| description | text | NULL | Custom plan description |
| is_public | boolean | DEFAULT FALSE | Whether other riders can view/copy this plan |
| avg_moving_speed | numeric | NULL | Custom average moving speed for recalculations |
| created_at | timestamp | DEFAULT CURRENT_TIMESTAMP | Creation timestamp |
| updated_at | timestamp | DEFAULT CURRENT_TIMESTAMP | Last update timestamp |

**Foreign Keys:**
- `rider_id` → `rider(id)` (CASCADE on delete)
- `base_plan_id` → `ride_plan(id)` (CASCADE on delete)

**Unique Constraints:**
- `UNIQUE(rider_id, base_plan_id)` - One custom plan per rider per base plan

#### `custom_ride_plan_stop`
Individual stop overrides within a custom ride plan. Implements a delta/override model where only changed values are stored.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | integer | PK, AUTO | Primary key |
| custom_plan_id | integer | NOT NULL, FK → custom_ride_plan(id) | Parent custom plan |
| base_stop_id | integer | FK → ride_plan_stop(id) | Base stop being overridden (NULL for custom stops) |
| stop_order | integer | NOT NULL | Sequential stop number |
| location | text | NOT NULL | Stop location name |
| stop_type | text | NOT NULL | Type: waypoint/control/rest/start/finish |
| distance_miles | numeric | NULL | Cumulative distance override |
| elevation_gain | integer | NULL | Elevation gain override |
| segment_time_min | integer | NULL | Custom segment time (NULL = inherit from base) |
| stop_duration_min | integer | NULL | Break duration override (NULL/0 = inherit, -1 = explicitly removed, >0 = custom) |
| stop_name | text | NULL | Break name override (NULL = inherit or cleared based on stop_duration_min) |
| notes | text | NULL | Custom notes override |
| is_custom_stop | boolean | DEFAULT FALSE | TRUE if this is a new stop not in base plan |
| is_hidden | boolean | DEFAULT FALSE | TRUE if base stop is hidden in this custom plan |

**Foreign Keys:**
- `custom_plan_id` → `custom_ride_plan(id)` (CASCADE on delete)
- `base_stop_id` → `ride_plan_stop(id)` (SET NULL on delete)

**Override/Inheritance Logic:**
- **Delta Model:** Only stores fields that differ from base plan
- **NULL values:** Means "inherit from base" (for most fields)
- **Special Sentinels:**
  - `stop_duration_min = -1`: Explicitly removed (don't inherit from base)
  - `stop_duration_min = NULL or 0`: Inherit from base
  - `stop_duration_min > 0`: Use custom value
- **Coupled Fields:** `stop_duration_min` and `stop_name` work together
  - When duration inherited → name also inherited
  - When duration removed (-1) → name also cleared
  - When duration custom (>0) → can optionally override name

---

## Key Relationships

### User Authentication Flow
```
app_user → rider → rider_profile
```
- User logs in via Google OAuth (`app_user`)
- Links to RUSA rider profile (`rider`)
- Extended profile information (`rider_profile`)

### Ride Organization
```
season → ride ← club
         ↓
    ride_plan → ride_plan_stop
```
- Rides belong to seasons
- Rides organized by clubs
- Rides can reference detailed ride plans
- Ride plans contain multiple stops

### Ride Participation
```
rider → rider_ride → ride
```
- Complete lifecycle in `rider_ride`: signup → participation → result
- Status progression: SIGNED_UP → FINISHED/DNF/DNS

### Custom Ride Plans
```
rider → custom_ride_plan → custom_ride_plan_stop
                ↓                      ↓
           base_plan_id           base_stop_id
                ↓                      ↓
           ride_plan          ride_plan_stop
```
- Riders can create custom versions of any ride plan
- Custom stops override or extend base stops
- Delta model: Only changed fields stored in custom_ride_plan_stop
- Inheritance: NULL values mean "use base plan value"
- Special handling for breaks: `-1` sentinel = explicitly removed

---

## Indexes & Constraints

### Unique Constraints
- `app_user.email` (UNIQUE)
- `app_user.google_id` (UNIQUE)
- `rider.rusa_id` (UNIQUE)
- `club.code` (UNIQUE)
- `ride_plan.slug` (UNIQUE)

### Referential Integrity
All foreign keys have proper CASCADE constraints on delete to maintain data integrity.

---

## Database Statistics

**Total Tables:** 11
**Total Foreign Key Relationships:** 13
**Authentication Tables:** 1
**Core Tables:** 5
**Junction Tables:** 1
**Reference Tables:** 2
**Custom Plan Tables:** 2
**Strava Tables:** 2

---

*Generated on: 2026-02-24*  
*Last updated: 2026-02-25 - Added stop_duration_min and stop_name columns to ride_plan_stop and custom_ride_plan_stop for break/rest stop management with delta inheritance model*
