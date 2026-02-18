from rest_framework import serializers
from .models import File

class FileSerializer(serializers.ModelSerializer):
    referenced_file_id = serializers.UUIDField(source='referenced_file.id', read_only=True, allow_null=True)
    
    class Meta:
        model = File
        fields = [
            'id', 'file', 'original_filename', 'file_type', 'size', 'uploaded_at',
            'content_hash', 'is_duplicate', 'reference_count', 'referenced_file_id'
        ]
        read_only_fields = ['id', 'uploaded_at', 'referenced_file_id'] 