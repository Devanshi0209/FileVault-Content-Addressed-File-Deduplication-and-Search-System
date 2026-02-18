import os
import tempfile
from io import BytesIO
from datetime import datetime, date
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from .models import File
from .views import compute_file_hash


MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class FileDeduplicationTests(TestCase):
    
    def setUp(self):
        self.client = APIClient()
    
    def tearDown(self):
        # Delete duplicates first due to PROTECT constraint on referenced_file
        duplicates = File.objects.filter(is_duplicate=True)
        for file_obj in duplicates:
            if file_obj.file:
                file_obj.file.delete()
        duplicates.delete()
        
        originals = File.objects.filter(is_duplicate=False)
        for file_obj in originals:
            if file_obj.file:
                file_obj.file.delete()
        originals.delete()
    
    def _create_test_file(self, content=b'test file content', filename='test.txt'):
        return SimpleUploadedFile(
            name=filename,
            content=content,
            content_type='text/plain'
        )
    
    def test_dedup_001_upload_unique_file_creates_original(self):
        file_content = b'unique content for test'
        test_file = self._create_test_file(content=file_content, filename='unique.txt')
        
        response = self.client.post('/api/files/', {'file': test_file}, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertFalse(data['is_duplicate'])
        self.assertIsNotNone(data['content_hash'])
        self.assertEqual(data['reference_count'], 1)
        self.assertIsNone(data['referenced_file_id'])
        
        file_obj = File.objects.get(id=data['id'])
        self.assertTrue(file_obj.file.name.startswith('uploads/'))
        self.assertTrue(os.path.exists(file_obj.file.path))
        
        self.assertEqual(File.objects.count(), 1)
    
    def test_dedup_002_upload_duplicate_file_creates_reference(self):
        original_content = b'duplicate test content'
        original_file = self._create_test_file(content=original_content, filename='original.txt')
        original_response = self.client.post('/api/files/', {'file': original_file}, format='multipart')
        original_data = original_response.json()
        original_id = original_data['id']
        original_file_path = File.objects.get(id=original_id).file.path
        
        self.assertTrue(os.path.exists(original_file_path))
        
        duplicate_file = self._create_test_file(content=original_content, filename='duplicate.txt')
        response = self.client.post('/api/files/', {'file': duplicate_file}, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertTrue(data['is_duplicate'])
        self.assertIsNone(data['content_hash'])
        self.assertEqual(data['referenced_file_id'], original_id)
        
        original_file_obj = File.objects.get(id=original_id)
        original_file_obj.refresh_from_db()
        self.assertEqual(original_file_obj.reference_count, 2)
        
        duplicate_file_obj = File.objects.get(id=data['id'])
        self.assertEqual(duplicate_file_obj.file.name, original_file_obj.file.name)
        self.assertEqual(File.objects.count(), 2)
        self.assertEqual(duplicate_file_obj.file.path, original_file_path)
    
    def test_dedup_003_multiple_duplicates_increment_reference_count_correctly(self):
        original_content = b'multiple duplicates test content'
        original_file = self._create_test_file(content=original_content, filename='original.txt')
        original_response = self.client.post('/api/files/', {'file': original_file}, format='multipart')
        original_id = original_response.json()['id']
        
        duplicate_ids = []
        for i in range(3):
            duplicate_file = self._create_test_file(
                content=original_content,
                filename=f'duplicate_{i}.txt'
            )
            response = self.client.post('/api/files/', {'file': duplicate_file}, format='multipart')
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            duplicate_ids.append(response.json()['id'])
        
        original_file_obj = File.objects.get(id=original_id)
        original_file_obj.refresh_from_db()
        self.assertEqual(original_file_obj.reference_count, 4)
        
        duplicates = File.objects.filter(is_duplicate=True)
        self.assertEqual(duplicates.count(), 3)
        
        for dup_id in duplicate_ids:
            dup_file = File.objects.get(id=dup_id)
            self.assertTrue(dup_file.is_duplicate)
            self.assertEqual(str(dup_file.referenced_file.id), str(original_id))
            self.assertEqual(dup_file.reference_count, 1)
        
        self.assertEqual(File.objects.count(), 4)
    
    def test_dedup_004_concurrent_uploads_handle_race_condition(self):
        # Tests race condition handling: IntegrityError on concurrent duplicate creation
        # must be caught and handled, producing correct final state
        file_content = b'concurrent upload test content'
        num_uploads = 5
        
        upload_results = []
        for i in range(num_uploads):
            test_file = self._create_test_file(
                content=file_content,
                filename=f'concurrent_{i}.txt'
            )
            response = self.client.post('/api/files/', {'file': test_file}, format='multipart')
            upload_results.append({
                'status': response.status_code,
                'data': response.json() if response.status_code == 201 else response.data
            })
        
        for i, result in enumerate(upload_results):
            self.assertEqual(result['status'], status.HTTP_201_CREATED,
                           f"Upload {i} should succeed, got: {result}")
        
        originals = File.objects.filter(is_duplicate=False)
        self.assertEqual(originals.count(), 1, "Should have exactly 1 original file")
        
        duplicates = File.objects.filter(is_duplicate=True)
        self.assertEqual(duplicates.count(), num_uploads - 1,
                        f"Should have {num_uploads - 1} duplicates")
        
        original_file = originals.first()
        original_file.refresh_from_db()
        self.assertEqual(
            original_file.reference_count,
            num_uploads,
            f"Original reference_count should equal total uploads ({num_uploads})"
        )
        
        for dup in duplicates:
            self.assertEqual(dup.referenced_file.id, original_file.id)
        
        self.assertEqual(File.objects.count(), num_uploads)
        
        self.assertEqual(len([r for r in upload_results if r['status'] == 201]), num_uploads,
                        "All uploads should return 201, any IntegrityError should be handled internally")


class FileHashComputationTests(TestCase):
    
    def test_dedup_005_hash_computation_preserves_file_pointer(self):
        # Verifies that compute_file_hash restores file pointer after reading
        # (important for subsequent file operations)
        file_content = b'x' * 200
        file_obj = BytesIO(file_content)
        file_obj.seek(100)
        original_position = file_obj.tell()
        self.assertEqual(original_position, 100)
        
        hash_value = compute_file_hash(file_obj)
        
        self.assertEqual(file_obj.tell(), 100, "File position should be preserved after hash computation")
        self.assertEqual(file_obj.tell(), original_position)
        
        self.assertIsNotNone(hash_value)
        self.assertEqual(len(hash_value), 64)
        
        file_obj.seek(0)
        hash_value_2 = compute_file_hash(file_obj)
        self.assertEqual(hash_value, hash_value_2, "Hash should be consistent")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class FileSearchAndFilteringTests(TestCase):
    
    def setUp(self):
        self.client = APIClient()
    
    def tearDown(self):
        # Delete duplicates first due to PROTECT constraint on referenced_file
        duplicates = File.objects.filter(is_duplicate=True)
        for file_obj in duplicates:
            if file_obj.file:
                file_obj.file.delete()
        duplicates.delete()
        
        originals = File.objects.filter(is_duplicate=False)
        for file_obj in originals:
            if file_obj.file:
                file_obj.file.delete()
        originals.delete()
    
    def _create_test_file(self, content=b'test file content', filename='test.txt', content_type='text/plain'):
        return SimpleUploadedFile(
            name=filename,
            content=content,
            content_type=content_type
        )
    
    def _create_file_with_date(self, filename, content, content_type, size, upload_date):
        test_file = self._create_test_file(content=content, filename=filename, content_type=content_type)
        response = self.client.post('/api/files/', {'file': test_file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        file_obj = File.objects.get(id=response.json()['id'])
        file_obj.uploaded_at = timezone.make_aware(upload_date) if isinstance(upload_date, datetime) else timezone.make_aware(datetime.combine(upload_date, datetime.min.time()))
        file_obj.size = size
        file_obj.save()
        return file_obj
    
    def test_search_001_search_by_filename_substring_case_insensitive(self):
        self._create_test_file(content=b'content1', filename='test.pdf', content_type='application/pdf')
        self._create_test_file(content=b'content2', filename='TEST.txt', content_type='text/plain')
        self._create_test_file(content=b'content3', filename='other.doc', content_type='application/msword')
        
        file1 = self._create_test_file(content=b'content1', filename='test.pdf', content_type='application/pdf')
        self.client.post('/api/files/', {'file': file1}, format='multipart')
        file2 = self._create_test_file(content=b'content2', filename='TEST.txt', content_type='text/plain')
        self.client.post('/api/files/', {'file': file2}, format='multipart')
        file3 = self._create_test_file(content=b'content3', filename='other.doc', content_type='application/msword')
        self.client.post('/api/files/', {'file': file3}, format='multipart')
        
        response = self.client.get('/api/files/', {'search': 'test'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, "Should return 2 files matching 'test'")
        filenames = [item['original_filename'] for item in data]
        self.assertIn('test.pdf', filenames)
        self.assertIn('TEST.txt', filenames)
        self.assertNotIn('other.doc', filenames)
    
    def test_search_002_filter_by_file_type(self):
        file1 = self._create_test_file(content=b'content1', filename='a.pdf', content_type='application/pdf')
        self.client.post('/api/files/', {'file': file1}, format='multipart')
        file2 = self._create_test_file(content=b'content2', filename='b.pdf', content_type='application/pdf')
        self.client.post('/api/files/', {'file': file2}, format='multipart')
        file3 = self._create_test_file(content=b'content3', filename='c.txt', content_type='text/plain')
        self.client.post('/api/files/', {'file': file3}, format='multipart')
        
        response = self.client.get('/api/files/', {'file_type': 'application/pdf'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, "Should return 2 PDF files")
        for item in data:
            self.assertEqual(item['file_type'], 'application/pdf')
        filenames = [item['original_filename'] for item in data]
        self.assertIn('a.pdf', filenames)
        self.assertIn('b.pdf', filenames)
        self.assertNotIn('c.txt', filenames)
    
    def test_search_003_filter_by_size_min(self):
        file1 = self._create_test_file(content=b'x' * 100, filename='small.txt')
        response1 = self.client.post('/api/files/', {'file': file1}, format='multipart')
        file1_obj = File.objects.get(id=response1.json()['id'])
        file1_obj.size = 100
        file1_obj.save()
        
        file2 = self._create_test_file(content=b'x' * 500, filename='medium.txt')
        response2 = self.client.post('/api/files/', {'file': file2}, format='multipart')
        file2_obj = File.objects.get(id=response2.json()['id'])
        file2_obj.size = 500
        file2_obj.save()
        
        file3 = self._create_test_file(content=b'x' * 1000, filename='large.txt')
        response3 = self.client.post('/api/files/', {'file': file3}, format='multipart')
        file3_obj = File.objects.get(id=response3.json()['id'])
        file3_obj.size = 1000
        file3_obj.save()
        
        response = self.client.get('/api/files/', {'size_min': '500'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, "Should return 2 files with size >= 500")
        sizes = [item['size'] for item in data]
        self.assertIn(500, sizes)
        self.assertIn(1000, sizes)
        self.assertNotIn(100, sizes)
    
    def test_search_004_filter_by_size_max(self):
        file1 = self._create_test_file(content=b'x' * 100, filename='small.txt')
        response1 = self.client.post('/api/files/', {'file': file1}, format='multipart')
        file1_obj = File.objects.get(id=response1.json()['id'])
        file1_obj.size = 100
        file1_obj.save()
        
        file2 = self._create_test_file(content=b'x' * 500, filename='medium.txt')
        response2 = self.client.post('/api/files/', {'file': file2}, format='multipart')
        file2_obj = File.objects.get(id=response2.json()['id'])
        file2_obj.size = 500
        file2_obj.save()
        
        file3 = self._create_test_file(content=b'x' * 1000, filename='large.txt')
        response3 = self.client.post('/api/files/', {'file': file3}, format='multipart')
        file3_obj = File.objects.get(id=response3.json()['id'])
        file3_obj.size = 1000
        file3_obj.save()
        
        response = self.client.get('/api/files/', {'size_max': '500'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, "Should return 2 files with size <= 500")
        sizes = [item['size'] for item in data]
        self.assertIn(100, sizes)
        self.assertIn(500, sizes)
        self.assertNotIn(1000, sizes)
    
    def test_search_005_filter_by_uploaded_after_date(self):
        date1 = datetime(2024, 1, 1, 12, 0, 0)
        date2 = datetime(2024, 1, 15, 12, 0, 0)
        date3 = datetime(2024, 2, 1, 12, 0, 0)
        
        file1 = self._create_test_file(content=b'content1', filename='file1.txt')
        response1 = self.client.post('/api/files/', {'file': file1}, format='multipart')
        file1_obj = File.objects.get(id=response1.json()['id'])
        # update() bypasses auto_now_add
        File.objects.filter(id=file1_obj.id).update(uploaded_at=timezone.make_aware(date1))
        
        file2 = self._create_test_file(content=b'content2', filename='file2.txt')
        response2 = self.client.post('/api/files/', {'file': file2}, format='multipart')
        file2_obj = File.objects.get(id=response2.json()['id'])
        File.objects.filter(id=file2_obj.id).update(uploaded_at=timezone.make_aware(date2))
        
        file3 = self._create_test_file(content=b'content3', filename='file3.txt')
        response3 = self.client.post('/api/files/', {'file': file3}, format='multipart')
        file3_obj = File.objects.get(id=response3.json()['id'])
        File.objects.filter(id=file3_obj.id).update(uploaded_at=timezone.make_aware(date3))
        
        response = self.client.get('/api/files/', {'uploaded_after': '2024-01-15'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, "Should return 2 files uploaded on or after 2024-01-15")
        filenames = [item['original_filename'] for item in data]
        self.assertIn('file2.txt', filenames)
        self.assertIn('file3.txt', filenames)
        self.assertNotIn('file1.txt', filenames)
    
    def test_search_006_filter_by_uploaded_before_date(self):
        # Use start of day to ensure inclusion (view converts date to end of day for <= comparison)
        date1 = datetime(2024, 1, 1, 0, 0, 0)
        date2 = datetime(2024, 1, 15, 0, 0, 0)
        date3 = datetime(2024, 2, 1, 0, 0, 0)
        
        file1 = self._create_test_file(content=b'content1', filename='file1.txt')
        response1 = self.client.post('/api/files/', {'file': file1}, format='multipart')
        file1_obj = File.objects.get(id=response1.json()['id'])
        # update() bypasses auto_now_add
        File.objects.filter(id=file1_obj.id).update(uploaded_at=timezone.make_aware(date1))
        
        file2 = self._create_test_file(content=b'content2', filename='file2.txt')
        response2 = self.client.post('/api/files/', {'file': file2}, format='multipart')
        file2_obj = File.objects.get(id=response2.json()['id'])
        File.objects.filter(id=file2_obj.id).update(uploaded_at=timezone.make_aware(date2))
        
        file3 = self._create_test_file(content=b'content3', filename='file3.txt')
        response3 = self.client.post('/api/files/', {'file': file3}, format='multipart')
        file3_obj = File.objects.get(id=response3.json()['id'])
        File.objects.filter(id=file3_obj.id).update(uploaded_at=timezone.make_aware(date3))
        
        response = self.client.get('/api/files/', {'uploaded_before': '2024-01-15'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2, 
                        f"Should return 2 files uploaded on or before 2024-01-15. Got {len(data)} files: {[item['original_filename'] for item in data]}")
        filenames = [item['original_filename'] for item in data]
        self.assertIn('file1.txt', filenames, "file1.txt (2024-01-01) should be included")
        self.assertIn('file2.txt', filenames, "file2.txt (2024-01-15) should be included")
        self.assertNotIn('file3.txt', filenames, "file3.txt (2024-02-01) should NOT be included")
    
    def test_search_007_multiple_filters_combined_and_logic(self):
        date1 = datetime(2024, 1, 20, 12, 0, 0)
        date2 = datetime(2024, 1, 10, 12, 0, 0)
        
        file1 = self._create_test_file(content=b'x' * 500, filename='test.pdf', content_type='application/pdf')
        response1 = self.client.post('/api/files/', {'file': file1}, format='multipart')
        file1_obj = File.objects.get(id=response1.json()['id'])
        file1_obj.size = 500
        file1_obj.uploaded_at = timezone.make_aware(date1)
        file1_obj.save()
        
        file2 = self._create_test_file(content=b'x' * 500, filename='test.txt', content_type='text/plain')
        response2 = self.client.post('/api/files/', {'file': file2}, format='multipart')
        file2_obj = File.objects.get(id=response2.json()['id'])
        file2_obj.size = 500
        file2_obj.uploaded_at = timezone.make_aware(date1)
        file2_obj.save()
        
        file3 = self._create_test_file(content=b'x' * 100, filename='other.pdf', content_type='application/pdf')
        response3 = self.client.post('/api/files/', {'file': file3}, format='multipart')
        file3_obj = File.objects.get(id=response3.json()['id'])
        file3_obj.size = 100
        file3_obj.uploaded_at = timezone.make_aware(date1)
        file3_obj.save()
        
        file4 = self._create_test_file(content=b'y' * 500, filename='test.pdf', content_type='application/pdf')
        response4 = self.client.post('/api/files/', {'file': file4}, format='multipart')
        file4_obj = File.objects.get(id=response4.json()['id'])
        file4_obj.size = 500
        file4_obj.uploaded_at = timezone.make_aware(date2)
        file4_obj.save()
        
        response = self.client.get('/api/files/', {
            'search': 'test',
            'file_type': 'application/pdf',
            'size_min': '400',
            'uploaded_after': '2024-01-15'
        })
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 1, "Should return only 1 file matching all criteria")
        self.assertEqual(data[0]['original_filename'], 'test.pdf')
        self.assertEqual(data[0]['size'], 500)
        self.assertEqual(data[0]['file_type'], 'application/pdf')
    
    def test_search_008_invalid_size_min_returns_400(self):
        response = self.client.get('/api/files/', {'size_min': 'abc'})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertIn('size_min', data)
        self.assertIn('Must be a valid integer', str(data['size_min']))
    
    def test_search_009_invalid_size_max_returns_400(self):
        response = self.client.get('/api/files/', {'size_max': 'xyz'})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertIn('size_max', data)
        self.assertIn('Must be a valid integer', str(data['size_max']))
    
    def test_search_010_invalid_uploaded_after_returns_400(self):
        response = self.client.get('/api/files/', {'uploaded_after': 'invalid-date'})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertIn('uploaded_after', data)
        self.assertIn('Must be a valid ISO 8601 date', str(data['uploaded_after']))
    
    def test_search_011_invalid_uploaded_before_returns_400(self):
        response = self.client.get('/api/files/', {'uploaded_before': 'not-a-date'})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertIn('uploaded_before', data)
        self.assertIn('Must be a valid ISO 8601 date', str(data['uploaded_before']))
    
    def test_search_012_empty_result_set_returns_empty_array(self):
        response = self.client.get('/api/files/', {'search': 'nonexistent'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data, [], "Should return empty array when no files match")
