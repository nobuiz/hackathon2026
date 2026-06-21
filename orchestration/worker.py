"""
Orkes Conductor workers for the ReferralGuard agent pipeline.

Each agent step (intake, extraction, completeness, validation, denial-risk, decision,
routing, submission) is a Conductor task worker, so the workflow in
referralguard_workflow.json runs durably on Orkes — including the HUMAN approval gate
that pauses the workflow until an approver signs off. Without Orkes creds the demo runs
the same logic in-process via backend/agent_pipeline.py (same audit trail).

Run:
    export CONDUCTOR_SERVER_URL=https://<cluster>.orkesconductor.io/api
    export CONDUCTOR_AUTH_KEY=...      CONDUCTOR_AUTH_SECRET=...
    python worker.py

Execution model: conductor-python's TaskHandler launches each worker in its own OS
process; on macOS + Python 3.12+ those child processes can die silently (tasks then sit
in SCHEDULED forever). To stay reliable we poll the Conductor REST queues directly from
threads — same task queues, same @worker_task registration, no fragile multiprocessing.
"""
import os, sys, time, json, inspect, threading
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import agent_pipeline as ap
import submission_agent as sub_agent

# Required fields the completeness agent checks (flat keys produced by extraction).
REQUIRED_KEYS = ["patient_name", "dob", "diagnosis_code", "requested_cpt",
                 "payer", "member_id", "npi"]


def _steps_collector():
    steps = []
    return steps, (lambda who, badge, act, det: steps.append(
        {"who": who, "badge": badge, "act": act, "det": det}))


# ---- Agent task logic (plain functions; each maps 1:1 to a Conductor task) --------
def intake_agent(request: dict = None) -> dict:
    request = request or {}
    return {"raw": request.get("raw", ""), "request_id": request.get("id", "")}


def extraction_agent(raw: str = "", request: dict = None) -> dict:
    # Claude extracts typed fields when its key is set; otherwise derive them from the
    # already-structured request so the DAG runs end-to-end without Claude.
    if ap.CLAUDE_ON:
        try:
            return {"fields": ap.claude_extract(raw)}
        except Exception:
            pass
    request = request or {}
    ins = request.get("insurance") or {}
    pat = request.get("patient") or {}
    return {"fields": {
        "patient_name": pat.get("name"), "dob": pat.get("dob"),
        "diagnosis_code": request.get("diagnosis_code"),
        "requested_cpt": request.get("requested_cpt"),
        "procedure": request.get("procedure"), "payer": ins.get("payer"),
        "member_id": ins.get("member_id"), "npi": request.get("npi")}}


def completeness_agent(fields: dict = None) -> dict:
    fields = fields or {}
    missing = [k for k in REQUIRED_KEYS if not fields.get(k)]
    return {"missing": missing, "complete": len(missing) == 0}


def validation_agent(member_id: str = "") -> dict:
    ok = bool(ap.MEMBER_ID_RE.match(member_id or ""))
    if not ok:
        # Mirror the pipeline's Sentry-captured path on a malformed member ID.
        try:
            raise ap.MemberIdFormatError(f"invalid member id: {member_id}")
        except ap.MemberIdFormatError as e:
            ap.sentry_capture(e, {"member_id": member_id, "agent": "validation"})
    return {"valid": ok, "member_id": member_id}


def denial_risk_agent(fields: dict = None, raw: str = "") -> dict:
    cpt = (fields or {}).get("requested_cpt")
    rule = ap.PAYER_RULES.get(cpt)
    if not rule:
        return {"risk": "none"}
    met = ap.requirement_met((raw or "").lower(), rule["keywords"])
    return {"risk": "none" if met else "high", "reason": rule["msg"]}


def decision_agent(completeness: dict = None, validation: dict = None,
                   risk: dict = None) -> dict:
    completeness = completeness or {}
    validation = validation or {}
    risk = risk or {}
    flags = []
    for m in completeness.get("missing", []):
        flags.append({"sev": "warn", "ttl": "Missing: " + m,
                      "rsn": "Payer will reject or pend a submission without this field."})
    if not validation.get("valid", True):
        flags.append({"sev": "warn", "ttl": "Unreadable member ID",
                      "rsn": "Invalid member ID; flagged for correction before submit."})
    if risk.get("risk") == "high":
        flags.append({"sev": "risk", "ttl": "Likely denial: requirement not documented",
                      "rsn": risk.get("reason", "")})
    has_risk = any(f["sev"] == "risk" for f in flags)
    has_warn = any(f["sev"] == "warn" for f in flags)
    verdict = ("HIGH DENIAL RISK" if has_risk
               else "NEEDS INFO" if has_warn else "READY TO SUBMIT")
    return {"verdict": verdict, "flags": flags, "steps": []}


def route_human_review(flags: list = None) -> dict:
    return {"routed": True, "flags": flags or []}


def submission_agent(request: dict = None, approved_by: str = "") -> dict:
    steps, push = _steps_collector()
    return sub_agent.run_submission(request or {}, push)


# name -> function, the source of truth for both the SDK registration and the poller.
TASKS = {
    "intake_agent": intake_agent, "extraction_agent": extraction_agent,
    "completeness_agent": completeness_agent, "validation_agent": validation_agent,
    "denial_risk_agent": denial_risk_agent, "decision_agent": decision_agent,
    "route_human_review": route_human_review, "submission_agent": submission_agent,
}

# Best-effort: also register with the official SDK decorator (documents the task names
# in Orkes). Execution still goes through the REST poller below.
try:
    from conductor.client.worker.worker_task import worker_task
    for _name, _fn in TASKS.items():
        worker_task(task_definition_name=_name)(_fn)
except Exception:
    pass


# ---- Reliable threaded REST poller ------------------------------------------------
WORKER_ID = "referralguard-rest-worker"


class Conductor:
    # (connect, read) timeouts: short reads so a slow poll retries fast instead of
    # blocking a worker thread for 30s on a transient cloud latency spike.
    POLL_TIMEOUT = (5, 12)
    UPDATE_TIMEOUT = (5, 20)

    def __init__(self):
        self.base = os.environ["CONDUCTOR_SERVER_URL"].rstrip("/")
        self.key = os.environ["CONDUCTOR_AUTH_KEY"]
        self.secret = os.environ["CONDUCTOR_AUTH_SECRET"]
        self._tok = None
        self._tok_ts = 0
        self._lock = threading.Lock()
        self._local = threading.local()  # one requests.Session per thread (conn reuse)

    @property
    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = self._local.session = requests.Session()
        return s

    def token(self):
        with self._lock:
            if not self._tok or time.time() - self._tok_ts > 1800:  # refresh every 30m
                r = requests.post(self.base + "/token",
                                  json={"keyId": self.key, "keySecret": self.secret}, timeout=30)
                r.raise_for_status()
                self._tok = r.json()["token"]
                self._tok_ts = time.time()
            return self._tok

    def hdr(self):
        # Orkes Cloud authenticates via X-Authorization (OSS Conductor uses Authorization).
        return {"X-Authorization": self.token(), "Content-Type": "application/json"}

    def poll(self, task_type):
        r = self._session.get(self.base + f"/tasks/poll/{task_type}?workerid={WORKER_ID}",
                              headers=self.hdr(), timeout=self.POLL_TIMEOUT)
        if r.status_code == 200 and r.text.strip():
            return r.json()
        return None  # 204 No Content when the queue is empty

    def update(self, wid, task_id, status, output):
        body = {"workflowInstanceId": wid, "taskId": task_id, "status": status,
                "outputData": output, "workerId": WORKER_ID}
        self._session.post(self.base + "/tasks", headers=self.hdr(),
                           data=json.dumps(body), timeout=self.UPDATE_TIMEOUT)


def _run_one(conductor, task_type, fn):
    task = conductor.poll(task_type)
    if not task:
        return
    wid, task_id = task["workflowInstanceId"], task["taskId"]
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in (task.get("inputData") or {}).items() if k in params}
    try:
        out = fn(**kwargs)
        conductor.update(wid, task_id, "COMPLETED", out)
        print(f"  ✓ {task_type:18} {wid[:24]} -> COMPLETED")
    except Exception as e:
        conductor.update(wid, task_id, "FAILED", {"error": str(e)})
        print(f"  ✗ {task_type:18} {wid[:24]} -> FAILED: {e}")


def _poll_loop(conductor, task_type, fn):
    while True:
        try:
            _run_one(conductor, task_type, fn)
        except Exception as e:
            print(f"  ! poll error on {task_type}: {e}")
            time.sleep(1)
        time.sleep(0.3)


def main():
    for v in ("CONDUCTOR_SERVER_URL", "CONDUCTOR_AUTH_KEY", "CONDUCTOR_AUTH_SECRET"):
        if not os.environ.get(v):
            sys.exit(f"{v} not set — export it or `source ../backend/.env` first.")
    conductor = Conductor()
    conductor.token()  # fail fast if creds are bad
    print(f"ReferralGuard workers polling {conductor.base} ({len(TASKS)} task types)…")
    for name, fn in TASKS.items():
        threading.Thread(target=_poll_loop, args=(conductor, name, fn), daemon=True).start()
        print(f"  polling {name}")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
