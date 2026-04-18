# Render Deployment Design

## Goal

Deploy the current Personal Finance Assistant Flask app as a publicly reachable website using Render, with GitHub as the source repository and automatic redeploys on push.

This design is intentionally limited to getting the existing website working in production. It does not add authentication, persistent user accounts, or long-term storage of uploaded financial statements.

## Scope

In scope:

- Prepare the existing Flask app to run correctly on Render.
- Verify the repository contains the files and runtime settings Render expects.
- Ensure the app can be built from GitHub and started with `gunicorn`.
- Validate that the deployed site can load, accept a statement upload, and return the existing analysis UI.

Out of scope:

- User authentication or authorization
- Multi-user data isolation
- Database-backed storage
- Custom domain setup
- Rewriting the app for GitHub Pages or another static host
- Major feature refactors unrelated to deployment

## Recommended Approach

Use a single Render web service connected directly to the GitHub repository.

Why this approach:

- The codebase is already a Flask server-rendered app.
- The repository already includes deployment-oriented files such as `Procfile`, `requirements.txt`, and `render.yaml`.
- This is the lowest-risk path to a working public site for a school project.
- It preserves the current architecture and avoids unnecessary rewrites.

Alternatives considered:

1. Convert the project to a static site for GitHub Pages. Rejected because the app relies on Python server-side processing.
2. Deploy on Railway or PythonAnywhere. Rejected because Render is already a better fit for the current repo and simpler for this project goal.

## Architecture

The deployed system consists of:

- GitHub repository as the source of truth
- One Render web service for the Flask application
- Browser clients accessing the Render public URL

Request flow:

1. User opens the public Render URL.
2. Render routes the request to the Flask app.
3. The app serves the main HTML interface from the existing template.
4. User uploads a CSV statement and enters budgeting inputs.
5. The Flask backend parses the uploaded data, computes recommendations, and returns the rendered result.

This is a stateless application design for deployment purposes. No persistent user-specific storage is introduced in this phase.

## Runtime Requirements

The deployed application must satisfy the following:

- Flask must bind to `0.0.0.0` in production.
- The app must use the `PORT` environment variable provided by Render when present.
- The production entrypoint must be `gunicorn app:app`.
- All Python dependencies required by the app must be declared in `requirements.txt`.
- The app must not require local-only paths, manually created files, or developer machine state to boot successfully.
- The app must not depend on bundled private financial files at startup.

## Security and Privacy Position

This first deployment is a working school-project website, not a production-safe financial platform.

Explicit assumptions:

- The site will be reachable publicly by URL.
- There is no login/auth layer in this phase.
- Real financial statements may be uploaded by you during demos or testing.
- Uploaded data should be processed only as needed to complete a request and should not be intentionally persisted as a product feature.

Implications:

- This deployment should be treated as a limited demo environment.
- It should not be presented as safe for general public use with sensitive personal financial data.
- If broader public use or real multi-user operation is required later, authentication, secure storage rules, and privacy controls must be added in a separate design cycle.

## Implementation Changes Expected

The likely code and config work falls into these buckets:

### Application startup

- Review `app.py` for host/port handling.
- Ensure local development defaults still work.
- Ensure production startup works without code edits on Render.

### Dependency and process config

- Verify `requirements.txt` includes Flask, Gunicorn, and any libraries imported by the app.
- Verify `Procfile` matches the intended production command.
- Verify `render.yaml` accurately defines a Python web service with the correct build and start commands.

### Template and static behavior

- Confirm the current HTML template renders correctly in production.
- Confirm any inline assets or client-side scripts do not assume localhost or local filesystem access.

### Data handling

- Confirm uploads are handled from HTTP requests only.
- Confirm no startup logic depends on the included CSV or PDF files.
- Remove or isolate any demo/test data assumptions if they interfere with deployment.

## Error Handling Expectations

The deployed site should degrade clearly rather than fail silently.

Minimum expectations:

- Invalid or malformed uploads should return a user-visible error message.
- Missing required columns should be handled gracefully.
- Unexpected server errors should not prevent Render from starting the service.
- A health endpoint should remain available for Render health checks.

## Testing Strategy

Testing for this deployment phase is pragmatic and deployment-focused:

1. Local run test
   - Start the app locally with the production-like entrypoint when possible.
   - Confirm the homepage loads.

2. Functional smoke test
   - Upload a representative CSV.
   - Confirm analysis and recommendations render.

3. Render deployment verification
   - Deploy from GitHub to Render.
   - Confirm the public URL loads.
   - Confirm a real upload works end-to-end.
   - Confirm redeploy on push behaves correctly.

4. Health check verification
   - Confirm the health endpoint responds successfully after deploy.

## Success Criteria

The work is successful when all of the following are true:

- The app is deployed on Render at a public URL.
- The deployed site loads without requiring local development tools.
- A statement upload works on the deployed site.
- The existing budgeting analysis flow works after deployment.
- GitHub remains the source of truth and can trigger redeploys.

## Risks and Follow-Up

Known risks in this phase:

- Public accessibility without authentication is not appropriate for broad real-world use.
- Financial uploads may expose sensitive data if the app is shared beyond controlled demos.
- Render free-tier behavior may introduce cold starts or resource limits during presentations.

Deferred follow-up work:

- Add authentication
- Add secure per-user storage model
- Add explicit privacy messaging in the UI
- Add custom domain if needed
- Add automated deployment checks/tests
