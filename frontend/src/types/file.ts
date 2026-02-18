export interface File {
  id: string;
  original_filename: string;
  file_type: string;
  size: number;
  uploaded_at: string;
  file: string;
  content_hash: string | null;
  is_duplicate: boolean;
  reference_count: number;
  referenced_file_id: string | null;
}

export interface FileFilters {
  search?: string;
  file_type?: string;
  size_min?: number;
  size_max?: number;
  uploaded_after?: string;
  uploaded_before?: string;
} 