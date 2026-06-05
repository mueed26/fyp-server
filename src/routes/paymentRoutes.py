# """
# Payment Routes — Stripe + Credit System
# ========================================
# Endpoints:
#   POST /api/payments/setup-session      → create Stripe checkout (setup mode) to save card
#   GET  /api/payments/retrieve-session   → Stripe redirects here; saves payment method to user
#   POST /api/payments/charge             → charge saved card and add credits
#   GET  /api/payments/credits            → get user's current credit balance + payment info
#   POST /api/payments/deduct             → internal helper to deduct credits (used by feature routes)

# Plans:
#   Free  — $0   → 0 credits,  enforced by limits in Supabase / route guards
#   Pro   — $5   → 50 credits  (1 USD = 10 credits)
#   Elite — $20  → 250 credits (200 base + 50 bonus)

# Credit costs (examples — adjust as needed):
#   Chat message        → 2  credits
#   Feature generate    → 10 credits
#   Feature expand      → 15 credits
#   Web search (future) → 5  credits
# """

# import stripe
# from fastapi import APIRouter, HTTPException, Depends, Request
# from fastapi.responses import RedirectResponse
# from pydantic import BaseModel
# from src.services.supabase import supabase
# from src.services.clerkAuth import get_current_user_clerk_id
# from src.config.index import appConfig
# from src.config.logging import get_logger, set_user_id

# logger = get_logger(__name__)

# router = APIRouter(tags=["paymentRoutes"])

# # ---------------------------------------------------------------------------
# # Stripe client — set STRIPE_SECRET_KEY in your .env / appConfig
# # ---------------------------------------------------------------------------
# stripe.api_key = appConfig.get("stripe_secret_key", "")

# # ---------------------------------------------------------------------------
# # Constants
# # ---------------------------------------------------------------------------
# CREDITS_PER_DOLLAR = 10
# PLAN_BONUS: dict[int, int] = {20: 50}   # $20 plan gets 50 bonus credits

# # Front-end URL — set FRONTEND_URL in your .env / appConfig
# FRONTEND_URL = appConfig.get("frontend_url", "http://localhost:3000")


# # ---------------------------------------------------------------------------
# # Pydantic schemas
# # ---------------------------------------------------------------------------
# class SetupSessionRequest(BaseModel):
#     email: str | None = None    # pre-fill Stripe checkout


# class ChargeRequest(BaseModel):
#     amount: int                  # USD amount — must be 5 or 20


# # ---------------------------------------------------------------------------
# # Helpers
# # ---------------------------------------------------------------------------

# def _get_user_row(clerk_id: str) -> dict:
#     """Fetch the full users row; raise 404 if missing."""
#     result = (
#         supabase.table("users")
#         .select("*")
#         .eq("clerk_id", clerk_id)
#         .execute()
#     )
#     if not result.data:
#         raise HTTPException(status_code=404, detail="User not found")
#     return result.data[0]


# def _add_credits(clerk_id: str, amount: int) -> dict:
#     """Increment user credits atomically and return updated row."""
#     user = _get_user_row(clerk_id)
#     new_credits = (user.get("credits") or 0) + amount
#     result = (
#         supabase.table("users")
#         .update({"credits": new_credits})
#         .eq("clerk_id", clerk_id)
#         .execute()
#     )
#     return result.data[0]


# def _deduct_credits_internal(clerk_id: str, amount: int) -> dict:
#     """Deduct credits; raises 402 if insufficient."""
#     user = _get_user_row(clerk_id)
#     current = user.get("credits") or 0
#     if current < amount:
#         raise HTTPException(
#             status_code=402,
#             detail=f"Insufficient credits. You have {current} but need {amount}. Please top up.",
#         )
#     result = (
#         supabase.table("users")
#         .update({"credits": current - amount})
#         .eq("clerk_id", clerk_id)
#         .execute()
#     )
#     return result.data[0]


# # ---------------------------------------------------------------------------
# # POST /api/payments/setup-session
# # Creates a Stripe Checkout session in "setup" mode to save a payment method.
# # Returns the Stripe-hosted URL for the frontend to redirect to.
# # ---------------------------------------------------------------------------
# @router.post("/setup-session")
# async def create_setup_session(
#     body: SetupSessionRequest,
#     current_user_clerk_id: str = Depends(get_current_user_clerk_id),
# ):
#     set_user_id(current_user_clerk_id)
#     try:
#         logger.info("create_setup_session_started", clerk_id=current_user_clerk_id)

#         user = _get_user_row(current_user_clerk_id)

#         # Re-use existing Stripe customer or create a new one
#         stripe_customer_id = user.get("stripe_customer_id")
#         if not stripe_customer_id:
#             customer = stripe.Customer.create(
#                 email=body.email or user.get("email") or "",
#                 metadata={"clerk_id": current_user_clerk_id},
#             )
#             stripe_customer_id = customer.id
#             supabase.table("users").update(
#                 {"stripe_customer_id": stripe_customer_id}
#             ).eq("clerk_id", current_user_clerk_id).execute()

#         session = stripe.checkout.Session.create(
#             mode="setup",
#             customer=stripe_customer_id,
#             payment_method_types=["card"],
#             success_url=(
#                 f"{appConfig.get('backend_url', 'http://localhost:8000')}"
#                 f"/api/payments/retrieve-session?session_id={{CHECKOUT_SESSION_ID}}"
#                 f"&clerk_id={current_user_clerk_id}"
#             ),
#             cancel_url=f"{FRONTEND_URL}/projects?payment=cancelled",
#         )

#         logger.info("setup_session_created", session_id=session.id)
#         return {"url": session.url}

#     except stripe.StripeError as e:
#         logger.error("stripe_error_setup_session", error=str(e))
#         raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message}")
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error("setup_session_error", error=str(e), exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))


# # ---------------------------------------------------------------------------
# # GET /api/payments/retrieve-session
# # Stripe redirects here after the user saves their card.
# # We pull the payment method details and save them to the users table,
# # then redirect the user back to the frontend.
# # ---------------------------------------------------------------------------
# @router.get("/retrieve-session")
# async def retrieve_session(session_id: str, clerk_id: str):
#     """
#     NOTE: This endpoint is called by Stripe (not the authenticated frontend),
#     so we accept clerk_id as a query param instead of from the JWT.
#     """
#     try:
#         logger.info("retrieve_session_started", session_id=session_id, clerk_id=clerk_id)

#         session = stripe.checkout.Session.retrieve(session_id)
#         setup_intent_id = session.setup_intent

#         if not setup_intent_id:
#             logger.error("no_setup_intent_in_session", session_id=session_id)
#             return RedirectResponse(url=f"{FRONTEND_URL}/projects?payment=failed")

#         setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
#         payment_method_id = setup_intent.payment_method

#         if not payment_method_id:
#             logger.error("no_payment_method_in_setup_intent")
#             return RedirectResponse(url=f"{FRONTEND_URL}/projects?payment=failed")

#         pm = stripe.PaymentMethod.retrieve(payment_method_id)
#         billing = pm.billing_details or {}

#         update_data = {
#             "stripe_customer_id": session.customer,
#             "stripe_payment_method_id": pm.id,
#             "payment_type": pm.type,
#             "card_brand": (pm.card.brand if pm.card else None),
#             "card_last4": (pm.card.last4 if pm.card else None),
#             "card_exp_month": (pm.card.exp_month if pm.card else None),
#             "card_exp_year": (pm.card.exp_year if pm.card else None),
#             "billing_email": billing.get("email"),
#             "billing_name": billing.get("name"),
#             "billing_country": (billing.get("address") or {}).get("country"),
#         }

#         supabase.table("users").update(update_data).eq("clerk_id", clerk_id).execute()
#         logger.info("payment_method_saved", clerk_id=clerk_id, card_brand=update_data["card_brand"])

#         return RedirectResponse(url=f"{FRONTEND_URL}/projects?payment=success")

#     except stripe.StripeError as e:
#         logger.error("stripe_error_retrieve_session", error=str(e))
#         return RedirectResponse(url=f"{FRONTEND_URL}/projects?payment=failed")
#     except Exception as e:
#         logger.error("retrieve_session_error", error=str(e), exc_info=True)
#         return RedirectResponse(url=f"{FRONTEND_URL}/projects?payment=failed")


# # ---------------------------------------------------------------------------
# # POST /api/payments/charge
# # Charges the user's saved card and adds credits to their account.
# # ---------------------------------------------------------------------------
# @router.post("/charge")
# async def charge_customer(
#     body: ChargeRequest,
#     current_user_clerk_id: str = Depends(get_current_user_clerk_id),
# ):
#     set_user_id(current_user_clerk_id)
#     try:
#         logger.info("charge_started", clerk_id=current_user_clerk_id, amount=body.amount)

#         if body.amount not in (5, 20):
#             raise HTTPException(status_code=400, detail="Amount must be 5 or 20.")

#         user = _get_user_row(current_user_clerk_id)

#         if not user.get("stripe_customer_id") or not user.get("stripe_payment_method_id"):
#             raise HTTPException(
#                 status_code=400,
#                 detail="No saved payment method. Please add a card first.",
#             )

#         amount_cents = body.amount * 100
#         payment_intent = stripe.PaymentIntent.create(
#             amount=amount_cents,
#             currency="usd",
#             customer=user["stripe_customer_id"],
#             payment_method=user["stripe_payment_method_id"],
#             confirm=True,
#             off_session=True,
#             description=f"Study AI Companion credits — ${body.amount}",
#         )

#         if payment_intent.status != "succeeded":
#             logger.error("payment_not_succeeded", status=payment_intent.status)
#             raise HTTPException(status_code=402, detail="Payment was not successful.")

#         # Calculate credits
#         base_credits = body.amount * CREDITS_PER_DOLLAR
#         bonus_credits = PLAN_BONUS.get(body.amount, 0)
#         total_credits = base_credits + bonus_credits

#         # Add credits + record payment
#         updated_user = _add_credits(current_user_clerk_id, total_credits)

#         supabase.table("payments").insert({
#             "clerk_id": current_user_clerk_id,
#             "stripe_payment_intent_id": payment_intent.id,
#             "stripe_customer_id": user["stripe_customer_id"],
#             "stripe_payment_method_id": user["stripe_payment_method_id"],
#             "amount": body.amount,
#             "currency": "usd",
#             "status": payment_intent.status,
#             "credits_added": total_credits,
#             "card_brand": user.get("card_brand"),
#             "card_last4": user.get("card_last4"),
#         }).execute()

#         logger.info(
#             "charge_successful",
#             clerk_id=current_user_clerk_id,
#             amount=body.amount,
#             credits_added=total_credits,
#             new_balance=updated_user["credits"],
#         )

#         return {
#             "message": "Payment successful",
#             "credits_added": total_credits,
#             "new_balance": updated_user["credits"],
#         }

#     except stripe.error.CardError as e:
#         logger.error("card_error", error=str(e))
#         raise HTTPException(status_code=402, detail=f"Card declined: {e.user_message}")
#     except stripe.StripeError as e:
#         logger.error("stripe_error_charge", error=str(e))
#         raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message}")
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error("charge_error", error=str(e), exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))


# # ---------------------------------------------------------------------------
# # GET /api/payments/credits
# # Returns the user's credit balance + saved card info (for the frontend pill).
# # ---------------------------------------------------------------------------
# @router.get("/credits")
# async def get_user_credits(
#     current_user_clerk_id: str = Depends(get_current_user_clerk_id),
# ):
#     set_user_id(current_user_clerk_id)
#     try:
#         user = _get_user_row(current_user_clerk_id)
#         return {
#             "credits": user.get("credits") or 0,
#             "payment_type": user.get("payment_type"),
#             "card_brand": user.get("card_brand"),
#             "card_last4": user.get("card_last4"),
#             "has_payment_method": bool(user.get("stripe_payment_method_id")),
#         }
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error("get_credits_error", error=str(e), exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))


# # ---------------------------------------------------------------------------
# # POST /api/payments/deduct  (internal — called from other route files)
# # ---------------------------------------------------------------------------
# @router.post("/deduct")
# async def deduct_credits(
#     amount: int,
#     current_user_clerk_id: str = Depends(get_current_user_clerk_id),
# ):
#     """
#     Deduct `amount` credits from the authenticated user.
#     Returns 402 if insufficient.
#     """
#     set_user_id(current_user_clerk_id)
#     updated = _deduct_credits_internal(current_user_clerk_id, amount)
#     return {"credits": updated["credits"]}