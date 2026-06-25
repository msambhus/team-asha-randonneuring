# Team Asha — iOS app (`mobile/`)

Native iOS app (Expo + React Native + TypeScript) whose reason to exist is **live
location tracking that keeps working with the phone screen off** — something the
web app's browser beacon can't do. It talks to the existing Flask backend's JSON
API (token auth added in PR #382).

**v1 features:** Google sign-in · the rider's upcoming rides · a ride's live map
(rider dots, ⌚/📱 source, stale fade) · opt-in **background** location sharing.
Calendar / seasons / weather are later milestones.

## Prerequisites (one-time, done by Mihir)
1. **Backend deployed with token auth** (PR #382 merged) and `GOOGLE_IOS_CLIENT_ID`
   set in Vercel.
2. **Google OAuth clients** (Google Cloud Console → Credentials). You need BOTH:
   - an **iOS** client (bundle id `org.teamasha.randonneuring`) — drives the native
     sign-in sheet. Put it in `app.json` `extra.googleIosClientId`, and set the
     `@react-native-google-signin/google-signin` plugin `iosUrlScheme` to its
     **reversed** form (`com.googleusercontent.apps.<...>`).
   - a **Web** client — `app.json` `extra.googleWebClientId`. This is required for
     the library to return an **ID token**, and that token's audience (`aud`) is
     the **web** client id. **Therefore set Vercel's `GOOGLE_IOS_CLIENT_ID` (the
     backend verification audience) to the WEB client id value** — that's the aud
     the backend will see. (The env var name is historical; it's just "the audience.")
3. **Apple Developer account** + an **EAS** project: `eas init` (fills
   `extra.eas.projectId`).

## Run it (dev build — NOT Expo Go; background location needs a dev build)
```bash
cd mobile
pnpm install              # or npm install
npx expo install --fix    # align native dep versions to the Expo SDK
eas build --profile development --platform ios   # build a dev client
# install the dev build on your iPhone, then:
npx expo start --dev-client
```

## Tests / typecheck (CI-friendly, no device)
```bash
cd mobile
pnpm test        # jest-expo unit tests (api client + beacon payload builders)
pnpm run lint    # tsc --noEmit typecheck
```

## On-device acceptance (the manual gate — the headline feature)
This is the real test and **cannot** be automated here:
1. Sign in with Google; open a ride from the list.
2. Tap **Share my location**, grant **"Always"** location permission.
3. **Lock the phone / background the app.** On another device (or the web map),
   confirm your dot keeps updating on that ride for several minutes screen-off.
4. Tap **Stop sharing** — updates stop.

## Layout
```
app/_layout.tsx      providers + auth gate (redirects to /login when no token)
app/login.tsx        Google sign-in
app/index.tsx        rides list (GET /api/live/rides)
app/ride/[id].tsx    live map (react-native-maps) + background share toggle
lib/api.ts           token store (secure-store) + apiFetch (Bearer, 401→logout)
lib/auth.ts          Google sign-in → POST /api/auth/google → app token
contexts/SessionContext.tsx   token lifecycle
hooks/                useRides, useLivePositions (TanStack Query, polling)
location/backgroundLocation.ts  TaskManager task → POST /api/live/beacon (screen-off)
```

## Notes / limits
- **Background cadence is OS-governed** (~best effort, not a hard 30–60s). If the
  club needs guarantees, `react-native-background-geolocation` (transistorsoft) is
  the upgrade path; Garmin LiveTrack already covers rock-solid screen-off.
- Map = `react-native-maps` (Apple Maps, no token). Mapbox (web parity) is a later swap.
- A token carries `rider_id` at sign-in; if you complete profile setup later,
  sign out/in again so the new token carries your `rider_id`.
- `mobile/` has its own Node toolchain; it doesn't touch the Flask backend.
