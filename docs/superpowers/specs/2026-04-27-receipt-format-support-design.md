# Receipt Format Support Design

## Goal

Extend the existing receipt-upload flow so it accepts common phone/photo receipt formats and PDFs without changing the review-first product model.

The user experience should stay simple:

- one uploaded file becomes one receipt review card
- common receipt photos should work directly
- a PDF should be treated as one receipt using page 1 only
- unsupported files should fail softly as per-file error cards

## Supported Inputs

### Explicitly supported

- `JPG`
- `JPEG`
- `PNG`
- `WEBP`
- `HEIC`
- `PDF`

### Explicitly unsupported

- multi-page receipt splitting
- arbitrary non-photo image formats
- office documents
- archives

If a file falls outside the supported set, the batch should continue and that file should return an error card with clear guidance.

## Product Behavior

### Upload behavior

The receipt picker should advertise support for receipt photos and PDFs.

Each uploaded file becomes exactly one receipt candidate:

- image file -> one receipt review card
- PDF file -> one receipt review card using only page 1

### Review behavior

The existing receipt review flow stays the same:

- extracted fields populate the card when available
- user can edit before approval
- low-confidence category still forces manual choice
- extraction failures still surface as reviewable error states instead of breaking the whole upload

### Error behavior

The app should return a per-file error card for:

- unsupported file type
- unreadable HEIC/image decode failure
- PDF render failure
- model extraction failure after normalization

Example error copy:

- `Unsupported receipt format. Upload JPG, PNG, WEBP, HEIC, or PDF.`
- `Could not render page 1 of this PDF receipt.`

## Backend Design

### Normalization Layer

Add a dedicated receipt-input normalization step ahead of the current image extraction helper.

Responsibilities:

- inspect file extension and mime type
- validate the file against the supported set
- normalize supported inputs into one image payload for extraction
- return a readable failure state when normalization is not possible

This layer should isolate format handling from the extraction logic so the rest of the review pipeline does not branch by file type.

### Image inputs

For supported image formats:

- read uploaded bytes
- normalize mime type
- if necessary, transcode to a model-safe image format before extraction

Preferred normalized extraction format is `PNG` or `JPEG`, whichever is easiest and most reliable for the decoder path.

### PDF inputs

For supported PDFs:

- open the uploaded PDF
- render page 1 only
- convert page 1 into an image buffer
- pass that rendered image into the exact same extraction path used for regular images

No attempt should be made in this feature to split one PDF into multiple receipts or process later pages.

## Extraction Flow

The current receipt extraction should remain single-path after normalization:

1. normalize upload into an extractable image
2. call the receipt extraction helper
3. produce merchant/date/total/category fields
4. run existing categorization and review-state logic
5. persist the receipt extraction record

This keeps PDF handling and image handling from diverging into separate OCR systems.

## Dependencies

The implementation will likely require:

- one PDF rendering dependency for page-1 conversion
- optional image-format support that can decode/transcode `WEBP` and `HEIC` safely on the backend

The design should prefer well-supported Python libraries over custom shell tooling.

If `HEIC` decode support is incomplete in the runtime environment, that specific file should fail as an error card instead of degrading the whole batch.

## UI Changes

Update the receipt upload section in the dashboard:

- label should mention receipt photos and PDFs
- file input `accept` should allow supported image types plus `.pdf,application/pdf`
- supporting copy should make it clear that each file becomes one review card

No other dashboard restructuring is needed for this feature.

## Testing

Add coverage for:

- supported image upload still works
- PDF upload is accepted and becomes one receipt review card
- unsupported file type becomes an error card
- mixed batch with one unsupported file still preserves valid files
- PDF normalization calls the same downstream extraction path as images

The regression suite should avoid repeating the earlier blind spot where the route was tested but the real extraction/normalization path was not.

## Risks And Constraints

- PDF rendering can be environment-sensitive; choose a dependency that works cleanly in local and Render deployments
- HEIC support may depend on codec availability; fail per-file if unavailable
- Larger PDFs/images may increase processing latency, but this feature should preserve the current review-first asynchronous feel by returning per-file results instead of blocking the whole batch on one failure

## Non-Goals

This design does not include:

- OCR quality improvements beyond format normalization
- multi-page PDF receipt extraction
- itemized PDF parsing into multiple transactions
- server-side retry queues or background jobs
