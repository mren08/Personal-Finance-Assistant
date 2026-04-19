# Real Backend Agent Design

Date: 2026-04-18

## Goal

Add a real backend agent to the Personal Finance AI Assistant using a hybrid architecture:

- LLM-driven interpretation and coaching
- Database-backed structured financial state
- Durable agent memory that updates automatically after uploads and chats

The agent should help users understand leftover money for the month, remember prior commitments and behavior patterns, and update its own running notes without waiting for explicit prompts.

## Product Direction

The agent is not a generic chatbot. It is a finance-specific coaching system that operates on top of persistent user data.

It should:

- Read transaction history, recurring subscriptions, goals, monthly income, and fixed expenses
- Interpret user chat input into structured actions
- Produce direct budgeting guidance
- Persist coaching memory over time
- Update memory automatically after uploads and chats

The product should continue using deterministic financial calculations where possible, while using the LLM for interpretation, summarization, coaching, and memory synthesis.

## Core Architecture

### Hybrid Agent Model

The system should use a hybrid agent approach rather than prompt-only memory.

The backend should own the source of truth for:

- User profile
- Transactions
- Monthly income
- Fixed expenses
- Goals
- Subscription decisions
- Agent notes
- Monthly summaries

The LLM should operate on that structured state and return:

- Conversational coaching
- Structured actions
- Candidate memory updates

This keeps the system explainable, auditable, and durable.

### Why Not Prompt-Only Memory

Prompt-only memory is not sufficient for this product because:

- It is fragile across sessions
- It is expensive as history grows
- It is hard to inspect and correct
- It weakens trust in a finance context

Structured storage is required for user-facing financial state and for reliable agent memory.

## User Data Model

The system should persist the following per user:

### Financial Profile

- Monthly income
- Fixed expenses
- Budgeting goals
- Saved subscription decisions
- Cash-flow summary values

### Transaction History

- Uploaded statement transactions
- Manual chat-added transactions
- Source type
- Category
- Matching/duplicate status when relevant

### Agent Memory

The agent should maintain its own running notes, such as:

- User wants to reduce restaurant spending
- Dining rose again after the user said they wanted to cut back
- User kept Netflix but canceled Hulu
- User frequently adds off-statement transactions manually
- Leftover money improved after lowering subscription load

These notes should be durable, reviewable, and tied to timestamps.

### Monthly Summaries

For each month, the system should track:

- Income
- Fixed expenses
- Total tracked spending
- Recurring monthly baseline
- Leftover money
- Discretionary remaining
- Top spending pressure categories
- Agent-generated summary and coaching focus

## Agent Responsibilities

### Chat Interpretation

The agent should convert user chat into structured actions where possible, including:

- Add transaction
- Confirm possible duplicate transaction
- Save monthly income
- Save fixed expense
- Update goal
- Mark subscription keep/cancel
- Save or revise agent note

### Coaching

The agent should answer:

- How much money is left this month
- Whether spending is on track
- What categories are driving overspending
- What subscriptions or habits are easiest to cut
- Whether current choices align with prior goals

### Memory Updates

After every statement upload or chat:

- Refresh financial summary metrics
- Recompute recurring/subscription signals
- Generate or revise agent notes
- Update the current month summary

The user should not need to ask for this explicitly.

## Leftover Money Guidance

This must become a first-class feature.

The system should compute:

- Monthly income
- Fixed expenses
- Total spending this month
- Remaining money for the month
- Remaining discretionary money after fixed obligations

The agent should explain those values clearly and use them in its coaching.

Example coaching patterns:

- "You have $420 left this month, but only $190 is truly discretionary after fixed costs."
- "Your leftover money is being compressed by restaurant spending and recurring subscriptions."
- "If you keep dining at the current pace, you will erase your remaining buffer before month-end."

## User Inputs

Add explicit user inputs for:

- Monthly income
- Fixed expenses
- Budgeting goals

These should be editable in the product UI and stored as durable profile values.

The agent should use them as part of every coaching pass.

## System Components

### Financial State Layer

Responsible for persistent user profile data, transaction history, and monthly summaries.

### Agent Orchestration Layer

Responsible for:

- Building agent context from structured state
- Calling the LLM
- Parsing structured output
- Applying valid actions
- Persisting memory updates

### Memory Layer

Responsible for storing:

- Agent-authored notes
- Monthly coaching summaries
- Notes tied to spending patterns and behavior shifts

### UI Layer

Responsible for:

- Displaying income and fixed-expense inputs
- Showing leftover-money summaries
- Showing current month coaching
- Showing chat and history

## Error Handling and Trust

Because this is a finance workflow, the agent must never silently change user-critical financial state.

- Transaction additions must be explicit
- Duplicate uncertainty must trigger confirmation
- Income and fixed expense changes should be clear and visible
- Agent notes should be reviewable
- Deterministic calculations should remain the source of truth for totals and leftover-money numbers

The LLM can interpret and coach, but it should not become the hidden calculator.

## First Implementation Slice

The first backend-agent implementation should include:

- A real LLM-backed chat route
- Structured user profile fields for monthly income and fixed expenses
- Automatic monthly leftover-money computation
- Agent memory notes stored in the database
- Automatic note refresh after uploads and chats
- Structured action parsing for profile updates and transaction actions

It should not yet include:

- Full long-horizon planning
- Bank integrations
- Auto-cancellation flows
- Advanced financial products like investments or debt optimization

## Success Criteria

This feature is successful if:

- The assistant gives materially better answers than a stateless chatbot
- Users can ask about leftover money and get answers grounded in stored income, fixed expenses, and spending
- The system remembers past goals and patterns across sessions
- The agent updates its own notes automatically after new data arrives
- Critical financial values remain deterministic and inspectable
