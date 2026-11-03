/* Meeting review is a saved draft. Approval and file creation are separate actions. */
window.MeetingUI = (() => {
  const $ = selector => document.querySelector(selector);
  let renderedMonth = null;
  const savedNoteKeys = new Set();
  let flushPendingNotes = async () => false;
  const render = ({draft, escapeHtml:esc, formatMoney:money, formatMonth:month}) => {
    const rows = draft?.rows || [];
    const notes = new Map();
    if (draft?.month === renderedMonth) {
      $('#meeting-sheet').querySelectorAll('[data-note-existing]').forEach(input=>{
        const key = input.dataset.noteExisting;
        const details = input.closest('details');
        const append = details.querySelector('[data-note-append]');
        notes.set(key,{open:details.open,existing:input.value !== input.defaultValue ? input.value : null,append:append.value,baseText:input.dataset.noteBaseText ?? input.defaultValue,revision:input.dataset.noteRevision});
      });
    }
    $('#add-meeting-row').disabled = !draft;
    $('#meeting-title').textContent = draft ? `${Number(draft.month.slice(5))}月奖金核算表` : '奖金核算表';
    $('#meeting-status').textContent = draft ? (draft.approved ? '会议结果已确认' : '会议审议稿 · 修改可保存') : '正在读取会议表…';
    $('#approve-meeting').disabled = !draft?.export_anchor || draft.approved;
    $('#approve-meeting').textContent = draft?.approved ? '终审已完成' : '确认会议结果，完成终审';
    const num = value => value == null ? '<span class="missing">—</span>' : esc(Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}));
    $('#meeting-sheet').innerHTML = draft ? `<div class="meeting-sheet-heading"><span>${esc(month(draft.month))} · 广播电视制作中心 · 单位：元</span><div class="meeting-totals"><strong>应发合计 ${money(draft.total,2)}</strong><strong>实发合计 ${money(draft.actual_total,2)}</strong></div></div>
      <div class="meeting-table-wrap" tabindex="0" aria-label="奖金核算表，可横向滚动"><table class="meeting-table"><thead><tr><th>序号</th><th>部门 / 项目</th><th class="num">设备使用</th><th class="num">技术服务</th><th class="num">毛利奖励</th><th class="num">其他</th><th class="num">应发</th><th class="num">实发</th><th>引进人</th><th>表单和证明</th><th>备注</th><th class="meeting-owner">负责人</th><th class="meeting-edit">会议修改</th></tr></thead><tbody>${rows.map(row => `<tr class="${row.supplemental ? 'meeting-supplemental' : ''}"><td>${row.number}</td><th scope="row">${esc(row.label)}</th>${['equipment','technical','profit','other','total'].map(key=>`<td class="num ${key==='total'?'payable':''}">${num(row[key])}</td>`).join('')}<td class="num actual">${num(row.actual)}</td><td>${esc(row.introducer || '—')}</td><td>${row.supplemental ? (row.attachments?.length ? `<div class="meeting-attachments">${row.attachments.map((file,index)=>`<div><span>${esc(file.file_name)}</span><div class="meeting-file-actions"><button class="text-action" data-supplemental-file="${esc(row.id)}" data-file-index="${index}" data-file-action="open">打开</button><button class="text-action" data-supplemental-file="${esc(row.id)}" data-file-index="${index}" data-file-action="download">下载</button></div></div>`).join('')}</div>` : '<span class="missing">无附件</span>') : row.batch_id ? `<button class="text-action" data-meeting-evidence="${esc(row.batch_id)}" data-label="${esc(row.label)}">查看材料</button>` : '<span class="missing">—</span>'}</td><td class="meeting-remark">${row.supplemental ? `<span>${esc(row.remark || '—')}</span>` : `<details><summary title="点击展开备注"><span>${esc(row.remark || '添加会议备注')}</span></summary><div class="meeting-note-editor"><label>已有备注<textarea maxlength="5000" data-note-existing="${esc(row.key)}">${esc(row.remark || '')}</textarea></label><label>追加会议记录<textarea maxlength="5000" data-note-append="${esc(row.key)}" placeholder="记录讨论意见或会议决定"></textarea></label><button class="secondary-button" data-note-save="${esc(row.key)}">保存备注</button></div></details>`}</td><td class="meeting-owner">${esc(row.owner || '—')}</td><td class="meeting-edit">${row.supplemental ? `<button class="text-action" data-supplemental-edit="${esc(row.id)}">修改条目 / 附件</button>` : ''}<button class="text-action" data-meeting-cells="${esc(row.key)}">会议数据</button><button class="text-action" data-remove-row="${esc(row.key)}">移出本月</button>${!row.supplemental && row.calculation_id ? `<button class="text-action" data-return-secondary="${esc(row.key)}">退回复审</button>` : ''}</td></tr>`).join('')}</tbody><tfoot><tr><th colspan="6">应发合计 / 实发合计</th><td class="num payable">${num(draft.total)}</td><td class="num actual">${num(draft.actual_total)}</td><td colspan="5">${draft.items.length} 份有效申报 · 其他计入应发，实发默认跟随应发</td></tr></tfoot></table></div>` : '<p class="meeting-loading">正在读取本月汇总，请稍候。</p>';
    $('#meeting-sheet').querySelectorAll('[data-note-existing]').forEach(input=>{
      const key = input.dataset.noteExisting;
      const row = rows.find(row=>row.key===key);
      input.dataset.noteRevision = String(draft?.remarks?.find(item=>item.key===row?.remark_key)?.revision || 0);
      input.dataset.noteBaseText = input.defaultValue;
      const snapshot = notes.get(key);
      if (!snapshot) return;
      const details = input.closest('details');
      details.open = snapshot.open;
      if (!savedNoteKeys.has(key)) {
        if (snapshot.existing !== null) {
          const latest = input.defaultValue;
          if (latest === snapshot.baseText) input.value = snapshot.existing;
          else if (!snapshot.baseText || latest.startsWith(snapshot.baseText+'\n')) {
            const additions = latest.slice(snapshot.baseText.length).split('\n').filter(line=>line && !snapshot.existing.split('\n').includes(line));
            input.value = snapshot.existing + (additions.length ? (snapshot.existing ? '\n' : '')+additions.join('\n') : '');
          } else {
            input.value = snapshot.existing;
            input.dataset.noteRevision = snapshot.revision;
            input.dataset.noteBaseText = snapshot.baseText;
          }
        }
        details.querySelector('[data-note-append]').value = snapshot.append;
      }
    });
    renderedMonth = draft?.month || null;
    savedNoteKeys.clear();
    $('#export-summary').innerHTML = `<div><h2>${draft ? `${esc(month(draft.month))}奖金核算表` : '选择结算月份'}</h2><p>${draft?.approved ? '会议结果已确认，可导出到电脑并保存历史版本。' : '请先到终审页面核对并确认会议结果。'}</p></div><div><small>应发合计</small><strong>${money(draft?.total || 0,2)}</strong></div><div><small>实发合计</small><strong>${money(draft?.actual_total || 0,2)}</strong></div>`;
    $('#export-ready-list').innerHTML = draft?.approved && draft.export_anchor ? `<div class="export-actions"><button class="primary-button" data-export-id="${esc(draft.export_anchor)}" data-export-mode="values">下载数值版完整归档包（ZIP）</button><button class="secondary-button" data-export-id="${esc(draft.export_anchor)}" data-export-mode="formula">下载公式版完整归档包（ZIP）</button><p>包含汇总表、原始表单、证明附件和备注。请先完整解压，再打开汇总表；材料链接直接读取本地文件。</p></div>` : '<button class="secondary-button" data-nav="meeting">前往终审</button>';
  };
  const bind = ({api,state,showToast,loadFinalReview,showView}) => {
    let noteSaveQueue = Promise.resolve(false);
    const savePendingNotes = async onlyKey => {
      const draft = state.finalReview;
      if (!draft) return false;
      const pending = [];
      $('#meeting-sheet').querySelectorAll('[data-note-existing]').forEach(input=>{
        const key = input.dataset.noteExisting;
        if (onlyKey && onlyKey !== key) return;
        const row = draft.rows.find(row=>row.key===key);
        if (!row) return;
        const append = input.closest('details').querySelector('[data-note-append]');
        const added = append.value.trim();
        const text = input.value + (added ? (input.value ? '\n' : '')+added : '');
        if (text.length > 5000) throw new Error('备注总长度不能超过5000字，请先整理备注');
        if (text !== (row.remark || '')) pending.push({input,append,row,text});
      });
      for (const {input,append} of pending) { input.disabled = true; append.disabled = true; }
      try {
        for (const {input,append,row,text} of pending) {
          const saved = draft.remarks.find(item=>item.key===row.remark_key);
          const revision = Number(input.dataset.noteRevision ?? saved?.revision ?? 0);
          const result = await api.request(`/months/${encodeURIComponent(draft.month)}/remarks`,{method:'PATCH',body:JSON.stringify({key:row.remark_key,text,revision})});
          if (saved && result?.remark) Object.assign(saved,result.remark);
          row.remark = text;
          input.value = text; input.defaultValue = text; append.value = '';
          input.dataset.noteBaseText = text;
          input.dataset.noteRevision = String(result?.remark?.revision ?? revision+1);
          savedNoteKeys.add(row.key);
        }
        if (pending.length) await loadFinalReview();
      } finally {
        for (const {input,append} of pending) { input.disabled = false; append.disabled = false; }
      }
      return pending.length > 0;
    };
    flushPendingNotes = onlyKey => {
      const next = noteSaveQueue.catch(()=>false).then(()=>savePendingNotes(onlyKey));
      noteSaveQueue = next;
      return next;
    };
    const useSupplementalFile = async (file,action,button) => {
      // Reserve the new tab during the click, before fetching the authenticated file.
      const preview = action === 'open' ? window.open('about:blank','_blank') : null;
      if (preview) preview.opener = null;
      button.disabled = true;
      try {
        const blob = await api.supplementalFile(file.content_url);
        const url = URL.createObjectURL(blob);
        if (action === 'open' && preview) preview.location.href = url;
        else if (action === 'open') {
          URL.revokeObjectURL(url);
          showToast('浏览器阻止了新窗口，请允许弹出窗口后重试',true);
          return;
        } else {
          const link = document.createElement('a');
          link.href = url; link.download = file.file_name; link.click();
        }
        window.setTimeout(()=>URL.revokeObjectURL(url),60000);
      } catch(error) {
        if (preview) preview.close();
        showToast(error.message,true);
      } finally { button.disabled = false; }
    };
    const openSupplemental = row => {
      if (!state.finalReview) return;
      const form = $('#supplemental-row-form');
      form.reset();
      form.dataset.id = row?.id || '';
      form.dataset.month = state.finalReview.month;
      form.dataset.revision = state.finalReview.revision;
      $('#supplemental-row-title').textContent = row ? '修改会议条目' : '新增会议条目';
      $('#supplemental-label').value = row?.label || '';
      $('#supplemental-other').value = row?.other ?? '';
      $('#supplemental-remark').value = row?.remark || '';
      $('#supplemental-existing-files').textContent = row?.attachments?.length ? `已有 ${row.attachments.length} 份附件；本次选择的文件会追加保存。` : '';
      $('#supplemental-row-dialog').showModal();
    };
    $('#add-meeting-row').addEventListener('click',()=>openSupplemental(null));
    $('#supplemental-cancel').addEventListener('click',()=>$('#supplemental-row-dialog').close());
    $('#supplemental-files-close').addEventListener('click',()=>$('#supplemental-files-dialog').close());
    $('#supplemental-row-form').addEventListener('submit',async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const label = $('#supplemental-label').value.trim();
      if (!label) { showToast('请填写条目名称',true); return; }
      const other = $('#supplemental-other').value;
      const remark = $('#supplemental-remark').value;
      const files = [...$('#supplemental-files').files];
      const {month,id,revision} = form.dataset;
      $('#supplemental-save').disabled = true;
      let edited = false;
      try {
        if (id) {
          const result = await api.updateSupplemental(month,id,{revision,label,other,remark});
          edited = true;
          form.dataset.revision = result.revision;
          if (files.length) {
            const body = new FormData();
            body.append('revision',result.revision);
            files.forEach(file=>body.append('files',file));
            await api.uploadSupplemental(month,id,body);
          }
        } else {
          const body = new FormData();
          Object.entries({revision,label,other,remark}).forEach(([key,value])=>body.append(key,value));
          files.forEach(file=>body.append('files',file));
          await api.createSupplemental(month,body);
        }
        $('#supplemental-row-dialog').close();
        await loadFinalReview();
        showToast('会议条目已保存，请核对合计并重新确认会议结果');
      } catch(error) {
        showToast(edited ? `条目已保存，附件追加失败：${error.message}` : error.message,true);
        await loadFinalReview();
        if (state.finalReview?.month === month) form.dataset.revision = state.finalReview.revision;
      } finally { $('#supplemental-save').disabled = false; }
    });
    $('#projection-toggle').addEventListener('click', () => {
      const enabled = document.body.classList.toggle('projection-mode');
      $('#projection-toggle').textContent = enabled ? '退出投影模式' : '投影模式';
    });
    $('#refresh-export-month').addEventListener('click', () => {
      $('#final-review-month').value = $('#export-month').value;
      loadFinalReview();
    });
    $('#export-month').addEventListener('change', () => $('#refresh-export-month').click());
    const closeEvidence = () => { $('#meeting-evidence-dialog').close(); $('#meeting-evidence-frame').src = 'about:blank'; };
    $('#close-meeting-evidence').addEventListener('click',closeEvidence);
    $('#meeting-evidence-dialog').addEventListener('close', () => { $('#meeting-evidence-frame').src = 'about:blank'; });
    document.addEventListener('click', async event => {
      const returnButton = event.target.closest('[data-return-secondary]');
      if (returnButton && state.finalReview) {
        const draft = state.finalReview;
        const row = draft.rows.find(row=>row.key===returnButton.dataset.returnSecondary);
        if (!row || row.supplemental || !row.calculation_id) return;
        const reason = window.prompt(`请填写将“${row.label}”退回复审的原因：`);
        if (reason === null) return;
        if (!reason.trim()) { showToast('请填写退回原因',true); return; }
        returnButton.disabled = true;
        try {
          await api.request(`/months/${encodeURIComponent(draft.month)}/return-to-secondary`,{method:'POST',body:JSON.stringify({key:row.key,revision:draft.revision,reason:reason.trim()})});
          await loadFinalReview();
          showToast('已退回复审，复审通过后可重新纳入本月终审');
        } catch(error) { showToast(error.message,true); }
        finally { returnButton.disabled = false; }
        return;
      }
      const supplementalFile = event.target.closest('[data-supplemental-file]');
      if (supplementalFile) {
        const row = state.finalReview?.rows.find(row=>row.id===supplementalFile.dataset.supplementalFile);
        const file = row?.attachments?.[Number(supplementalFile.dataset.fileIndex)];
        if (file) await useSupplementalFile(file,supplementalFile.dataset.fileAction,supplementalFile);
        return;
      }
      const remove = event.target.closest('[data-remove-row]');
      if (remove && state.finalReview) {
        const draft = state.finalReview;
        const row = draft.rows.find(r=>r.key===remove.dataset.removeRow);
        if (!row || !window.confirm(`确认将“${row.label}”从本月汇总中移除？本月合计将重新计算，原申报和历史导出保留；可重新提交替换条目或以后月份再申报。`)) return;
        remove.disabled = true;
        try {
          const path = row.supplemental ? `/months/${draft.month}/supplemental-rows/${row.id}?revision=${draft.revision}` : `/months/${draft.month}/rows?key=${encodeURIComponent(row.key)}&revision=${draft.revision}`;
          await api.request(path,{method:'DELETE'});
          await loadFinalReview();
          showToast('已从本月移除，请核对合计并重新确认终审');
        } catch(error) { showToast(error.message,true); }
        finally { remove.disabled = false; }
        return;
      }
      const supplementalEdit = event.target.closest('[data-supplemental-edit]');
      if (supplementalEdit) { openSupplemental(state.finalReview?.rows.find(row=>row.id === supplementalEdit.dataset.supplementalEdit)); return; }
      const supplementalFiles = event.target.closest('[data-supplemental-files]');
      if (supplementalFiles) {
        const row = state.finalReview?.rows.find(row=>row.id === supplementalFiles.dataset.supplementalFiles);
        if (!row) return;
        $('#supplemental-files-title').textContent = `${row.label} · 证明附件`;
        const list = $('#supplemental-files-list');
        list.replaceChildren();
        (row.attachments || []).forEach(file => {
          const item = document.createElement('div');
          const name = document.createElement('span');
          name.textContent = file.file_name;
          item.appendChild(name);
          ['open','download'].forEach(action=>{
            const button = document.createElement('button');
            button.className = 'text-action';
            button.textContent = action === 'open' ? '打开' : '下载';
            button.addEventListener('click',()=>useSupplementalFile(file,action,button));
            item.appendChild(button);
          });
          list.appendChild(item);
        });
        $('#supplemental-files-dialog').showModal();
        return;
      }
      const noteButton = event.target.closest('[data-note-save]');
      if (noteButton && state.finalReview) {
        noteButton.disabled = true;
        try {
          await flushPendingNotes(noteButton.dataset.noteSave);
          showToast('会议备注已保存，导出时会同步写入');
        } catch(error) { showToast(error.message,true); }
        finally { noteButton.disabled = false; }
        return;
      }
      const evidence = event.target.closest('[data-meeting-evidence]');
      if (evidence) {
        $('#meeting-evidence-title').textContent = evidence.dataset.label;
        $('#close-meeting-evidence').textContent = `返回${$('#meeting-title').textContent}`;
        $('#meeting-evidence-frame').src = api.resolveUrl(`/api/v1/batches/${encodeURIComponent(evidence.dataset.meetingEvidence)}/evidence`);
        $('#meeting-evidence-dialog').showModal();
      }
      const edit = event.target.closest('[data-meeting-cells]');
      if (edit && state.finalReview) {
        const row = state.finalReview.rows.find(row => row.key === edit.dataset.meetingCells);
        if (!row) return;
        const form = $('#meeting-cells-form');
        form.dataset.key = row.key;
        form.dataset.revision = state.finalReview.revision;
        form.dataset.month = state.finalReview.month;
        $('#meeting-cells-title').textContent = row.label;
        $('#meeting-total-label').hidden = false;
        $('#meeting-cell-other').value = row.other ?? '';
        $('#meeting-cell-actual').value = state.finalReview.cells?.[row.key]?.actual ?? '';
        $('#meeting-cell-actual').placeholder = `留空跟随应发（${row.total}）`;
        $('#meeting-cell-total').value = state.finalReview.cells?.[row.key]?.total ?? '';
        $('#meeting-cell-total').placeholder = `留空按计算金额（当前应发 ${row.total}）`;
        $('#meeting-cell-reason').value = row.reason || '';
        $('#meeting-cells-dialog').showModal();
      }
    });
    $('#meeting-cells-cancel').addEventListener('click',()=>$('#meeting-cells-dialog').close());
    $('#meeting-cells-form').addEventListener('submit',async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const payload = {key:form.dataset.key,revision:form.dataset.revision,reason:$('#meeting-cell-reason').value.trim()};
      for (const key of ['other','actual']) payload[key] = $('#meeting-cell-'+key).value || null;
      payload.total = $('#meeting-cell-total').value || null;
      $('#meeting-cells-save').disabled = true;
      try {
        await api.meetingCells(form.dataset.month,payload);
        $('#meeting-cells-dialog').close();
        await loadFinalReview();
        showToast('会议数据已保存');
      } catch(error) { showToast(error.message,true); }
      finally { $('#meeting-cells-save').disabled = false; }
    });
    $('#approve-meeting').addEventListener('click',async () => {
      try {
        await flushPendingNotes();
        const draft = state.finalReview;
        if (!draft || !window.confirm('确认会议修改均已保存，并通过本月终审？确认后可前往导出；之后再修改需重新确认。')) return;
        $('#approve-meeting').disabled = true;
        await api.approveMeeting(draft.month,draft.revision);
        await loadFinalReview();
        showToast('终审已完成，可前往导出保存到电脑');
        showView('exports');
      } catch(error) { showToast(error.message,true); await loadFinalReview(); }
    });
  };
  return {render,bind,flushNotes:()=>flushPendingNotes()};
})();
