# Receipt Upload Design

## Goal

Add a receipt-upload flow that lets users capture spending in real time without typing every purchase into chat. The MVP should accept multiple receipt photos, extract summary fields, show a review card for each receipt before saving, and then write exactly one top-level transaction per approved receipt into the existing history.

The feature should improve:

- real-time transaction capture
- merchant-aware categorization
- behavioral coaching
- monthly insight quality

## Scope

### In scope

- Upload multiple receipt images in one flow
- Extract merchant name, date, total amount, and top-level category
- Show one review card per receipt before saving
- Allow user edits before approval
- Save one standard transaction per approved receipt
- Generate a short behavioral note per receipt when context supports it
- Promote strong receipt-derived behavioral notes into `Top 3 Insights This Month`
- Use AI-assisted internet enrichment for unclear merchants
- Cache merchant categorization results

### Out of scope for MVP

- Writing itemized line items into the main transaction history
- Fully automated save without review
- Cross-user merchant knowledge sharing requirements
- Receipt reimbursement workflows
- Tax reporting workflows
- Returns, exchanges, and split-tender edge cases beyond manual user correction

## Product Flow

1. User uploads one or more receipt images from the signed-in dashboard.
2. Backend creates upload records and runs extraction for each image.
3. App returns a review queue with one receipt card per image.
4. Each review card shows:
   - merchant
   - date
   - total
   - category
   - extraction state
   - short behavioral note when available
5. User approves or edits each card individually.
6. On approval, the app writes exactly one top-level transaction into the existing transaction history.
7. The approved receipt can contribute a behavioral note candidate into the monthly insight engine.

Receipts do not create itemized ledger rows in MVP. Any extracted line-item information remains receipt metadata only.

## Review Card States

### Ready to save

Merchant, date, total, and category are sufficiently reliable. User can approve immediately or edit first.

### Needs category

Merchant, date, and total are usable, but category confidence is too low. The app must force the user to choose a category before approval.

This state is required after low-confidence categorization, including after failed internet-assisted enrichment.

### Needs correction

One or more core fields are missing, malformed, or suspicious. User must edit before approval.

### Error

Extraction failed for that receipt. The card shows failure details and supports retry or discard without blocking the rest of the batch.

## Data Model

The existing `transactions` table remains the source of truth for saved spending history.

Add sidecar receipt tables:

### `receipt_uploads`

One row per uploaded image.

Fields:

- `id`
- `user_id`
- `filename`
- `storage_path`
- `status`
- `created_at`

### `receipt_extractions`

One row per extracted receipt candidate.

Fields:

- `id`
- `receipt_upload_id`
- `user_id`
- `merchant`
- `transaction_date`
- `total_amount`
- `category`
- `category_confidence`
- `status`
- `behavior_note`
- `item_tags_json`
- `raw_extraction_json`
- `web_enrichment_json`
- `reviewed_at`
- `created_at`

### `receipt_transaction_links`

Links approved extraction records to the final saved transaction.

Fields:

- `id`
- `receipt_extraction_id`
- `transaction_id`
- `created_at`

### Merchant categorization cache

Add a cache table or equivalent store keyed by normalized merchant identity.

Fields:

- normalized merchant key
- resolved category
- confidence
- enrichment source
- last checked timestamp

This cache prevents repeated internet lookups for the same merchant.

## Categorization Logic

Categorization should run in layers:

1. Prior known merchant mapping from user history or local cache
2. Obvious merchant-pattern rules
3. Receipt-text clues when present
4. AI-assisted web enrichment for low-confidence merchants

Examples:

- `Trader Joe's` -> `Groceries`
- `Sweetgreen` -> `Dining`

If the enrichment step still cannot confidently classify the merchant, the card must move to `Needs category` and the user must choose a category manually.

The app should not save a guessed category in that case.

## Item-Level Classification

If receipt extraction provides usable line items, the backend may classify them for future reasoning, such as:

- salad -> dining
- grocery staples -> essential spending

This item-level information is not displayed as ledger rows in MVP and is not required to render the review card. It exists only as receipt metadata for later insights and coaching.

## Behavioral Notes

Each extraction may produce a short behavior note using saved transaction history and current receipt context.

Examples:

- `This is your 5th dining expense this week`
- `This merchant is outside your usual grocery pattern`
- `This spend continues a weekend overspending pattern`

Behavioral notes should appear on the review card when available.

If a note is strong enough for the selected month, the insight engine may promote it into `Top 3 Insights This Month`.

Promotion rules should prefer concise, high-signal notes rather than repeating weak or redundant observations.

## Backend Components

### `receipt_ingestion`

Responsibilities:

- accept multiple uploaded receipt images
- validate file type and size
- persist upload metadata
- create pending extraction jobs or records

### `receipt_extraction_service`

Responsibilities:

- run OCR and structured extraction
- derive merchant, date, total, category
- compute review state
- generate behavioral note candidate
- run merchant web enrichment when needed
- cache merchant categorization results

### `receipt_review_api`

Responsibilities:

- list pending receipt review cards
- accept user edits
- approve or discard individual receipts
- save one standard transaction on approval
- create receipt-to-transaction link record

### `insight_promotion`

Responsibilities:

- evaluate approved receipt behavioral notes against the selected month
- promote strong notes into the monthly insights pipeline
- avoid duplicates with existing insight generation

## UI Shape

Add a receipt-upload entry point to the signed-in dashboard.

The MVP UI should support:

- selecting multiple images
- showing upload progress
- rendering a review queue
- editing merchant/date/total/category inline per card
- approving, retrying, or discarding each receipt independently

The dashboard should not auto-save receipts immediately after extraction.

## Error Handling

- One failed receipt must not block the rest of a batch.
- Low-confidence categorization must block save until category selection is explicit.
- Missing total or invalid date must force correction.
- Duplicate detection is advisory only in MVP; it should warn, not hard-block, unless product requirements change later.
- Web enrichment failure must degrade to manual category selection, not abort the whole receipt.

## Testing Strategy

Add coverage for:

- multi-image upload creates multiple pending receipt cards
- approving a receipt writes one top-level transaction only
- low-confidence categorization forces manual category selection
- merchant enrichment results are cached and reused
- failed extraction on one receipt does not block the rest of the batch
- approved receipt behavioral note can appear in `Top 3 Insights This Month`
- discarded receipts do not write transactions
- edited review-card values are what get saved

## Open Implementation Notes

- The MVP should follow the existing app pattern of keeping the dashboard and chatbot grounded in one shared transaction history.
- Receipt uploads should enrich that same history, not create a parallel budgeting view.
- Web-assisted merchant categorization should be used selectively because it adds latency and possible failure points.
- The review card is the main safeguard against OCR and categorization mistakes.
