"""
Payment routes for Stripe-powered upgrades.

Endpoints:
  GET   /api/payments/me                    — current user's plan, credits, limits
  POST  /api/payments/create-checkout-session — start a Stripe Checkout flow
  POST  /api/payments/webhook               — Stripe webhook receiver
  GET   /api/payments/plans                 — public plan catalog
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.config.logging import get_logger, set_user_id
from src.services.clerkAuth import get_current_user_clerk_id
from src.services.stripe_service import (
    PLAN_CATALOG,
    construct_webhook_event,
    create_checkout_session,
    get_plan,
    is_paid_plan,
)
from src.services.supabase import supabase

logger = get_logger(__name__)
router = APIRouter(tags=["paymentRoutes"])


# ──────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────
def _stripe_obj_to_dict(obj):
    """
    Convert a Stripe StripeObject into a plain Python dict so we can use
    .get() on it. StripeObject doesn't support .get() in the current Stripe
    Python SDK — only bracket access. Converting up-front is cleaner.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict_recursive"):
        return obj.to_dict_recursive()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    try:
        return dict(obj)
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────────────
#  Request / response models
# ──────────────────────────────────────────────────────────────────────
class CheckoutRequest(BaseModel):
    plan_id: str = Field(..., description="'pro' or 'elite'")


# ──────────────────────────────────────────────────────────────────────
#  GET /api/payments/me
# ──────────────────────────────────────────────────────────────────────
@router.get("/me")
async def get_my_plan(
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Return the current user's plan, credits, and the limits attached to that plan."""
    set_user_id(current_user_clerk_id)
    try:
        result = (
            supabase.table("users")
            .select("clerk_id, plan, credits, plan_purchased_at, stripe_customer_id")
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        if not result.data:
            logger.warning("user_row_missing_returning_free_defaults")
            return {
                "plan": "free",
                "credits": 0,
                "limits": get_plan("free")["limits"],
                "plan_purchased_at": None,
            }

        user = result.data[0]
        plan_id = user.get("plan") or "free"
        plan = get_plan(plan_id)

        return {
            "plan": plan_id,
            "credits": user.get("credits") or 0,
            "limits": plan["limits"],
            "plan_purchased_at": user.get("plan_purchased_at"),
        }
    except Exception as e:
        logger.error("get_my_plan_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch plan: {str(e)}")


# ──────────────────────────────────────────────────────────────────────
#  GET /api/payments/plans
# ──────────────────────────────────────────────────────────────────────
@router.get("/plans")
async def get_plans():
    """Return the public plan catalog for the pricing page."""
    return {"plans": PLAN_CATALOG}


# ──────────────────────────────────────────────────────────────────────
#  POST /api/payments/create-checkout-session
# ──────────────────────────────────────────────────────────────────────
@router.post("/create-checkout-session")
async def create_checkout(
    body: CheckoutRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Start a Stripe Checkout session for a paid plan purchase."""
    set_user_id(current_user_clerk_id)
    try:
        if not is_paid_plan(body.plan_id):
            raise HTTPException(status_code=400, detail="Plan is not purchasable")

        user_result = (
            supabase.table("users")
            .select("clerk_id, email, stripe_customer_id")
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        user = user_result.data[0] if user_result.data else {}

        url = create_checkout_session(
            plan_id=body.plan_id,
            clerk_id=current_user_clerk_id,
            customer_email=user.get("email"),
            existing_stripe_customer_id=user.get("stripe_customer_id"),
        )

        logger.info("checkout_session_created", plan_id=body.plan_id)
        return {"url": url}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_checkout_error", plan_id=body.plan_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create checkout session: {str(e)}")


# ──────────────────────────────────────────────────────────────────────
#  POST /api/payments/webhook   (called by Stripe)
# ──────────────────────────────────────────────────────────────────────
@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe webhook receiver.
    Verifies the signature, then handles checkout.session.completed by
    upgrading the user's plan and granting credits.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    # 1. Verify the webhook signature.
    try:
        event = construct_webhook_event(payload=payload, signature=signature)
    except stripe.error.SignatureVerificationError as e:
        logger.warning("stripe_webhook_signature_invalid", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("stripe_webhook_parse_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    # 2. StripeObject doesn't support .get() — use bracket access.
    event_type = event["type"]
    event_id = event["id"]
    logger.info("stripe_webhook_received", event_type=event_type, event_id=event_id)

    # 3. Handle the events we care about.
    if event_type == "checkout.session.completed":
        session = _stripe_obj_to_dict(event["data"]["object"])
        await _handle_checkout_completed(session)
    else:
        # Other events (payment_intent.created, charge.succeeded, etc.)
        # are not actionable for us — just ack so Stripe stops retrying.
        logger.debug("stripe_webhook_ignored_event", event_type=event_type)

    return {"received": True}


async def _handle_checkout_completed(session: dict):
    """Upgrade a user's plan after a successful Stripe Checkout."""
    metadata = session.get("metadata") or {}
    clerk_id = session.get("client_reference_id") or metadata.get("clerk_id")
    plan_id = metadata.get("plan_id")
    stripe_customer_id = session.get("customer")
    session_id = session.get("id")
    payment_intent_id = session.get("payment_intent")
    amount_total = session.get("amount_total") or 0
    currency = session.get("currency") or "usd"

    if not clerk_id or not plan_id:
        logger.error(
            "webhook_missing_identifiers",
            session_id=session_id,
            clerk_id=clerk_id,
            plan_id=plan_id,
        )
        return

    plan = get_plan(plan_id)
    if not is_paid_plan(plan_id):
        logger.warning("webhook_unexpected_plan", plan_id=plan_id, clerk_id=clerk_id)
        return

    # Idempotency: skip if we already processed this session.
    existing = (
        supabase.table("payments")
        .select("id")
        .eq("stripe_checkout_session_id", session_id)
        .execute()
    )
    if existing.data:
        logger.info("webhook_duplicate_event_ignored", session_id=session_id, clerk_id=clerk_id)
        return

    # 1. Look up the user so we can add credits to their current balance.
    user_result = (
        supabase.table("users")
        .select("clerk_id, plan, credits")
        .eq("clerk_id", clerk_id)
        .execute()
    )
    if not user_result.data:
        logger.error("webhook_user_not_found", clerk_id=clerk_id)
        return

    current = user_result.data[0]
    new_credits = (current.get("credits") or 0) + plan["credits"]

    # 2. Upgrade the user.
    supabase.table("users").update(
        {
            "plan": plan_id,
            "credits": new_credits,
            "stripe_customer_id": stripe_customer_id,
            "plan_purchased_at": "now()",
        }
    ).eq("clerk_id", clerk_id).execute()

    # 3. Record the payment.
    supabase.table("payments").insert(
        {
            "clerk_id": clerk_id,
            "plan": plan_id,
            "amount_cents": amount_total,
            "currency": currency,
            "credits_granted": plan["credits"],
            "stripe_checkout_session_id": session_id,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_customer_id": stripe_customer_id,
            "status": "succeeded",
            "metadata": {"plan_name": plan["name"]},
        }
    ).execute()

    logger.info(
        "user_upgraded",
        clerk_id=clerk_id,
        plan_id=plan_id,
        credits_granted=plan["credits"],
        new_credit_balance=new_credits,
    )