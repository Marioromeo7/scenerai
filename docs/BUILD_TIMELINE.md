# Scenarai — Build Timeline

Generated from filesystem modification times (118 files, 60 clustered edit groups) — git history collapses this session into a single commit, so mtimes are the only real record of sequence.


## 2026-05-07

- **00:01** — `codex/api_transaction_security_test.py`
- **00:20** — `codex/engine_stress_test.py`

## 2026-05-10

- **16:28–16:29** — 26 files:
  - `backend/alembic/env.py`
  - `backend/alembic.ini`
  - `backend/alembic/script.py.mako`
  - `backend/alembic/versions/0000_initial_schema.py`
  - `backend/alembic/versions/0001_composite_indexes.py`
  - `backend/alembic/versions/0002_scenario_prefab.py`
  - `backend/alembic/versions/0003_scenario_published.py`
  - `backend/database.py`
  - `backend/engine/__init__.py`
  - `backend/engine/call.py`
  - `backend/engine/serializer.py`
  - `backend/setup.cfg`
  - `backend/tests/__init__.py`
  - `backend/tests/conftest.py`
  - `backend/tests/test_call.py`
  - `backend/tests/test_hardening.py`
  - `backend/tests/test_serializer.py`
  - `frontend/app/Dockerfile`
  - `frontend/app/index.html`
  - `frontend/app/package-lock.json`
  - `frontend/app/package.json`
  - `frontend/app/public/index.html`
  - `frontend/app/src/index.css`
  - `frontend/app/src/main.jsx`
  - `nginx/nginx.conf`
  - `README.md`

## 2026-08-05

- **15:57** — `NEXT_PHASE_PLAN.md`
- **16:14** — `backend/alembic/versions/0004_prefab_status.py`
- **16:19** — `backend/requirements-dev.txt`
- **16:34** — 4 files:
  - `loadtest/requirements.txt`
  - `loadtest/README.md`
  - `.gitignore`
  - `docker-compose.override.yml`
- **19:38** — `backend/tests/test_types.py`
- **20:22** — `backend/tests/test_async_helpers.py`
- **20:26** — `backend/tests/test_guard.py`
- **20:31** — `backend/worker.py`
- **21:24–21:27** — 2 files:
  - `backend/tests/test_worker.py`
  - `backend/tests/test_session_checkpoint.py`
- **21:44** — `loadtest/thresholds.json`
- **21:49–21:50** — 2 files:
  - `loadtest/scenarai_client.py`
  - `loadtest/run.py`
- **22:03** — `loadtest/report.json`
- **22:11** — `judge/golden_scenarios.py`
- **22:17–22:19** — 2 files:
  - `docs/eval-runs/20260805T191743Z.json`
  - `judge/judge_model.py`
- **22:24** — `docs/eval-runs/20260805T192420Z.json`
- **23:04–23:08** — 4 files:
  - `docs/eval-runs/20260805T200444Z.json`
  - `backend/engine/inference.py`
  - `backend/tests/test_inference.py`
  - `judge/README.md`
- **23:14** — `judge/run_eval.py`

## 2026-08-06

- **01:08–01:12** — 3 files:
  - `backend/requirements.txt`
  - `backend/Dockerfile`
  - `docker-compose.yml`
- **01:22** — `backend/alembic/versions/0005_turn_ratings.py`
- **01:27** — `backend/tests/test_engine_step_continue.py`
- **03:32** — `backend/engine/engine.py`
- **03:37** — `backend/tests/test_engine_regenerate.py`
- **16:13–16:14** — 2 files:
  - `imagepipe/visual_profile.py`
  - `imagepipe/requirements.txt`
- **16:31** — `imagepipe/run_experiment.py`

## 2026-08-07

- **01:24–01:27** — 4 files:
  - `backend/alembic/versions/0006_visual_profile.py`
  - `backend/storage.py`
  - `imagepipe/stitch.py`
  - `SESSION_ADVICE.txt`
- **14:41** — 2 files:
  - `imagepipe/sd_pipeline.py`
  - `imagepipe/caption_model.py`
- **14:54** — `imagepipe/similarity.py`
- **17:46** — `imagepipe/correlate_results.py`
- **22:08** — `imagepipe/run_session_consistency.py`
- **23:34** — `imagepipe/canonicalize_profile.py`

## 2026-08-08

- **00:42** — `SESSION_SUMMARY.md`
- **16:08–16:09** — 2 files:
  - `mario_george_ml_cv_updated.docx`
  - `mario_george_backend_cv_updated.docx`

## 2026-08-09

- **16:28–16:31** — 3 files:
  - `imagepipe/run_incremental_build.py`
  - `imagepipe/run_background_test.py`
  - `imagepipe/multi_subject_profiles.py`

## 2026-08-12

- **12:59** — `imagepipe/attribute_check.py`
- **15:04** — `imagepipe/run_inpaint_correction.py`

## 2026-08-13

- **03:19** — `imagepipe/sd_inpaint.py`
- **03:28** — `imagepipe/face_detect.py`

## 2026-08-14

- **17:03** — `frontend/app/vite.config.js`
- **17:12** — `imagepipe/narration.py`
- **18:00–18:01** — 2 files:
  - `backend/rate_limiter.py`
  - `backend/tests/test_rate_limiter.py`
- **18:21–18:22** — 2 files:
  - `backend/user_rate_limit.py`
  - `backend/tests/test_user_rate_limit.py`
- **18:35** — `backend/refresh_tokens.py`
- **18:41** — `backend/tests/test_refresh_tokens.py`
- **18:57–18:58** — 2 files:
  - `scripts/backup_db.sh`
  - `scripts/restore_db.sh`
- **19:04** — `backend/error_monitoring.py`
- **19:13** — `backend/tests/test_error_monitoring.py`
- **19:30** — `frontend/app/src/AuthContext.jsx`

## 2026-08-15

- **13:15–13:18** — 2 files:
  - `backend/engine/visual_prompts.py`
  - `backend/tests/test_visual_prompts.py`
- **13:23–13:24** — 3 files:
  - `backend/engine/voice_prompts.py`
  - `backend/engine/types.py`
  - `backend/tests/test_voice_prompts.py`
- **13:37** — `backend/alembic/versions/0007_turn_media.py`
- **13:53** — `scripts/test_groq_direct.py`
- **14:35–14:36** — 2 files:
  - `backend/alembic/versions/0008_mock_billing.py`
  - `backend/config.py`
- **15:12** — `backend/ai_service.py`
- **15:16** — `backend/tests/test_rate_limiter_wiring.py`
- **15:29–15:33** — 4 files:
  - `backend/models.py`
  - `backend/alembic/versions/0009_user_is_admin.py`
  - `backend/auth.py`
  - `backend/main.py`
- **15:36–15:40** — 4 files:
  - `backend/tests/test_admin_telemetry.py`
  - `backend/schemas.py`
  - `frontend/app/src/api.js`
  - `frontend/app/src/App.jsx`
- **15:46–15:47** — 2 files:
  - `docs/BUILD_TIMELINE.md`
  - `scripts/build_timeline.py`
