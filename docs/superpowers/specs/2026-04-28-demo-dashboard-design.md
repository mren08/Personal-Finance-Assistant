# Demo Dashboard Design

## Goal

Add a `Try Demo (No signup required)` entry point on the landing page that opens the full real dashboard with sanitized fixed sample data and no account creation.

The demo should immediately show the product’s value without any uploads:

- insights
- recommendations
- category breakdown
- prefilled monthly plan
- subscriptions
- proactive chat

## Product Outcome

A first-time visitor should be able to click one button and land on a believable, fully populated dashboard that looks like an already active budgeting account.

The demo should feel real because it uses the existing dashboard logic, not a fake mock page.

## Scope

### In scope

- Landing-page `Try Demo (No signup required)` button
- Temporary demo session that opens the real dashboard
- Fixed sanitized sample data derived from real statement patterns
- 2–3 months of transactions
- Subscriptions, goal, monthly plan, chat history, and user decisions
- Small demo-mode indicator in the dashboard
- Demo-specific copy adjustments for section titles where needed

### Out of scope

- Shared persistent demo account across users
- Live generation from statement files at runtime
- Editable multi-tenant demo state
- Special dashboard calculation rules only for demo mode
- Separate fake preview page

## Core Flow

1. User lands on the logged-out homepage.
2. Under the login form, they click `Try Demo (No signup required)`.
3. App creates or resets a disposable demo user/profile in storage.
4. App sets session state as if the user had signed in.
5. App redirects to `/`.
6. The existing dashboard renders from demo-backed stored data.

## Demo Data Model

The demo data must be fixed and sanitized, not derived dynamically at request time from private raw statement files.

### Demo shape

- 2–3 months of realistic transactions
- realistic category spread
- recurring subscriptions
- one active savings goal
- prefilled monthly plan
- seeded user decisions & notes
- seeded chat history

### Prefilled financial profile

- Monthly income: `$4,000`
- Fixed expenses: `$2,200`
- Goal: `Save for Japan trip`

### Selected month

- Default selected month: `April 2026`

### Transaction profile

The transactions should be sanitized from real patterns and designed so the dashboard naturally produces strong output.

The April month should support:

- dining above recent average
- multiple recurring subscriptions
- visible goal pressure
- actionable recommendations

Categories should include realistic examples such as:

- Dining
- Groceries
- Travel
- Shopping
- Wellness
- Gas

## Demo Dashboard Experience

The demo must open directly into the standard dashboard, not a mock page.

### Header behavior

- Show a small `Demo mode` indicator near the signed-in header
- Demo should clearly look like a product walkthrough, but still feel like a real account

### Insight section

Rename the top section in demo mode to:

- `AI Insights for April`

Example demo insights:

- `Dining is 25% above your 3-month average`
- `You have 4 subscriptions totaling $78/month`
- `At current spending, you’ll miss your savings goal by $220`

These should come from seeded data plus existing insight logic where possible.

### Recommended actions

Place directly below insights.

Example actions:

- `Reduce dining by $60/month to reach goal on time`
- `Cancel 2 subscriptions to save $32/month`
- `Set weekly discretionary cap to $140`

### Category breakdown

Rename in demo mode to:

- `Where your money is going`

The existing donut chart should still render from real category totals.

### Monthly plan

Prefill:

- Income: `$4,000`
- Fixed expenses: `$2,200`
- Goal: `Save for Japan trip`

### Subscriptions

Seed visible recurring examples such as:

- Netflix — `$15.99`
- Spotify — `$9.99`
- Gym — `$60.00`

At least one subscription should visibly look like a cut candidate.

If the current data model does not support a dedicated `recommended to cancel` flag, the demo should surface that recommendation through the existing recommendation section and/or chat context instead of adding a new subscription schema just for demo mode.

### Chat

The chat must not appear empty.

Seed at least one assistant-led opening message based on the demo data.

It should proactively call out:

- dining pressure
- recurring charges
- savings goal gap
- one immediate next move

The purpose is to create an immediate “this feels intelligent” moment.

## Backend Design

### Demo seed helper

Add a dedicated demo seeding helper responsible for:

- creating or locating the demo user record
- resetting demo transactions/profile/chat/decisions to a known good state
- seeding the 2–3 months of fixed demo data

This helper should be deterministic so every new demo session starts from the same state.

### Storage behavior

Recommended behavior:

- use a reserved local demo identity such as `demo@local`
- on each demo entry, reset and reseed the demo data

This avoids accumulating mutated state between demo visits.

## Session behavior

The `/demo` route should:

- ensure demo data exists in storage
- set session user identity
- set demo-mode flag in session
- set selected month to April 2026
- redirect to `/`

Logout should clear the demo session the same way a normal session is cleared.

No special cleanup is required on logout if demo reseeding is deterministic on the next entry.

## UI Changes

### Landing page

Add a button under the login area:

- `Try Demo (No signup required)`

This should be visually secondary to sign-in but still clearly visible.

### Dashboard

In demo mode only:

- show `Demo mode`
- rename `Top 3 Insights This Month` to `AI Insights for April`
- rename `Category breakdown` to `Where your money is going`

All other dashboard sections should continue using the existing rendering path.

## Testing

Add coverage for:

- landing page shows demo button
- `/demo` redirects into the dashboard
- demo session loads dashboard without signup
- demo mode indicator is visible
- demo selected month defaults to April 2026
- monthly plan fields are prefilled
- insights and recommendations are present
- seeded subscriptions are present
- seeded messages are present
- logout exits the demo session cleanly

The tests should verify the real dashboard route output rather than a mocked preview page.

## Risks And Constraints

- Existing helper methods may assume a normal user lifecycle; demo reseeding must avoid corrupting real user data
- The demo should reuse real dashboard logic, but demo-only naming changes must not leak into normal signed-in behavior
- Fixed sample data should be believable without being traceable back to private raw statement details

## Non-Goals

This design does not include:

- public multi-user editable demo collaboration
- live demo data generation from uploaded files
- a separate marketing microsite
- new analytics/storage models only for demo mode
