from django.contrib import admin
from .models import ContactSubmission


@admin.register(ContactSubmission)
class ContactSubmissionAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "truck", "sms_consent", "created_at")
    list_filter = ("truck", "sms_consent", "created_at")
    search_fields = ("name", "email", "message")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)