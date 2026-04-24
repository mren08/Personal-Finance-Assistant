# Password Reset Email Design

## Goal

Replace the current public homepage password-reset flow, which lets anyone type a new password directly into the landing page, with a standard email-based reset-link flow. The new design must be provider-ready, avoid account-enumeration leaks, and keep the current lightweight Flask app structure.

## Why This Change

The current forgot-password flow is not strong enough for a public product:

- it exposes a new-password form on the public auth surface
- it lets password reset happen with only an email address and a new password
- it does not use time-limited, one-time credentials
- it has no email delivery abstraction for a real reset workflow

The replacement flow should match normal user expectations:

- request reset by email
- receive a reset link
- open a dedicated reset page
- set a new password there

## User Experience

### Logged-out Homepage

The homepage keeps a small `Forgot password?` link near sign-in. Clicking it reveals a minimal panel with:

- `Email`
- `Send reset link`

The homepage no longer asks for a new password in this panel.

When the user submits the form, the UI always returns the same notice:

`If that account exists, a reset link has been sent.`

That message is shown regardless of whether the email is present in the database.

### Reset Link Email

If the submitted email belongs to an account, the backend generates a short-lived one-time token and creates a reset URL:

`/reset-password/<token>`

The mailer abstraction sends a password-reset email containing that link.

If no real provider is configured, the app does not fail the request. Instead, the development mailer logs the reset URL to server output so the flow can still be exercised locally.

### Reset Password Page

The reset page is separate from the homepage and contains:

- `New password`
- `Confirm password`
- `Reset password`

Possible outcomes:

- valid token: password is updated, token is consumed, user is redirected to sign-in with a success notice
- invalid token: show a clear invalid-link error and direct the user back to request a new reset
- expired token: show a clear expired-link error and direct the user back to request a new reset
- already-used token: show a clear already-used-link error and direct the user back to request a new reset

Resetting the password does not automatically sign the user in.

## Architecture

### Storage

Add a new `password_reset_tokens` table with these fields:

- `id`
- `user_id`
- `token_hash`
- `expires_at`
- `used_at`
- `created_at`

Rules:

- store only a hash of the token, never the raw token
- a token is valid only if it is unexpired and `used_at` is null
- successful password reset marks the matched token as used
- successful password reset also invalidates any other active reset tokens for the same user

### Storage Methods

Add storage methods with focused responsibilities:

- `create_password_reset_token(email)`:
  - normalize email
  - look up user if present
  - if user exists, generate raw token, store token hash and expiry, return data needed for email delivery
  - if user does not exist, return a neutral no-op result without raising an existence-revealing error
- `get_password_reset_token(raw_token)`:
  - hash incoming token
  - find matching active row
  - reject invalid, expired, or used tokens
  - return token metadata and target user when valid
- `reset_password_with_token(raw_token, new_password)`:
  - validate token
  - update password hash
  - mark matched token used
  - invalidate any other still-active tokens for that user
  - execute atomically

### Mailer Abstraction

Add a small mailer interface that keeps route logic independent from the email provider:

- `send_password_reset_email(email, reset_url)`

Provide two implementations:

1. `LoggingMailer`
   - used by default in development or when provider configuration is absent
   - writes the reset URL to server logs
   - never breaks the user request

2. Provider-ready mailer
   - selected later through environment configuration
   - same interface, so route code does not change when a real provider is added

This design intentionally avoids locking the app to a specific provider at this stage.

## Routes

### `POST /forgot-password`

Responsibilities:

- accept only `email`
- call storage to create a token if the account exists
- send reset email through the mailer abstraction only when a token was created
- always return the same neutral success notice

This route must never reveal whether the email exists.

### `GET /reset-password/<token>`

Responsibilities:

- validate token state
- render the reset-password page for valid tokens
- render a user-friendly invalid or expired state for bad tokens

### `POST /reset-password/<token>`

Responsibilities:

- validate token again
- validate password fields
- update password through the atomic storage method
- redirect to homepage sign-in with success notice

This route must reject invalid, expired, and already-used tokens cleanly.

## Security Requirements

- Token lifetime is 30 minutes.
- Tokens are one-time use.
- Raw tokens are never persisted in storage.
- Forgot-password request response is always neutral and identical.
- Password reset does not create a session.
- Old active reset tokens for a user are invalidated after a successful reset.
- Temporary passwords are not emailed.

## UI Requirements

### Homepage Auth Card

- Keep sign-in as the default visible form.
- Keep create-account behind its existing reveal link.
- Keep forgot-password behind its own reveal link.
- Forgot-password panel contains only the email field and submit button.
- If forgot-password request validation fails for input formatting, reopen the forgot-password panel.
- Success notice returns the user to normal sign-in state.

### Reset Page

- Use the same visual language as the homepage auth card so it feels like part of the same product
- Show a concise explanation of what the page does
- Require new password and confirm password
- Show concise inline errors for mismatch or invalid token state

## Error Handling

- unknown email on forgot-password request: return neutral success message
- malformed email on forgot-password request: show validation error without leaking account state
- missing provider config: fall back to logging mailer instead of failing
- expired token: show expired-link state
- invalid token: show invalid-link state
- used token: show already-used state
- password mismatch: show inline form error and keep token page open

## Testing

Add coverage for:

- homepage forgot-password panel is hidden by default
- forgot-password panel submits email only
- forgot-password request returns the same success notice for existing and non-existing emails
- existing account creates a reset token and triggers mailer output
- non-existing account does not create a token and does not leak existence
- valid token renders reset-password page
- invalid token is rejected
- expired token is rejected
- used token is rejected
- successful reset changes the stored password
- successful reset consumes the token
- successful reset invalidates older active tokens for that user
- login works with the new password after reset
- logging mailer can be asserted in tests without requiring a real provider

## Scope Boundaries

Included:

- reset-link flow
- provider-ready mailer abstraction
- dedicated reset page
- secure token storage and lifecycle
- test coverage for the new reset flow

Not included:

- wiring a specific third-party email provider
- passwordless login
- MFA
- forced password rotation
- account lockout or rate-limiting work beyond current app behavior

## Success Criteria

The feature is complete when:

- the homepage no longer accepts a new password directly in the forgot-password panel
- password reset requests are email-based and neutral
- valid reset links allow secure password update on a dedicated page
- invalid, expired, and used links fail clearly
- the app remains usable without email-provider setup through a logging fallback
- the full route and storage test suite passes
