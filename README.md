# Overspending Coach

This app can:

1. Create user accounts with private saved history
2. Parse credit card CSV statements and store transactions per user
3. Detect recurring subscriptions from saved history
4. Keep a persistent accountability chat tied to each account
5. Let users add missing spend in plain language such as cash, Zelle, or split bills
6. Save keep-or-cancel subscription decisions from chat
7. Preserve the existing one-off analyzer endpoint for budgeting recommendations

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5055`.

On first run, the app creates a local SQLite database file named `budget_app.db` unless you override it with `APP_DB_PATH`.

## Backend Agent Setup

The backend agent can use the OpenAI Responses API for coaching and structured actions.

Set:

```bash
export OPENAI_API_KEY=your-key-here
```

If `OPENAI_API_KEY` is not set, the app falls back to deterministic coaching so the dashboard and tests still work locally.

The logged-in dashboard now supports:

- monthly income
- fixed expenses
- budgeting goal
- leftover-money summaries
- agent notes
- persistent chat tied to saved account data

## Product Direction

This version is built as an overspending coach rather than a generic budgeting dashboard. The main product loop is:

1. Sign up or sign in
2. Upload a statement to build saved history
3. Review recurring subscriptions and recent transactions
4. Use persistent chat to add missing transactions or mark subscriptions to keep/cancel

The chat is intentionally blunt. It is meant to act like an accountability partner, not a neutral assistant.
The monthly plan form lets users save monthly income and fixed expenses so the app can compute what is actually left this month.

## Password Reset

From the homepage, the `Forgot password?` link asks for an email address only.

If that account exists, the app creates a short-lived reset link and sends it through the current mailer.

When no real provider is configured, the app uses a logging mailer fallback, which writes the reset link to the app logs instead of sending a live email.

## Deploy on Render

1. Push the project to GitHub.
2. In Render, choose `New > Web Service` and connect this repository.
3. Enter these settings manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --bind 0.0.0.0:$PORT app:app`
4. Wait for the deploy to finish, then open the Render URL.

If you want to deploy with `render.yaml` instead, use Render's `New > Blueprint` flow.

### Demo Privacy Note

This deployment is for coursework/demo use. It now includes basic authentication and per-user data storage, but it is still not production-safe for sensitive financial data because it does not yet include hardened auth flows, encryption-at-rest, or production-grade security controls.

### Custom Domain

Once deployed, you can attach your own domain in Render:

1. Buy a domain from a registrar such as Namecheap, Squarespace Domains, or GoDaddy
2. In Render, open your web service and choose `Settings` -> `Custom Domains`
3. Add your domain
4. Copy the DNS records Render gives you into your domain registrar
5. Wait for DNS to propagate, then your budgeting app will live on your own website URL

## Turning It Into An App

Fastest path:

1. Deploy the backend/web app first
2. Use the hosted web app with mobile-friendly UI for user testing
3. If needed later, wrap it in an iPhone shell using SwiftUI, Capacitor, or a simple WebView

For your final project timeline, hosted web app first is the strongest move. It gets real users in quickly and keeps the architecture simple.

## CSV Format

The upload expects a CSV with these columns:

- `Transaction Date`
- `Description`
- `Category`
- `Amount`

The app treats negative amounts as expenses, ignores non-expense rows (like payments/credits), and uses the `Category` value directly.

## Receipt Uploads

Signed-in users can upload one or more receipt photos/PDFs from the dashboard.

Supported receipt formats:

- JPG
- JPEG
- PNG
- WEBP
- HEIC
- PDF

PDF receipts use page 1 only.

For each receipt, the app extracts:
- merchant
- date
- total
- category

The app shows a review card before saving anything into spending history. Each approved receipt writes one top-level transaction into the normal ledger. If category confidence is low, the user must choose a category manually before approval.

## Analyzer Compatibility

The original analyzer route still supports richer budgeting inputs:

- `Fixed costs ($)` optional
- `Goal name / goal amount / timeline` optional
- `History CSV(s)` optional for month-over-month category comparisons and recurring-expense detection
- Actionable tips now include `why`, `impact`, and `data source` explanations in the UI
- Transactions table includes a category filter dropdown
- Manual expense categories use a dropdown sourced from known categories
- Budget caps are editable in the `Budget Caps (Editable)` section and sent with each analysis request

## Notes

- Categories are not inferred; they come directly from the CSV.
- Credits/payments/refunds are filtered out by default so the analysis focuses on expenses.
- Merchant classification in chat is still heuristic in this phase. External lookup-backed classification is a later step.
