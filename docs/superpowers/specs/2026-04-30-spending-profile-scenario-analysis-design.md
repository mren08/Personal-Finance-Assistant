# Spending Profile and Scenario Analysis Design

## Goal

Make the app feel like an AI financial decision-support system rather than only a spending dashboard.

The dashboard should help users move from:

- `Here is where your money went`

to:

- `Here are your choices and what each choice could change`

This design adds two real product features to the logged-in experience:

1. `Your Spending Profile`
2. `Ways to Improve Your Outcome`

It also adds structured savings-goal fields so the app can reason about pacing, outcome changes, and more grounded coaching.

## Scope

This feature applies to the actual app dashboard, not demo mode only.

It will:

- replace the loose single goal text field in the real monthly plan/profile form with structured goal fields
- infer a user spending profile from selected-month spending, budget-cap logic, subscriptions, and goal progress
- compute three dynamic outcome scenarios from the user’s actual data
- connect scenario cards to the existing AI chat flow
- add “Why this?” transparency copy for the new recommendation surfaces

It will not:

- provide formal financial advice
- introduce portfolio or investment recommendations beyond contextual budgeting guidance
- depend on a new model or external financial data source

## Current Constraints

The app already has:

- uploaded statement history
- selected-month dashboard views
- category totals and category breakdown
- recurring subscription detection
- top insights and recommended actions
- an AI chatbot with selected-month context
- a monthly plan/profile form backed by `financial_profiles` and `monthly_plans`

The current goal model is too weak for this feature because it stores a single `budgeting_goal` string instead of structured target values.

## Product Changes

### 1. Structured Savings Goal Fields

Replace the current single free-text goal field in the real app experience with structured goal inputs:

- `goal_name`
- `goal_target_amount`
- `goal_target_date`
- `current_saved_amount`

The monthly plan card should still feel compact, so these fields should be grouped under a small `Savings goal` subsection within the existing form.

The backend should treat these structured fields as the source of truth for planning logic.

For compatibility during rollout:

- the backend may keep deriving a display summary string from these fields
- chat context may still include a human-readable goal summary
- the old unstructured `budgeting_goal` value should be phased out of the main UI

### 2. Spending Profile

Add a compact dashboard card near the top called:

- `Your Spending Profile`

This is inferred from user data, not selected by the user.

The card should show:

- profile name
- one-sentence explanation
- 2–3 bullet reasons
- a short transparency line:
  - `Based on your uploaded transactions, budget caps, recurring subscriptions, and savings goal.`

### 3. Scenario Analysis

Add a new dashboard section below the spending chart and above the monthly plan called:

- `Ways to Improve Your Outcome`

This section should contain three scenario cards:

1. `Stay on Current Path`
2. `Moderate Adjustment`
3. `Aggressive Savings`

Each card should show:

- scenario title
- estimated monthly savings impact
- 2–3 concrete actions
- effect on savings goal timeline or month-end budget
- short tradeoff explanation
- `Why this?` transparency line
- `Ask AI to build this plan` button

## Data Model

### Financial profile persistence

The existing financial profile and monthly plan persistence should be expanded from:

- monthly income
- fixed expenses
- budgeting goal text

to:

- monthly income
- fixed expenses
- goal name
- goal target amount
- goal target date
- current saved amount

These fields should exist for:

- current financial profile storage
- month-scoped saved plans

The selected-month plan remains the active planning context for dashboard logic.

### Dashboard payload additions

The existing dashboard/profile payload should be extended with:

- `goal_summary`
- `spending_profile`
- `scenario_analysis`

`spending_profile` should include:

- `name`
- `description`
- `reasons`
- `why_this`

`scenario_analysis` should include a list of three cards, each with:

- `title`
- `savings_impact_monthly`
- `actions`
- `goal_impact`
- `tradeoff`
- `why_this`
- `chat_prompt`
- optional structured metadata for downstream chat use

## Inference Logic

### Discretionary categories

The system should treat the following as discretionary by default when present:

- Dining
- Shopping
- Entertainment
- Travel
- Coffee
- Rideshare
- Delivery
- Subscriptions
- Other clearly non-essential categories already present in the user’s data

The system should treat categories such as rent, utilities, insurance, loan payments, phone, internet, and required bills as baseline/fixed rather than discretionary.

When exact category names differ, the app should use existing categories where possible and map only the obvious ones into the discretionary set.

### Spending Profile rule priority

If multiple rules match, profile selection should use this priority order:

1. `Goal-Focused but Behind`
2. `Reactive Spender`
3. `Budget Optimizer`
4. `Flexible Spender`

### Profile rules

#### Goal-Focused but Behind

Use this when:

- a structured goal exists
- goal target amount and target date are present
- the user’s current projected savings pace is below the monthly pace required to reach the goal on time

Description:

- `You have a clear goal, but your current spending pace may delay your progress.`

Typical reasons:

- current estimated leftover is below needed monthly savings pace
- top discretionary category is crowding out goal progress
- recurring subscriptions are reducing monthly room

#### Reactive Spender

Use this when:

- discretionary spending is more than `25%` above the discretionary cap model for the selected month

Description:

- `You tend to overspend in discretionary categories, especially when expenses are not actively tracked.`

Typical reasons:

- one or more discretionary categories are materially above cap
- a discretionary category is above historical average
- negative or weak leftover after fixed expenses

#### Budget Optimizer

Use this when:

- discretionary spending is within `+/-10%` of the cap model

Description:

- `You are generally staying within budget and may benefit most from small optimizations.`

Typical reasons:

- major categories are near cap, not far above it
- small subscription trimming or minor dining changes could improve outcome
- month-end leftover exists but is not fully aligned to the goal

#### Flexible Spender

Fallback when no higher-priority rule applies.

Description:

- `Your spending patterns are mixed, with room for more consistent planning.`

Typical reasons:

- no single strong overspending signal
- goal may be absent or incomplete
- spending pattern is mixed across categories

## Scenario Analysis Logic

The scenarios should be dynamic, server-computed, and based on the user’s actual data.

They must use hedged wording such as:

- `could`
- `may`
- `estimated`

They must not be framed as formal financial advice.

### Inputs

Scenario generation should use:

- current monthly discretionary spending
- category-level spending
- category cap model
- recurring subscriptions
- structured goal fields when present
- selected-month summary

### Scenario 1: Stay on Current Path

This scenario assumes:

- no changes to current habits

It should show:

- `$0/month` savings impact
- a neutral action list such as continuing the current pace
- expected goal gap or month-end surplus/shortfall
- tradeoff emphasizing ease but limited improvement

Example framing:

- `At your current pace, you may miss your goal by $220.`

### Scenario 2: Moderate Adjustment

This scenario assumes:

- reduce the top overspent discretionary category by about `15–20%`
- cancel or pause one lower-priority subscription if subscriptions exist

It should:

- compute estimated monthly savings from the category cut plus one subscription cut
- describe the concrete changes
- estimate how that affects goal progress or month-end room
- emphasize modest behavior change

Example framing:

- `Reducing dining by 20% and canceling one $12 subscription could save about $84/month.`

### Scenario 3: Aggressive Savings

This scenario assumes:

- reduce the top two discretionary categories by about `25–40%`
- cancel or pause up to two subscriptions if available

It should:

- compute estimated monthly savings from those changes
- describe the stronger cuts
- estimate goal timeline improvement or stronger month-end surplus
- emphasize higher impact and lower sustainability

Example framing:

- `Cutting dining and shopping more aggressively could save about $185/month and help you reach your goal one month earlier.`

## Goal Pace and Outcome Logic

### With structured goal data

If the user has:

- `goal_target_amount`
- `goal_target_date`

then the system should compute:

- remaining amount needed
- months remaining until target date
- required monthly savings pace
- projected monthly pace from current leftover after fixed expenses

If projected pace is below required pace:

- profile and scenario cards should reflect the gap

If projected pace is above required pace:

- cards should describe that the current path may already be sufficient

### Without full structured goal data

If goal fields are incomplete:

- profile logic should skip goal-behind classification
- scenario cards should talk about month-end room instead of goal timing
- the UI should remain functional, but less specific

## Fallback Behavior

If there is not enough data to support strong recommendations:

- still show the Spending Profile and Scenario Analysis sections
- use softened guidance such as:
  - `Upload more transaction history to generate stronger scenario recommendations.`

Cases that count as weak data:

- too few transactions in the selected month
- no meaningful discretionary categories
- no recurring subscriptions and little history
- incomplete goal data when goal-based reasoning is needed

## AI Chat Integration

Each scenario card gets a button:

- `Ask AI to build this plan`

Clicking the button should:

- submit or prefill a grounded prompt into the existing chat flow
- use wording such as:
  - `Help me follow the Moderate Adjustment plan using my current spending data.`

The chat layer should receive:

- the user message
- the current selected-month context
- the selected scenario payload

This lets the assistant answer with a concrete weekly plan rather than a generic response.

Typical response style:

- weekly cap guidance
- specific subscription action
- near-term behavioral constraint
- estimated savings impact using the selected scenario assumptions

## UI Layout

### Dashboard order

The updated dashboard order should be:

1. Top insights
2. `Your Spending Profile`
3. existing summary / high-level dashboard metrics
4. category chart
5. `Ways to Improve Your Outcome`
6. monthly plan
7. remaining sections

This keeps interpretation before action, and action before data-entry adjustments.

### Spending Profile card

Compact card near the top with:

- title
- profile name
- description
- bullets
- transparency line

### Scenario cards

Desktop:

- 3 cards in a row

Mobile:

- stacked vertically

Each card should contain:

- title
- savings impact
- actions
- goal impact
- tradeoff
- transparency line
- action button

The UI should stay consistent with the existing dashboard card system.

## Transparency Copy

Both Spending Profile and Scenario Analysis must include a short explanation line.

Preferred wording:

- `Based on your uploaded transactions, budget caps, recurring subscriptions, and savings goal.`

If goal data is incomplete, the wording may omit savings-goal language, but it should still explain the underlying data sources.

## Error Handling

- Missing structured goal fields should not break the dashboard.
- Scenario generation should degrade to month-end budget framing when goal timing is unavailable.
- AI scenario prompts should still work even if the user has no goal, but the assistant should focus on spending outcome instead.
- The UI must never imply certainty where the data is weak.

## Testing Strategy

### Storage and logic tests

Add tests for:

- structured goal fields save and reload correctly
- derived goal summary formatting
- profile classification priority
- reactive vs optimizer thresholds
- flexible fallback
- goal pace behind classification
- scenario generation with:
  - no goal
  - full goal
  - with subscriptions
  - without subscriptions
  - insufficient data fallback

### Route and UI tests

Add tests for:

- profile card near the top of dashboard response
- scenario section below chart and above monthly plan
- transparency copy rendering
- scenario button presence
- structured goal field rendering in the profile form
- scenario button prompt plumbing into chat behavior

### Regression expectations

Existing features that must keep working:

- month selection
- recurring subscription detection
- top insights
- recommended actions
- chat response grounding
- demo mode

## Implementation Notes

- Keep the source of truth server-side in the dashboard payload builder rather than deriving profiles/scenarios in browser JS.
- Follow the current pattern used for `top_insights` and `recommended_actions`.
- Prefer small new helper methods over one large dashboard-calculation block.
- Preserve explainability over sophistication in the first version.

## Success Criteria

This feature is successful when a user can log in and immediately understand:

- what kind of spender the app thinks they are
- why the app thinks that
- what three outcome paths are available
- how each path could change savings or month-end room
- how to ask the AI to operationalize one of those paths

The experience should feel like the app is helping the user evaluate tradeoffs, not just reporting past spending.
