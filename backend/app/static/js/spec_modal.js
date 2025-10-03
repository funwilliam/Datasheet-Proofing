// window.SpecModal 共用模組
(function () {
  const $ = (s, r=document) => r.querySelector(s);

  let fetchModel = null;    // (modelNumber) => Promise<Model>
  let patchModel = null;    // (modelNumber, payload) => Promise<Model>
  let onSaved    = null;    // () => void（可選）

  const modal      = document.getElementById('specModal');
  const titleEl    = document.getElementById('specTitle');
  const statusEl   = document.getElementById('specStatus');
  const form       = document.getElementById('specForm');
  const chkVerified= document.getElementById('chkVerified');
  const reviewerEl = document.getElementById('reviewerInp');

  if (!modal || !form) {
    console.warn('[SpecModal] modal DOM not found. Did you include partials/spec_modal.html ?');
  }

  function open() {
    modal?.classList.remove('hidden');
  }
  function close() {
    modal?.classList.add('hidden');
  }
  function isOpen() {
    return !modal?.classList.contains('hidden');
  }

  function fillForm(data){
    form.elements['input_voltage_range'].value = data.input_voltage_range || '';
    form.elements['output_voltage'].value      = data.output_voltage || '';
    form.elements['output_power'].value        = data.output_power || '';
    form.elements['package'].value             = data.package || '';
    form.elements['isolation'].value           = data.isolation || '';
    form.elements['insulation'].value          = data.insulation || '';
    form.elements['dimension'].value           = data.dimension || '';
    form.elements['notes'].value               = data.notes || '';
    form.elements['applications'].value        = Array.isArray(data.applications) ? data.applications.join(', ') : '';

    chkVerified.checked = (data.verify_status === 'verified');
    reviewerEl.value    = data.reviewer || '';

    const badge = (data.verify_status === 'verified')
      ? '<span class="badge badge-verified">目前狀態：verified</span>'
      : '<span class="badge badge-unverified">目前狀態：unverified</span>';
    const when = data.reviewed_at
      ? `　<span style="color:#6b7280;font-size:12px">於 ${new Date(data.reviewed_at).toLocaleString()} 驗證</span>`
      : '';
    statusEl.innerHTML = badge + when;

    const hint = document.getElementById('verifiedHint');
    if (hint){
      hint.textContent = data.reviewed_at ? `上次驗證：${new Date(data.reviewed_at).toLocaleString()}` : '';
    }
  }

  async function openFor(modelNumber){
    if (!fetchModel) throw new Error('[SpecModal] fetchModel not set. Call SpecModal.setup().');
    try{
      const data = await fetchModel(modelNumber);
      titleEl.textContent = modelNumber;
      form.dataset.model  = modelNumber;
      fillForm(data);
      open();
    }catch(e){
      alert('讀取型號失敗：' + (e?.message || e));
    }
  }

  // 設定外部依賴（fetch/patch，與保存成功回呼）
  function setup(opts){
    fetchModel = opts.fetchModel;
    patchModel = opts.patchModel;
    onSaved    = opts.onSaved || null;

    // 關閉事件
    $('#specCancel')?.addEventListener('click', close);
    modal?.addEventListener('click', (e)=>{ if (e.target === modal) close(); });
    document.addEventListener('keydown', (e)=>{ if (e.key === 'Escape' && isOpen()) close(); });

    // 提交事件
    form?.addEventListener('submit', async (e)=>{
      e.preventDefault();
      if (!patchModel) throw new Error('[SpecModal] patchModel not set. Call SpecModal.setup().');
      const model = form.dataset.model;
      const apps = (form.elements['applications'].value || '')
        .split(',').map(s=>s.trim()).filter(Boolean);

      const payload = {
        input_voltage_range: form.elements['input_voltage_range'].value || null,
        output_voltage:      form.elements['output_voltage'].value || null,
        output_power:        form.elements['output_power'].value || null,
        package:             form.elements['package'].value || null,
        isolation:           form.elements['isolation'].value || null,
        insulation:          form.elements['insulation'].value || null,
        dimension:           form.elements['dimension'].value || null,
        applications:        apps,
        notes:               form.elements['notes'].value || null,
        verify_status:       (document.getElementById('chkVerified').checked ? 'verified' : 'unverified'),
        reviewer:            document.getElementById('reviewerInp').value || null,
      };

      try{
        await patchModel(model, payload);
        close();
        if (onSaved) onSaved(model);
        else alert('已儲存');
      }catch(e){
        alert('儲存失敗：' + (e?.message || e));
      }
    });
  }

  window.SpecModal = { setup, openFor, open, close, isOpen };
})();
