"""Redis channel constants (PROMPT §3 ruling #3).

"Topic" = Redis channel name. Engine channels map 後端資料規格書 §3.2; governance
channels are added by design-backend. FastAPI only ever publishes to Redis — it
never opens a browser-facing WebSocket (the Flask BFF fans out to browsers).
"""

from __future__ import annotations

# --- Engine channels (後端資料規格書 §3.2) ----------------------------------
L1_INFERENCE = "ai_servo:l1_inference"
L1_SUMMARY = "ai_servo:l1_summary"
L2_FINETUNE = "ai_servo:l2_finetune"
L3_DEPLOY = "ai_servo:l3_deploy"
SHAP_DIAGNOSIS = "ai_servo:shap_diagnosis"
FALLBACK_EVENT = "ai_servo:fallback_event"
FALLBACK_ESCALATION = "ai_servo:fallback_escalation"
CONTROL_STATUS = "ai_servo:control_status"
EXPERT_NOTIFICATION = "ai_servo:expert_notification"

# --- Governance channels (design-backend §3/§4/§6/§7) -----------------------
COMMAND = "ai_servo:command"
ALARM = "ai_servo:alarm"
GOVERNANCE = "ai_servo:governance"
SYSTEM = "ai_servo:system"

ALL_CHANNELS: tuple[str, ...] = (
    L1_INFERENCE,
    L1_SUMMARY,
    L2_FINETUNE,
    L3_DEPLOY,
    SHAP_DIAGNOSIS,
    FALLBACK_EVENT,
    FALLBACK_ESCALATION,
    CONTROL_STATUS,
    EXPERT_NOTIFICATION,
    COMMAND,
    ALARM,
    GOVERNANCE,
    SYSTEM,
)
