"""
Fetch.ai uAgent adapter — agent-to-agent referral intake.

Exposes ReferralGuard as a uAgent on Agentverse so other agents (e.g. a partner
clinic's intake agent, or ASI:One via the Chat Protocol) can send a referral and
get the verdict + flags back — the same pipeline, reached agent-to-agent instead
of over HTTP.

  - uagents installed + FETCH_AGENT_SEED set -> a real uAgent runs on :8001
  - otherwise -> this file is a no-op with instructions (demo still runs via REST)

Run:
    pip install uagents
    export FETCH_AGENT_SEED="referralguard-unique-seed"
    python fetch_agent.py
Then register / discover it on https://agentverse.ai.
"""
import os
from agent_pipeline import run_pipeline

FETCH_SEED = os.getenv("FETCH_AGENT_SEED", "referralguard-demo-seed")
FETCH_ON = False

try:
    from uagents import Agent, Context, Model

    class ReferralRequest(Model):
        id: str
        chan: str = "fetch_agent"
        clinic: str = ""
        patient: dict
        diagnosis_code: str | None = None
        requested_cpt: str | None = None
        procedure: str = ""
        insurance: dict
        npi: str | None = None
        raw: str = ""

    class ReferralVerdict(Model):
        request_id: str
        verdict: str
        flags: list
        confirmation: str | None = None

    agent = Agent(name="referralguard", seed=FETCH_SEED, port=8001,
                  endpoint=["http://localhost:8001/submit"])
    FETCH_ON = True

    @agent.on_message(model=ReferralRequest)
    async def handle_referral(ctx: Context, sender: str, msg: ReferralRequest):
        ctx.logger.info(f"Referral {msg.id} received from {sender}")
        result = run_pipeline(msg.dict())
        sub = result.get("submission") or {}
        await ctx.send(sender, ReferralVerdict(
            request_id=msg.id, verdict=result["verdict"],
            flags=[f["ttl"] for f in result["flags"]],
            confirmation=sub.get("confirmation")))

    # Optional: ASI:One Chat Protocol so the agent is discoverable in natural language.
    try:
        from uagents_core.contrib.protocols.chat import chat_protocol_spec, ChatMessage, TextContent
        from uagents import Protocol
        chat = Protocol(spec=chat_protocol_spec)

        @chat.on_message(model=ChatMessage)
        async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
            await ctx.send(sender, ChatMessage(content=[TextContent(
                text="Send a ReferralRequest and I'll return a prior-auth verdict (READY / NEEDS INFO / HIGH DENIAL RISK) with flags.")]))

        agent.include(chat, publish_manifest=True)
    except Exception:
        pass

    if __name__ == "__main__":
        print(f"ReferralGuard uAgent address: {agent.address}")
        agent.run()

except ImportError:
    if __name__ == "__main__":
        print("uagents not installed. This is the optional Fetch.ai path; the demo "
              "runs the same pipeline via backend/server.py (REST) and the dashboard.")
