import React, { useMemo, useState } from 'react';
import { fileService } from '../services/fileService';
import { File as FileType, FileFilters } from '../types/file';
import { DocumentIcon, TrashIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

export const FileList: React.FC = () => {
  const queryClient = useQueryClient();

  const [searchTerm, setSearchTerm] = useState<string>('');
  const [fileType, setFileType] = useState<string>('');
  const [sizeMin, setSizeMin] = useState<string>('');
  const [sizeMax, setSizeMax] = useState<string>('');
  const [uploadedAfter, setUploadedAfter] = useState<string>('');
  const [uploadedBefore, setUploadedBefore] = useState<string>('');

  // Separate pending (editing) and applied (query) filter state to prevent query on every keystroke
  const [appliedFilters, setAppliedFilters] = useState<FileFilters>({});

  const { data: allFiles } = useQuery({
    queryKey: ['files', 'all'],
    queryFn: () => fileService.getFiles(),
  });

  const uniqueFileTypes = useMemo(() => {
    if (!allFiles) return [];
    const types = Array.from(new Set(allFiles.map(f => f.file_type).filter(Boolean)));
    return types.sort();
  }, [allFiles]);

  const filters: FileFilters = useMemo(() => {
    return appliedFilters;
  }, [appliedFilters]);

  const hasActiveFilters = useMemo(() => {
    return !!(
      appliedFilters.search ||
      appliedFilters.file_type ||
      appliedFilters.size_min !== undefined ||
      appliedFilters.size_max !== undefined ||
      appliedFilters.uploaded_after ||
      appliedFilters.uploaded_before
    );
  }, [appliedFilters]);

  const handleApplyFilters = () => {
    const newFilters: FileFilters = {};
    
    if (searchTerm.trim()) {
      newFilters.search = searchTerm.trim();
    }
    if (fileType && fileType !== 'all') {
      newFilters.file_type = fileType;
    }
    if (sizeMin) {
      const minBytes = parseFloat(sizeMin) * 1024;
      if (!isNaN(minBytes)) {
        newFilters.size_min = Math.round(minBytes);
      }
    }
    if (sizeMax) {
      const maxBytes = parseFloat(sizeMax) * 1024;
      if (!isNaN(maxBytes)) {
        newFilters.size_max = Math.round(maxBytes);
      }
    }
    if (uploadedAfter) {
      newFilters.uploaded_after = uploadedAfter;
    }
    if (uploadedBefore) {
      newFilters.uploaded_before = uploadedBefore;
    }
    
    setAppliedFilters(newFilters);
  };

  const { data: files, isLoading, error } = useQuery({
    queryKey: ['files', filters],
    queryFn: () => fileService.getFiles(filters),
  });

  const storageSavings = useMemo(() => {
    if (!files) return { totalBytes: 0, totalFiles: 0 };
    
    let totalBytes = 0;
    let totalDuplicates = 0;
    
    files.forEach((file) => {
      if (!file.is_duplicate && file.reference_count > 1) {
        // Only count savings from originals with duplicates (duplicates don't save additional space)
        const duplicates = file.reference_count - 1;
        totalBytes += file.size * duplicates;
        totalDuplicates += duplicates;
      }
    });
    
    return { totalBytes, totalFiles: totalDuplicates };
  }, [files]);

  const deleteMutation = useMutation({
    mutationFn: fileService.deleteFile,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files'] });
    },
  });

  const downloadMutation = useMutation({
    mutationFn: ({ fileUrl, filename }: { fileUrl: string; filename: string }) =>
      fileService.downloadFile(fileUrl, filename),
  });

  const handleDelete = async (id: string) => {
    try {
      await deleteMutation.mutateAsync(id);
    } catch (err) {
      console.error('Delete error:', err);
    }
  };

  const handleDownload = async (fileUrl: string, filename: string) => {
    try {
      await downloadMutation.mutateAsync({ fileUrl, filename });
    } catch (err) {
      console.error('Download error:', err);
    }
  };

  const handleClearFilters = () => {
    setSearchTerm('');
    setFileType('');
    setSizeMin('');
    setSizeMax('');
    setUploadedAfter('');
    setUploadedBefore('');
    setAppliedFilters({});
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-4 bg-gray-200 rounded w-1/4"></div>
          <div className="space-y-3">
            <div className="h-8 bg-gray-200 rounded"></div>
            <div className="h-8 bg-gray-200 rounded"></div>
            <div className="h-8 bg-gray-200 rounded"></div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 border-l-4 border-red-400 p-4">
          <div className="flex">
            <div className="flex-shrink-0">
              <svg
                className="h-5 w-5 text-red-400"
                viewBox="0 0 20 20"
                fill="currentColor"
              >
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
            <div className="ml-3">
              <p className="text-sm text-red-700">Failed to load files. Please try again.</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">Uploaded Files</h2>
        {storageSavings.totalFiles > 0 && (
          <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-2">
            <p className="text-sm font-medium text-green-800">
              Storage Saved: {formatFileSize(storageSavings.totalBytes)}
            </p>
            <p className="text-xs text-green-600">
              {storageSavings.totalFiles} duplicate{storageSavings.totalFiles !== 1 ? 's' : ''} prevented
            </p>
          </div>
        )}
      </div>

      <div className="mb-6 bg-gray-50 rounded-lg p-4 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label htmlFor="search" className="block text-sm font-medium text-gray-700 mb-1">
              Search Filename
            </label>
            <input
              id="search"
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Enter filename..."
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            />
          </div>

          <div>
            <label htmlFor="file_type" className="block text-sm font-medium text-gray-700 mb-1">
              File Type
            </label>
            <select
              id="file_type"
              value={fileType}
              onChange={(e) => setFileType(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="all">All</option>
              {uniqueFileTypes.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="size_min" className="block text-sm font-medium text-gray-700 mb-1">
              Size Min (KB)
            </label>
            <input
              id="size_min"
              type="number"
              value={sizeMin}
              onChange={(e) => setSizeMin(e.target.value)}
              placeholder="Min size in KB"
              min="0"
              step="0.01"
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            />
          </div>

          <div>
            <label htmlFor="size_max" className="block text-sm font-medium text-gray-700 mb-1">
              Size Max (KB)
            </label>
            <input
              id="size_max"
              type="number"
              value={sizeMax}
              onChange={(e) => setSizeMax(e.target.value)}
              placeholder="Max size in KB"
              min="0"
              step="0.01"
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            />
          </div>

          <div>
            <label htmlFor="uploaded_after" className="block text-sm font-medium text-gray-700 mb-1">
              Uploaded After
            </label>
            <input
              id="uploaded_after"
              type="date"
              value={uploadedAfter}
              onChange={(e) => setUploadedAfter(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            />
          </div>

          <div>
            <label htmlFor="uploaded_before" className="block text-sm font-medium text-gray-700 mb-1">
              Uploaded Before
            </label>
            <input
              id="uploaded_before"
              type="date"
              value={uploadedBefore}
              onChange={(e) => setUploadedBefore(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-primary-500 focus:border-primary-500"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <button
            onClick={handleApplyFilters}
            className="px-4 py-2 text-sm font-medium text-white bg-primary-600 border border-transparent rounded-md hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
          >
            Apply Filters
          </button>
          {hasActiveFilters && (
            <button
              onClick={handleClearFilters}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
            >
              Clear Filters
            </button>
          )}
        </div>
      </div>

      {!files || files.length === 0 ? (
        <div className="text-center py-12">
          <DocumentIcon className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-2 text-sm font-medium text-gray-900">
            {hasActiveFilters ? 'No files match filters' : 'No files'}
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            {hasActiveFilters
              ? 'Try adjusting your filters or clear them to see all files'
              : 'Get started by uploading a file'}
          </p>
        </div>
      ) : (
        <div className="mt-6 flow-root">
          <ul className="-my-5 divide-y divide-gray-200">
            {files.map((file) => (
              <li key={file.id} className="py-4">
                <div className="flex items-center space-x-4">
                  <div className="flex-shrink-0">
                    <DocumentIcon className="h-8 w-8 text-gray-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {file.original_filename}
                      </p>
                      {file.is_duplicate && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800">
                          Duplicate
                        </span>
                      )}
                      {!file.is_duplicate && file.reference_count > 1 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                          {file.reference_count - 1} duplicate{file.reference_count - 1 !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-500">
                      {file.file_type} • {formatFileSize(file.size)}
                    </p>
                    <div className="flex items-center gap-2 text-sm text-gray-500">
                      <span>Uploaded {new Date(file.uploaded_at).toLocaleString()}</span>
                      {file.is_duplicate && file.referenced_file_id && (
                        <span className="text-xs text-gray-400">
                          • References original file
                        </span>
                      )}
                      {!file.is_duplicate && file.reference_count > 1 && (
                        <span className="text-xs text-green-600">
                          • Saved {formatFileSize(file.size * (file.reference_count - 1))}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex space-x-2">
                    <button
                      onClick={() => handleDownload(file.file, file.original_filename)}
                      disabled={downloadMutation.isPending}
                      className="inline-flex items-center px-3 py-2 border border-transparent shadow-sm text-sm leading-4 font-medium rounded-md text-white bg-primary-600 hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
                    >
                      <ArrowDownTrayIcon className="h-4 w-4 mr-1" />
                      Download
                    </button>
                    <button
                      onClick={() => handleDelete(file.id)}
                      disabled={deleteMutation.isPending}
                      className="inline-flex items-center px-3 py-2 border border-transparent shadow-sm text-sm leading-4 font-medium rounded-md text-white bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
                    >
                      <TrashIcon className="h-4 w-4 mr-1" />
                      Delete
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}; 