#!/usr/bin/env python3
"""Tests for the remote GPU worker control plane, its API and its agent.

Covers the properties the architecture actually rests on: a job waits instead
of failing while the PC is off, an attempt is spent only when a worker really
takes the work, a worker that vanishes mid-render loses the job rather than
keeping it, a zombie worker cannot overwrite state after its lease is gone,
and an uploaded asset is only published once its bytes have been hashed on
the receiving side.

Nothing here needs a GPU, the workstation, or the network beyond loopback.
The agent is exercised end to end against a real control-plane HTTP server
and the ComfyUI stand-in from ``tests.test_generation``, so the wire protocol
is tested rather than mocked.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generation  # noqa: E402
import worker  # noqa: E402
import worker_agent  # noqa: E402
import worker_api  # noqa: E402
from test_generation import FakeComfyHandler  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES"


class SlowComfyHandler(FakeComfyHandler):
    """ComfyUI that takes a few polls to finish, so progress ticks happen.

    The stand-in in ``test_generation`` answers on the first poll, which is
    right for testing the protocol but means a callback-driven behaviour
    (lease renewal, heartbeat) never gets a second tick to exercise.
    """

    polls_before_finishing = 3
    polls = 0

    def do_GET(self):
        if self.path.startswith("/history/"):
            type(self).polls += 1
            if type(self).polls < type(self).polls_before_finishing:
                self._json(200, {})
                return
        super().do_GET()


class WorkerTestCase(unittest.TestCase):
    """Redirects the whole jobs tree, including the completed-job store."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-worker-"))
        self._jobs_dir = generation.JOBS_DIR
        self._provider_state = generation.PROVIDER_STATE_PATH
        generation.JOBS_DIR = self.tmp / "jobs"
        generation.PROVIDER_STATE_PATH = generation.JOBS_DIR / "_provider_state.json"
        self.out_dir = ROOT / "jobs" / "_test_out" / self.id().rsplit(".", 1)[-1]
        self.addCleanup(shutil.rmtree, ROOT / "jobs" / "_test_out", True)

    def tearDown(self):
        generation.JOBS_DIR = self._jobs_dir
        generation.PROVIDER_STATE_PATH = self._provider_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---------------------------------------------------------

    def enroll(self, worker_id="home-gpu-01", capabilities=("comfyui", "sd15")):
        record, token = worker.enroll(worker_id, capabilities)
        return record, token

    def online(self, worker_id="home-gpu-01"):
        return worker.heartbeat(worker_id, {"comfyui": "reachable"})

    def request(self, prompt="a lit window at night"):
        return generation.GenerationRequest(prompt=prompt, count=1,
                                            require_depicted=True)

    def enqueue(self, prompt="a lit window at night", **kwargs):
        return worker.enqueue(self.request(prompt), self.out_dir, **kwargs)

    def take(self, worker_id="home-gpu-01"):
        """Claim and walk to UPLOADING, returning (job_id, lease_id)."""
        job, _ = worker.claim(worker_id)
        lease = job["lease"]["lease_id"]
        worker.report_progress(job["job_id"], worker_id, lease, worker.SUBMITTED)
        worker.report_progress(job["job_id"], worker_id, lease, worker.RUNNING)
        worker.report_progress(job["job_id"], worker_id, lease, worker.UPLOADING)
        return job["job_id"], lease

    def upload(self, job_id, lease, name="ComfyUI_00001_.png", data=PNG,
               worker_id="home-gpu-01"):
        digest = hashlib.sha256(data).hexdigest()
        worker.stage_asset(job_id, worker_id, lease, name, data, digest)
        return {"filename": name, "sha256": digest, "bytes": len(data)}


# --- Authentication --------------------------------------------------------

class AuthenticationTests(WorkerTestCase):

    def test_enroll_stores_only_a_hash_of_the_token(self):
        record, token = self.enroll()
        stored = json.loads((worker.workers_dir() / "home-gpu-01.json").read_text())
        self.assertNotIn(token, json.dumps(stored))
        self.assertEqual(stored["token_sha256"],
                         hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(record["capabilities"], ["comfyui", "sd15"])

    def test_a_valid_token_authenticates_and_identity_comes_from_it(self):
        _, token = self.enroll()
        self.assertEqual(worker.authenticate(token)["worker_id"], "home-gpu-01")

    def test_an_unknown_token_is_refused(self):
        self.enroll()
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.authenticate("not-a-real-token")
        self.assertEqual(ctx.exception.status, 401)

    def test_a_missing_token_is_refused(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.authenticate("")
        self.assertEqual(ctx.exception.status, 401)

    def test_a_revoked_worker_is_refused_even_with_a_valid_token(self):
        _, token = self.enroll()
        worker.set_revoked("home-gpu-01")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.authenticate(token)
        self.assertEqual(ctx.exception.status, 403)

    def test_re_enrolling_without_rotate_is_refused(self):
        self.enroll()
        with self.assertRaises(worker.WorkerError):
            worker.enroll("home-gpu-01", ("comfyui",))

    def test_rotating_invalidates_the_previous_token(self):
        _, first = self.enroll()
        _, second = worker.enroll("home-gpu-01", ("comfyui", "sd15"), rotate=True)
        self.assertNotEqual(first, second)
        self.assertEqual(worker.authenticate(second)["worker_id"], "home-gpu-01")
        with self.assertRaises(worker.WorkerError):
            worker.authenticate(first)

    def test_a_worker_id_must_be_a_safe_identifier(self):
        for bad in ("../escape", "Home-GPU", "a", "x" * 65, ""):
            with self.assertRaises(worker.WorkerError, msg=bad):
                worker.enroll(bad, ("comfyui",))

    def test_a_worker_must_declare_a_capability(self):
        with self.assertRaises(worker.WorkerError):
            worker.enroll("home-gpu-02", ())


# --- Worker liveness -------------------------------------------------------

class WorkerStateTests(WorkerTestCase):

    def test_a_worker_that_never_heartbeat_is_offline(self):
        record, _ = self.enroll()
        self.assertEqual(worker.worker_state(record), worker.OFFLINE)

    def test_state_is_derived_from_heartbeat_age(self):
        self.enroll()
        record = self.online()
        now = record["last_heartbeat_epoch"]
        self.assertEqual(worker.worker_state(record, now), worker.ONLINE)
        self.assertEqual(
            worker.worker_state(record, now + worker.DEFAULT_ONLINE_SECONDS + 1),
            worker.STALE)
        self.assertEqual(
            worker.worker_state(record, now + worker.DEFAULT_STALE_SECONDS + 1),
            worker.OFFLINE)

    def test_liveness_is_never_stored_so_a_crash_cannot_leave_it_online(self):
        self.enroll()
        record = self.online()
        self.assertNotIn("state", record)
        stored = json.loads((worker.workers_dir() / "home-gpu-01.json").read_text())
        self.assertNotIn("state", stored)

    def test_a_revoked_worker_is_offline_however_recently_it_spoke(self):
        self.enroll()
        self.online()
        worker.set_revoked("home-gpu-01")
        record = json.loads((worker.workers_dir() / "home-gpu-01.json").read_text())
        self.assertEqual(worker.worker_state(record), worker.OFFLINE)

    def test_heartbeat_records_reported_status(self):
        self.enroll()
        record = worker.heartbeat("home-gpu-01",
                                  {"comfyui": "reachable", "secret": "leak"})
        self.assertEqual(record["status"]["comfyui"], "reachable")
        # Only known keys are kept; a worker cannot write arbitrary fields.
        self.assertNotIn("secret", record["status"])

    def test_heartbeat_from_an_unknown_worker_is_refused(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.heartbeat("ghost-01")
        self.assertEqual(ctx.exception.status, 404)


# --- Queueing while the PC is off -----------------------------------------

class OfflineQueueTests(WorkerTestCase):
    """The headline requirement: work waits, it does not fail."""

    def test_a_job_queued_with_no_worker_at_all_waits(self):
        job = self.enqueue()
        self.assertEqual(job["state"], worker.QUEUED)
        self.assertEqual(worker.wait_reason(job), worker.WAITING_FOR_CAPABLE_WORKER)
        self.assertEqual(job["attempt"], 0)

    def test_a_job_waits_while_its_only_worker_is_offline(self):
        self.enroll()
        job = self.enqueue()
        self.assertEqual(worker.wait_reason(job), worker.WAITING_FOR_CAPABLE_WORKER)

    def test_a_stale_worker_does_not_count_as_capable(self):
        self.enroll()
        self.online()
        job = self.enqueue()
        later = time.time() + worker.DEFAULT_ONLINE_SECONDS + 1
        self.assertEqual(worker.wait_reason(job, now=later),
                         worker.WAITING_FOR_CAPABLE_WORKER)

    def test_an_online_capable_worker_makes_the_job_ready(self):
        self.enroll()
        self.online()
        job = self.enqueue()
        self.assertEqual(worker.wait_reason(job), worker.READY_TO_CLAIM)

    def test_waiting_consumes_no_attempts_and_reaping_never_fails_it(self):
        self.enroll()
        job = self.enqueue()
        for _ in range(5):
            worker.reap()
        reloaded = worker.load_job(job["job_id"])
        self.assertEqual(reloaded["state"], worker.QUEUED)
        self.assertEqual(reloaded["attempt"], 0)

    def test_a_worker_without_the_capability_is_not_offered_the_job(self):
        worker.enroll("cpu-only-01", ("procedural",))
        worker.heartbeat("cpu-only-01")
        job = self.enqueue()
        claimed, reason = worker.claim("cpu-only-01")
        self.assertIsNone(claimed)
        self.assertEqual(reason, "NO_CAPABLE_JOB")
        self.assertEqual(worker.load_job(job["job_id"])["attempt"], 0)

    def test_enqueue_is_idempotent_on_the_request_digest(self):
        first = self.enqueue()
        second = self.enqueue()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(worker.load_jobs()), 1)

    def test_the_job_id_is_the_generation_idempotency_key(self):
        job = self.enqueue()
        self.assertEqual(job["job_id"], self.request().digest())

    def test_an_already_generated_request_is_not_queued_again(self):
        request = self.request()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        asset = self.out_dir / "gen_existing_01.png"
        asset.write_bytes(PNG)
        generation.save_job({"job_id": request.digest(),
                             "status": generation.COMPLETED,
                             "assets": [str(asset)]})
        with self.assertRaises(worker.WorkerError):
            worker.enqueue(request, self.out_dir)

    def test_an_out_dir_outside_the_repository_is_refused(self):
        with self.assertRaises(worker.WorkerError):
            worker.enqueue(self.request(), Path("/tmp/somewhere-else"))


# --- Claiming and leases ---------------------------------------------------

class ClaimAndLeaseTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()

    def test_claiming_leases_the_job_and_spends_one_attempt(self):
        self.enqueue()
        job, reason = worker.claim("home-gpu-01")
        self.assertEqual(reason, "CLAIMED")
        self.assertEqual(job["state"], worker.CLAIMED)
        self.assertEqual(job["attempt"], 1)
        self.assertEqual(job["lease"]["worker_id"], "home-gpu-01")
        self.assertGreater(job["lease"]["expires_at_epoch"], time.time())

    def test_a_leased_job_is_not_offered_to_a_second_worker(self):
        self.enqueue()
        worker.claim("home-gpu-01")
        worker.enroll("home-gpu-02", ("comfyui", "sd15"))
        worker.heartbeat("home-gpu-02")
        second, reason = worker.claim("home-gpu-02")
        self.assertIsNone(second)
        self.assertEqual(reason, "NO_JOB_READY")

    def test_the_oldest_ready_job_goes_first(self):
        first = self.enqueue("first prompt")
        second = self.enqueue("second prompt")
        self.assertNotEqual(first["job_id"], second["job_id"])
        taken, _ = worker.claim("home-gpu-01")
        self.assertEqual(taken["job_id"], first["job_id"])

    def test_a_revoked_worker_cannot_claim(self):
        self.enqueue()
        worker.set_revoked("home-gpu-01")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.claim("home-gpu-01")
        self.assertEqual(ctx.exception.status, 403)

    def test_progress_needs_the_current_lease_id(self):
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.report_progress(job["job_id"], "home-gpu-01", "wrong-lease",
                                   worker.RUNNING)
        self.assertEqual(ctx.exception.status, 409)

    def test_another_worker_cannot_report_on_a_job_it_does_not_hold(self):
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        worker.enroll("home-gpu-02", ("comfyui",))
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.report_progress(job["job_id"], "home-gpu-02",
                                   job["lease"]["lease_id"], worker.RUNNING)
        self.assertEqual(ctx.exception.status, 409)

    def test_progress_renews_the_lease(self):
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        before = job["lease"]["expires_at_epoch"]
        time.sleep(0.01)
        renewed = worker.report_progress(job["job_id"], "home-gpu-01",
                                         job["lease"]["lease_id"])
        self.assertGreater(renewed["lease"]["expires_at_epoch"], before)

    def test_a_heartbeat_does_not_renew_a_lease(self):
        """Liveness is not progress: a hung render must still be reaped."""
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        before = job["lease"]["expires_at_epoch"]
        self.online()
        self.assertEqual(
            worker.load_job(job["job_id"])["lease"]["expires_at_epoch"], before)

    def test_re_reporting_the_same_state_renews_without_an_audit_entry(self):
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        lease = job["lease"]["lease_id"]
        worker.report_progress(job["job_id"], "home-gpu-01", lease, worker.RUNNING)
        count = len(worker.load_job(job["job_id"])["transitions"])
        worker.report_progress(job["job_id"], "home-gpu-01", lease, worker.RUNNING)
        self.assertEqual(len(worker.load_job(job["job_id"])["transitions"]), count)

    def test_a_worker_cannot_declare_its_own_job_succeeded(self):
        self.enqueue()
        job, _ = worker.claim("home-gpu-01")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.report_progress(job["job_id"], "home-gpu-01",
                                   job["lease"]["lease_id"], worker.SUCCEEDED)
        self.assertEqual(ctx.exception.status, 400)


# --- Lease expiry ----------------------------------------------------------

class LeaseExpiryTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()
        self.enqueue()

    def _expire(self, job_id):
        """Force the lease into the past, as a dead PC would."""
        job = worker.load_job(job_id)
        job["lease"]["expires_at_epoch"] = time.time() - 1
        worker._save_job(job)
        return job

    def test_an_expired_lease_defers_the_job_for_retry(self):
        job, _ = worker.claim("home-gpu-01")
        self._expire(job["job_id"])
        worker.reap()
        reaped = worker.load_job(job["job_id"])
        self.assertEqual(reaped["state"], worker.RETRY_WAIT)
        self.assertIsNone(reaped["lease"])
        self.assertEqual(reaped["attempt"], 1)
        self.assertIn("expired", reaped["transitions"][-1]["detail"])

    def test_the_retry_backoff_then_returns_it_to_the_queue(self):
        job, _ = worker.claim("home-gpu-01")
        self._expire(job["job_id"])
        worker.reap()
        deferred = worker.load_job(job["job_id"])
        self.assertEqual(worker.wait_reason(deferred).split()[0],
                         worker.RETRY_BACKOFF)
        worker.reap(now=deferred["not_before_epoch"] + 1)
        self.assertEqual(worker.load_job(job["job_id"])["state"], worker.QUEUED)

    def test_a_reaped_job_can_be_claimed_again_by_a_fresh_lease(self):
        first, _ = worker.claim("home-gpu-01")
        self._expire(first["job_id"])
        worker.reap()
        worker.reap(now=worker.load_job(first["job_id"])["not_before_epoch"] + 1)
        second, _ = worker.claim("home-gpu-01")
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(second["attempt"], 2)
        self.assertNotEqual(second["lease"]["lease_id"],
                            first["lease"]["lease_id"])

    def test_the_old_lease_cannot_write_after_the_job_is_handed_on(self):
        """A zombie worker must not be able to corrupt work in progress."""
        first, _ = worker.claim("home-gpu-01")
        stale_lease = first["lease"]["lease_id"]
        self._expire(first["job_id"])
        worker.reap()
        worker.reap(now=worker.load_job(first["job_id"])["not_before_epoch"] + 1)
        worker.claim("home-gpu-01")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.report_progress(first["job_id"], "home-gpu-01", stale_lease,
                                   worker.RUNNING)
        self.assertEqual(ctx.exception.status, 409)

    def test_an_expired_but_unreaped_lease_is_still_refused(self):
        job, _ = worker.claim("home-gpu-01")
        self._expire(job["job_id"])
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.report_progress(job["job_id"], "home-gpu-01",
                                   job["lease"]["lease_id"], worker.RUNNING)
        self.assertIn("expired", str(ctx.exception))

    def test_repeated_abandonment_eventually_fails_rather_than_looping(self):
        job_id = None
        for expected in range(1, 4):
            job, _ = worker.claim("home-gpu-01")
            job_id = job["job_id"]
            self.assertEqual(job["attempt"], expected)
            self._expire(job_id)
            worker.reap()
            state = worker.load_job(job_id)
            if state["state"] == worker.RETRY_WAIT:
                worker.reap(now=state["not_before_epoch"] + 1)
        final = worker.load_job(job_id)
        self.assertEqual(final["state"], worker.FAILED)
        self.assertEqual(final["attempt"], 3)
        self.assertIsNone(final["lease"])


# --- Failure and retry -----------------------------------------------------

class FailureTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()
        self.enqueue()

    def test_a_reported_failure_with_attempts_left_defers(self):
        job, _ = worker.claim("home-gpu-01")
        failed = worker.report_failure(job["job_id"], "home-gpu-01",
                                       job["lease"]["lease_id"], "CUDA OOM")
        self.assertEqual(failed["state"], worker.RETRY_WAIT)
        self.assertIn("CUDA OOM", failed["error"])
        self.assertGreater(failed["not_before_epoch"], time.time())

    def test_a_permanent_failure_fails_immediately(self):
        """A missing checkpoint will still be missing in five minutes."""
        job, _ = worker.claim("home-gpu-01")
        failed = worker.report_failure(
            job["job_id"], "home-gpu-01", job["lease"]["lease_id"],
            "workflow template not found", permanent=True)
        self.assertEqual(failed["state"], worker.FAILED)
        self.assertEqual(failed["attempt"], 1)

    def test_the_last_attempt_fails_rather_than_deferring(self):
        job_id = None
        for _ in range(3):
            job, _ = worker.claim("home-gpu-01")
            job_id = job["job_id"]
            worker.report_failure(job_id, "home-gpu-01",
                                  job["lease"]["lease_id"], "boom")
            state = worker.load_job(job_id)
            if state["state"] == worker.RETRY_WAIT:
                worker.reap(now=state["not_before_epoch"] + 1)
        self.assertEqual(worker.load_job(job_id)["state"], worker.FAILED)

    def test_backoff_grows_with_each_attempt(self):
        self.assertLess(worker._backoff_seconds(1), worker._backoff_seconds(2))
        self.assertLessEqual(worker._backoff_seconds(50),
                             worker.DEFAULT_BACKOFF_CAP)

    def test_a_failed_job_is_terminal(self):
        job, _ = worker.claim("home-gpu-01")
        worker.report_failure(job["job_id"], "home-gpu-01",
                              job["lease"]["lease_id"], "fatal", permanent=True)
        reloaded = worker.load_job(job["job_id"])
        with self.assertRaises(worker.WorkerError):
            worker._transition(reloaded, worker.QUEUED, "test")


# --- State machine and audit ----------------------------------------------

class StateMachineTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()
        self.enqueue()

    def test_the_happy_path_walks_every_state_in_order(self):
        job_id, lease = self.take()
        entry = self.upload(job_id, lease)
        worker.complete(job_id, "home-gpu-01", lease, [entry],
                        provider_job_id="prompt-1", model="v1-5.safetensors")
        job = worker.load_job(job_id)
        self.assertEqual(job["state"], worker.SUCCEEDED)
        self.assertEqual(
            [t["to"] for t in job["transitions"]],
            [worker.QUEUED, worker.CLAIMED, worker.SUBMITTED, worker.RUNNING,
             worker.UPLOADING, worker.SUCCEEDED])

    def test_every_transition_records_who_and_when(self):
        job_id, _ = self.take()
        for entry in worker.load_job(job_id)["transitions"]:
            self.assertIn("at", entry)
            self.assertIn("actor", entry)
        actors = {t["actor"] for t in worker.load_job(job_id)["transitions"]}
        self.assertIn("operator", actors)
        self.assertIn("worker:home-gpu-01", actors)

    def test_an_illegal_transition_is_refused(self):
        job = worker.load_job(self.request().digest())
        with self.assertRaises(worker.WorkerError) as ctx:
            worker._transition(job, worker.SUCCEEDED, "test")
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("illegal transition", str(ctx.exception))

    def test_a_succeeded_job_is_terminal(self):
        job_id, lease = self.take()
        worker.complete(job_id, "home-gpu-01", lease,
                        [self.upload(job_id, lease)])
        with self.assertRaises(worker.WorkerError):
            worker.cancel(job_id)

    def test_cancelling_a_queued_job_is_terminal(self):
        job = worker.cancel(self.request().digest(), reason="operator changed mind")
        self.assertEqual(job["state"], worker.CANCELLED)
        self.assertEqual(job["transitions"][-1]["actor"], "operator")
        self.assertIn("changed mind", job["transitions"][-1]["detail"])

    def test_cancelling_a_running_job_releases_its_lease(self):
        job_id, lease = self.take()
        cancelled = worker.cancel(job_id)
        self.assertEqual(cancelled["state"], worker.CANCELLED)
        self.assertIsNone(cancelled["lease"])
        with self.assertRaises(worker.WorkerError):
            worker.report_progress(job_id, "home-gpu-01", lease, worker.RUNNING)

    def test_every_lifecycle_state_the_milestone_names_exists(self):
        named = {"QUEUED", "CLAIMED", "SUBMITTED", "RUNNING", "UPLOADING",
                 "SUCCEEDED", "RETRY_WAIT", "FAILED", "CANCELLED"}
        self.assertEqual(named, set(worker.TRANSITIONS))


# --- Upload and manifest ---------------------------------------------------

class UploadTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()
        self.enqueue()
        self.job_id, self.lease = self.take()

    def test_a_mismatched_digest_is_refused_and_nothing_is_written(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.stage_asset(self.job_id, "home-gpu-01", self.lease,
                               "a.png", PNG, "0" * 64)
        self.assertEqual(ctx.exception.status, 422)
        self.assertFalse((worker.uploads_dir() / self.job_id / "a.png").exists())

    def test_an_oversized_asset_is_refused(self):
        limit = worker.max_asset_bytes()
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.stage_asset(self.job_id, "home-gpu-01", self.lease,
                               "a.png", b"x" * (limit + 1))
        self.assertEqual(ctx.exception.status, 413)

    def test_an_empty_asset_is_refused(self):
        with self.assertRaises(worker.WorkerError):
            worker.stage_asset(self.job_id, "home-gpu-01", self.lease,
                               "a.png", b"")

    def test_a_traversing_filename_cannot_escape_the_staging_area(self):
        for name in ("../../evil.png", "/etc/passwd.png", "sub/dir.png",
                     ".hidden.png"):
            with self.assertRaises(worker.WorkerError, msg=name):
                worker.stage_asset(self.job_id, "home-gpu-01", self.lease,
                                   name, PNG)

    def test_a_non_image_extension_is_refused(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.stage_asset(self.job_id, "home-gpu-01", self.lease,
                               "payload.sh", PNG)
        self.assertEqual(ctx.exception.status, 415)

    def test_an_upload_without_the_lease_is_refused(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.stage_asset(self.job_id, "home-gpu-01", "wrong", "a.png", PNG)
        self.assertEqual(ctx.exception.status, 409)

    def test_a_manifest_entry_that_was_never_uploaded_is_refused(self):
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.complete(self.job_id, "home-gpu-01", self.lease,
                            [{"filename": "ghost.png", "sha256": "0" * 64,
                              "bytes": 1}])
        self.assertEqual(ctx.exception.status, 422)

    def test_a_staged_file_absent_from_the_manifest_is_refused(self):
        self.upload(self.job_id, self.lease, "one.png")
        self.upload(self.job_id, self.lease, "two.png", PNG + b"2")
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.complete(self.job_id, "home-gpu-01", self.lease,
                            [{"filename": "one.png",
                              "sha256": hashlib.sha256(PNG).hexdigest(),
                              "bytes": len(PNG)}])
        self.assertIn("absent from the manifest", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 422)

    def test_a_manifest_hash_is_reverified_against_the_staged_bytes(self):
        entry = self.upload(self.job_id, self.lease)
        entry["sha256"] = "0" * 64
        with self.assertRaises(worker.WorkerError) as ctx:
            worker.complete(self.job_id, "home-gpu-01", self.lease, [entry])
        self.assertIn("does not match the staged file", str(ctx.exception))

    def test_an_empty_manifest_is_refused(self):
        self.upload(self.job_id, self.lease)
        with self.assertRaises(worker.WorkerError):
            worker.complete(self.job_id, "home-gpu-01", self.lease, [])

    def test_a_duplicated_manifest_entry_is_refused(self):
        entry = self.upload(self.job_id, self.lease)
        with self.assertRaises(worker.WorkerError):
            worker.complete(self.job_id, "home-gpu-01", self.lease,
                            [entry, dict(entry)])

    def test_completion_publishes_the_assets_with_verified_hashes(self):
        entry = self.upload(self.job_id, self.lease)
        job = worker.complete(self.job_id, "home-gpu-01", self.lease, [entry])
        self.assertEqual(len(job["assets"]), 1)
        published = Path(job["assets"][0])
        self.assertTrue(published.is_file())
        self.assertEqual(published.parent, self.out_dir)
        self.assertEqual(published.name, f"gen_{self.job_id[:8]}_01.png")
        self.assertEqual(hashlib.sha256(published.read_bytes()).hexdigest(),
                         job["manifest"][0]["sha256"])

    def test_the_staging_area_is_cleared_after_completion(self):
        entry = self.upload(self.job_id, self.lease)
        worker.complete(self.job_id, "home-gpu-01", self.lease, [entry])
        self.assertFalse((worker.uploads_dir() / self.job_id).exists())


# --- Integration with the generation job store ----------------------------

class GenerationHandoffTests(WorkerTestCase):

    def setUp(self):
        super().setUp()
        self.enroll()
        self.online()
        self.enqueue()

    def _succeed(self):
        job_id, lease = self.take()
        entry = self.upload(job_id, lease)
        return worker.complete(job_id, "home-gpu-01", lease, [entry],
                               provider_job_id="prompt-9",
                               model="v1-5-pruned-emaonly.safetensors")

    def test_success_writes_a_completed_generation_job(self):
        job = self._succeed()
        record = generation.load_job(job["job_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], generation.COMPLETED)
        self.assertEqual(record["provider"], "comfyui")
        self.assertEqual(record["worker_id"], "home-gpu-01")
        self.assertTrue(record["produces_depicted"])
        self.assertEqual(record["cost_usd"], 0.0)
        self.assertEqual(record["assets"], job["assets"])

    def test_the_router_reuses_the_remote_result_instead_of_regenerating(self):
        """The whole point of the digest being the job id."""
        self._succeed()
        provider = _CountingProvider()
        router = generation.Router(providers={"counting": provider},
                                   order=("counting",))
        reused = router.generate(self.request(), self.out_dir)
        self.assertTrue(reused.get("reused"))
        self.assertEqual(provider.calls, 0)

    def test_the_manifest_survives_into_the_generation_record(self):
        job = self._succeed()
        record = generation.load_job(job["job_id"])
        self.assertEqual(record["manifest"], job["manifest"])
        self.assertEqual(record["worker_job"]["attempt"], 1)
        self.assertTrue(record["worker_job"]["transitions"])

    def test_no_worker_path_ever_claims_production_grade(self):
        """The non-negotiable: only a human may make that claim."""
        job = self._succeed()
        record = generation.load_job(job["job_id"])
        for payload in (job, record):
            self.assertNotIn("production_grade", _all_keys(payload))


def _all_keys(payload):
    """Every key anywhere in a nested structure."""
    keys = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= _all_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= _all_keys(item)
    return keys


class _CountingProvider(generation.Provider):
    name = "counting"
    produces_depicted = True

    def __init__(self):
        self.calls = 0

    def configured(self):
        return True

    def health(self, timeout=None):
        return True, "up"

    def generate(self, request, out_dir, timeout=None):
        self.calls += 1
        raise generation.GenerationError("should never be reached")


# --- HTTP API --------------------------------------------------------------

class ApiTestCase(WorkerTestCase):
    """The real handler over a real socket, on loopback."""

    def setUp(self):
        super().setUp()
        _, self.token = self.enroll()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          worker_api.ControlPlaneHandler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, payload=None, token=None, method="POST"):
        import urllib.error
        import urllib.request
        data = json.dumps(payload or {}).encode() if method == "POST" else None
        headers = {"Content-Type": "application/json"}
        chosen = self.token if token is None else token
        if chosen:
            headers["Authorization"] = f"Bearer {chosen}"
        request = urllib.request.Request(f"{self.url}{path}", data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


class ApiTests(ApiTestCase):

    def test_ping_needs_no_token(self):
        status, body = self.call("/v1/ping", token="", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_every_other_route_needs_a_token(self):
        status, body = self.call("/v1/claim", token="")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_a_wrong_token_is_refused(self):
        status, _ = self.call("/v1/claim", token="nope")
        self.assertEqual(status, 401)

    def test_heartbeat_returns_derived_liveness_and_the_poll_cadence(self):
        status, body = self.call("/v1/heartbeat",
                                 {"status": {"comfyui": "reachable"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["worker"]["state"], worker.ONLINE)
        self.assertGreater(body["poll_seconds"], 0)

    def test_claiming_with_an_empty_queue_says_why(self):
        status, body = self.call("/v1/claim")
        self.assertEqual(status, 200)
        self.assertIsNone(body["job"])
        self.assertEqual(body["reason"], "NO_JOB_READY")

    def test_a_claim_hands_over_a_lease_and_the_request(self):
        self.enqueue()
        status, body = self.call("/v1/claim")
        self.assertEqual(status, 200)
        self.assertEqual(body["job"]["request"]["prompt"], "a lit window at night")
        self.assertTrue(body["job"]["lease_id"])

    def test_a_missing_lease_id_is_refused(self):
        self.enqueue()
        _, body = self.call("/v1/claim")
        status, _ = self.call(f"/v1/jobs/{body['job']['job_id']}/progress",
                              {"state": worker.RUNNING})
        self.assertEqual(status, 400)

    def test_an_illegal_state_report_is_refused_over_http(self):
        self.enqueue()
        _, body = self.call("/v1/claim")
        job = body["job"]
        status, _ = self.call(f"/v1/jobs/{job['job_id']}/progress",
                              {"lease_id": job["lease_id"],
                               "state": worker.SUCCEEDED})
        self.assertEqual(status, 400)

    def test_an_unknown_route_is_a_404(self):
        status, _ = self.call("/v1/nope")
        self.assertEqual(status, 404)

    def test_an_unknown_job_is_a_404(self):
        status, _ = self.call("/v1/jobs/abcdef01/progress",
                              {"lease_id": "x", "state": worker.RUNNING})
        self.assertEqual(status, 404)

    def test_a_malformed_job_id_is_rejected_not_resolved(self):
        status, _ = self.call("/v1/jobs/..%2f..%2fetc/progress",
                              {"lease_id": "x"})
        self.assertIn(status, (400, 404))


# --- The agent, end to end -------------------------------------------------

class AgentTests(ApiTestCase):
    """Agent -> control plane -> ComfyUI stand-in, over real sockets."""

    def setUp(self):
        super().setUp()
        FakeComfyHandler.behaviour["mode"] = "ok"
        self.comfy = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfyHandler)
        self.comfy_thread = threading.Thread(target=self.comfy.serve_forever,
                                             daemon=True)
        self.comfy_thread.start()
        self.addCleanup(self._stop_comfy)
        self.agent = worker_agent.Agent(
            client=worker_agent.ControlPlaneClient(self.url, self.token),
            provider=generation.ComfyUIProvider(
                url=f"http://127.0.0.1:{self.comfy.server_port}",
                workflow_path=str(ROOT / "config" / "comfyui_workflow.example.json")))

    def _stop_comfy(self):
        self.comfy.shutdown()
        self.comfy.server_close()

    def _use_slow_comfyui(self):
        """Swap in a ComfyUI that needs several polls before it answers."""
        self._stop_comfy()
        SlowComfyHandler.polls = 0
        self.comfy = ThreadingHTTPServer(("127.0.0.1", 0), SlowComfyHandler)
        threading.Thread(target=self.comfy.serve_forever, daemon=True).start()
        self.agent.provider.url = f"http://127.0.0.1:{self.comfy.server_port}"

    def test_the_agent_refuses_to_start_without_a_control_plane_url(self):
        with self.assertRaises(worker_agent.AgentError):
            worker_agent.ControlPlaneClient(base_url="", token="x")

    def test_the_agent_refuses_to_start_without_a_token(self):
        with self.assertRaises(worker_agent.AgentError):
            worker_agent.ControlPlaneClient(base_url=self.url, token="")

    def test_an_idle_poll_reports_a_heartbeat_and_claims_nothing(self):
        self.assertIsNone(self.agent.poll_once())
        record = json.loads(
            (worker.workers_dir() / "home-gpu-01.json").read_text())
        self.assertEqual(worker.worker_state(record), worker.ONLINE)
        self.assertIn("reachable", record["status"]["comfyui"])

    def test_a_full_cycle_lands_a_verified_asset_in_the_project(self):
        job = self.enqueue()
        self.assertEqual(self.agent.poll_once(), worker.SUCCEEDED)
        finished = worker.load_job(job["job_id"])
        self.assertEqual(finished["state"], worker.SUCCEEDED)
        self.assertEqual(
            [t["to"] for t in finished["transitions"]],
            [worker.QUEUED, worker.CLAIMED, worker.SUBMITTED, worker.RUNNING,
             worker.UPLOADING, worker.SUCCEEDED])
        self.assertEqual(finished["provider_job_id"], "test-prompt-1")
        asset = Path(finished["assets"][0])
        self.assertTrue(asset.is_file())
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(),
                         finished["manifest"][0]["sha256"])
        self.assertEqual(generation.load_job(job["job_id"])["status"],
                         generation.COMPLETED)

    def test_a_render_failure_is_reported_and_the_job_is_deferred(self):
        FakeComfyHandler.behaviour["mode"] = "job_error"
        job = self.enqueue()
        self.assertEqual(self.agent.poll_once(), worker.RETRY_WAIT)
        deferred = worker.load_job(job["job_id"])
        self.assertEqual(deferred["state"], worker.RETRY_WAIT)
        self.assertEqual(deferred["attempt"], 1)
        self.assertIsNone(deferred["lease"])

    def test_a_config_fault_fails_permanently_without_burning_attempts(self):
        self.agent.provider.workflow_path = str(self.tmp / "missing.json")
        job = self.enqueue()
        self.assertEqual(self.agent.poll_once(), worker.FAILED)
        failed = worker.load_job(job["job_id"])
        self.assertEqual(failed["state"], worker.FAILED)
        self.assertEqual(failed["attempt"], 1)

    def test_a_long_render_keeps_the_worker_online_without_renewing_forever(self):
        """A render longer than the ONLINE window must not look STALE.

        Mid-render the agent never calls /claim, so liveness has to come from
        its own heartbeat - otherwise a working machine reports OFFLINE and
        the next queued job blames it for WAITING_FOR_CAPABLE_WORKER.
        """
        self.enqueue()
        self._use_slow_comfyui()
        # Shrink the ONLINE window on the control plane, not on the agent:
        # the cadence the agent obeys has to be the one it is handed.
        self.enterContext(unittest.mock.patch.dict(
            "os.environ", {"WORKER_ONLINE_SECONDS": "0.3"}))
        beats, renewals = [], []
        original_beat, original_progress = self.agent.heartbeat, self.agent._progress

        def record_beat(current_job=None):
            beats.append(current_job)
            return original_beat(current_job)

        def record_progress(job_id, lease_id, state, detail, **kwargs):
            renewals.append(state)
            return original_progress(job_id, lease_id, state, detail, **kwargs)

        self.agent.heartbeat = record_beat
        self.agent._progress = record_progress
        self.assertEqual(self.agent.poll_once(), worker.SUCCEEDED)
        self.assertTrue(beats, "no heartbeat was sent during the render")
        self.assertEqual(set(beats), {self.request().digest()})
        # Lease renewals stay on their own, slower clock.
        self.assertNotIn(None, renewals[:3])

    def test_the_control_plane_dictates_the_heartbeat_cadence(self):
        self.agent.heartbeat_seconds = 99999.0
        self.agent.poll_once()
        self.assertEqual(self.agent.heartbeat_seconds, worker.heartbeat_seconds())

    def test_the_agent_survives_a_control_plane_that_is_down(self):
        self._stop()
        agent = worker_agent.Agent(
            client=worker_agent.ControlPlaneClient(self.url, self.token),
            provider=self.agent.provider)
        self.assertEqual(worker_agent.run(once=True, agent=agent), 0)


if __name__ == "__main__":
    unittest.main()
