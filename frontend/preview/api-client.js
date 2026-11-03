(() => {
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

  class MonthlyAwardAPI {
    constructor(baseUrl = '/api/v1') {
      this.baseUrl = baseUrl.replace(/\/$/, '');
      this.reviewerToken = window.sessionStorage.getItem('monthly_award_reviewer_token') || '';
      this.adminToken = window.sessionStorage.getItem('monthly_award_admin_token') || '';
      this.uploadedFiles = new Map();
      this.pendingMutations = 0;
      this.mutationVersion = 0;
      this.submitterKey = window.localStorage.getItem('monthly_award_submitter_key') || '';
      if (!/^[A-Za-z0-9_-]{32,128}$/.test(this.submitterKey)) {
        this.submitterKey = (createRequestId() + createRequestId()).replaceAll('-', '');
        window.localStorage.setItem('monthly_award_submitter_key', this.submitterKey);
      }
    }

    setBaseUrl(baseUrl) {
      this.baseUrl = baseUrl.replace(/\/$/, '');
    }

    setReviewerToken(token) {
      this.reviewerToken = token.trim();
      if (this.reviewerToken) {
        window.sessionStorage.setItem('monthly_award_reviewer_token', this.reviewerToken);
      } else {
        window.sessionStorage.removeItem('monthly_award_reviewer_token');
      }
    }

    setAdminToken(token) {
      this.adminToken = token.trim();
      if (this.adminToken) {
        window.sessionStorage.setItem('monthly_award_admin_token', this.adminToken);
      } else {
        window.sessionStorage.removeItem('monthly_award_admin_token');
      }
    }

    async request(path, options = {}) {
      const controller = new AbortController();
      const { timeout: timeoutMs = 30000, ...requestOptions } = options;
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
      const mutation = !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase());
      if (mutation) { this.pendingMutations++; this.mutationVersion++; }
      try {
        const response = await fetch(`${this.baseUrl}${path}`, {
          ...requestOptions,
          credentials: 'include',
          signal: controller.signal,
          headers: options.body instanceof FormData
            ? { 'X-Submitter-Key':this.submitterKey, ...(this.reviewerToken ? { 'X-Reviewer-Token': this.reviewerToken } : {}), ...(options.headers || {}) }
            : {
                'Content-Type': 'application/json',
                'X-Submitter-Key': this.submitterKey,
                ...(this.reviewerToken ? { 'X-Reviewer-Token': this.reviewerToken } : {}),
                ...(options.headers || {}),
              },
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({ detail: `请求失败（${response.status}）` }));
          const detail = error.detail;
          const message = Array.isArray(detail)
            ? detail.map((item) => `${(item.loc || []).join('.')}: ${item.msg || '输入无效'}`).join('；')
            : typeof detail === 'string' ? detail : `请求失败（${response.status}），请稍后重试`;
          const requestError = new Error(message);
          requestError.status = response.status;
          throw requestError;
        }
        return await response.json();
      } catch (error) {
        if (error.name === 'AbortError' || error.name === 'TimeoutError') {
          throw new Error('请求超时，请检查连接后重试；本次已上传材料会保留');
        }
        if (error instanceof TypeError) throw new Error('无法连接服务，请检查网络或等待服务恢复后重试');
        throw error;
      } finally {
        if (mutation) this.pendingMutations--;
        window.clearTimeout(timeout);
      }
    }

    async health() {
      const candidates = ['/health'];
      if (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') {
        candidates.push('http://' + window.location.hostname + ':8000/health');
      }
      let lastError = new Error('后端未就绪');
      for (const healthUrl of candidates) {
        try {
          const response = await fetch(healthUrl, { signal: AbortSignal.timeout(8000) });
          if (!response.ok) throw new Error('后端未就绪');
          if (healthUrl.startsWith('http')) {
            this.setBaseUrl(new URL(healthUrl).origin + '/api/v1');
          }
          return await response.json();
        } catch (error) {
          lastError = error;
        }
      }
      throw new Error(lastError.name === 'TimeoutError' ? '服务响应超时，正在等待恢复' : '暂时无法连接服务，请确认服务已启动');
    }

    createRequestId() { return createRequestId(); }
    createBatch(payload, requestId = createRequestId()) {
      return this.request('/batches', { method: 'POST', headers: { 'Idempotency-Key': requestId }, body: JSON.stringify(payload) });
    }

    formTypes() { return this.request('/form-types'); }
    listBatches(month = '') { return this.request('/batches' + (month ? `?month=${encodeURIComponent(month)}` : '')); }
    getBatch(batchId) { return this.request(`/batches/${batchId}`); }
    getFields(batchId) { return this.request(`/batches/${batchId}/fields`); }
    getReview(batchId) { return this.request(`/batches/${batchId}/review`); }
    listExports() { return this.request('/exports'); }
    deleteExport(id) { return this.request('/exports/'+encodeURIComponent(id), {method:'DELETE'}); }
    adminLogin(password) { return this.request('/admin/login', { method: 'POST', body: JSON.stringify({ password }) }); }
    listAdminBatches() {
      return this.request('/admin/batches', { headers: { Authorization: `Bearer ${this.adminToken}` } });
    }
    deleteAdminBatch(batchId) {
      return this.request(`/admin/batches/${batchId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${this.adminToken}` },
      });
    }

    detectTemplate(file) {
      const body = new FormData();
      body.append('file', file);
      return this.request('/templates/detect', { method: 'POST', body, timeout: 60000 });
    }
    withdrawBatch(batchId) { return this.request(`/batches/${batchId}/withdraw`, { method: 'POST' }); }
    reviseBatch(batchId) { return this.request(`/batches/${batchId}/revisions`, { method: 'POST' }); }
    async resetUpload(batchId) {
      const result = await this.request(`/batches/${batchId}/reset-upload`, { method: 'POST' });
      this.uploadedFiles.delete(batchId);
      return result;
    }

    async uploadDocuments(batchId, files, evidenceFiles = [], onProgress = () => {}) {
      const uploaded = this.uploadedFiles.get(batchId) || new Set();
      this.uploadedFiles.set(batchId, uploaded);
      const queue = [...files.map(file => ({file, role: 'files'})),
        ...evidenceFiles.map(file => ({file, role: 'evidence_files'}))];
      let result;
      for (let index = 0; index < queue.length; index++) {
        const {file, role} = queue[index];
        onProgress(index, queue.length, file.name);
        if (!uploaded.has(file)) {
          const body = new FormData();
          body.append(role, file);
          for (let attempt = 0; ; attempt++) {
            try {
              result = await this.request(`/batches/${batchId}/documents`, { method: 'POST', body, timeout: 120000 });
              uploaded.add(file);
              break;
            } catch (error) {
              if (attempt >= 1 || (error.status && error.status < 500 && error.status !== 408 && error.status !== 429)) throw error;
              await new Promise(resolve => window.setTimeout(resolve, 1000));
            }
          }
        }
        onProgress(index + 1, queue.length, file.name);
      }
      return result;
    }

    extract(batchId, requestId = createRequestId()) {
      return this.request('/batches/' + batchId + '/extract', {
        method: 'POST',
        headers: { 'Idempotency-Key': requestId },
        timeout: 120000,
        body: '{}',
      });
    }
    getJob(jobId) { return this.request('/jobs/' + jobId); }
    async waitForJob(jobId, onProgress, timeoutMs = 120000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const result = await this.getJob(jobId);
        if (onProgress) onProgress(result.job);
        if (result.job.status === 'SUCCEEDED' || result.job.status === 'MANUAL_REQUIRED') return result;
        if (result.job.status === 'FAILED') {
          const error = new Error(result.job.error_message || '识别失败，请重试');
          error.jobFailed = true;
          throw error;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 450));
      }
      throw new Error('识别任务仍在处理中，请稍后重试');
    }
    patchFields(batchId, revision, fields) { return this.request(`/batches/${batchId}/fields`, { method: 'PATCH', body: JSON.stringify({ revision, fields }) }); }
    validate(batchId) { return this.request(`/batches/${batchId}/validate`, { method: 'POST', body: '{}' }); }
    calculate(batchId, ruleCode, inputs) { return this.request(`/batches/${batchId}/calculate`, { method: 'POST', body: JSON.stringify({ rule_code: ruleCode, inputs }) }); }
    confirm(calculationId, payload) { return this.request(`/calculations/${calculationId}/confirm`, { method: 'POST', body: JSON.stringify(payload) }); }
    returnBatch(batchId, reason) { return this.request(`/batches/${batchId}/return`, { method: 'POST', body: JSON.stringify({ reason }) }); }
    initialReview(id,payload) { return this.request(`/calculations/${id}/initial-review`, {method:'POST',body:JSON.stringify(payload)}); }
    approveMeeting(month,revision) { return this.request(`/months/${month}/approve`, {method:'POST',body:JSON.stringify({revision})}); }
    meetingCells(month,payload) { return this.request(`/months/${month}/meeting-cells`, {method:'PATCH',body:JSON.stringify(payload)}); }
    createSupplemental(month, body) { return this.request(`/months/${month}/supplemental-rows`, {method:'POST',body,timeout:120000}); }
    updateSupplemental(month, id, payload) { return this.request(`/months/${month}/supplemental-rows/${encodeURIComponent(id)}`, {method:'PATCH',body:JSON.stringify(payload)}); }
    uploadSupplemental(month, id, body) { return this.request(`/months/${month}/supplemental-rows/${encodeURIComponent(id)}/attachments`, {method:'POST',body,timeout:120000}); }
    async supplementalFile(path) {
      const response = await fetch(this.resolveUrl(path), {headers:this.reviewerToken ? {'X-Reviewer-Token':this.reviewerToken} : {},signal:AbortSignal.timeout(120000)});
      if (!response.ok) throw new Error(`附件读取失败（${response.status}）`);
      return response.blob();
    }
    finalReview(month) { return this.request(`/months/${month}/final-review`); }
    finalAmount(calculationId, total, reason, revision) {
      return this.request(`/calculations/${calculationId}/final-amount`, { method: 'PATCH', body: JSON.stringify({ total, reason, revision }) });
    }
    createExport(calculationId, mode, revision) { return this.request(`/calculations/${calculationId}/exports`, { method: 'POST', timeout: 120000, body: JSON.stringify({ mode, final_review_revision: revision }) }); }
    resolveUrl(path) {
      const origin = this.baseUrl.startsWith('http') ? new URL(this.baseUrl).origin : window.location.origin;
      return new URL(path, origin).href;
    }
    downloadUrl(path) { return this.resolveUrl(path); }
  }

  window.MonthlyAwardAPI = MonthlyAwardAPI;
})();
