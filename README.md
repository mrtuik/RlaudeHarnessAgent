# RlaudeHarnessAgent — shared lesson store

Fully automatic, free, serverless "learn from mistakes" pipeline for
Rlaude Harness, built entirely on GitHub Issues + Actions (no backend).

## How it works
1. The app detects when a user is correcting the agent (a small LLM
   call on-device or via your existing provider).
2. The app opens a GitHub Issue on this repo, labeled `candidate`,
   using a fine-grained PAT scoped to **Issues: write only** on this
   one repo.
3. `.github/workflows/process-candidate.yml` fires automatically and
   runs `scripts/process_lesson.py`, which:
   - asks an LLM (Groq) whether this is a real, actionable lesson
     (filters spam/noise)
   - checks it against `pending_candidates.json` for matching reports
     from other users
   - once the same lesson has been reported `CONFIRM_THRESHOLD` times
     (default 3, set in the workflow file), appends it to
     `lessons.json` and closes out all contributing issues
4. The app fetches the public raw file below at startup / periodically
   and injects its contents into the agent's system prompt for every
   user:

   ```
   https://raw.githubusercontent.com/<your-username>/RlaudeHarnessAgent/main/lessons.json
   ```

   This URL is public and free to read, no auth needed.

## One-time setup
1. Push this folder as the contents of the `RlaudeHarnessAgent` repo,
   on the `main` branch.
2. Repo Settings → Secrets and variables → Actions → New repository
   secret:
   - `GROQ_API_KEY` = your Groq API key (used only inside Actions,
     never shipped in the app)
3. Repo Settings → Actions → General → Workflow permissions → set to
   **Read and write permissions** (so the workflow's built-in
   `GITHUB_TOKEN` can push commits and close issues).
4. Nothing else to configure — `lessons.json` and
   `pending_candidates.json` start empty and fill in automatically.

## Tuning
- `CONFIRM_THRESHOLD` in `.github/workflows/process-candidate.yml` —
  how many independent reports are needed before a lesson goes live
  for everyone. Lower = faster rollout, higher = safer against bad
  reports.

## App-side token
The Android app only ever needs a **fine-grained PAT scoped to this
one repo, Issues: write only** — it can never read or change code,
never touch `lessons.json` directly, and never affects other repos.
That token should live in the app's encrypted `ApiKeyVault`, not in
source code.
