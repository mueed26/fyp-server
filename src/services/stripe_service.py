"""
Stripe service: plan catalog, client initialization, and limit definitions.

The PLAN_CATALOG is the single source of truth for what each plan costs,
what features it unlocks, and how many credits it grants.
"""

import os
from typing import Optional

import stripe
from src.config.logging import get_logger

logger = get_logger(__name__)

# Initialize Stripe with the secret key from environment.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

if not STRIPE_SECRET_KEY:
    logger.warning("stripe_secret_key_not_set")
stripe.api_key = STRIPE_SECRET_KEY


# ──────────────────────────────────────────────────────────────────────
#  PLAN CATALOG — single source of truth
# ──────────────────────────────────────────────────────────────────────
PLAN_CATALOG = {
    "free": {
        "id": "free",
        "name": "Free",
        "price_cents": 0,
        "credits": 0,
        "limits": {
            "max_projects": 3,
            "max_docs_per_project": 5,
            "max_pages_per_doc": 20,
            "max_chats_per_project": 2,
            "max_messages_per_chat": 10,
            "feature_generations_per_doc": 1,
            "feature_expand_per_source": 0,
        },
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "price_cents": 500,  # $5.00
        "credits": 50,
        "limits": {
            "max_projects": 15,
            "max_docs_per_project": 20,
            "max_pages_per_doc": 100,
            "max_chats_per_project": 10,
            "max_messages_per_chat": -1,           # unlimited
            "feature_generations_per_doc": -1,     # unlimited
            "feature_expand_per_source": 1,
        },
    },
    "elite": {
        "id": "elite",
        "name": "Elite",
        "price_cents": 2000,  # $20.00
        "credits": 250,
        "limits": {
            "max_projects": 100,
            "max_docs_per_project": 50,
            "max_pages_per_doc": 300,
            "max_chats_per_project": -1,
            "max_messages_per_chat": -1,
            "feature_generations_per_doc": -1,
            "feature_expand_per_source": -1,
        },
    },
}


def get_plan(plan_id: str) -> dict:
    return PLAN_CATALOG.get(plan_id, PLAN_CATALOG["free"])


def is_unlimited(limit_value: int) -> bool:
    """A limit of -1 means unlimited."""
    return limit_value == -1


def is_paid_plan(plan_id: str) -> bool:
    return plan_id in {"pro", "elite"}


def create_checkout_session(
    *,
    plan_id: str,
    clerk_id: str,
    customer_email: Optional[str] = None,
    existing_stripe_customer_id: Optional[str] = None,
) -> str:
    """
    Create a Stripe Checkout Session for a paid plan purchase.
    Returns the session URL that the frontend redirects the user to.
    """
    plan = PLAN_CATALOG.get(plan_id)
    if not plan or plan["price_cents"] <= 0:
        raise ValueError(f"Cannot purchase plan: {plan_id}")

    success_url = f"{FRONTEND_URL}/projects?upgrade=success&plan={plan_id}"
    cancel_url = f"{FRONTEND_URL}/projects?upgrade=cancel"

    session_kwargs = {
        "mode": "payment",  # one-time payment (not subscription)
        "payment_method_types": ["card"],
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Study AI Companion — {plan['name']} Plan",
                        "description": (
                            f"{plan['credits']} credits + premium features"
                        ),
                    },
                    "unit_amount": plan["price_cents"],
                },
                "quantity": 1,
            }
        ],
        "success_url": success_url,
        "cancel_url": cancel_url,
        # Store our identifiers so the webhook can update the right user.
        "client_reference_id": clerk_id,
        "metadata": {
            "clerk_id": clerk_id,
            "plan_id": plan_id,
        },
        # Allow promotion codes in case you ever want to issue them.
        "allow_promotion_codes": True,
    }

    if existing_stripe_customer_id:
        session_kwargs["customer"] = existing_stripe_customer_id
    elif customer_email:
        session_kwargs["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**session_kwargs)
    logger.info(
        "stripe_checkout_session_created",
        session_id=session.id,
        clerk_id=clerk_id,
        plan_id=plan_id,
    )
    return session.url


def construct_webhook_event(payload: bytes, signature: str):
    """
    Verify and parse a Stripe webhook payload.
    Raises stripe.error.SignatureVerificationError if invalid.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=STRIPE_WEBHOOK_SECRET,
    )