from .models import UserRole


def business_profiles_for(user_role_pairs):
    profile_keys = set(user_role_pairs)
    if not profile_keys:
        return {}

    user_ids = {user_id for user_id, _role in profile_keys}
    roles = {role for _user_id, role in profile_keys}
    profiles = UserRole.objects.filter(user_id__in=user_ids, role__in=roles)
    return {
        (profile.user_id, profile.role): profile
        for profile in profiles
        if (profile.user_id, profile.role) in profile_keys
    }


def business_profile_for(user, role):
    if user is None:
        return None
    return business_profiles_for([(user.pk, role)]).get((user.pk, role))
