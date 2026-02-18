import axios from 'axios';
import { File as FileType, FileFilters } from '../types/file';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

export const fileService = {
  async uploadFile(file: File): Promise<FileType> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await axios.post(`${API_URL}/files/`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  async getFiles(filters?: FileFilters): Promise<FileType[]> {
    let url = `${API_URL}/files/`;
    
    if (filters) {
      const params = new URLSearchParams();
      
      if (filters.search !== undefined && filters.search !== '') {
        params.append('search', filters.search);
      }
      if (filters.file_type !== undefined && filters.file_type !== '') {
        params.append('file_type', filters.file_type);
      }
      if (filters.size_min !== undefined) {
        params.append('size_min', filters.size_min.toString());
      }
      if (filters.size_max !== undefined) {
        params.append('size_max', filters.size_max.toString());
      }
      if (filters.uploaded_after !== undefined && filters.uploaded_after !== '') {
        params.append('uploaded_after', filters.uploaded_after);
      }
      if (filters.uploaded_before !== undefined && filters.uploaded_before !== '') {
        params.append('uploaded_before', filters.uploaded_before);
      }
      
      const queryString = params.toString();
      if (queryString) {
        url += `?${queryString}`;
      }
    }
    
    const response = await axios.get(url);
    return response.data;
  },

  async deleteFile(id: string): Promise<void> {
    await axios.delete(`${API_URL}/files/${id}/`);
  },

  async downloadFile(fileUrl: string, filename: string): Promise<void> {
    try {
      const response = await axios.get(fileUrl, {
        responseType: 'blob',
      });
      
      const blob = new Blob([response.data]);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      // Revoke URL to free memory
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Download error:', error);
      throw new Error('Failed to download file');
    }
  },
}; 