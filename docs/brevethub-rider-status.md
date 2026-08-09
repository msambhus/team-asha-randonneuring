# BrevetHub rider status & registration flow

This document describes how a rider moves through event sign-up, registration,
withdrawal, and post-ride results on BrevetHub (`rp_event_signup`).

BrevetHub uses **lowercase** status strings in `rp_event_signup.status`. Team Asha
uses **uppercase** strings in `rider_ride.status` — same concepts, different casing.

Apply migration **`081_rename_going_to_registered.sql`** before deploying code that
uses `registered` / `REGISTERED` (replaces legacy `going` / `GOING`).

---

## Two fields on every sign-up row

| Field | Purpose |
|---|---|
| `status` | Rider progress on the event (intent, registered, withdrawal, result). |
| `registration_status` | Outcome of the registration wizard: `confirmed`, `waitlist`, `exception`, or `NULL`. |

Registration is **required to appear on the admin roster**. Interest alone (`interested`
with no `registration_status`) is calendar-only and does not count toward the roster.

---

## Status reference table

| Stored status | UI label | Phase | On admin roster? | Set by | Notes |
|---|---|---|---|---|---|
| `interested` | Interested | Pre-ride | Only if `registration_status` is set (waitlist/exception) | Rider (calendar) | Default before registration. Calendar: **Interested** + **Clear** only. |
| `maybe` | Maybe | Pre-ride | Legacy rows only | — | Cannot be newly created on BrevetHub calendar. |
| `registered` | Registered | Pre-ride / in-progress | Yes (when `registration_status = confirmed`) | Registration confirm | Set when registration evaluates to `confirmed`. Rider stays here until a post-ride result. |
| `withdraw` | Withdrawn | Pre-ride | Legacy / admin | Admin | Old immediate-withdraw path; registered riders use `withdrawal_requested` instead. |
| `withdrawal_requested` | Withdrawal Requested | Pre-ride | Yes | Rider (post-register) | **Red row** on admin roster. **Blocks event close** until approved or rejected. |
| `rejected` | Rejected | Terminal | No (`registration_status` cleared) | Admin | After rejecting a withdrawal request. Rider is off roster; may register again. |
| `finished` | Finished | Post-ride | Yes | Rider or admin | Available **1 minute after event start** (see below). |
| `dnf` | DNF | Post-ride | Yes | Rider or admin | Did not finish. |
| `dns` | DNS | Post-ride | Yes | Rider, admin, or auto on late withdraw | Did not start. Also set when a registered rider requests withdraw **after ride start + 1 min**. |
| `otl` | OTL | Post-ride | Yes | Rider or admin | Over time limit. |

Legacy value `going` is accepted by `RideStatus.normalize()` and mapped to `registered`
during a transition window; the migration rewrites existing rows.

---

## Registration status table

Set by `confirm_event_registration()` when the rider completes the registration wizard.

| `registration_status` | Effect on `status` | Roster badge | On roster? |
|---|---|---|---|
| `confirmed` | Sets `status = registered` | You're registered | Yes |
| `waitlist` | Keeps / sets `status = interested` | Waitlist | Yes |
| `exception` | Keeps / sets `status = interested` | Registered · review | Yes — **red row** |
| `NULL` | No registration | — | No (unless post-ride result row remains) |

---

## Lifecycle by phase

### 1. Before registration (calendar)

| Rider action | API | Result |
|---|---|---|
| Mark interested | `POST /calendar/<id>/signup` `{status: "interested"}` | `status = interested` |
| Clear interest | `DELETE /calendar/<id>/signup` | Row deleted (blocked if registered) |
| Register | Registration wizard | See registration table above |

### 2. After registration

| Rider action | When | Result |
|---|---|---|
| Request withdraw | Before ride start + 1 min | `status = withdrawal_requested` (still on roster) |
| Request withdraw | After ride start + 1 min | `status = dns` (immediate, no admin queue) |
| Set post-ride result | After ride start + 1 min, while `registered` or correcting a result | `finished` / `dnf` / `dns` / `otl` via `POST /calendar/<id>/result` |

Riders with `registered` keep that status until a post-ride result is recorded.

### 3. Withdrawal admin actions (roster page)

| Admin action | Result |
|---|---|
| **Approve withdrawal** | Sign-up row **deleted** — rider off roster, may re-register |
| **Reject withdrawal** | `status = rejected`, `registration_status = NULL` — off roster |

### 4. Event close (admin)

An in-progress event can close only when every **roster** rider has a final status:

| Final statuses (allow close) |
|---|
| `finished`, `dnf`, `dns`, `otl`, `withdraw` |

| Blockers (prevent close) |
|---|
| `registered` (awaiting result), `interested` on roster, `withdrawal_requested`, any other non-final status |

---

## Post-ride timing

Post-ride results unlock when:

```
now >= event.date + event.start_time + 1 minute
```

Default start time when missing: `06:00`. Implemented in `event_post_ride_open()` in
`brevethub/models.py`.

---

## Admin roster UI conventions

| Condition | UI treatment |
|---|---|
| `status = withdrawal_requested` | Red row (`row-alert`) |
| `registration_status = exception` | Red row (`row-alert`) |
| Filter pill **Registered** | Internal filter `?filter=registered` |
| Count columns | `registered_count` (SQL alias) |

---

## Team Asha calendar (same repo, different app)

Team Asha uses `REGISTERED` (uppercase) in `rider_ride.status`. Calendar intent is
**Interested + Clear** only on the upcoming brevets page. Legacy `GOING` / `SIGNED_UP`
values normalize to `REGISTERED`.

---

## Key code locations

| Concern | File |
|---|---|
| Enum & DB mutations | `brevethub/models.py` |
| Display labels | `brevethub/services/registration.py` — `status_display_label`, `progress_label` |
| Migration | `migrations/081_rename_going_to_registered.sql` |
| Calendar API | `brevethub/routes/calendar.py` |
| Admin roster | `brevethub/routes/admin.py`, `brevethub/templates/admin/event_roster.html` |

---

## Quick decision tree

```
Not signed up?
  → Interested (optional) → Register when open
       confirmed  → registered on roster
       waitlist   → interested + waitlist badge on roster
       exception  → interested + review badge on roster (red)

Registered, before start + 1 min?
  → Request withdraw → withdrawal_requested (admin approve/reject)

Registered, after start + 1 min?
  → Request withdraw → dns
  → Or set finished / dnf / dns / otl

All roster riders final?
  → Admin can close event
```
