# Real Backend Agent Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real backend agent with stored financial profile data, deterministic leftover-money calculations, durable agent notes, and LLM-backed coaching/actions.

**Architecture:** Keep Flask as the app shell and SQLite as the source of truth. Add a focused agent orchestration module that builds structured context from stored financial state, calls an LLM client, parses structured actions, and persists agent notes plus monthly summaries automatically after uploads and chats.

**Tech Stack:** Python 3, Flask, sqlite3, unittest, OpenAI API client, Jinja templates, vanilla JavaScript

---

## File Structure

- Create: `agent_service.py`
  Responsibility: Build prompt/context, call the LLM, parse structured responses, and apply memory-update semantics.
- Create: `financial_state.py`
  Responsibility: Deterministic monthly summary calculations, leftover-money math, and helper transforms from stored rows to agent context.
- Create: `tests/test_agent_service.py`
  Responsibility: Unit coverage for LLM-response parsing, profile updates, and monthly summary logic.
- Modify: `storage.py`
  Responsibility: Persist profile fields, agent notes, monthly summaries, and richer pending actions.
- Modify: `app.py`
  Responsibility: Inject agent service, add profile update routes, wire upload/chat to automatic summary + note refresh.
- Modify: `templates/index.html`
  Responsibility: Add monthly income / fixed expense / goals inputs plus UI for leftover money and agent notes.
- Modify: `README.md`
  Responsibility: Document env vars and local setup for the backend agent.
- Modify: `tests/test_app_routes.py`
  Responsibility: End-to-end route coverage for profile updates, note creation, and leftover-money display.

### Task 1: Persist financial profile, agent notes, and monthly summaries

**Files:**
- Modify: `storage.py`
- Test: `tests/test_storage_and_coach.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_storage_persists_profile_notes_and_monthly_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Cut dining spend",
            )
            storage.save_agent_note(
                user_id,
                note_type="behavior_pattern",
                content="Dining usually spikes on weekends.",
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=1800,
                tracked_spending=1200,
                recurring_monthly_total=80,
                leftover_money=3000,
                discretionary_remaining=1200,
                summary_text="You still have room this month, but dining is the swing category.",
            )

            profile = storage.get_dashboard_data(user_id)

            self.assertEqual(profile["financial_profile"]["monthly_income"], 4200)
            self.assertEqual(profile["financial_profile"]["fixed_expenses"], 1800)
            self.assertEqual(profile["financial_profile"]["budgeting_goal"], "Cut dining spend")
            self.assertEqual(profile["agent_notes"][0]["content"], "Dining usually spikes on weekends.")
            self.assertEqual(profile["monthly_summary"]["leftover_money"], 3000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_storage_and_coach.StorageTests.test_storage_persists_profile_notes_and_monthly_summary -v`
Expected: FAIL with missing methods or missing `financial_profile` / `agent_notes` / `monthly_summary`

- [ ] **Step 3: Write minimal implementation**

```python
CREATE TABLE IF NOT EXISTS financial_profiles (
    user_id INTEGER PRIMARY KEY,
    monthly_income REAL NOT NULL DEFAULT 0,
    fixed_expenses REAL NOT NULL DEFAULT 0,
    budgeting_goal TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    note_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS monthly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    month_key TEXT NOT NULL,
    income REAL NOT NULL,
    fixed_expenses REAL NOT NULL,
    tracked_spending REAL NOT NULL,
    recurring_monthly_total REAL NOT NULL,
    leftover_money REAL NOT NULL,
    discretionary_remaining REAL NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

Also add:

```python
def upsert_financial_profile(self, user_id: int, monthly_income: float, fixed_expenses: float, budgeting_goal: str) -> None:
    ...

def save_agent_note(self, user_id: int, note_type: str, content: str) -> None:
    ...

def save_monthly_summary(...):
    ...
```

Update `get_dashboard_data()` to return:

```python
"financial_profile": self.get_financial_profile(user_id),
"agent_notes": self.list_agent_notes(user_id),
"monthly_summary": self.get_latest_monthly_summary(user_id),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_storage_and_coach.StorageTests.test_storage_persists_profile_notes_and_monthly_summary -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/test_storage_and_coach.py
git commit -m "feat: persist profile and agent memory state"
```

### Task 2: Add deterministic monthly summary calculations

**Files:**
- Create: `financial_state.py`
- Create: `tests/test_agent_service.py`

- [ ] **Step 1: Write the failing test**

```python
from financial_state import build_monthly_summary


class FinancialStateTests(unittest.TestCase):
    def test_build_monthly_summary_computes_leftover_and_discretionary_remaining(self):
        summary = build_monthly_summary(
            monthly_income=4200,
            fixed_expenses=1800,
            tracked_spending=1450,
            recurring_monthly_total=90,
        )

        self.assertEqual(summary["leftover_money"], 2750)
        self.assertEqual(summary["discretionary_remaining"], 950)
        self.assertEqual(summary["recurring_monthly_total"], 90)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_service.FinancialStateTests.test_build_monthly_summary_computes_leftover_and_discretionary_remaining -v`
Expected: FAIL with missing module or missing `build_monthly_summary`

- [ ] **Step 3: Write minimal implementation**

```python
def build_monthly_summary(
    monthly_income: float,
    fixed_expenses: float,
    tracked_spending: float,
    recurring_monthly_total: float,
) -> dict[str, float]:
    leftover_money = round(monthly_income - tracked_spending, 2)
    discretionary_remaining = round(monthly_income - fixed_expenses - tracked_spending, 2)
    return {
        "monthly_income": round(monthly_income, 2),
        "fixed_expenses": round(fixed_expenses, 2),
        "tracked_spending": round(tracked_spending, 2),
        "recurring_monthly_total": round(recurring_monthly_total, 2),
        "leftover_money": leftover_money,
        "discretionary_remaining": discretionary_remaining,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_service.FinancialStateTests.test_build_monthly_summary_computes_leftover_and_discretionary_remaining -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add financial_state.py tests/test_agent_service.py
git commit -m "feat: add monthly summary calculations"
```

### Task 3: Add LLM-backed agent orchestration with structured actions

**Files:**
- Create: `agent_service.py`
- Modify: `tests/test_agent_service.py`

- [ ] **Step 1: Write the failing test**

```python
from agent_service import AgentService


class AgentServiceTests(unittest.TestCase):
    def test_agent_service_parses_llm_actions_and_notes(self):
        fake_response = {
            "reply": "You have $950 left after fixed costs. Dining is still the weak spot.",
            "actions": [
                {"type": "save_agent_note", "note_type": "behavior_pattern", "content": "Dining remains the main pressure category."}
            ],
        }

        service = AgentService(llm_client=lambda payload: fake_response)
        result = service.run_chat_turn(
            message="How much money do I have left this month?",
            agent_context={"monthly_summary": {"discretionary_remaining": 950}},
        )

        self.assertEqual(result["reply"], fake_response["reply"])
        self.assertEqual(result["actions"][0]["type"], "save_agent_note")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_service.AgentServiceTests.test_agent_service_parses_llm_actions_and_notes -v`
Expected: FAIL with missing module or missing `AgentService`

- [ ] **Step 3: Write minimal implementation**

```python
class AgentService:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def run_chat_turn(self, message: str, agent_context: dict) -> dict:
        response = self.llm_client(
            {
                "message": message,
                "context": agent_context,
            }
        )
        return {
            "reply": str(response.get("reply", "")).strip(),
            "actions": response.get("actions", []),
        }
```

Also add a production client factory in the same file:

```python
def build_openai_llm_client():
    from openai import OpenAI

    client = OpenAI()

    def call_agent(payload: dict) -> dict:
        response = client.responses.create(
            model="gpt-5-mini",
            input=[{"role": "system", "content": "You are a personal finance coaching agent. Return JSON only."},
                   {"role": "user", "content": json.dumps(payload)}],
        )
        return json.loads(response.output_text)

    return call_agent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_service.AgentServiceTests.test_agent_service_parses_llm_actions_and_notes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_service.py tests/test_agent_service.py
git commit -m "feat: add llm agent orchestration"
```

### Task 4: Wire profile updates and automatic monthly summaries into Flask routes

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_routes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_profile_update_route_saves_income_and_fixed_expenses(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/api/profile",
            json={
                "monthly_income": 4200,
                "fixed_expenses": 1800,
                "budgeting_goal": "Spend less on dining",
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["profile"]["financial_profile"]["monthly_income"], 4200)
        self.assertEqual(payload["profile"]["financial_profile"]["fixed_expenses"], 1800)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_profile_update_route_saves_income_and_fixed_expenses -v`
Expected: FAIL with 404 on `/api/profile`

- [ ] **Step 3: Write minimal implementation**

```python
@app.route("/api/profile", methods=["POST"])
def update_profile():
    user_id = require_user_id()
    payload = request.get_json(force=True) or {}
    storage.upsert_financial_profile(
        user_id,
        monthly_income=float(payload.get("monthly_income", 0)),
        fixed_expenses=float(payload.get("fixed_expenses", 0)),
        budgeting_goal=str(payload.get("budgeting_goal", "")).strip(),
    )
    profile = refresh_user_summary(user_id)
    return jsonify({"profile": profile})
```

Also add a helper:

```python
def refresh_user_summary(user_id: int) -> dict:
    profile = storage.get_dashboard_data(user_id)
    summary = build_monthly_summary(
        monthly_income=profile["financial_profile"]["monthly_income"],
        fixed_expenses=profile["financial_profile"]["fixed_expenses"],
        tracked_spending=profile["total_spent"],
        recurring_monthly_total=profile["monthly_recurring_total"],
    )
    storage.save_monthly_summary(...)
    return storage.get_dashboard_data(user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_profile_update_route_saves_income_and_fixed_expenses -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_routes.py
git commit -m "feat: add profile update and summary refresh routes"
```

### Task 5: Connect upload/chat flows to automatic agent notes

**Files:**
- Modify: `app.py`
- Modify: `storage.py`
- Modify: `agent_service.py`
- Modify: `tests/test_app_routes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_chat_route_persists_agent_note_from_llm_result(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})
        self.client.post("/api/profile", json={"monthly_income": 4200, "fixed_expenses": 1800, "budgeting_goal": "Spend less on dining"})

        with patch("app.build_agent_service") as build_agent_service:
            build_agent_service.return_value.run_chat_turn.return_value = {
                "reply": "You have $2400 left this month.",
                "actions": [
                    {
                        "type": "save_agent_note",
                        "note_type": "monthly_focus",
                        "content": "Dining should stay under control if the user wants buffer.",
                    }
                ],
            }

            response = self.client.post("/api/chat", json={"message": "How much do I have left this month?"})

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["profile"]["agent_notes"][0]["note_type"], "monthly_focus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_chat_route_persists_agent_note_from_llm_result -v`
Expected: FAIL because chat route does not yet call the agent service or save notes

- [ ] **Step 3: Write minimal implementation**

```python
def apply_agent_actions(user_id: int, actions: list[dict]) -> None:
    for action in actions:
        if action["type"] == "save_agent_note":
            storage.save_agent_note(
                user_id,
                note_type=action["note_type"],
                content=action["content"],
            )
```

Update `/api/chat` and `/api/upload-statement` to:

```python
agent_context = storage.get_dashboard_data(user_id)
agent_result = agent_service.run_chat_turn(message=message, agent_context=agent_context)
apply_agent_actions(user_id, agent_result["actions"])
refresh_user_summary(user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_chat_route_persists_agent_note_from_llm_result -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py storage.py agent_service.py tests/test_app_routes.py
git commit -m "feat: persist agent notes from llm actions"
```

### Task 6: Add UI for income, fixed expenses, goals, and monthly coaching

**Files:**
- Modify: `templates/index.html`
- Modify: `tests/test_app_routes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_logged_in_dashboard_shows_income_and_leftover_money_sections(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"}, follow_redirects=True)
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Monthly income", response.data)
        self.assertIn(b"Fixed expenses", response.data)
        self.assertIn(b"Left this month", response.data)
        self.assertIn(b"Agent notes", response.data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_logged_in_dashboard_shows_income_and_leftover_money_sections -v`
Expected: FAIL because the dashboard does not yet render those sections

- [ ] **Step 3: Write minimal implementation**

```html
<div class="panel">
  <h2>Monthly plan</h2>
  <form id="profile-form">
    <label for="monthly-income">Monthly income</label>
    <input id="monthly-income" type="number" step="0.01">
    <label for="fixed-expenses">Fixed expenses</label>
    <input id="fixed-expenses" type="number" step="0.01">
    <label for="budgeting-goal">Budgeting goal</label>
    <input id="budgeting-goal" type="text">
    <button type="submit">Save plan</button>
  </form>
</div>

<div class="metric">
  <div class="label">Left this month</div>
  <div class="value" id="leftover-money">$0.00</div>
</div>

<div class="panel">
  <h2>Agent notes</h2>
  <ul id="agent-notes-list"></ul>
</div>
```

Also add JS to POST `/api/profile` and rerender:

```javascript
await fetch("/api/profile", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    monthly_income: Number(monthlyIncomeInput.value || 0),
    fixed_expenses: Number(fixedExpensesInput.value || 0),
    budgeting_goal: budgetingGoalInput.value.trim(),
  }),
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_logged_in_dashboard_shows_income_and_leftover_money_sections -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/index.html tests/test_app_routes.py
git commit -m "feat: add monthly coaching UI"
```

### Task 7: Document agent env vars and verify full suite

**Files:**
- Modify: `README.md`
- Modify: `tests/test_app_routes.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_storage_and_coach.py`
- Test: `tests/test_runtime_config.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_readme_mentions_openai_key_and_profile_inputs(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("OPENAI_API_KEY", readme)
        self.assertIn("monthly income", readme.lower())
        self.assertIn("fixed expenses", readme.lower())
        self.assertIn("agent notes", readme.lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_readme_mentions_openai_key_and_profile_inputs -v`
Expected: FAIL because README does not yet document the agent setup

- [ ] **Step 3: Write minimal implementation**

```md
## Backend Agent Setup

Set:

```bash
export OPENAI_API_KEY=...
```

The dashboard now supports:
- monthly income
- fixed expenses
- budgeting goal
- automatic agent notes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_app_routes.AppRouteTests.test_readme_mentions_openai_key_and_profile_inputs -v`
Expected: PASS

- [ ] **Step 5: Run full verification**

Run: `python -m unittest -v`
Expected: PASS for all tests including the new agent and profile coverage

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_app_routes.py tests/test_agent_service.py tests/test_storage_and_coach.py tests/test_runtime_config.py
git commit -m "docs: add backend agent setup guidance"
```

## Self-Review

### Spec coverage

- Hybrid architecture: covered by Tasks 2, 3, 4, and 5
- Financial profile fields and user inputs: covered by Tasks 1, 4, and 6
- Leftover-money guidance: covered by Tasks 2, 4, and 6
- Durable agent notes and monthly summaries: covered by Tasks 1 and 5
- Automatic updates after uploads and chats: covered by Tasks 4 and 5
- Real LLM-backed route: covered by Task 3 and wired in Task 5

### Scope guard

This plan intentionally leaves out:

- advanced long-term planning
- banking integrations
- subscription cancellation automation
- investment or debt-specific reasoning

Those belong in later plans after the first backend-agent slice is stable.
