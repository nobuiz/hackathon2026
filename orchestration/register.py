"""
Register the ReferralGuard workflow on Orkes Conductor and drive runs from the CLI.

Reads Orkes creds from ../backend/.env (CONDUCTOR_SERVER_URL / CONDUCTOR_AUTH_KEY /
CONDUCTOR_AUTH_SECRET). Pure REST (requests) so it's transparent and dependency-light.

    python register.py                       # register task defs + the workflow
    python register.py --run ../samples/01_clean_cardiology.json   # start a run, print workflow id
    python register.py --status <workflowId> # show current state + which task is waiting
    python register.py --approve <workflowId> # complete the HUMAN approval gate -> workflow resumes
"""
import os, sys, json, argparse
import requests

HERE = os.path.dirname(__file__)


def _load_env():
    env = os.path.join(HERE, "..", "backend", ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _base():
    url = os.environ.get("CONDUCTOR_SERVER_URL", "").rstrip("/")
    if not url:
        sys.exit("CONDUCTOR_SERVER_URL not set (check backend/.env)")
    return url


def _token():
    r = requests.post(_base() + "/token", json={
        "keyId": os.environ["CONDUCTOR_AUTH_KEY"],
        "keySecret": os.environ["CONDUCTOR_AUTH_SECRET"]}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def _hdr(tok):
    # Orkes Cloud authenticates via X-Authorization (OSS Conductor uses Authorization).
    return {"X-Authorization": tok, "Content-Type": "application/json"}


# Task definitions for every SIMPLE task in the workflow. retryCount=0 keeps the
# demo deterministic; a missing worker fails fast instead of silently retrying.
TASK_NAMES = ["intake_agent", "extraction_agent", "completeness_agent",
              "validation_agent", "denial_risk_agent", "decision_agent",
              "route_human_review", "submission_agent"]


def register(tok):
    taskdefs = [{"name": n, "retryCount": 0, "timeoutSeconds": 120,
                 "responseTimeoutSeconds": 60, "ownerEmail": "team@referralguard.dev"}
                for n in TASK_NAMES]
    r = requests.post(_base() + "/metadata/taskdefs", headers=_hdr(tok),
                      data=json.dumps(taskdefs), timeout=30)
    print(f"taskdefs: HTTP {r.status_code} {r.text[:160]}")

    wf = json.load(open(os.path.join(HERE, "referralguard_workflow.json")))
    wf.pop("failureWorkflow", None)  # don't require a separate failure workflow for the demo
    r = requests.post(_base() + "/metadata/workflow?overwrite=true", headers=_hdr(tok),
                      data=json.dumps(wf), timeout=30)
    print(f"workflow '{wf['name']}': HTTP {r.status_code} {r.text[:160] or 'OK'}")


def to_req(s):
    """Sample JSON -> the request shape agent_pipeline / the workers expect."""
    return {"id": s["request_id"], "chan": s["channel"], "clinic": s["source_clinic"],
            "patient": s["patient"], "diagnosis_code": s["diagnosis_code"],
            "requested_cpt": s["requested_cpt"], "procedure": s["requested_procedure"],
            "insurance": s["insurance"], "npi": s["referring_provider_npi"],
            "raw": s["raw_text"]}


def run(tok, sample_path):
    s = json.load(open(sample_path))
    req = to_req(s)
    r = requests.post(_base() + "/workflow/referralguard_prior_auth?version=1",
                      headers=_hdr(tok), data=json.dumps({"request": req}), timeout=30)
    r.raise_for_status()
    wid = r.text.strip().strip('"')
    print(f"started workflow id: {wid}")
    print(f"  watch it:  python register.py --status {wid}")
    print(f"  approve:   python register.py --approve {wid}")
    return wid


def status(tok, wid):
    r = requests.get(_base() + f"/workflow/{wid}?includeTasks=true", headers=_hdr(tok), timeout=30)
    r.raise_for_status()
    w = r.json()
    print(f"workflow status: {w['status']}")
    for t in w.get("tasks", []):
        line = f"  {t['referenceTaskName']:14} {t['taskType']:8} -> {t['status']}"
        if t["referenceTaskName"] == "approval" and t["status"] == "IN_PROGRESS":
            line += "   <== WAITING ON HUMAN APPROVAL (paused here)"
        print(line)
    return w


def approve(tok, wid):
    w = status(tok, wid)
    gate = next((t for t in w.get("tasks", [])
                 if t["referenceTaskName"] == "approval" and t["status"] == "IN_PROGRESS"), None)
    if not gate:
        print("no pending approval gate to approve."); return
    body = {"workflowInstanceId": wid, "taskId": gate["taskId"], "status": "COMPLETED",
            "outputData": {"user": "ops@clinic", "approved": True}}
    r = requests.post(_base() + "/tasks", headers=_hdr(tok), data=json.dumps(body), timeout=30)
    print(f"approve: HTTP {r.status_code} {r.text[:160] or 'OK'}")
    print("re-check with:  python register.py --status " + wid)


if __name__ == "__main__":
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", metavar="SAMPLE.json")
    ap.add_argument("--status", metavar="WORKFLOW_ID")
    ap.add_argument("--approve", metavar="WORKFLOW_ID")
    args = ap.parse_args()
    tok = _token()
    if args.status:
        status(tok, args.status)
    elif args.approve:
        approve(tok, args.approve)
    elif args.run:
        run(tok, args.run)
    else:
        register(tok)
        print("done. start a run with:  python register.py --run ../samples/01_clean_cardiology.json")
