(() => {
  const api = new window.MonthlyAwardAPI();
  const appMode = new URLSearchParams(window.location.search).get('mode') === 'reviewer'
    ? 'reviewer'
    : 'employee';
  const reviewerViews = new Set(['dashboard', 'approval', 'secondary', 'meeting', 'exports', 'admin']);
  if (appMode === 'employee') api.setReviewerToken('');
  const state = {
    backendOnline: false,
    reviewerAuthRequired: false,
    health: null,
    formTypes: [],
    ruleVersion: '—',
    batches: [],
    exports: [],
    finalReview: null,
    adminBatches: [],
    activeBatch: null,
    activeRevision: 0,
    activeFields: [],
    activeDocuments: [],
    activeReview: null,
    approvalStage: 'initial',
    selectedReviews: new Set(),
    batchReviewBusy: false,
    currentView: 'new',
    uploadBusy: false,
    materialFiles: [],
    evidenceFiles: [],
    pendingUpload: null,
    connectionBusy: false,
  };

  const storedBatchIds = () => {
    try { const ids = JSON.parse(window.localStorage.getItem('monthly_award_batch_ids') || '[]').filter(id => typeof id === 'string').slice(-1); window.localStorage.setItem('monthly_award_batch_ids', JSON.stringify(ids)); return ids; }
    catch { return []; }
  };
  const rememberBatch = (batch) => {
    try { window.localStorage.setItem('monthly_award_batch_ids', JSON.stringify([batch.id])); } catch { /* Browser may disable persistent storage. */ }
  };

  const titleMap = {
    dashboard: '月奖结算工作台',
    new: appMode === 'employee' ? '员工工作台' : '开始申报',
    review: '申报字段核对',
    approval: '初审', secondary: '复审', meeting: '终审',
    exports: '导出与归档',
    admin: '系统状态',
  };
  const categoryLabels = {
    PROJECT: '项目表',
    EQUIPMENT: '设备类',
    TECHNICAL_SERVICE: '技术服务',
    GROUP: '组别类',
    VEHICLE: '车辆类',
    REPAIR: '维修类',
  };
  const fieldLabels = {
    'project.name': '项目名称',
    'project.contract_income': '合同金额',
    'project.group_equipment_labor_fee': '集团设备使用费及人工费',
    'project.labor_fee': '劳务费',
    'project.equipment_rental_service_fee': '设备租赁费/技术服务费',
    'project.stage_production_fee': '舞美制作费',
    'project.internal_group_equipment_fee': '内部成本 · 集团设备使用费',
    'project.internal_labor_fee': '内部成本 · 人工费',
    'project.internal_cost_total': '内部成本费用合计',
    'project.external_labor_fee': '外购成本 · 劳务费',
    'project.travel_meal_fee': '差旅费 · 伙食费',
    'project.travel_lodging_fee': '差旅费 · 住宿费',
    'project.travel_transportation_fee': '差旅费 · 交通费',
    'project.travel_expense': '差旅费合计',
    'project.equipment_purchase_fee': '设备购置费',
    'project.external_cost_total': '外部成本费用合计',
    'project.misc_expense': '杂费合计',
    'project.misc_handling_fee': '杂费 · 搬运费',
    'project.misc_consumables_fee': '杂费 · 设备耗材费',
    'project.misc_courier_fee': '杂费 · 快递费',
    'project.misc_venue_fee': '杂费 · 场租费',
    'project.misc_other_fee': '杂费 · 其它费用',
    'project.total_expense': '支出总计',
    'project.gross_profit_candidate': '节余（候选项目毛利）',
    'project.gross_profit': '项目毛利',
    'project.owner': '项目负责人',
    'project.department': '申报部门',
    'project.month': '结算月份',
    'equipment.fee': '设备使用费',
    'technical_service.fee': '技术服务费',
    'meeting.session_count': '会议场次数',
    'vehicle.type': '车型',
    'vehicle.monthly_km': '当月累计里程',
    'vehicle.half_days': '半天数',
    'vehicle.full_days': '全天数',
    'repair.internal_quote_total': '内部维修申报价',
    'repair.external_repair_total': '对外维修总计',
    'repair.parts_cost': '零件采购费',
  };
  const ruleByFormType = {
    PROJECT_SETTLEMENT: 'PROJECT_EXTERNAL',
    PROJECT_BUDGET: 'PROJECT_EXTERNAL',
    EQUIPMENT_USAGE: 'EQUIPMENT_USAGE',
    EFP_SERVICE: 'PROJECT_EFP',
    SCREEN_SERVICE: 'PROJECT_SCREEN',
    MEETING_AUDIO: 'GROUP_MEETING_AUDIO',
    AUDIO_POST: 'GROUP_AUDIO_POST',
    NONLINEAR: 'GROUP_NONLINEAR',
    DRIVER: 'GROUP_DRIVER',
    SONY_REPAIR: 'GROUP_SONY_REPAIR',
  };
  const statusMeta = {
    DRAFT: ['草稿', 'status-neutral'],
    UPLOADED: ['已上传', 'status-info'],
    EXTRACTING: ['识别中', 'status-info'],
    REVIEW_REQUIRED: ['待字段核对', 'status-warning'],
    WAITING_FOR_EVIDENCE: ['待补原件', 'status-warning'],
    READY_TO_CALCULATE: ['待计算', 'status-info'],
    CALCULATED: ['待初审', 'status-purple'],
    MANUAL_APPROVAL_REQUIRED: ['待初审', 'status-warning'],
    INITIAL_REVIEWED: ['待复审', 'status-info'],
    CONFIRMED: ['待会议终审', 'status-success'],
    EXPORTED: ['已导出', 'status-success'],
    FAILED: ['处理失败', 'status-warning'],
    SUPERSEDED: ['已由修订替代', 'status-neutral'],
  };

  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
  const parseAmount = (value) => Number(String(value ?? '').replace(/[^\d.-]/g, '')) || 0;
  const formatMoney = (value, digits = 0) => `¥${Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
  const formatMonth = (month) => {
    if (!/^\d{4}-\d{2}$/.test(month || '')) return month || '—';
    const [year, number] = month.split('-');
    return `${year} 年 ${Number(number)} 月`;
  };
  const formatDateTime = (value) => {
    if (!value) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(value));
  };
  const formatSize = (bytes) => {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  };
  const formTypeMeta = (code) => state.formTypes.find((item) => item.code === code) || { code, label: code || '—' };
  const fieldValue = (fields, path) => fields.find((item) => item.path === path)?.value_normalized;
  const keyAuditFieldPaths = new Set([
    'project.contract_income',
    'project.total_expense',
    'project.gross_profit_candidate',
    'project.gross_profit',
  ]);
  const budgetComponentPaths = [
    'project.group_equipment_labor_fee',
    'project.labor_fee',
    'project.equipment_rental_service_fee',
    'project.stage_production_fee',
  ];
  const budgetMiscPaths = [
    'project.misc_handling_fee',
    'project.misc_consumables_fee',
    'project.misc_courier_fee',
    'project.misc_venue_fee',
    'project.misc_other_fee',
  ];
  const budgetDerivedPaths = new Set([
    'project.misc_expense',
    'project.total_expense',
    'project.gross_profit_candidate',
  ]);
  const settlementDerivedPaths = new Set([
    'project.internal_cost_total', 'project.travel_expense', 'project.misc_expense',
    'project.external_cost_total', 'project.total_expense', 'project.gross_profit_candidate',
  ]);
  const budgetFieldOrder = [
    'project.name', 'project.contract_income', 'project.group_equipment_labor_fee',
    'project.labor_fee', 'project.equipment_rental_service_fee', 'project.stage_production_fee',
    'project.misc_handling_fee', 'project.misc_consumables_fee', 'project.misc_courier_fee',
    'project.misc_venue_fee', 'project.misc_other_fee', 'project.misc_expense',
    'project.total_expense', 'project.gross_profit_candidate',
  ];
  const settlementFieldOrder = [
    'project.name', 'project.contract_income', 'project.internal_group_equipment_fee',
    'project.internal_labor_fee', 'project.internal_cost_total', 'project.external_labor_fee',
    'project.travel_meal_fee', 'project.travel_lodging_fee', 'project.travel_transportation_fee',
    'project.travel_expense', 'project.equipment_rental_service_fee', 'project.stage_production_fee',
    'project.equipment_purchase_fee', 'project.misc_handling_fee', 'project.misc_consumables_fee',
    'project.misc_courier_fee', 'project.misc_venue_fee', 'project.misc_other_fee',
    'project.misc_expense', 'project.external_cost_total', 'project.total_expense',
    'project.gross_profit_candidate',
  ];
  const orderFields = (fields, formType) => {
    const order = formType === 'PROJECT_BUDGET' ? budgetFieldOrder
      : formType === 'PROJECT_SETTLEMENT' ? settlementFieldOrder
        : formType === 'STD_PROJECT' ? ['project.name', 'project.owner', 'project.introducer', 'standard.equipment_fee', 'standard.efp_fee', 'standard.screen_fee', 'standard.gross_profit'] : null;
    const rank = (path) => order.includes(path) ? order.indexOf(path) : order.length;
    return order ? [...fields].sort((left, right) => rank(left.path) - rank(right.path)) : fields;
  };

  let toastTimer;
  const showToast = (text, isError = false) => {
    const toast = $('#app-toast');
    $('#toast-text').textContent = text;
    toast.classList.toggle('is-error', isError);
    toast.querySelector('span').textContent = isError ? '!' : '✓';
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3600);
  };

  const openReviewerLogin = () => {
    const dialog = $('#reviewer-login-dialog');
    $('#reviewer-login-error').hidden = true;
    $('#reviewer-login-error').textContent = '';
    $('#reviewer-admin-password').value = '';
    $('#reviewer-admin-password').type = 'password';
    document.querySelector('[data-toggle-password="reviewer-admin-password"]').textContent = '显示';
    dialog.showModal();
    window.setTimeout(() => $('#reviewer-admin-password').focus(), 0);
  };

  const closeReviewerLogin = () => {
    const dialog = $('#reviewer-login-dialog');
    if (dialog.open) dialog.close();
  };

  const showView = (view) => {
    const allowedView = appMode === 'employee' && reviewerViews.has(view) ? 'new' : view;
    state.currentView = allowedView;
    if (['approval','secondary'].includes(allowedView)) {
      state.approvalStage = allowedView === 'secondary' ? 'secondary' : 'initial';
      state.activeReview = null;
      $('#approval-heading').textContent = allowedView === 'secondary' ? '复审' : '初审';
      $('#approval-description').textContent = allowedView === 'secondary' ? '复核初审意见、材料和金额，通过后进入办公会议终审。' : '核对申报材料和金额，通过后交由复审。';
      $('#approval-list-title').textContent = allowedView === 'secondary' ? '待复审申报' : '待初审申报';
      $('#approval-confirm').textContent = allowedView === 'secondary' ? '复审通过，送终审' : '初审通过，送复审';
    }
    if (allowedView !== 'meeting') document.body.classList.remove('projection-mode');
    document.querySelectorAll('[data-view]').forEach((panel) => { panel.hidden = panel.dataset.view !== (allowedView === 'secondary' ? 'approval' : allowedView); });
    const activeNav = allowedView === 'review' ? 'new' : allowedView;
    document.querySelectorAll('.sidebar [data-nav]').forEach((item) => item.classList.toggle('active', item.dataset.nav === activeNav));
    $('#employee-submissions').hidden = allowedView !== 'new';
    $('#topbar-view-title').textContent = titleMap[allowedView] || titleMap.new;
    document.title = `员工月奖自动核算系统 · ${titleMap[allowedView] || titleMap.new}`;
    if (['new', 'dashboard', 'approval', 'secondary', 'meeting', 'exports'].includes(allowedView)) refreshBusinessData({ silent: true });
    if (allowedView === 'admin' && api.adminToken) loadAdminBatches({ silent: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const updateBatchPreview = () => {
    const month = $('#settlement-month').value;
    $('#batch-month-preview').textContent = month ? `${formatMonth(month)}结算` : '请选择结算月份';
    $('#owner-preview').textContent = $('#project-owner').value.trim() || '未填写';
    const selected = $('#form-type').selectedOptions[0];
    $('#form-preview').textContent = selected?.textContent || '请选择';
    const meta = formTypeMeta($('#form-type').value);
    const link = $('#standard-template-download');
    if (link) {
      link.hidden = !meta.template_url;
      link.href = meta.template_url ? api.resolveUrl(meta.template_url) : '#';
    }
  };

  const renderFormTypeOptions = () => {
    const category = $('#business-category').value;
    const items = state.formTypes.filter((item) => item.category === category);
    $('#form-type').innerHTML = items.map((item) => `<option value="${escapeHtml(item.code)}">${escapeHtml(item.label)}</option>`).join('');
    updateBatchPreview();
  };

  const initializeFormTypes = () => {
    const categories = [...new Set(state.formTypes.map((item) => item.category))];
    $('#business-category').innerHTML = categories.map((code) => `<option value="${escapeHtml(code)}">${escapeHtml(categoryLabels[code] || code)}</option>`).join('');
    renderFormTypeOptions();
    $('#rule-version-preview').textContent = state.ruleVersion;
    $('#review-rule-version').textContent = state.ruleVersion;
    $('#system-rule-version').textContent = state.ruleVersion;
    $('#system-form-count').textContent = `${state.formTypes.length} 种已发布表格类型`;
  };

  const renderSelectedFiles = (containerId, files, statusText, statusClass = 'status-info') => {
    const container = $(containerId);
    if (!files.length) {
      container.hidden = true;
      container.innerHTML = '';
      return;
    }
    container.hidden = false;
    const kind = containerId.includes('evidence') ? 'evidence' : 'material';
    container.innerHTML = files.map((file, index) => {
      const suffix = file.name.split('.').pop()?.toUpperCase() || 'FILE';
      const alreadyUploaded = ['上传中','待重试','处理失败'].includes(statusText) && api.uploadedFiles?.get(state.pendingUpload?.batch?.id)?.has(file);
      const label = alreadyUploaded ? '已上传，无需重传' : statusText;
      const css = alreadyUploaded ? 'status-success' : statusClass;
      return `<div class="uploaded-file"><span class="file-type">${escapeHtml(suffix.slice(0, 4))}</span><div><b>${escapeHtml(file.name)}</b><p>${formatSize(file.size)} · 本机文件</p></div><span class="status-tag ${css}">${escapeHtml(label)}</span><button type="button" data-remove-file="${kind}" data-file-index="${index}" aria-label="移除 ${escapeHtml(file.name)}" ${state.uploadBusy ? 'disabled' : ''}>移除</button></div>`;
    }).join('');
  };

  const validateFileGroup = (files, allowed, label) => {
    for (const file of files) {
      const suffix = file.name.split('.').pop()?.toLowerCase();
      if (!allowed.has(suffix)) throw new Error(`${file.name} 不是支持的${label}格式`);
      if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10MB`);
    }
  };

  const validateUploadFiles = (materials, evidence) => {
    if ($('#form-type').value.startsWith('STD_')) {
      if (materials.length !== 1) throw new Error('每次申报请选择一份标准 XLSX 主表');
      validateFileGroup(materials, new Set(['xlsx']), '标准表格');
    }
    if (!materials.length) throw new Error('请至少选择一份 Excel 文件或 Excel 截图');
    if (materials.length + evidence.length > 30) throw new Error('单批次最多上传 30 个文件');
    validateFileGroup(materials, new Set(['jpg', 'jpeg', 'png', 'xlsx']), '申报材料');
    validateFileGroup(evidence, new Set(['jpg', 'jpeg', 'png']), '证明附件');
  };

  const updateUploadReady = () => {
    $('#start-upload').disabled = state.uploadBusy || !state.backendOnline || !state.formTypes.length || state.materialFiles.length === 0;
  };

  const setUploadBusy = (busy) => {
    state.uploadBusy = busy;
    $('#material-upload-zone').disabled = busy;
    $('#evidence-upload-zone').disabled = busy;
    $('#material-file-input').disabled = busy;
    $('#evidence-file-input').disabled = busy;
    ['#settlement-month', '#project-owner', '#business-category', '#form-type'].forEach((selector) => {
      $(selector).disabled = busy || Boolean(state.pendingUpload?.batch);
    });
    updateUploadReady();
  };

  const documentGalleryHtml = (documents) => {
    if (!documents.length) return '<div class="empty-state"><span>件</span><h3>未找到上传原件</h3><p>请返回申报页重新上传。</p></div>';
    return documents.map((document) => {
      const isImage = String(document.mime || '').startsWith('image/');
      const url = api.resolveUrl(isImage ? document.preview_url : document.workbook_preview_url || `/api/v1/documents/${document.id}/preview`);
      const downloadUrl = api.resolveUrl(document.download_url || document.preview_url);
      const fileName = escapeHtml(document.file_name);
      const roleLabel = document.document_role === 'EVIDENCE' ? '证明附件' : '申报材料';
      const roleClass = document.document_role === 'EVIDENCE' ? 'document-role-evidence' : 'document-role-material';
      if (isImage) {
        return `<article class="document-card"><div class="document-card-head"><div><span class="document-role ${roleClass}">${roleLabel}</span><b>${fileName}</b><small>${formatSize(document.size_bytes)}</small></div><a href="${escapeHtml(url)}" target="_blank" rel="noopener">直接打开 ↗</a><a href="${escapeHtml(downloadUrl)}" download>下载原文件</a></div><a class="document-image-link" href="${escapeHtml(url)}" target="_blank" rel="noopener"><img src="${escapeHtml(url)}" alt="${roleLabel}：${fileName}" /></a></article>`;
      }
      return `<article class="document-card workbook-card"><span class="workbook-icon">X</span><div><span class="document-role ${roleClass}">${roleLabel}</span><b>${fileName}</b><p>${formatSize(document.size_bytes)} · XLSX 工作簿</p><small>在线打开即可查看表格，下载原文件请使用独立按钮。</small></div><div class="document-actions"><a class="secondary-button" href="${escapeHtml(url)}" target="_blank" rel="noopener">直接打开 ↗</a><a class="document-download" href="${escapeHtml(downloadUrl)}" download>下载原文件</a></div></article>`;
    }).join('');
  };

  const recalculateProjectReview = () => {
    if (!['PROJECT_BUDGET', 'PROJECT_SETTLEMENT'].includes(state.activeBatch?.form_type)) return;
    const inputFor = (path) => {
      const index = state.activeFields.findIndex((field) => field.path === path);
      return index >= 0 ? document.querySelector(`.field-value[data-field-index="${index}"]`) : null;
    };
    const readAmounts = (paths) => paths.map((path) => inputFor(path)?.value.trim() ?? '');
    const miscValues = readAmounts(budgetMiscPaths);
    const miscInput = inputFor('project.misc_expense');
    if (miscInput && miscValues.every((value) => value !== '')) {
      miscInput.value = miscValues.reduce((sum, value) => sum + parseAmount(value), 0).toFixed(2);
    }
    if (state.activeBatch.form_type === 'PROJECT_SETTLEMENT') {
      const calculateInto = (targetPath, sourcePaths) => {
        const target = inputFor(targetPath);
        const values = readAmounts(sourcePaths);
        if (target && values.every((value) => value !== '')) {
          target.value = values.reduce((sum, value) => sum + parseAmount(value), 0).toFixed(2);
        }
        return target;
      };
      const internal = calculateInto('project.internal_cost_total', [
        'project.internal_group_equipment_fee', 'project.internal_labor_fee',
      ]);
      calculateInto('project.travel_expense', [
        'project.travel_meal_fee', 'project.travel_lodging_fee', 'project.travel_transportation_fee',
      ]);
      const external = calculateInto('project.external_cost_total', [
        'project.external_labor_fee', 'project.travel_expense',
        'project.equipment_rental_service_fee', 'project.stage_production_fee',
        'project.equipment_purchase_fee', 'project.misc_expense',
      ]);
      const totalInput = inputFor('project.total_expense');
      if (totalInput && internal?.value.trim() && external?.value.trim()) {
        totalInput.value = (parseAmount(internal.value) + parseAmount(external.value)).toFixed(2);
      }
      const incomeInput = inputFor('project.contract_income');
      const profitInput = inputFor('project.gross_profit_candidate');
      if (profitInput && incomeInput?.value.trim() && totalInput?.value.trim()) {
        profitInput.value = (parseAmount(incomeInput.value) - parseAmount(totalInput.value)).toFixed(2);
      }
      return;
    }
    const componentValues = readAmounts(budgetComponentPaths);
    const totalInput = inputFor('project.total_expense');
    if (totalInput && miscInput?.value.trim() && componentValues.every((value) => value !== '')) {
      totalInput.value = (componentValues.reduce((sum, value) => sum + parseAmount(value), 0)
        + parseAmount(miscInput.value)).toFixed(2);
    }
    const incomeInput = inputFor('project.contract_income');
    const profitInput = inputFor('project.gross_profit_candidate');
    if (profitInput && incomeInput?.value.trim() && totalInput?.value.trim()) {
      profitInput.value = (parseAmount(incomeInput.value) - parseAmount(totalInput.value)).toFixed(2);
    }
  };

  const renderFieldReview = ({ batch, documents, revision, fields }) => {
    fields = orderFields(fields, batch.form_type);
    state.activeBatch = batch;
    state.activeDocuments = documents;
    state.activeRevision = revision;
    state.activeFields = fields;
    $('#review-batch-no').textContent = batch.batch_no;
    $('#review-status').textContent = statusMeta[batch.status]?.[0] || batch.status;
    $('#batch-no-preview').textContent = batch.batch_no;
    const materialCount = documents.filter((document) => document.document_role !== 'EVIDENCE').length;
    const evidenceCount = documents.filter((document) => document.document_role === 'EVIDENCE').length;
    $('#review-document-count').textContent = `${materialCount} 份材料 · ${evidenceCount} 份证明`;
    $('#review-documents').innerHTML = documentGalleryHtml(documents);
    const hasValue = (field) => field.value_normalized !== null && field.value_normalized !== '';
    const recognizedCount = fields.filter(hasValue).length;
    const missingCount = fields.length - recognizedCount;
    const lowCount = fields.filter((field) => hasValue(field) && Number(field.confidence) < 0.9).length;
    $('#field-heading').textContent = recognizedCount === fields.length
      ? `已识别 ${recognizedCount} 个字段`
      : `识别到 ${recognizedCount}/${fields.length} 个字段`;
    $('#field-confidence-summary').textContent = missingCount
      ? `${missingCount} 项未识别，需人工补充`
      : lowCount ? `${lowCount} 项需重点核对` : '识别结果完整，请逐项确认';
    $('#field-list').innerHTML = fields.length ? fields.map((field, index) => {
      const confidence = Math.round(Number(field.confidence || 0) * 100);
      const missing = !hasValue(field);
      const derived = (batch.form_type === 'PROJECT_BUDGET' && budgetDerivedPaths.has(field.path))
        || (batch.form_type === 'PROJECT_SETTLEMENT' && settlementDerivedPaths.has(field.path));
      return `<label class="field-row ${missing || confidence < 90 ? 'low-confidence' : ''} ${keyAuditFieldPaths.has(field.path) ? 'key-audit-field' : ''}"><span class="field-label">${escapeHtml(fieldLabels[field.path] || field.path)}<small>${escapeHtml(field.path)}${field.page_no ? ` · 第 ${field.page_no} 页/工作表` : ''}${derived ? ' · 系统复算' : ''}</small></span><input class="field-value" data-field-index="${index}" value="${escapeHtml(field.value_normalized ?? '')}" autocomplete="off" ${derived ? 'readonly title="根据费用明细自动计算"' : ''} /><span class="confidence"><i style="width:${Math.min(46, confidence * 0.46)}px"></i><b>${missing ? '未识别' : `${confidence}%`}</b></span></label>`;
    }).join('') : '<div class="empty-state"><span>字</span><h3>没有识别到字段</h3><p>请确认文件内容，或改用人工可录入的图片表单。</p></div>';
    recalculateProjectReview();
    $('#confirm-fields').checked = false;
    $('#review-submit').disabled = true;
    showView('review');
  };

  const openFieldReview = async (batchId) => {
    try {
      const [batchResult, fieldsResult] = await Promise.all([api.getBatch(batchId), api.getFields(batchId)]);
      renderFieldReview({
        batch: batchResult.batch,
        documents: batchResult.documents,
        revision: fieldsResult.revision,
        fields: fieldsResult.fields,
      });
    } catch (error) {
      showToast(error.message, true);
    }
  };

  const retryRecognition = async () => {
    if (!state.activeBatch || !state.activeDocuments.length) return;
    if (!window.confirm('重新识别会覆盖当前尚未提交的字段修改，是否继续？')) return;
    const button = $('#retry-recognition');
    button.disabled = true;
    button.textContent = '正在重新识别…';
    try {
      const started = await api.extract(state.activeBatch.id);
      const extracted = await api.waitForJob(started.job.id, (job) => {
        button.textContent = `重新识别 ${Number(job.progress || 0)}%`;
      });
      const batchResult = await api.getBatch(state.activeBatch.id);
      renderFieldReview({
        batch: batchResult.batch,
        documents: batchResult.documents,
        revision: extracted.revision,
        fields: extracted.fields,
      });
      showToast(extracted.job.status === 'MANUAL_REQUIRED'
        ? (extracted.job.error_message || '部分字段仍需人工补充')
        : '重新识别完成，请逐项确认');
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '重新识别';
    }
  };

  const processRealFiles = async () => {
    if (state.uploadBusy) return;
    const materials = [...state.materialFiles];
    const evidence = [...state.evidenceFiles];
    let succeeded = false;
    try {
      if (!state.backendOnline) throw new Error('后端尚未连接，不能创建真实批次');
      validateUploadFiles(materials, evidence);
      if (!$('#settlement-month').value) throw new Error('请选择结算月份');
      setUploadBusy(true);
      $('#upload-error').hidden = true;
      if (!state.pendingUpload?.batch && materials[0].name.toLowerCase().endsWith('.xlsx')) {
        const detected = await api.detectTemplate(materials[0]);
        if (detected.code !== $('#form-type').value) {
          if (!window.confirm(`当前选择“${formTypeMeta($('#form-type').value).label}”，文件识别为“${detected.label}”。是否切换到匹配类型并继续上传？`)) return;
          $('#business-category').value = detected.category;
          renderFormTypeOptions();
          $('#form-type').value = detected.code;
          updateBatchPreview();
        }
      }
      renderSelectedFiles('#selected-material-files', materials, '准备上传');
      renderSelectedFiles('#selected-evidence-files', evidence, '准备上传');
      const payload = {
        month: $('#settlement-month').value,
        project_owner: $('#project-owner').value.trim() || null,
        business_category: $('#business-category').value,
        form_type: $('#form-type').value,
      };
      if (!state.pendingUpload) state.pendingUpload = { createRequestId: api.createRequestId(), payload, batch: null };
      const pending = state.pendingUpload;
      if (!pending.batch) {
        const created = await api.createBatch(pending.payload, pending.createRequestId);
        pending.batch = created.batch;
        rememberBatch(created.batch);
      }
      if (pending.needsReset) {
        if (!window.confirm('所选材料已更改，是否清除本批次旧上传内容并重新上传？')) return;
        const reset = await api.resetUpload(pending.batch.id);
        pending.batch = reset.batch;
        pending.uploaded = false;
        pending.jobId = null;
        pending.extractRequestId = null;
        pending.needsReset = false;
      }
      state.activeBatch = pending.batch;
      $('#batch-no-preview').textContent = pending.batch.batch_no;
      renderSelectedFiles('#selected-material-files', materials, '上传中');
      renderSelectedFiles('#selected-evidence-files', evidence, '上传中');
      if (!pending.uploaded) {
        await api.uploadDocuments(pending.batch.id, materials, evidence, (done, total, name) => {
          $('#start-upload').textContent = `已上传 ${done}/${total}：${name}`;
          renderSelectedFiles('#selected-material-files', materials, '上传中');
          renderSelectedFiles('#selected-evidence-files', evidence, '上传中');
        });
        pending.uploaded = true;
      }
      renderSelectedFiles('#selected-material-files', materials, 'OCR 识别中');
      renderSelectedFiles('#selected-evidence-files', evidence, '已存档', 'status-success');
      if (!pending.jobId) {
        pending.extractRequestId ||= api.createRequestId();
        const started = await api.extract(pending.batch.id, pending.extractRequestId);
        pending.jobId = started.job.id;
      }
      const extracted = await api.waitForJob(pending.jobId, (job) => {
        const engine = job.provider === 'LOCAL_OCR' ? '本地 OCR' : job.provider === 'LOCAL_XLSX' ? 'Excel 提取' : '人工字段准备';
        renderSelectedFiles('#selected-material-files', materials, `${engine} ${Number(job.progress || 0)}%`);
      });
      const batchResult = await api.getBatch(pending.batch.id);
      renderSelectedFiles('#selected-material-files', materials, '识别完成', 'status-success');
      renderFieldReview({
        batch: batchResult.batch,
        documents: batchResult.documents,
        revision: extracted.revision,
        fields: extracted.fields,
      });
      succeeded = true;
      api.uploadedFiles?.delete(pending.batch.id);
      state.pendingUpload = null;
      showToast(extracted.job.status === 'MANUAL_REQUIRED'
        ? (extracted.job.error_message || 'OCR 未能确认全部字段，请人工填写并核对')
        : '材料识别完成，请逐项核对');
      refreshBusinessData({ silent: true });
    } catch (error) {
      if (error.jobFailed && state.pendingUpload) {
        state.pendingUpload.jobId = null;
        state.pendingUpload.extractRequestId = null;
      }
      renderSelectedFiles('#selected-material-files', materials, '处理失败', 'status-warning');
      renderSelectedFiles('#selected-evidence-files', evidence, '处理失败', 'status-warning');
      $('#upload-error').textContent = `${error.message}。${state.pendingUpload?.batch ? `已保留批次 ${state.pendingUpload.batch.batch_no}，点击重试会继续该批次。` : '所选文件已保留，可检查后重试。'}`;
      $('#upload-error').hidden = false;
      showToast(error.message, true);
    } finally {
      setUploadBusy(false);
      $('#material-file-input').value = '';
      $('#evidence-file-input').value = '';
      if (succeeded) {
        state.materialFiles = [];
        state.evidenceFiles = [];
        renderSelectedFiles('#selected-material-files', [], '待上传');
        renderSelectedFiles('#selected-evidence-files', [], '待上传');
      } else {
        renderSelectedFiles('#selected-material-files', state.materialFiles, '待重试');
        renderSelectedFiles('#selected-evidence-files', state.evidenceFiles, '待重试');
      }
      $('#start-upload').textContent = state.pendingUpload ? '重试本次上传 →' : '上传并开始核对 →';
      updateUploadReady();
    }
  };

  const prepareBatchUpload = (batch) => {
    rememberBatch(batch);
    state.activeBatch = batch;
    state.pendingUpload = { batch, uploaded: false };
    state.materialFiles = [];
    state.evidenceFiles = [];
    $('#settlement-month').value = batch.month;
    $('#project-owner').value = batch.project_owner || '';
    $('#business-category').value = batch.business_category;
    renderFormTypeOptions();
    $('#form-type').value = batch.form_type;
    $('#batch-no-preview').textContent = batch.batch_no;
    $('#upload-context').textContent = batch.supersedes_batch_id
      ? `正在填写修订批次 ${batch.batch_no}。请重新上传完整材料；修订审核通过前，原已审核版本继续有效。`
      : `正在修改批次 ${batch.batch_no}，请重新选择主表和证明附件。`;
    $('#upload-context').hidden = false;
    $('#upload-error').hidden = true;
    $('#start-upload').textContent = '上传并开始核对 →';
    renderSelectedFiles('#selected-material-files', [], '待上传');
    renderSelectedFiles('#selected-evidence-files', [], '待上传');
    updateBatchPreview();
    setUploadBusy(false);
    showView('new');
  };

  const returnToUpload = async () => {
    if (!state.activeBatch) return;
    if (!window.confirm('返回修改将清除本批次已上传材料和识别字段，请重新选择完整材料。是否继续？')) return;
    const button = $('#review-change-upload');
    button.disabled = true;
    try {
      const result = await api.resetUpload(state.activeBatch.id);
      prepareBatchUpload(result.batch);
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  };

  const changeSubmittedBatch = async (batchId, action, button) => {
    if (state.uploadBusy) return;
    if (state.materialFiles.length && !window.confirm('切换批次会清空当前所选文件，是否继续？')) return;
    const message = action === 'withdraw'
      ? '撤回后本次计算将作废，可修改字段或重新上传，再次提交审核。是否撤回？'
      : '创建修订后需重新上传完整材料并审核；修订通过前保留原已审核结果。是否继续？';
    if (!window.confirm(message)) return;
    button.disabled = true;
    try {
      const result = action === 'withdraw' ? await api.withdrawBatch(batchId) : await api.reviseBatch(batchId);
      if (action === 'withdraw') {
        state.pendingUpload = null;
        state.materialFiles = [];
        state.evidenceFiles = [];
        await openFieldReview(result.batch.id);
      } else prepareBatchUpload(result.batch);
      await refreshBusinessData({ silent: true });
      showToast(action === 'withdraw' ? '已撤回，可修改后重新提交' : '修订草稿已准备，请上传完整材料');
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  };

  const submitFieldReview = async () => {
    if (!state.activeBatch) return;
    const button = $('#review-submit');
    button.disabled = true;
    button.textContent = '正在校验并送审…';
    try {
      const fields = state.activeFields.map((field, index) => {
        const input = document.querySelector(`.field-value[data-field-index="${index}"]`);
        return {
          path: field.path,
          value_raw: field.value_raw ?? input.value,
          value_normalized: input.value.trim() || null,
          confidence: field.confidence ?? 0,
          source_document_id: field.source_document_id ?? null,
          page_no: field.page_no ?? null,
          bounding_box: field.bounding_box ?? null,
          confirmed: true,
        };
      });
      const patched = await api.patchFields(state.activeBatch.id, state.activeRevision, fields);
      state.activeRevision = patched.revision;
      state.activeFields = patched.fields;
      const validation = await api.validate(state.activeBatch.id);
      if (!validation.valid) throw new Error(validation.errors.join('；'));
      const ruleCode = ruleByFormType[state.activeBatch.form_type];
      if (!ruleCode) throw new Error('当前表格类型尚未配置核算规则');
      await api.calculate(state.activeBatch.id, ruleCode, {});
      $('#upload-context').hidden = true;
      $('#batch-no-preview').textContent = '上传后自动生成';
      showToast(appMode === 'employee'
        ? '字段已确认并送审核，可继续新建申报'
        : '字段已确认并完成系统核算，批次已送审核');
      await refreshBusinessData({ silent: true });
      if (appMode === 'reviewer') {
        showView('approval');
        await openApproval(state.activeBatch.id);
      } else {
        showView('new');
      }
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.textContent = '确认字段并提交审核 →';
      button.disabled = !$('#confirm-fields').checked;
    }
  };

  const statusTag = (status) => {
    const [label, className] = statusMeta[status] || [status, 'status-neutral'];
    return `<span class="status-tag ${className}">${escapeHtml(label)}</span>`;
  };

  const batchLabel = (batch) => `${formTypeMeta(batch.form_type).label}${batch.project_name ? ` · ${batch.project_name}` : ''}`;

  const renderSubmissions = () => {
    const returned = state.batches.filter(batch => batch.return_notice && !batch.excluded && !batch.superseded_by_batch_id);
    const badge = $('#review-badge');
    badge.textContent = String(returned.length);
    badge.hidden = returned.length === 0;
    badge.title = `${returned.length} 份申报被初审退回，待修改后重新提交`;
    const items = state.batches.filter(batch => !batch.excluded &&
      (batch.month === $('#settlement-month').value || batch.return_notice));
    $('#submission-items').innerHTML = items.length ? items.map(batch => {
      const status = batch.superseded_by_batch_id ? 'SUPERSEDED' : batch.status;
      let action = '';
      if (['CALCULATED','MANUAL_APPROVAL_REQUIRED','READY_TO_CALCULATE'].includes(status)) action = `<button class="secondary-button" data-submission-id="${escapeHtml(batch.id)}" data-submission-action="withdraw">撤回修改</button>`;
      else if (['CONFIRMED','EXPORTED'].includes(status)) action = `<button class="secondary-button" disabled title="已进入终审，材料锁定">重新选择材料（已锁定）</button>`;
      else if (status === 'REVIEW_REQUIRED') action = `<button class="secondary-button" data-resume-review="${escapeHtml(batch.id)}">继续核对</button>`;
      else if (['DRAFT','UPLOADED','FAILED'].includes(status)) action = `<button class="secondary-button" data-replace-upload="${escapeHtml(batch.id)}">重新选择材料</button>`;
      const notice = batch.return_notice ? `<p class="status-warning">${escapeHtml(formatMonth(batch.month))} · 初审退回：${escapeHtml(batch.return_notice.reason)} · 请修改后重新提交</p>` : '';
      const tag = batch.return_notice ? '<span class="status-tag status-warning">初审退回 · 待修改</span>' : statusTag(status);
      return `<div class="submission-row"><div><b>${escapeHtml(batch.batch_no)}</b><p>${escapeHtml(batchLabel(batch))} · ${escapeHtml(batch.project_owner || '未填写负责人')}</p>${notice}</div>${tag}${action}<a href="${api.resolveUrl(`/batches/${batch.id}/evidence`.replace('/batches/','/api/v1/batches/'))}" target="_blank" rel="noopener">查看材料</a></div>`;
    }).join('') : '<p>当前月份暂无本机申报。这里只显示在本机浏览器上传的材料。</p>';
  };

  const renderDashboard = () => {
    const month = $('#settlement-month').value;
    const items = state.batches.filter((batch) => batch.month === month);
    const reviewCount = items.filter((batch) => batch.status === 'REVIEW_REQUIRED').length;
    const approvalCount = items.filter((batch) => ['CALCULATED', 'MANUAL_APPROVAL_REQUIRED', 'INITIAL_REVIEWED'].includes(batch.status)).length;
    const confirmed = items.filter((batch) => ['CONFIRMED', 'EXPORTED'].includes(batch.status));
    const total = confirmed.reduce((sum, batch) => sum + parseAmount(batch.calculation?.confirmed_total), 0);
    $('#dashboard-month').textContent = `${formatMonth(month)} · 真实业务数据`;
    $('#dashboard-summary').textContent = items.length ? `本月共有 ${items.length} 个真实结算批次。` : '本月尚未创建任何真实结算批次。';
    $('#metric-batches').textContent = String(items.length);
    $('#metric-review').textContent = String(reviewCount);
    $('#metric-approval').textContent = String(approvalCount);
    $('#metric-total').textContent = formatMoney(total);
    const body = $('#dashboard-table-body');
    body.innerHTML = state.batches.map((batch) => `<tr data-open-batch="${escapeHtml(batch.id)}" data-status="${escapeHtml(batch.status)}"><td><b>${escapeHtml(batch.batch_no)}</b><small>${escapeHtml(batch.project_owner || '未填写项目负责人')}</small></td><td>${escapeHtml(formatMonth(batch.month))}</td><td>${escapeHtml(batchLabel(batch))}</td><td>${escapeHtml(formatDateTime(batch.updated_at))}</td><td>${statusTag(batch.status)}</td></tr>`).join('');
    $('#dashboard-empty').hidden = state.batches.length > 0;
  };

  const renderApprovalList = () => {
    const first = state.batches.filter(batch => ['CALCULATED','MANUAL_APPROVAL_REQUIRED'].includes(batch.status));
    const second = state.batches.filter(batch => batch.status === 'INITIAL_REVIEWED');
    const pending = state.approvalStage === 'secondary' ? second : first;
    state.selectedReviews = new Set([...state.selectedReviews].filter(id => second.some(batch => batch.id === id)));
    $('#secondary-batch-tools').hidden = state.approvalStage !== 'secondary';
    $('#secondary-select-all').checked = second.length > 0 && state.selectedReviews.size === second.length;
    $('#secondary-select-all').indeterminate = state.selectedReviews.size > 0 && state.selectedReviews.size < second.length;
    $('#secondary-select-all').disabled = state.batchReviewBusy || !second.length;
    $('#secondary-batch-confirm').disabled = state.batchReviewBusy || !state.selectedReviews.size;
    $('#secondary-selection-count').textContent = `已选 ${state.selectedReviews.size} 份`;
    $('#secondary-badge').textContent = String(second.length);
    $('#secondary-badge').hidden = second.length === 0;
    const badge = $('#approval-badge');
    badge.textContent = String(first.length);
    badge.hidden = first.length === 0;
    $('#approval-list-items').innerHTML = pending.length ? pending.map((batch) => `<div class="approval-select-row">${state.approvalStage === 'secondary' ? `<input type="checkbox" data-review-select="${escapeHtml(batch.id)}" aria-label="选择 ${escapeHtml(batch.batch_no)}" ${state.selectedReviews.has(batch.id) ? 'checked' : ''} ${state.batchReviewBusy ? 'disabled' : ''} />` : ''}<button class="approval-list-item ${state.activeReview?.batch?.id === batch.id ? 'selected' : ''}" data-approval-id="${escapeHtml(batch.id)}"><span class="approval-type">${escapeHtml((formTypeMeta(batch.form_type).label || '审').slice(0, 1))}</span><div><b>${escapeHtml(batchLabel(batch))}</b><p>${escapeHtml(batch.batch_no)} · ${escapeHtml(formatMonth(batch.month))}</p><small>${escapeHtml(formatDateTime(batch.updated_at))}</small></div>${statusTag(batch.status)}</button></div>`).join('') : '<div class="empty-state list-empty"><span>审</span><h3>暂无待审核批次</h3><p>完成真实申报后才会显示。</p></div>';
    const activeStillPending = pending.some((batch) => batch.id === state.activeReview?.batch?.id);
    if (!pending.length) {
      state.activeReview = null;
      $('#approval-detail').hidden = true;
      $('#approval-empty').hidden = false;
    } else if (!activeStillPending) {
      $('#approval-detail').hidden = true;
      $('#approval-empty').hidden = false;
    }
  };

  const auditFormulaLines = (review) => {
    const amount = path => {
      const field = review.fields.find(field => field.path === path);
      if (!field || field.value_normalized == null || field.value_normalized === '') return null;
      const value = Number(String(field.value_normalized).replace(/,/g,''));
      return Number.isFinite(value) ? value : null;
    };
    const expression = (line,path,factor,suffix,label=line.label) => {
      const base = amount(path);
      return base == null ? line : {...line,label,expression:`${base} × ${suffix}`};
    };
    return review.calculation.lines.flatMap(line => {
      if (line.label === '设备使用费奖励') return [expression(line,'standard.equipment_fee',0.15,'15%')];
      if (review.batch.form_type === 'STD_SERVICE' && line.label === '技术服务费奖励') return [expression(line,'standard.service_fee',0.3,'30%')];
      if (review.batch.form_type === 'STD_L' && ['上传核定金额或场次奖励','上传核定金额','会议场次奖励','技术服务费奖励'].includes(line.label)) return [expression(line,'standard.session_count',70,'70元/场','会议场次奖励')];
      if (review.batch.form_type === 'STD_PROJECT' && line.label === '技术服务费奖励') {
        const efp = amount('standard.efp_fee'), screen = amount('standard.screen_fee');
        // Split historical combined lines only when their saved total agrees with these bases.
        if (efp != null && screen != null && Math.abs((efp + screen)*0.3 - Number(line.raw_result ?? line.rounded_result)) < 0.011) return [
          {...line,label:'EFP技术服务费奖励',expression:`${efp} × 30%`,rounded_result:efp*0.3},
          {...line,label:'大屏技术服务费奖励',expression:`${screen} × 30%`,rounded_result:screen*0.3},
        ];
      }
      return [line];
    });
  };

  let reviewNoteQueue = Promise.resolve();
  const queueReviewNote = () => {
    const review = state.activeReview;
    if (!review?.calculation) return;
    const stage = state.approvalStage;
    const text = $('#review-comment').value;
    $('#review-note-status').textContent = '正在保存审核意见…';
    reviewNoteQueue = reviewNoteQueue.catch(()=>{}).then(async () => {
      const note = review.review_notes?.[stage];
      const result = await api.request(`/batches/${encodeURIComponent(review.batch.id)}/review-note`, {
        method:'PATCH', body:JSON.stringify({stage,text,revision:note?.revision || 0,calculation_id:review.calculation.id})
      });
      review.review_notes ||= {};
      review.review_notes[stage] = result.note;
      if (state.activeReview === review && state.approvalStage === stage && $('#review-comment').value === text) $('#review-note-status').textContent = '审核意见已保存';
    });
    reviewNoteQueue.catch(error=>{
      if (state.activeReview === review) $('#review-note-status').textContent = '保存失败：'+error.message+'；请重试保存';
    });
  };
  $('#review-comment').addEventListener('input',queueReviewNote);
  $('#save-review-note').addEventListener('click',queueReviewNote);

  const renderApprovalDetail = (review) => {
    const { batch, documents, calculation } = review;
    const fields = orderFields(review.fields, batch.form_type);
    if (!calculation) throw new Error('该批次尚未生成计算快照');
    const projectName = fieldValue(fields, 'project.name') || batch.project_owner || formTypeMeta(batch.form_type).label;
    $('#approval-batch-id').textContent = batch.batch_no;
    $('#approval-project').textContent = projectName;
    $('#approval-meta').textContent = `${formatMonth(batch.month)} · 项目负责人：${batch.project_owner || '未填写'}`;
    const [statusLabel, statusClass] = statusMeta[batch.status] || [batch.status, 'status-neutral'];
    $('#approval-status').textContent = statusLabel;
    $('#approval-status').className = `status-tag ${statusClass}`;
    $('#approval-field-count').textContent = `${fields.filter((field) => field.confirmed).length} / ${fields.length} 已确认`;
    const materials = documents.filter((document) => document.document_role !== 'EVIDENCE');
    const evidence = documents.filter((document) => document.document_role === 'EVIDENCE');
    $('#approval-document-count').textContent = `${materials.length} 份材料 · ${evidence.length} 份证明`;
    $('#approval-rule-version').textContent = calculation.rule_version;
    $('#approval-documents').innerHTML = documentGalleryHtml(materials);
    $('#approval-evidence-count').textContent = `${evidence.length} 份`;
    $('#approval-evidence-documents').innerHTML = documentGalleryHtml(evidence);
    $('#approval-evidence-panel').hidden = evidence.length === 0;
    $('#approval-evidence-panel').open = false;
    $('#approval-fields').innerHTML = fields.map((field) => `<div class="${keyAuditFieldPaths.has(field.path) ? 'key-audit-field' : ''}"><span>${escapeHtml(fieldLabels[field.path] || field.path)}</span><b>${escapeHtml(field.value_normalized ?? '—')}</b><small>${Math.round(Number(field.confidence || 0) * 100)}%</small></div>`).join('');
    $('#approval-total').textContent = formatMoney(calculation.rounded_total);
    $('#approval-rounding').textContent = `舍入前 ${formatMoney(calculation.raw_total, 2)}`;
    $('#approval-formulas').innerHTML = auditFormulaLines(review).map((line) => `<p><span>${escapeHtml(line.label)}</span><b>${escapeHtml(line.expression)}</b><em>${formatMoney(line.rounded_result, 2)}</em></p>`).join('');
    $('#initial-review-summary').hidden = !review.initial_review;
    $('#initial-review-summary').textContent = review.initial_review ? `初审核定 ${formatMoney(review.initial_review.total,2)} · 初审意见：${review.initial_review.reason || '无调整'}。复审金额留空时沿用初审核定额。` : '';
    $('#approval-override').value = '';
    $('#review-comment').value = review.review_notes?.[state.approvalStage]?.text || '';
    $('#review-note-status').textContent = review.review_notes?.[state.approvalStage] ? '审核意见已保存' : '填写后自动保存，不改变审核状态';
    const returned = [...(review.audit || [])].reverse().find(item=>['BATCH_RETURNED','FINAL_RETURNED_TO_SECONDARY'].includes(item.action));
    if (returned && (state.approvalStage === 'initial' || returned.action === 'FINAL_RETURNED_TO_SECONDARY')) {
      $('#initial-review-summary').hidden = false;
      $('#initial-review-summary').textContent += ` ${returned.action === 'FINAL_RETURNED_TO_SECONDARY' ? '终审退回原因' : '复审退回原因'}：${returned.after?.reason || ''}`;
    }
    $('#summary-remark-text').value = review.remark?.text ?? '';
    $('#approval-standard-warnings').textContent = (calculation.warnings || []).join('；');
    $('#approval-detail').hidden = false;
    $('#approval-empty').hidden = true;
    renderApprovalList();
  };

  const openApproval = async (batchId) => {
    try {
      await reviewNoteQueue.catch(()=>{});
      const review = await api.getReview(batchId);
      state.activeReview = review;
      renderApprovalDetail(review);
    } catch (error) {
      showToast(error.message, true);
    }
  };

  const confirmApproval = async () => {
    const review = state.activeReview;
    if (!review?.calculation || state.batchReviewBusy) return;
    const overrideText = $('#approval-override').value.trim();
    const reason = $('#review-comment').value.trim();
    let overrideTotal = null;
    if (overrideText) {
      overrideTotal = Number(overrideText.replace(/,/g, ''));
      if (!Number.isFinite(overrideTotal) || overrideTotal < 0) return showToast('人工确认金额格式不正确', true);
      if (!reason) return showToast('修改金额时必须填写审核原因', true);
    }
    const button = $('#approval-confirm');
    button.disabled = true;
    try {
      await reviewNoteQueue;
      if ($('#summary-remark-text').value !== (review.remark?.text ?? '')) await saveBatchRemark();
      if (state.approvalStage === 'initial') {
        await api.initialReview(review.calculation.id, {override_total:overrideTotal,reason:reason || null});
        state.activeReview = null;
        showToast('初审通过，已交复审');
        await refreshBusinessData({silent:true});
        return;
      }
      const latest = await api.listBatches(review.batch.month);
      const projectTypes = new Set(['STD_PROJECT', 'STD_SERVICE']);
      const name = String(fieldValue(review.fields, 'project.name') || '').trim();
      const duplicate = (latest.items || []).find(item =>
        item.id !== review.batch.id && item.id !== review.batch.supersedes_batch_id &&
        !item.excluded && !item.superseded_by_batch_id && ['CONFIRMED', 'EXPORTED'].includes(item.status) &&
        (projectTypes.has(review.batch.form_type)
          ? projectTypes.has(item.form_type) && name && name === String(item.project_name || '').trim()
          : review.batch.form_type.startsWith('STD_') && item.form_type === review.batch.form_type));
      if (duplicate) return showToast('本月该部门或项目已进入终审，材料已锁定，不能替换', true);
      await api.confirm(review.calculation.id, { reviewer: null, override_total: overrideTotal, reason: reason || null, replace_batch_id: duplicate?.id || null });
      $('#final-review-month').value = review.batch.month;
      showToast('复审通过，已进入会议终审表');
      state.activeReview = null;
      await refreshBusinessData({ silent: true });
      if (state.batches.some(batch => batch.status === 'INITIAL_REVIEWED')) {
        showView('secondary');
      } else { showView('meeting'); }
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  // Sequential processing rechecks each item so one failure does not approve or hide another.
  const confirmSelectedReviews = async () => {
    if (state.batchReviewBusy || state.approvalStage !== 'secondary') return;
    const ids = [...state.selectedReviews];
    if (!ids.length || !window.confirm(`确认复审通过所选 ${ids.length} 份申报？每份沿用初审核定金额；需要调额或填写意见的申报请单独处理。`)) return;
    state.batchReviewBusy = true;
    $('#approval-confirm').disabled = true;
    renderApprovalList();
    const failures = [];
    let passed = 0;
    try {
      for (const id of ids) {
        let label = id;
        try {
          const review = await api.getReview(id);
          label = review.batch.batch_no;
          if (review.batch.status !== 'INITIAL_REVIEWED' || !review.initial_review || !review.calculation) throw new Error('状态已变更或未完成初审，请刷新后单独处理');
          const latest = await api.listBatches(review.batch.month);
          const projectTypes = new Set(['STD_PROJECT','STD_SERVICE']);
          const name = String(fieldValue(review.fields,'project.name') || '').trim();
          const duplicate = (latest.items || []).find(item => item.id !== id && item.id !== review.batch.supersedes_batch_id && !item.excluded && !item.superseded_by_batch_id && ['CONFIRMED','EXPORTED'].includes(item.status) && (projectTypes.has(review.batch.form_type) ? projectTypes.has(item.form_type) && name && name === String(item.project_name || '').trim() : review.batch.form_type.startsWith('STD_') && item.form_type === review.batch.form_type));
          if (duplicate) throw new Error('同月该部门或项目已有终审申报，请单独核对');
          await api.confirm(review.calculation.id,{reviewer:null,override_total:null,reason:null,replace_batch_id:null});
          state.selectedReviews.delete(id);
          passed++;
        } catch(error) { failures.push(`${label}：${error.message}`); }
      }
    } finally {
      state.batchReviewBusy = false;
      $('#approval-confirm').disabled = false;
      state.activeReview = null;
      await refreshBusinessData({silent:true});
      $('#secondary-batch-result').textContent = `复审通过 ${passed} 份，未通过 ${failures.length} 份。` + (failures.length ? '\n' + failures.join('\n') : '');
      renderApprovalList();
    }
  };

  const returnActiveBatch = async () => {
    const review = state.activeReview;
    if (!review) return;
    const reason = $('#review-comment').value.trim();
    if (!reason) return showToast('退回批次前请填写退回原因', true);
    try {
      await reviewNoteQueue;
      await api.returnBatch(review.batch.id, reason);
      showToast(review.batch.status === 'INITIAL_REVIEWED' ? '已退回初审，退回原因已保存' : '批次已退回字段核对，原因已保存');
      state.activeReview = null;
      await refreshBusinessData({ silent: true });
      renderApprovalList();
    } catch (error) {
      showToast(error.message, true);
    }
  };

  const renderExports = (updateMeeting = true) => {
    const draft = state.finalReview;
    const items = draft?.items || [];
    if (updateMeeting) window.MeetingUI.render({draft, escapeHtml, formatMoney, formatMonth});
    $('#export-history-body').innerHTML = state.exports.map((artifact) => `<tr><td><b>${escapeHtml(artifact.archive_name || artifact.file_name)}</b><small>${escapeHtml(artifact.id)} · ${escapeHtml(formatDateTime(artifact.created_at))} · ${Number(artifact.project_count ?? 1)} 个项目</small></td><td>${escapeHtml(formatMonth(artifact.month))}</td><td>${formatMoney(artifact.confirmed_total)}</td><td>${escapeHtml(artifact.template_version)}</td><td>${escapeHtml(artifact.confirmed_by || '审核员')}</td><td>${artifact.archive_url ? `<a class="table-download" href="${escapeHtml(api.resolveUrl(artifact.archive_url))}">下载完整离线归档包</a>` : '<button class="text-action" data-nav="meeting">旧版记录，请重新终审导出</button>'} <button class="text-action" data-delete-export="${escapeHtml(artifact.id)}">删除</button></td></tr>`).join('');
    $('#export-empty').hidden = state.exports.length > 0;
  };

  const generateExport = async (calculationId, mode, button) => {
    try {
      if (await window.MeetingUI.flushNotes()) {
        showToast('会议备注已自动保存，请重新确认终审后再导出',true);
        showView('meeting');
        return;
      }
    } catch(error) { showToast(error.message,true); return; }
    const draft = state.finalReview;
    if (!draft?.approved || !window.confirm(`确认导出 ${formatMonth(draft.month)} 已终审结果并归档${mode === 'formula' ? '公式版' : '数值版'}？共 ${draft.items.length} 份申报，合计 ${formatMoney(draft.total, 2)}。本次历史文件生成后不可修改。`)) return;
    button.disabled = true;
    try {
      const generated = await api.createExport(calculationId, mode, draft.revision);
      const link = document.createElement('a');
      link.href = api.resolveUrl(generated.export.archive_url || generated.export.download_url);
      link.download = '';
      document.body.appendChild(link);
      link.click();
      link.remove();
      showToast('已归档，正在下载到电脑');
      await refreshBusinessData({ silent: true });
    } catch (error) {
      showToast(error.message, true);
      await loadFinalReview();
    } finally {
      button.disabled = false;
    }
  };

  const loadFinalReview = async () => {
    const month = $('#final-review-month').value || $('#settlement-month').value;
    const previousMonth = state.finalReview?.month;
    const wrap = $('#meeting-sheet .meeting-table-wrap');
    const position = {top:wrap?.scrollTop || 0,left:wrap?.scrollLeft || 0,x:window.scrollX,y:window.scrollY};
    const requestNumber = (state.finalReviewRequest || 0) + 1;
    state.finalReviewRequest = requestNumber;
    $('#final-review-month').value = month;
    if (previousMonth !== month) { state.finalReview = null; renderExports(); }
    if (!month) return;
    try {
      const draft = await api.finalReview(month);
      if ($('#final-review-month').value !== month || state.finalReviewRequest !== requestNumber) return;
      state.finalReview = draft;
      $('#export-month').value = month;
      $('#remark-month').value = month;
      renderExports();
      if (previousMonth === month) {
        const restore = () => {
          const current = $('#meeting-sheet .meeting-table-wrap');
          if (current) { current.scrollTop = position.top; current.scrollLeft = position.left; }
          window.scrollTo(position.x,position.y);
        };
        restore();
        window.requestAnimationFrame(restore);
      }
    } catch (error) {
      if ($('#final-review-month').value === month && state.finalReviewRequest === requestNumber) showToast(error.message, true);
    }
  };

  const changeFinalAmount = (id) => {
    const draft = state.finalReview;
    const item = draft?.items.find(item => item.calculation_id === id);
    if (!item) return;
    state.pendingFinalAmount = {id, revision:draft.revision};
    $('#final-amount-name').textContent = batchLabel(item) + (['STD_K','STD_L'].includes(item.form_type) ? '（仅修改浮动奖金，固定其它金额另计）' : '（核定金额；其它栏的会议调整另计）');
    $('#final-amount-value').value = item.total;
    $('#final-amount-reason').value = item.final_reason || '';
    $('#final-amount-dialog').showModal();
  };

  const saveFinalAmount = async (event) => {
    event.preventDefault();
    if (!state.pendingFinalAmount) return;
    const {id, revision} = state.pendingFinalAmount;
    const total = $('#final-amount-value').value.trim();
    const reason = $('#final-amount-reason').value.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(total)) return showToast('请输入非负金额，最多两位小数', true);
    const button = $('#final-amount-save');
    button.disabled = true;
    try {
      await api.finalAmount(id, total, reason, revision);
      $('#final-amount-dialog').close();
      state.pendingFinalAmount = null;
      showToast('已保存新的核定金额，历史版本保持不变');
      await refreshBusinessData({silent: true});
    } catch (error) { showToast(error.message, true); await loadFinalReview(); }
    finally { button.disabled = false; }
  };

  const refreshBusinessData = async ({ silent = false } = {}) => {
    if (!state.backendOnline) return;
    const requestRevision = (state.businessRefreshRevision || 0) + 1;
    state.businessRefreshRevision = requestRevision;
    try {
      if (appMode === 'employee') {
        const result = await api.listBatches();
        if (state.businessRefreshRevision !== requestRevision) return;
        state.batches = result.items || [];
        renderSubmissions();
        return;
      }
      const [batchResult, exportResult] = await Promise.all([api.listBatches(), api.listExports()]);
      if (state.businessRefreshRevision !== requestRevision) return;
      state.batches = (batchResult.items || []).map(batch => batch.superseded_by_batch_id ? {...batch, status: 'SUPERSEDED'} : batch);
      renderSubmissions();
      state.exports = exportResult.items || [];
      renderDashboard();
      renderApprovalList();
      renderExports();
      if (['meeting','exports'].includes(state.currentView)) await loadFinalReview();
      if (!silent) showToast('已刷新真实业务数据');
    } catch (error) {
      if (state.businessRefreshRevision !== requestRevision) return;
      if (appMode === 'employee') {
        if (!silent) showToast(error.message, true);
        return;
      }
      state.batches = [];
      state.exports = [];
      state.finalReview = null;
      renderDashboard();
      renderApprovalList();
      renderExports();
      if (!silent) showToast(error.message, true);
    }
  };

  const canRefreshAutomatically = () => {
    if (!state.backendOnline || document.hidden || state.uploadBusy || state.batchReviewBusy || api.pendingMutations > 0) return false;
    if (document.querySelector('dialog[open]') || document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')) return false;
    const review = state.activeReview;
    if (review && ['approval', 'secondary'].includes(state.currentView)) {
      if ($('#approval-confirm').disabled || $('#approval-override').value.trim() ||
          $('#review-comment').value !== (review.review_notes?.[state.approvalStage]?.text || '') ||
          $('#summary-remark-text').value !== (review.remark?.text ?? '')) return false;
    }
    if (['meeting', 'exports'].includes(state.currentView)) {
      const notes = document.querySelectorAll('#meeting-sheet [data-note-existing], #meeting-sheet [data-note-append]');
      if ([...notes].some(input => input.disabled || (input.dataset?.noteAppend
        ? input.value.trim() : input.value !== input.defaultValue))) return false;
    }
    return true;
  };

  const refreshAutomatically = async () => {
    if (state.autoRefreshBusy || !canRefreshAutomatically()) return;
    state.autoRefreshBusy = true;
    const view = state.currentView, review = state.activeReview, stage = state.approvalStage;
    const month = $('#final-review-month').value || $('#settlement-month').value;
    const mutationVersion = api.mutationVersion;
    const businessRevision = state.businessRefreshRevision;
    const finalRequest = state.finalReviewRequest;
    const stillCurrent = () => canRefreshAutomatically() && state.currentView === view &&
      state.activeReview === review && state.approvalStage === stage &&
      ($('#final-review-month').value || $('#settlement-month').value) === month &&
      api.mutationVersion === mutationVersion && state.businessRefreshRevision === businessRevision &&
      state.finalReviewRequest === finalRequest;
    try {
      const [batchResult, exportResult] = await Promise.all([
        api.listBatches(), appMode === 'reviewer' ? api.listExports() : Promise.resolve(null),
      ]);
      if (!stillCurrent()) return;
      const batches = (batchResult.items || []).map(batch => batch.superseded_by_batch_id ? {...batch, status: 'SUPERSEDED'} : batch);
      const pendingStatuses = stage === 'secondary' ? ['INITIAL_REVIEWED'] : ['CALCULATED', 'MANUAL_APPROVAL_REQUIRED'];
      const reviewPending = review && ['approval', 'secondary'].includes(view) &&
        batches.some(batch => batch.id === review.batch.id && pendingStatuses.includes(batch.status));
      const needsDraft = appMode === 'reviewer' && ['meeting', 'exports'].includes(view) && month;
      const [latestReview, latestDraft] = await Promise.all([
        reviewPending ? api.getReview(review.batch.id) : Promise.resolve(null),
        needsDraft ? api.finalReview(month) : Promise.resolve(null),
      ]);
      if (!stillCurrent()) return;
      const x = window.scrollX, y = window.scrollY;
      const wrap = $('#meeting-sheet .meeting-table-wrap');
      const scroll = {top: wrap?.scrollTop || 0, left: wrap?.scrollLeft || 0};
      state.businessRefreshRevision = (state.businessRefreshRevision || 0) + 1;
      state.batches = batches;
      renderSubmissions();
      if (appMode === 'employee') return;
      state.exports = exportResult.items || [];
      const draftChanged = latestDraft && JSON.stringify(latestDraft) !== JSON.stringify(state.finalReview);
      if (latestDraft) {
        state.finalReviewRequest = (state.finalReviewRequest || 0) + 1;
        state.finalReview = latestDraft;
        $('#export-month').value = month;
        $('#remark-month').value = month;
      }
      if (review && ['approval', 'secondary'].includes(view) && !reviewPending) state.activeReview = null;
      renderDashboard();
      renderApprovalList();
      if (latestReview && JSON.stringify(latestReview) !== JSON.stringify(review)) {
        if (pendingStatuses.includes(latestReview.batch.status)) {
          const evidenceOpen = $('#approval-evidence-panel').open;
          state.activeReview = latestReview;
          renderApprovalDetail(latestReview);
          $('#approval-evidence-panel').open = evidenceOpen;
        } else {
          state.activeReview = null;
          $('#approval-detail').hidden = true;
          $('#approval-empty').hidden = false;
        }
      }
      renderExports(Boolean(draftChanged));
      const currentWrap = $('#meeting-sheet .meeting-table-wrap');
      if (currentWrap) { currentWrap.scrollTop = scroll.top; currentWrap.scrollLeft = scroll.left; }
      window.scrollTo(x, y);
    } catch {
      // Keep the last successful screen; the next tick retries without losing edits.
    } finally {
      state.autoRefreshBusy = false;
    }
  };

  const updateSystemStatus = () => {
    const health = state.health;
    if (!health) {
      $('#system-health-tag').textContent = '未连接';
      $('#system-health-tag').className = 'status-tag status-warning';
      $('#system-api-state').textContent = '未连接';
      return;
    }
    $('#system-health-tag').textContent = '运行正常';
    $('#system-health-tag').className = 'status-tag status-success';
    $('#system-api-state').textContent = '运行正常';
    $('#system-version').textContent = `v${health.version}`;
    $('#system-worker').textContent = health.worker_mode === 'inline' ? '内联任务' : '独立任务进程';
    const recognitionLabels = {
      available: '本地 OCR 可用',
      configured_unverified: 'OCR 已配置',
      degraded: 'OCR 异常 · 转人工核对',
      unavailable: 'OCR 不可用 · 转人工录入',
      manual_fallback: 'OCR 未启用 · 转人工录入',
    };
    const imageMode = $('#system-image-mode');
    imageMode.textContent = recognitionLabels[health.image_recognition] || '状态未知';
    imageMode.classList.toggle('healthy', health.image_recognition === 'available');
    imageMode.title = health.image_recognition_error || '';
    $('#system-image-model').textContent = health.image_model || '未配置';
    $('#system-reviewer-auth').textContent = health.reviewer_auth === 'required' ? '需要审核令牌' : '开发环境免令牌';
    if (health.admin_auth !== 'configured') {
      $('#admin-login-hint').textContent = '请先在项目 .env 中配置 ADMIN_PASSWORD，然后重启后端。';
    }
  };

  const renderAdminBatches = () => {
    $('#admin-batch-body').innerHTML = state.adminBatches.map((batch) => {
      const status = statusMeta[batch.status] || [batch.status, 'status-neutral'];
      return `<tr><td><b>${escapeHtml(batch.batch_no)}</b><small>${escapeHtml(batch.project_owner || '未填写项目负责人')}</small></td><td>${escapeHtml(formatMonth(batch.month))}</td><td><span class="status-tag ${status[1]}">${escapeHtml(status[0])}</span></td><td>${Number(batch.document_count || 0)} 份</td><td>${escapeHtml(formatDateTime(batch.updated_at))}</td><td><button class="danger-button compact-danger-button" type="button" data-admin-delete-batch="${escapeHtml(batch.id)}">删除</button></td></tr>`;
    }).join('');
    $('#admin-batch-empty').hidden = state.adminBatches.length > 0;
  };

  const setAdminSession = (authenticated) => {
    $('#admin-login-form').hidden = authenticated;
    $('#admin-batch-panel').hidden = !authenticated;
    $('#admin-session-tag').textContent = authenticated ? '管理员已登录' : '未登录';
    $('#admin-session-tag').className = `status-tag ${authenticated ? 'status-success' : 'status-neutral'}`;
    if (!authenticated) {
      state.adminBatches = [];
      renderAdminBatches();
    }
  };

  const loadAdminBatches = async ({ silent = false } = {}) => {
    if (!api.adminToken) {
      setAdminSession(false);
      return;
    }
    try {
      const result = await api.listAdminBatches();
      state.adminBatches = result.items || [];
      setAdminSession(true);
      renderAdminBatches();
      if (!silent) showToast('批次列表已刷新');
    } catch (error) {
      if (error.status === 401) {
        api.setAdminToken('');
        setAdminSession(false);
      }
      if (!silent) showToast(error.message, true);
    }
  };

  const loginAdmin = async (event) => {
    event.preventDefault();
    const password = $('#admin-password').value;
    const button = $('#admin-login-form button[type="submit"]');
    button.disabled = true;
    try {
      const result = await api.adminLogin(password);
      api.setAdminToken(result.token || '');
      $('#admin-password').value = '';
      await loadAdminBatches({ silent: true });
      showToast('管理员已登录');
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  const enterReviewerMode = async (event) => {
    event.preventDefault();
    const password = $('#reviewer-admin-password').value;
    const errorMessage = $('#reviewer-login-error');
    const button = $('#reviewer-login-form button[type="submit"]');
    errorMessage.hidden = true;
    button.disabled = true;
    try {
      const result = await api.adminLogin(password);
      const sessionToken = result.token || '';
      if (!sessionToken) throw new Error('管理员会话创建失败');
      api.setAdminToken(sessionToken);
      api.setReviewerToken(sessionToken);
      const reviewerUrl = new URL(window.location.href);
      reviewerUrl.searchParams.set('mode', 'reviewer');
      window.location.assign(reviewerUrl.toString());
    } catch (error) {
      errorMessage.textContent = error.message;
      errorMessage.hidden = false;
      $('#reviewer-admin-password').select();
    } finally {
      button.disabled = false;
    }
  };

  const deleteBatchAsAdmin = async (batchId) => {
    const batch = state.adminBatches.find((item) => item.id === batchId);
    if (!batch) return;
    if (!window.confirm(`确定删除批次 ${batch.batch_no}？相关附件、核算和导出记录将一并删除。`)) return;
    const button = document.querySelector(`[data-admin-delete-batch="${CSS.escape(batchId)}"]`);
    if (button) button.disabled = true;
    try {
      await api.deleteAdminBatch(batchId);
      showToast(`批次 ${batch.batch_no} 已删除`);
      await Promise.all([loadAdminBatches({ silent: true }), refreshBusinessData({ silent: true })]);
    } catch (error) {
      if (error.status === 401) {
        api.setAdminToken('');
        setAdminSession(false);
      }
      showToast(error.message, true);
      if (button) button.disabled = false;
    }
  };

  document.addEventListener('click', async (event) => {
    const returnUpload = event.target.closest('[data-return-upload]');
    if (returnUpload) { returnToUpload(); return; }
    const finalAmount = event.target.closest('[data-final-amount]');
    if (finalAmount) { changeFinalAmount(finalAmount.dataset.finalAmount, finalAmount); return; }
    const password = event.target.closest('[data-toggle-password]');
    if (password) {
      const input = document.getElementById(password.dataset.togglePassword);
      const visible = input.type === 'password'; input.type = visible ? 'text' : 'password';
      password.textContent = visible ? '隐藏' : '显示'; password.setAttribute('aria-pressed', String(visible)); return;
    }
    const remove = event.target.closest('[data-remove-file]');
    if (remove && !state.uploadBusy) {
      const kind = remove.dataset.removeFile;
      const files = kind === 'evidence' ? state.evidenceFiles : state.materialFiles;
      files.splice(Number(remove.dataset.fileIndex), 1);
      if (state.pendingUpload?.batch) state.pendingUpload.needsReset = true;
      renderSelectedFiles(kind === 'evidence' ? '#selected-evidence-files' : '#selected-material-files', files, '待上传');
      updateUploadReady(); return;
    }
    const change = event.target.closest('[data-submission-id]');
    if (change) { changeSubmittedBatch(change.dataset.submissionId, change.dataset.submissionAction, change); return; }
    const resume = event.target.closest('[data-resume-review]');
    if (resume) { openFieldReview(resume.dataset.resumeReview); return; }
    const replace = event.target.closest('[data-replace-upload]');
    if (replace) {
      if (!window.confirm('清除该未审核批次已上传材料后重新选择，是否继续？')) return;
      api.resetUpload(replace.dataset.replaceUpload).then(result => prepareBatchUpload(result.batch)).catch(error => showToast(error.message,true)); return;
    }

    const nav = event.target.closest('[data-nav]');
    if (nav) {
      showView(nav.dataset.nav);
      return;
    }
    const batchRow = event.target.closest('[data-open-batch]');
    if (batchRow) {
      const status = batchRow.dataset.status;
      if (status === 'REVIEW_REQUIRED') openFieldReview(batchRow.dataset.openBatch);
      else if (['CALCULATED', 'MANUAL_APPROVAL_REQUIRED'].includes(status)) {
        showView('approval');
        openApproval(batchRow.dataset.openBatch);
      } else if (status === 'INITIAL_REVIEWED') { showView('secondary'); openApproval(batchRow.dataset.openBatch); }
      else if (['CONFIRMED', 'EXPORTED'].includes(status)) showView('meeting');
      else showToast(`当前批次状态：${statusMeta[status]?.[0] || status}`);
      return;
    }
    const approvalItem = event.target.closest('[data-approval-id]');
    if (approvalItem) {
      openApproval(approvalItem.dataset.approvalId);
      return;
    }
    const exportButton = event.target.closest('[data-export-id]');
    if (exportButton) {
      generateExport(exportButton.dataset.exportId, exportButton.dataset.exportMode, exportButton);
      return;
    }
    const deleteExportButton = event.target.closest('[data-delete-export]');
    if (deleteExportButton) {
      if (!window.confirm('确认删除这份历史导出及其服务器归档文件？已下载到电脑的副本不受影响。')) return;
      deleteExportButton.disabled = true;
      try { await api.deleteExport(deleteExportButton.dataset.deleteExport); await refreshBusinessData({silent:true}); showToast('历史导出已删除'); }
      catch(error) { showToast(error.message,true); deleteExportButton.disabled = false; }
      return;
    }
    const adminDeleteButton = event.target.closest('[data-admin-delete-batch]');
    if (adminDeleteButton) {
      deleteBatchAsAdmin(adminDeleteButton.dataset.adminDeleteBatch);
      return;
    }
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (action === 'refresh-data') refreshBusinessData();
    if (action === 'reconnect') connectBackend();
    if (action === 'return-batch') returnActiveBatch();
    if (action === 'reviewer-login') openReviewerLogin();
    if (action === 'reviewer-login-close') closeReviewerLogin();
    if (action === 'admin-logout') {
      api.setAdminToken('');
      setAdminSession(false);
      showToast('已退出管理员');
    }
    if (action === 'profile') {
      if (!state.reviewerAuthRequired) return showToast('当前为开发环境，审核接口免令牌');
      const token = window.prompt('请输入审核令牌（仅保存在当前标签页）');
      if (token == null) return;
      api.setReviewerToken(token);
      $('#auth-state').textContent = token.trim() ? '令牌已填写' : '需要令牌';
      refreshBusinessData();
    }
  });

  window.MeetingUI.bind({api,state,showToast,loadFinalReview,showView,changeFinalAmount});
  $('#business-category').addEventListener('change', renderFormTypeOptions);
  $('#form-type').addEventListener('change', updateBatchPreview);
  $('#project-owner').addEventListener('input', updateBatchPreview);
  $('#settlement-month').addEventListener('change', () => {
    updateBatchPreview();
    renderDashboard();
    renderSubmissions();
  });
  const selectFiles = (kind, fileList) => {
    const files = Array.from(fileList || []);
    if (kind === 'material') {
      state.materialFiles = files;
      renderSelectedFiles('#selected-material-files', files, '待上传');
    } else {
      const known = new Set(state.evidenceFiles.map(file => `${file.name}:${file.size}:${file.lastModified}`));
      state.evidenceFiles.push(...files.filter(file => !known.has(`${file.name}:${file.size}:${file.lastModified}`)));
      renderSelectedFiles('#selected-evidence-files', state.evidenceFiles, '待上传');
    }
    if (state.pendingUpload?.batch && files.length) state.pendingUpload.needsReset = true;
    $(kind === 'evidence' ? '#evidence-file-input' : '#material-file-input').value = '';
    updateUploadReady();
  };
  const bindUploadZone = (zoneId, inputId, kind) => {
    $(zoneId).addEventListener('click', () => { if (!state.uploadBusy) $(inputId).click(); });
    $(zoneId).addEventListener('dragover', (event) => {
      event.preventDefault();
      event.currentTarget.classList.add('is-dragging');
    });
    $(zoneId).addEventListener('dragleave', (event) => event.currentTarget.classList.remove('is-dragging'));
    $(zoneId).addEventListener('drop', (event) => {
      event.preventDefault();
      event.currentTarget.classList.remove('is-dragging');
      if (!state.uploadBusy) selectFiles(kind, event.dataTransfer.files);
    });
    $(inputId).addEventListener('change', () => selectFiles(kind, $(inputId).files));
  };
  bindUploadZone('#material-upload-zone', '#material-file-input', 'material');
  bindUploadZone('#evidence-upload-zone', '#evidence-file-input', 'evidence');
  $('#start-upload').addEventListener('click', processRealFiles);
  $('#confirm-fields').addEventListener('change', () => {
    $('#review-submit').disabled = !$('#confirm-fields').checked || state.activeFields.length === 0;
  });
  $('#field-list').addEventListener('input', (event) => {
    if (event.target.classList.contains('field-value')) recalculateProjectReview();
  });
  $('#review-submit').addEventListener('click', submitFieldReview);
  $('#review-change-upload').addEventListener('click', returnToUpload);
  $('#retry-recognition').addEventListener('click', retryRecognition);
  $('#approval-confirm').addEventListener('click', confirmApproval);
  $('#secondary-batch-confirm').addEventListener('click', confirmSelectedReviews);
  $('#secondary-select-all').addEventListener('change', event => {
    state.selectedReviews = new Set(event.target.checked ? state.batches.filter(batch => batch.status === 'INITIAL_REVIEWED').map(batch => batch.id) : []);
    renderApprovalList();
  });
  $('#approval-list-items').addEventListener('change', event => {
    const id = event.target.dataset.reviewSelect;
    if (!id || state.batchReviewBusy) return;
    if (event.target.checked) state.selectedReviews.add(id); else state.selectedReviews.delete(id);
    renderApprovalList();
  });
  $('#final-review-month').addEventListener('change', loadFinalReview);
  $('#refresh-final-review').addEventListener('click', loadFinalReview);
  $('#final-amount-form').addEventListener('submit', saveFinalAmount);
  $('#final-amount-cancel').addEventListener('click', () => $('#final-amount-dialog').close());
  const saveBatchRemark = async () => {
    const review = state.activeReview;
    if (!review) return;
    const saved = await api.request(`/batches/${review.batch.id}/remark`, {method: 'PATCH', body: JSON.stringify({text: $('#summary-remark-text').value, revision: review.remark?.revision || 0})});
    review.remark = saved.remark;
  };
  $('#save-batch-remark').addEventListener('click', async () => {
    try { await saveBatchRemark(); showToast('汇总备注已保存'); } catch (error) { showToast(error.message, true); }
  });
  let monthlyRemarks = [];
  let loadedRemarkMonth = '';
  $('#load-month-remarks').addEventListener('click', async () => {
    try {
      const month = $('#remark-month').value;
      if (loadedRemarkMonth === month && $('#monthly-remarks').childElementCount) {
        $('#monthly-remarks').hidden = !$('#monthly-remarks').hidden;
        $('#load-month-remarks').textContent = $('#monthly-remarks').hidden ? '读取备注' : '收起备注';
        return;
      }
      const result = await api.request(`/months/${encodeURIComponent(month)}/remarks`);
      monthlyRemarks = result.items;
      loadedRemarkMonth = month;
      $('#monthly-remarks').hidden = false;
      $('#load-month-remarks').textContent = '收起备注';
      $('#monthly-remarks').innerHTML = monthlyRemarks.map((item,index) => `<div class="review-comment"><label>${escapeHtml(item.label)}<textarea maxlength="5000" data-remark-text="${index}">${escapeHtml(item.text)}</textarea></label><button class="secondary-button" type="button" data-save-remark="${index}">保存备注</button></div>`).join('');
    } catch (error) { showToast(error.message, true); }
  });
  $('#monthly-remarks').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-save-remark]');
    if (!button) return;
    const index = Number(button.dataset.saveRemark);
    const item = monthlyRemarks[index];
    button.disabled = true;
    try {
      const text = document.querySelector(`[data-remark-text="${index}"]`).value;
      const result = await api.request(`/months/${loadedRemarkMonth}/remarks`, {method: 'PATCH', body: JSON.stringify({key: item.key, text, revision: item.revision})});
      Object.assign(item, result.remark);
      showToast('备注已保存，下一次导出时生效');
      if (['meeting','exports'].includes(state.currentView)) await loadFinalReview();
    } catch (error) { showToast(error.message, true); } finally { button.disabled = false; }
  });
  $('#admin-login-form').addEventListener('submit', loginAdmin);
  $('#reviewer-login-form').addEventListener('submit', enterReviewerMode);

  const connectBackend = async () => {
    if (state.connectionBusy) return;
    state.connectionBusy = true;
    $('#connection-status').textContent = '正在连接…';
    try {
      state.health = await api.health();
      state.reviewerAuthRequired = state.health.reviewer_auth === 'required';
      if (appMode === 'reviewer' && !state.reviewerAuthRequired && !api.reviewerToken) api.setReviewerToken('development-reviewer');
      if (!state.formTypes.length) {
        const reference = await api.formTypes();
        state.formTypes = reference.items || [];
        Object.assign(fieldLabels, reference.field_labels || {});
        state.formTypes.forEach(item => { if (item.rule_code) ruleByFormType[item.code] = item.rule_code; });
        state.ruleVersion = reference.rule_version || state.health.rule_version;
        initializeFormTypes();
      }
      state.backendOnline = true;
      $('#auth-state').textContent = api.reviewerToken ? '已验证会话' : (state.reviewerAuthRequired ? '需要审核令牌' : '开发环境免令牌');
      $('#connection-status').textContent = '服务已连接';
      await refreshBusinessData({ silent: true });
    } catch (error) {
      state.backendOnline = false;
      state.health = null;
      $('#connection-status').textContent = '连接暂时中断，自动重试中';
    } finally {
      state.connectionBusy = false;
      updateSystemStatus();
      if (!state.uploadBusy) setUploadBusy(false);
    }
  };

  const initialize = async () => {
    document.documentElement.dataset.appMode = appMode;
    $('#submission-nav-label').textContent = appMode === 'employee' ? '工作台' : '申报与核对';
    document.querySelectorAll('[data-reviewer-only]').forEach((element) => {
      element.hidden = appMode !== 'reviewer';
    });
    document.querySelectorAll('[data-employee-only]').forEach((element) => {
      element.hidden = appMode !== 'employee';
    });
    const now = new Date();
    $('#settlement-month').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    $('#remark-month').value = $('#settlement-month').value;
    updateBatchPreview();
    setUploadBusy(true);
    await connectBackend();
    if (appMode === 'employee' && state.backendOnline) await api.request('/session/employee', {method:'POST'});
    setUploadBusy(false);
    showView(appMode === 'reviewer' ? 'dashboard' : 'new');
  };

  initialize();
  window.setInterval(() => { if (!state.backendOnline && !state.uploadBusy) connectBackend(); }, 10000);
  window.setInterval(refreshAutomatically, 15000);
  window.addEventListener('focus', refreshAutomatically);
  document.addEventListener('visibilitychange', refreshAutomatically);
  window.addEventListener('online', () => connectBackend());
})();
