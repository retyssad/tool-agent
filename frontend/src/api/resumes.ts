export interface ImportedResumeItem {
  id: number
  title: string
  original_name: string
  page_count: number
  has_text_layer: boolean
  file_size: number
  tags: string[]
  notes: string
  created_at: string
  updated_at: string
}

export interface ImportedResumeDetail extends ImportedResumeItem {
  text: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* 保留默认文本 */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export const resumesApi = {
  listImported: (q?: string) =>
    request<ImportedResumeItem[]>(`/api/resumes/imported${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  getImported: (id: number) => request<ImportedResumeDetail>(`/api/resumes/imported/${id}`),
  upload: (file: File, title: string, tags: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', title)
    form.append('tags', tags)
    return request<ImportedResumeDetail>('/api/resumes/imported', { method: 'POST', body: form })
  },
  updateImported: (id: number, body: { title?: string; tags?: string[]; notes?: string }) =>
    request<ImportedResumeItem>(`/api/resumes/imported/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteImported: (id: number) =>
    request<{ ok: boolean }>(`/api/resumes/imported/${id}`, { method: 'DELETE' }),
  fileUrl: (id: number) => `/api/resumes/imported/${id}/file`,
}
