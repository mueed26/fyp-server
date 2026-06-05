# """
# Credit Guard — Reusable FastAPI dependency
# ==========================================
# Import `require_credits(n)` and add it as a FastAPI dependency to any route
# that should consume credits before executing.

# Usage example (in featureRoutes.py):

#     from src.services.creditGuard import require_credits

#     @router.post("/{project_id}/features/generate")
#     async def generate_features(
#         ...
#         _: None = Depends(require_credits(10)),   # costs 10 credits
#         current_user_clerk_id: str = Depends(get_current_user_clerk_id),
#     ):
#         ...

# Plan limits are also enforced here.
# """

# from fastapi import Depends, HTTPException
# from src.services.supabase import supabase
# from src.services.clerkAuth import get_current_user_clerk_id
# from src.config.logging import get_logger

# logger = get_logger(__name__)

# # ---------------------------------------------------------------------------
# # Credit costs per action
# # ---------------------------------------------------------------------------
# CREDIT_COSTS = {
#     "chat_message": 2,
#     "feature_generate": 10,
#     "feature_expand": 15,
#     "web_search": 5,
# }

# # ---------------------------------------------------------------------------
# # Plan limits
# # ---------------------------------------------------------------------------
# PLAN_LIMITS = {
#     "free": {
#         "max_projects": 3,
#         "max_docs_per_project": 5,
#         "max_pages_per_doc": 20,
#         "max_chats_per_project": 2,
#         "max_messages_per_chat": 10,
#         "feature_expand_allowed": False,
#         "max_feature_expands": 0,
#     },
#     "pro": {
#         "max_projects": 15,
#         "max_docs_per_project": 20,
#         "max_pages_per_doc": 100,
#         "max_chats_per_project": 10,
#         "max_messages_per_chat": None,   # unlimited
#         "feature_expand_allowed": True,
#         "max_feature_expands": 1,        # 1 expand per source
#     },
#     "elite": {
#         "max_projects": 100,
#         "max_docs_per_project": 50,
#         "max_pages_per_doc": 300,
#         "max_chats_per_project": None,   # unlimited
#         "max_messages_per_chat": None,   # unlimited
#         "feature_expand_allowed": True,
#         "max_feature_expands": None,     # unlimited
#     },
# }


# def get_user_plan(clerk_id: str) -> str:
#     """Return the user's plan string ('free', 'pro', 'elite')."""
#     result = (
#         supabase.table("users")
#         .select("plan, credits")
#         .eq("clerk_id", clerk_id)
#         .execute()
#     )
#     if not result.data:
#         return "free"
#     return result.data[0].get("plan") or "free"


# def get_user_credits(clerk_id: str) -> int:
#     result = (
#         supabase.table("users")
#         .select("credits")
#         .eq("clerk_id", clerk_id)
#         .execute()
#     )
#     if not result.data:
#         return 0
#     return result.data[0].get("credits") or 0


# def deduct_credits(clerk_id: str, amount: int) -> int:
#     """
#     Deduct `amount` credits from user. Returns new balance.
#     Raises 402 if not enough credits.
#     """
#     current = get_user_credits(clerk_id)
#     if current < amount:
#         raise HTTPException(
#             status_code=402,
#             detail=(
#                 f"Not enough credits ({current} available, {amount} required). "
#                 "Please top up your credits."
#             ),
#         )
#     new_balance = current - amount
#     supabase.table("users").update({"credits": new_balance}).eq("clerk_id", clerk_id).execute()
#     logger.info("credits_deducted", clerk_id=clerk_id, deducted=amount, new_balance=new_balance)
#     return new_balance


# def require_credits(amount: int):
#     """
#     FastAPI dependency factory.
#     Deducts `amount` credits from the current user before the route runs.
#     Returns the user's new credit balance (ignored by most callers).

#     Example:
#         @router.post("/some-endpoint")
#         async def handler(
#             _: int = Depends(require_credits(10)),
#             current_user: str = Depends(get_current_user_clerk_id),
#         ):
#             ...
#     """
#     async def _dependency(
#         current_user_clerk_id: str = Depends(get_current_user_clerk_id),
#     ) -> int:
#         return deduct_credits(current_user_clerk_id, amount)

#     return _dependency


# def check_plan_limit(clerk_id: str, limit_key: str, current_count: int) -> None:
#     """
#     Check whether the user has hit a plan limit.
#     Raises 402 with an upgrade message if they have.

#     Example:
#         check_plan_limit(clerk_id, "max_projects", len(existing_projects))
#     """
#     plan = get_user_plan(clerk_id)
#     limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
#     limit_value = limits.get(limit_key)

#     if limit_value is None:
#         return  # unlimited on this plan

#     if current_count >= limit_value:
#         plan_label = plan.capitalize()
#         raise HTTPException(
#             status_code=402,
#             detail=(
#                 f"You have reached the {plan_label} plan limit for {limit_key.replace('_', ' ')} "
#                 f"({limit_value}). Please upgrade your plan to continue."
#             ),
#         )