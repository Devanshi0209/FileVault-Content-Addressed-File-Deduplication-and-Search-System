from django.db import models
import uuid
import os

def file_upload_path(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('uploads', filename)

class File(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to=file_upload_path)
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=100)
    size = models.BigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # content_hash is null for duplicates to avoid unique constraint violation
    content_hash = models.CharField(max_length=64, unique=True, db_index=True, null=True, blank=True)
    is_duplicate = models.BooleanField(default=False)
    reference_count = models.PositiveIntegerField(default=1)
    # PROTECT prevents deletion of original files that have duplicates
    referenced_file = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='duplicates'
    )
    
    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['original_filename']),
            models.Index(fields=['file_type']),
            models.Index(fields=['size']),
            models.Index(fields=['uploaded_at']),
        ]
    
    def __str__(self):
        return self.original_filename
