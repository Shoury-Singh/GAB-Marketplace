import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import User


class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={
        'class': 'form-control', 'placeholder': 'Enter your email'
    }))
    role = forms.ChoiceField(choices=User.Role.choices, required=True, widget=forms.Select(attrs={
        'class': 'form-select', 'id': 'id_role'
    }))
    phone = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Phone number (optional)'
    }))
    business_name = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Your business / shop / factory name'
    }))
    city = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'City'
    }))
    state = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'State'
    }))
    pincode = forms.RegexField(
        regex=r'^[1-9][0-9]{5}$',
        required=False,
        error_messages={'invalid': 'Enter a valid six-digit Indian PIN code.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'inputmode': 'numeric',
            'maxlength': '6',
            'placeholder': 'Six-digit PIN code (optional)',
        }),
        help_text='Add your PIN code to find nearby shops and products.',
    )

    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'role',
            'phone',
            'business_name',
            'city',
            'state',
            'pincode',
            'password1',
            'password2',
        ]
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Choose a username'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Create a strong password'})
        self.fields['password2'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Confirm password'})

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("This email is already registered.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        business_name = cleaned_data.get('business_name')
        if role in [User.Role.INDUSTRY, User.Role.WHOLESALER, User.Role.SHOPKEEPER]:
            if not business_name or not business_name.strip():
                self.add_error('business_name', "Business name is required for Industry, Wholesaler and Shopkeeper roles.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.role = self.cleaned_data['role']
        user.phone = self.cleaned_data.get('phone', '')
        user.business_name = self.cleaned_data.get('business_name', '')
        user.city = self.cleaned_data.get('city', '')
        user.state = self.cleaned_data.get('state', '')
        user.pincode = self.cleaned_data.get('pincode') or None
        if commit:
            user.save()
        return user


class AccountLocationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['pincode']
        labels = {'pincode': 'PIN code'}
        widgets = {
            'pincode': forms.TextInput(attrs={
                'class': 'form-control',
                'inputmode': 'numeric',
                'maxlength': '6',
                'placeholder': 'Six-digit Indian PIN code',
            }),
        }

    def clean_pincode(self):
        return self.cleaned_data.get('pincode') or None


class SellerBusinessProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'business_name',
            'phone',
            'address',
            'city',
            'state',
            'pincode',
        ]
        labels = {
            'business_name': 'Shop / business name',
            'phone': 'Business phone',
            'address': 'Business address',
            'city': 'City',
            'state': 'State',
            'pincode': 'Business PIN code',
        }
        widgets = {
            'business_name': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'organization',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'tel',
                'autocomplete': 'tel',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'autocomplete': 'street-address',
            }),
            'city': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'address-level2',
            }),
            'state': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'address-level1',
            }),
            'pincode': forms.TextInput(attrs={
                'class': 'form-control',
                'inputmode': 'numeric',
                'maxlength': '6',
                'autocomplete': 'postal-code',
                'placeholder': 'Six-digit Indian PIN code',
            }),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        seller_roles = {
            User.Role.INDUSTRY,
            User.Role.WHOLESALER,
            User.Role.SHOPKEEPER,
        }
        self.fields['business_name'].required = bool(
            seller_roles.intersection(user.get_available_roles())
        )
        self.fields['pincode'].help_text = (
            'Used for approximate supplier distance and nearby discovery.'
        )


class AddAccountRoleForm(forms.Form):
    role = forms.ChoiceField(
        choices=User.Role.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Role to add',
    )
    business_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Business, shop, or factory name',
        }),
        label='Business name',
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        assigned_roles = set(user.get_available_roles())
        self.fields['role'].choices = [
            choice for choice in User.Role.choices
            if choice[0] not in assigned_roles
        ]
        self.fields['business_name'].initial = user.business_name or ''

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        business_name = cleaned_data.get('business_name', '').strip()
        seller_roles = {
            User.Role.INDUSTRY,
            User.Role.WHOLESALER,
            User.Role.SHOPKEEPER,
        }
        if role in seller_roles and not (business_name or self.user.business_name):
            self.add_error(
                'business_name',
                'A business name is required when adding a seller role.',
            )
        return cleaned_data


class SwitchAccountRoleForm(forms.Form):
    role = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Active role',
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        available_roles = set(user.get_available_roles())
        self.fields['role'].choices = [
            choice for choice in User.Role.choices
            if choice[0] in available_roles
        ]
        self.fields['role'].initial = user.role


class UserLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Username or Email', 'autofocus': True
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Password'
    }))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Username or Email"

    def clean(self):
        cleaned_data = self.cleaned_data.copy()
        username = (cleaned_data.get('username') or '').strip()
        password = cleaned_data.get('password')

        if not username:
            self.add_error('username', self.fields['username'].error_messages['required'])
        if not password:
            self.add_error('password', self.fields['password'].error_messages['required'])

        if username and password:
            user_model = get_user_model()
            user = user_model._default_manager.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).order_by('id').first()

            if user is None or not user.check_password(password):
                raise forms.ValidationError(
                    self.error_messages['invalid_login'],
                    code='invalid_login',
                    params={'username': self.username_field.verbose_name},
                )
            self.user_cache = user

        return cleaned_data


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'autocomplete': 'email',
            'placeholder': 'Email address on your account',
        }),
    )


class PasswordResetVerifyForm(forms.Form):
    code = forms.RegexField(
        regex=re.compile(r'^\d{6}$'),
        error_messages={'invalid': 'Enter the 6-digit code from your email.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'maxlength': '6',
            'placeholder': '6-digit code',
        }),
    )
    new_password = forms.CharField(
        label='New password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('confirm_password')
        if password and confirmation and password != confirmation:
            self.add_error('confirm_password', 'The passwords do not match.')
        return cleaned_data