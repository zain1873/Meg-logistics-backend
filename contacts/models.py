import uuid
from django.db import models


TRUCK_TYPES = [
    ('Dry Van', 'Dry Van'),
    ('Flatbed', 'Flatbed'),
    ('Reefers', 'Reefers'),
    ('Box Truck', 'Box Truck'),
    ('Reefer Van', 'Reefer Van'),
    ('Power-Only', 'Power-Only'),
    ('Step Deck', 'Step Deck'),
    ('Conestoga', 'Conestoga'),
    ('Intermodal', 'Intermodal'),
]


class ContactSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    truck = models.CharField(max_length=50, blank=True, null=True, choices=TRUCK_TYPES)
    message = models.TextField(max_length=5000)
    sms_consent = models.BooleanField(default=False)
    unsubscribed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contact_submissions'
        indexes = [
            models.Index(fields=['email'], name='idx_contact_email'),
            models.Index(fields=['created_at'], name='idx_contact_created_at'),
        ]

    def __str__(self):
        return f"{self.name} - {self.email}"