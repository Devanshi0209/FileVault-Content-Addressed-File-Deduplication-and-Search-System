from django.shortcuts import render
from django.db import transaction
from django.db import IntegrityError
from django.db.models import F
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
import hashlib
from datetime import datetime
from .models import File
from .serializers import FileSerializer


def compute_file_hash(file_obj, chunk_size=8192):
    # Preserves file pointer position after hashing (required for subsequent file operations)
    sha256 = hashlib.sha256()
    original_position = file_obj.tell()
    
    file_obj.seek(0)
    try:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
    finally:
        file_obj.seek(original_position)
    
    return sha256.hexdigest()


class FileViewSet(viewsets.ModelViewSet):
    queryset = File.objects.all()
    serializer_class = FileSerializer

    def get_queryset(self):
        queryset = File.objects.all()
        query_params = self.request.query_params
        
        search = query_params.get('search')
        if search:
            queryset = queryset.filter(original_filename__icontains=search)
        
        file_type = query_params.get('file_type')
        if file_type:
            queryset = queryset.filter(file_type=file_type)
        
        size_min = query_params.get('size_min')
        if size_min:
            try:
                size_min_int = int(size_min)
                queryset = queryset.filter(size__gte=size_min_int)
            except ValueError:
                raise ValidationError({'size_min': 'Must be a valid integer'})
        
        size_max = query_params.get('size_max')
        if size_max:
            try:
                size_max_int = int(size_max)
                queryset = queryset.filter(size__lte=size_max_int)
            except ValueError:
                raise ValidationError({'size_max': 'Must be a valid integer'})
        
        uploaded_after = query_params.get('uploaded_after')
        if uploaded_after:
            try:
                # Try datetime first, fallback to date (start of day for >= comparison)
                datetime_obj = parse_datetime(uploaded_after)
                if datetime_obj is None:
                    date_obj = parse_date(uploaded_after)
                    if date_obj is None:
                        raise ValueError("Invalid date format")
                    datetime_obj = timezone.make_aware(datetime.combine(date_obj, datetime.min.time()))
                elif timezone.is_naive(datetime_obj):
                    datetime_obj = timezone.make_aware(datetime_obj)
                queryset = queryset.filter(uploaded_at__gte=datetime_obj)
            except (ValueError, TypeError, AttributeError):
                raise ValidationError({'uploaded_after': 'Must be a valid ISO 8601 date (YYYY-MM-DD)'})
        
        uploaded_before = query_params.get('uploaded_before')
        if uploaded_before:
            try:
                # Try datetime first, fallback to date (end of day for <= comparison)
                datetime_obj = parse_datetime(uploaded_before)
                if datetime_obj is None:
                    date_obj = parse_date(uploaded_before)
                    if date_obj is None:
                        raise ValueError("Invalid date format")
                    datetime_obj = timezone.make_aware(datetime.combine(date_obj, datetime.max.time()))
                elif timezone.is_naive(datetime_obj):
                    datetime_obj = timezone.make_aware(datetime_obj)
                queryset = queryset.filter(uploaded_at__lte=datetime_obj)
            except (ValueError, TypeError, AttributeError):
                raise ValidationError({'uploaded_before': 'Must be a valid ISO 8601 date (YYYY-MM-DD)'})
        
        return queryset

    def create(self, request, *args, **kwargs):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No file provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        content_hash = compute_file_hash(file_obj)
        
        with transaction.atomic():
            existing_file = File.objects.filter(content_hash=content_hash).first()
            
            if existing_file:
                # Reuse original file on disk (use string path, not FieldFile, to avoid duplicate write)
                # content_hash=None: unique constraint only applies to originals
                duplicate_file = File.objects.create(
                    original_filename=file_obj.name,
                    file_type=file_obj.content_type,
                    size=file_obj.size,
                    content_hash=None,
                    is_duplicate=True,
                    referenced_file=existing_file,
                    reference_count=1,
                    file=existing_file.file.name,
                )
                
                # Atomic increment to prevent race conditions
                File.objects.filter(id=existing_file.id).update(
                    reference_count=F('reference_count') + 1
                )
                
                existing_file.refresh_from_db()
                
                serializer = self.get_serializer(duplicate_file)
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
            
            try:
                data = {
                    'file': file_obj,
                    'original_filename': file_obj.name,
                    'file_type': file_obj.content_type,
                    'size': file_obj.size,
                }
                
                serializer = self.get_serializer(data=data)
                serializer.is_valid(raise_exception=True)
                # Pass via save() to bypass read_only_fields restriction
                serializer.save(
                    content_hash=content_hash,
                    is_duplicate=False,
                    reference_count=1
                )
                
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
                
            except IntegrityError:
                # Race condition: concurrent upload created file with same hash between check and save
                existing_file = File.objects.get(content_hash=content_hash)
                
                duplicate_file = File.objects.create(
                    original_filename=file_obj.name,
                    file_type=file_obj.content_type,
                    size=file_obj.size,
                    content_hash=None,
                    is_duplicate=True,
                    referenced_file=existing_file,
                    reference_count=1,
                    file=existing_file.file.name,
                )
                
                File.objects.filter(id=existing_file.id).update(
                    reference_count=F('reference_count') + 1
                )
                
                existing_file.refresh_from_db()
                
                serializer = self.get_serializer(duplicate_file)
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        
        with transaction.atomic():
            if instance.is_duplicate:
                # Deleting a duplicate: decrement reference_count on original
                original = instance.referenced_file
                if original:
                    File.objects.filter(id=original.id).update(
                        reference_count=F('reference_count') - 1
                    )
                # Delete the duplicate record (no physical file to delete)
                instance.delete()
                return Response(status=status.HTTP_204_NO_CONTENT)
            else:
                # Deleting an original: only allow if no duplicates exist
                if instance.reference_count > 1:
                    raise PermissionDenied(
                        detail='Cannot delete original file that has duplicates. Delete duplicates first.'
                    )
                
                # Delete physical file if it exists
                if instance.file:
                    instance.file.delete()
                
                # Delete the record
                instance.delete()
                return Response(status=status.HTTP_204_NO_CONTENT)
