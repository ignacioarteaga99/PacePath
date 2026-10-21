# Strava API review — draft answers for PacePath

Fill in the form from strava.com/settings/api → your app → "Request more athletes" (Developer Program).
These answers describe exactly what the app does today. Review them before submitting.

**App name:** PacePath
**Website:** https://pacepath.onrender.com
**Privacy policy:** https://pacepath.onrender.com/privacidad (English: https://pacepath.onrender.com/privacy?lang=en)
**Authorization Callback Domain:** pacepath.onrender.com
**Client ID:** 289395

## What does your app do?
PacePath designs running routes for the workout a runner has to do today (intervals, tempo, long run, hills, easy
run) on real city streets: e.g. interval reps on straight, flat stretches with no traffic lights or street
crossings, or long runs as loops that don't repeat streets. It works without Strava. Connecting Strava is optional
and only adds a personal layer for the athlete themselves: a heatmap of where they have run, their running
totals and history, a personal "Passport" of achievements (cities, countries, consistency), and routes that
prefer the streets they usually run.

## Which scopes and why?
- `read`: athlete profile (name, photo, city) shown only to the athlete.
- `activity:read_all`: the athlete's own runs (date, distance, moving time, elevation, average heart rate,
  workout type, summary polyline) to draw their personal heatmap and stats. Private runs are included only if the
  athlete grants this scope on the Strava screen.

## How is Strava data stored and used?
- Activities are stored only on the athlete's own device (browser IndexedDB). Our server does not store them.
- Tokens are kept in an encrypted, HTTP-only cookie (Fernet) that only our server can read.
- Some features send the athlete's runs to our server, which processes them in memory and returns the result
  without storing anything (usual streets, city/country of each run, the profile photo for the share card).
- Strava data is shown only to the athlete who owns it. There are no leaderboards, no public profiles and no
  sharing between users. Data is not sold, not used for advertising and not used to train AI models.
- Only runs are kept (`Run`, `TrailRun`); other activity types returned by the API are discarded immediately.

## Data deletion
"Disconnect" in the app calls `oauth/deauthorize` and deletes all Strava data and anything derived from it on the
device. Athletes can also revoke access from Strava → Settings → My Apps.

## Brand guidelines
- "Connect with Strava" button in Strava orange (#FC4C02), 48 px high, with the Strava logo.
  *TODO before submitting: replace it with the official "Connect with Strava" button pack (1.1, separate from
  the logo pack 1.2) from developers.strava.com/guidelines.*
- Official "Powered by Strava" logo (from Strava's API logo pack, unmodified; orange in light mode, white in
  dark mode) wherever Strava data is displayed (Mi ciudad, Profile).
- The app is not named after Strava and does not imply endorsement.

## Expected usage
- Private beta: ~30 runners in Santiago, Chile, over the next weeks.
- Goal for the first 6 months: up to 500 athletes.
- API usage per athlete: one paginated import of runs (100 per page), then incremental imports (`after=`) when the
  athlete opens the app; profile and stats when they open their profile. Well within default rate limits.

## Screenshots to attach
1. Route for today's session (intervals) — the core feature, no Strava data.
2. "Connect with Strava" screen with the beta notice.
3. Heatmap ("Mi ciudad") with "Powered by Strava".
4. Profile with totals and the Passport.
5. Privacy policy page.
