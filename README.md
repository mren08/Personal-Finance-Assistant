# Budgeting Web App

This app can:

1. Parse credit card CSV statements
2. Use the CSV's explicit categories
3. Display spending graphically (including category percentages in the donut chart)
4. Provide budgeting recommendations
5. Suggest actionable tips based on remaining money
6. Add optional manual expenses that are not in your statement
7. Edit category budget caps directly in the UI
8. Detect recurring expenses from prior statements and normalize them into weekly planning guidance

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5055`.

## Deploy on Render

1. Push the project to GitHub.
2. Create a new web service on Render.
3. Connect this repository.
4. Use the default settings from `render.yaml`, or confirm:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --bind 0.0.0.0:$PORT app:app`
5. Wait for the deploy to finish, then open the Render URL.

### Demo Privacy Note

This deployment is for coursework/demo use. It is not production-safe for sensitive financial data because it does not yet include authentication, per-user data isolation, or long-term storage controls.

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

## Better Tips Inputs

The recommendation engine supports richer inputs from the UI:

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
