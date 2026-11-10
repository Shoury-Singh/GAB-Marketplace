"""Synchronize role-specific account data tables without copying credentials."""

from .models import (
    BuyerAccountData,
    IndustryAccountData,
    ShopkeeperAccountData,
    User,
    UserRole,
    WholesalerAccountData,
)


ROLE_ACCOUNT_MODELS = {
    User.Role.BUYER: BuyerAccountData,
    User.Role.WHOLESALER: WholesalerAccountData,
    User.Role.SHOPKEEPER: ShopkeeperAccountData,
    User.Role.INDUSTRY: IndustryAccountData,
}


def synchronize_role_account_data(user_id):
    user = User.objects.get(pk=user_id)
    assignments = {
        assignment.role: assignment
        for assignment in UserRole.objects.filter(user_id=user_id)
    }

    for role, model in ROLE_ACCOUNT_MODELS.items():
        assignment = assignments.get(role)
        if assignment is None:
            model.objects.filter(user_id=user_id).delete()
            continue

        model.objects.update_or_create(
            user_id=user_id,
            defaults={
                'account_created_at': user.date_joined,
                'role_added_at': assignment.created_at,
            },
        )
