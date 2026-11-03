const createRequestId = () => {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  if (typeof cryptoApi?.getRandomValues !== 'function') {
    throw new Error('当前浏览器不支持生成请求编号，请升级浏览器后重试');
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
};

export type BatchCreate = {
  month: string;
  project_owner?: string | null;
  business_category: 'PROJECT' | 'EQUIPMENT' | 'TECHNICAL_SERVICE' | 'GROUP' | 'VEHICLE' | 'REPAIR';
  form_type: string;
};

export type ExtractedField = {
  path: string;
  value_raw: string | null;
  value_normalized: unknown;
  confidence: number | string;
  source_document_id: string | null;
  page_no: number | null;
  bounding_box: number[] | null;
  confirmed: boolean;
};

export type ExtractionJob = {
  id: string;
  batch_id: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'MANUAL_REQUIRED' | 'FAILED';
  progress: number;
  error_message?: string | null;
};

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

class ApiClient {
  private reviewerToken = '';

  constructor(private readonly baseUrl = '/api/v1') {}

  setReviewerToken(token: string) {
    this.reviewerToken = token.trim();
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: init.body instanceof FormData
        ? { ...(this.reviewerToken ? { 'X-Reviewer-Token': this.reviewerToken } : {}), ...init.headers }
        : {
            'Content-Type': 'application/json',
            ...(this.reviewerToken ? { 'X-Reviewer-Token': this.reviewerToken } : {}),
            ...init.headers,
          },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: `请求失败（${response.status}）` }));
      throw new ApiError(payload.detail, response.status);
    }
    return response.json() as Promise<T>;
  }

  createBatch(payload: BatchCreate) {
    return this.request<{ request_id: string; batch: Record<string, unknown> }>('/batches', {
      method: 'POST',
      headers: { 'Idempotency-Key': createRequestId() },
      body: JSON.stringify(payload),
    });
  }

  uploadDocuments(batchId: string, files: File[], evidenceFiles: File[] = []) {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    evidenceFiles.forEach((file) => body.append('evidence_files', file));
    return this.request<{ request_id: string; documents: Record<string, unknown>[] }>(`/batches/${batchId}/documents`, { method: 'POST', body });
  }

  extract(batchId: string) {
    return this.request<{ request_id: string; job: ExtractionJob }>(`/batches/${batchId}/extract`, {
      method: 'POST',
      headers: { 'Idempotency-Key': createRequestId() },
      body: '{}',
    });
  }

  getJob(jobId: string) {
    return this.request<{ job: ExtractionJob; revision?: number; fields?: ExtractedField[] }>(`/jobs/${jobId}`);
  }

  async waitForJob(jobId: string, timeoutMs = 120000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const result = await this.getJob(jobId);
      if (result.job.status === 'SUCCEEDED' || result.job.status === 'MANUAL_REQUIRED') return result;
      if (result.job.status === 'FAILED') throw new ApiError(result.job.error_message || '识别失败，请重试', 422);
      await new Promise((resolve) => window.setTimeout(resolve, 450));
    }
    throw new ApiError('识别任务仍在处理中，请稍后重试', 408);
  }

  patchFields(batchId: string, revision: number, fields: ExtractedField[]) {
    return this.request<{ request_id: string; revision: number; fields: ExtractedField[] }>(`/batches/${batchId}/fields`, { method: 'PATCH', body: JSON.stringify({ revision, fields }) });
  }

  validate(batchId: string) {
    return this.request<{ request_id: string; valid: boolean; errors: string[]; warnings: string[] }>(`/batches/${batchId}/validate`, { method: 'POST', body: '{}' });
  }

  calculate(batchId: string, ruleCode: string, inputs: Record<string, unknown>) {
    return this.request<{ request_id: string; calculation: Record<string, unknown> }>(`/batches/${batchId}/calculate`, { method: 'POST', body: JSON.stringify({ rule_code: ruleCode, inputs }) });
  }

  confirm(calculationId: string, payload: { reviewer: string; override_total?: string | null; reason?: string | null }) {
    return this.request<{ request_id: string; calculation: Record<string, unknown> }>(`/calculations/${calculationId}/confirm`, { method: 'POST', body: JSON.stringify(payload) });
  }

  createExport(calculationId: string, mode: 'formula' | 'values') {
    return this.request<{ request_id: string; export: { id: string; download_url: string } }>(`/calculations/${calculationId}/exports`, { method: 'POST', body: JSON.stringify({ mode }) });
  }
}

export const api = new ApiClient();
