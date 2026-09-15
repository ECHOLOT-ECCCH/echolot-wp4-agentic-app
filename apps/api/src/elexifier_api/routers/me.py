from fastapi import APIRouter

from elexifier_api.deps import CurrentUser
from elexifier_api.schemas import UserOut

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
