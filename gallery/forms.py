from decimal import Decimal

from django import forms

from .models import GalleryImage, Order, Product, QuoteRequest, SellerBanner


class GalleryImageForm(forms.ModelForm):
    """Form for uploading images to the gallery."""

    class Meta:
        model = GalleryImage
        fields = ['title', 'description', 'image']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Premium Cotton Fabric Sample'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional short description about this image...'
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            }),
        }
        labels = {
            'title': 'Image Title',
            'description': 'Description',
            'image': 'Select Image',
        }


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name',
            'image',
            'category',
            'description',
            'price',
            'old_price',
            'minimum_order',
            'inventory',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            }),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'old_price': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'minimum_order': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'inventory': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_price'].required = False

    def clean_price(self):
        price = self.cleaned_data['price']
        if price < 0:
            raise forms.ValidationError('Price cannot be negative.')
        return price

    def clean_old_price(self):
        old_price = self.cleaned_data.get('old_price') or Decimal('0.00')
        if old_price < 0:
            raise forms.ValidationError('Previous price cannot be negative.')
        return old_price

    def clean_minimum_order(self):
        minimum_order = self.cleaned_data['minimum_order']
        if minimum_order < 1:
            raise forms.ValidationError('Minimum order must be at least 1.')
        return minimum_order


class QuoteRequestForm(forms.ModelForm):
    class Meta:
        model = QuoteRequest
        fields = ['quantity', 'message']
        widgets = {
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Add specifications or a message for the seller (optional)',
            }),
        }

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        if quantity < 1:
            raise forms.ValidationError('Quantity must be at least 1.')
        return quantity


class QuoteResponseForm(forms.ModelForm):
    class Meta:
        model = QuoteRequest
        fields = ['status', 'seller_response']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'seller_response': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Price, availability, or a message for the buyer',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            (QuoteRequest.Status.QUOTED, 'Quoted'),
            (QuoteRequest.Status.DECLINED, 'Declined'),
        ]


class OrderCreateForm(forms.Form):
    quantity = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
    )
    notes = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Share product customizations or other order details.',
        }),
    )

    def __init__(self, *args, product, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        if quantity < self.product.minimum_order:
            raise forms.ValidationError(
                f'The minimum order for this product is {self.product.minimum_order}.'
            )
        if quantity > self.product.inventory:
            raise forms.ValidationError(
                f'Only {self.product.inventory} units are currently available.'
            )
        return quantity


class OrderMessageForm(forms.Form):
    body = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Message the buyer or supplier about this order.',
        }),
    )


class OrderStatusForm(forms.Form):
    status = forms.ChoiceField(widget=forms.Select(attrs={'class': 'form-select'}))

    def __init__(self, *args, allowed_statuses, **kwargs):
        super().__init__(*args, **kwargs)
        choices = dict(Order.Status.choices)
        self.fields['status'].choices = [
            (status, choices[status]) for status in allowed_statuses
        ]


class SellerBannerForm(forms.ModelForm):
    class Meta:
        model = SellerBanner
        fields = ['title', 'image', 'target_url']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. New season cotton collection',
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            }),
            'target_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://your-business.example/',
            }),
        }

    def clean_target_url(self):
        target_url = self.cleaned_data['target_url']
        if not target_url.startswith(('https://', 'http://')):
            raise forms.ValidationError('Use a web link beginning with http:// or https://.')
        return target_url