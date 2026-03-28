# Team 71 Submission Skeleton

Expected final structure:

```text
71/
  code/
  report.<ext>
  video.<ext>
```

## Current Intent

This skeleton exists only to prepare the final submission layout.
It is a local repo aid and must not be packaged inside the final zip.

It does not mean the submission is complete yet.

Missing final artifacts to place here:

- `report.<ext>`
- `video.<ext>`

## Code Packaging Rule

Put only submission-required runnable project files inside `code/`.

Include only what the project brief requires:

- source code
- database initialization or importable database file
- step-by-step deployment and usage document
- only the helper scripts needed to deploy and run the project

For `code/scripts/`, include only:

- `scripts/bootstrap-env.sh`
- `scripts/bootstrap-env.bat`
- `scripts/bootstrap_env.py`
- `scripts/run-server.sh`
- `scripts/run-server.bat`
- `scripts/run-client.sh`
- `scripts/run-client.bat`
- `scripts/docker-entrypoint.sh`

Exclude all other non-required docs and development artifacts, including:

- non-required project docs
- planning notes
- freeze notes
- review notes
- local settings
- internal development-only context files
- non-required helper scripts such as:
  - `scripts/build_submission_zip.sh`
  - `scripts/check_silent_excepts.py`
  - `scripts/check_stale_security_claims.py`
  - `scripts/demo_textual.py`
  - `scripts/pre-commit`
  - `scripts/review_ruff.py`
  - `scripts/seed.py`
  - `scripts/test-deploy.sh`

## Build Reminder

Before zipping:

1. prepare the final report and video files
2. run:

   ```bash
   bash scripts/build_submission_zip.sh 71 <report_path> <video_path>
   ```

3. verify that `submission/71.zip` contains only:
   - `71/code/`
   - `71/report.<ext>`
   - `71/video.<ext>`
4. submit `submission/71.zip`
