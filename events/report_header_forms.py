"""Validated administrator uploads for official report branding."""
from pathlib import Path
from django import forms
from django.core.exceptions import ValidationError
from .models import ReportHeaderLogo, SystemSettings

MAX_LOGO_SIZE = 5 * 1024 * 1024


class LogoImageField(forms.ImageField):
    def to_python(self, data):
        if data and getattr(data, 'size', 0) > MAX_LOGO_SIZE:
            raise ValidationError('Logo must be 5 MB or smaller.')
        return super().to_python(data)


class ReportHeaderForm(forms.ModelForm):
    school_logo = LogoImageField(required=False, label='Left Header Logo', widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.png,.jpg,.jpeg,.webp'}))
    report_right_logo = LogoImageField(required=False, label='Right Header Logo', widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.png,.jpg,.jpeg,.webp'}))
    remove_left_logo = forms.BooleanField(required=False)
    remove_right_logo = forms.BooleanField(required=False)

    class Meta:
        model = SystemSettings
        fields = ['school_name', 'campus_name', 'report_subtitle', 'school_logo', 'report_right_logo']
        labels = {'school_name': 'School / Organization Name', 'campus_name': 'Department / Campus Name', 'report_subtitle': 'Report Subtitle', 'school_logo': 'Left Header Logo', 'report_right_logo': 'Right Header Logo'}
        widgets = {
            'school_name': forms.TextInput(attrs={'class': 'form-control'}),
            'campus_name': forms.TextInput(attrs={'class': 'form-control'}),
            'report_subtitle': forms.TextInput(attrs={'class': 'form-control'}),
            'school_logo': forms.FileInput(attrs={'class': 'form-control', 'accept': '.png,.jpg,.jpeg,.webp'}),
            'report_right_logo': forms.FileInput(attrs={'class': 'form-control', 'accept': '.png,.jpg,.jpeg,.webp'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Check size before Django's ImageField decodes user-supplied content.
        for name in ('school_logo', 'report_right_logo'):
            self.fields[name].validators.append(self.validate_logo)

    @staticmethod
    def validate_logo(upload):
        if not upload:
            return
        if upload.size > MAX_LOGO_SIZE:
            raise ValidationError('Logo must be 5 MB or smaller.')
        extension = Path(upload.name).suffix.lower()
        formats = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}
        if extension not in formats:
            raise ValidationError('Upload a PNG, JPG, JPEG, or WebP image.')
        image = getattr(upload, 'image', None)
        if image is None or image.format != formats[extension]:
            raise ValidationError('The image content must match its file type.')
        if image.width * image.height > 20_000_000:
            raise ValidationError('Logo dimensions are too large (maximum 20 megapixels).')

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get('remove_left_logo'):
            instance.school_logo = None
        if self.cleaned_data.get('remove_right_logo'):
            instance.report_right_logo = None
        if commit:
            instance.save(update_fields=['school_name', 'campus_name', 'report_subtitle', 'school_logo', 'report_right_logo', 'updated_at', 'updated_by'])
        return instance


class AdditionalLogoForm(forms.ModelForm):
    image = LogoImageField(required=True, widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.png,.jpg,.jpeg,.webp'}))

    class Meta:
        model = ReportHeaderLogo
        fields = ['side', 'image']
        widgets = {'side': forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].validators.append(ReportHeaderForm.validate_logo)


class AdditionalLogoFormSet(forms.BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields['id'].queryset = self.get_queryset()

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        counts = {'left': 0, 'right': 0}
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get('DELETE') or not data.get('image'):
                continue
            counts[data['side']] += 1
        if any(count > 5 for count in counts.values()):
            raise ValidationError('Add up to five extra logos on each side (six including the main logo).')


ReportHeaderLogoFormSet = forms.inlineformset_factory(
    SystemSettings, ReportHeaderLogo, form=AdditionalLogoForm,
    formset=AdditionalLogoFormSet, extra=0, can_delete=True,
    max_num=10, validate_max=True, absolute_max=20,
)
