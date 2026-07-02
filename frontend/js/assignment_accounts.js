// ── Bulk-delete logic ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  var chkAll       = document.getElementById('chk-select-all');
  var bulkBar      = document.getElementById('bulk-action-bar');
  var bulkCount    = document.getElementById('bulk-selected-count');
  var btnBulkDel   = document.getElementById('btn-bulk-delete');
  var btnBulkCancel = document.getElementById('btn-bulk-cancel');

  function getChecked() {
    return Array.from(document.querySelectorAll('.row-checkbox:checked'));
  }

  function syncBulkBar() {
    var checked = getChecked();
    var n = checked.length;
    if (n > 0) {
      bulkBar.classList.remove('bulk-action-bar--hidden');
      bulkCount.textContent = n + ' selected';
    } else {
      bulkBar.classList.add('bulk-action-bar--hidden');
    }
    // Sync header checkbox state
    var all = document.querySelectorAll('.row-checkbox');
    if (chkAll) {
      chkAll.checked       = all.length > 0 && n === all.length;
      chkAll.indeterminate = n > 0 && n < all.length;
    }
  }

  if (chkAll) {
    chkAll.addEventListener('change', function () {
      document.querySelectorAll('.row-checkbox').forEach(function (cb) {
        cb.checked = chkAll.checked;
      });
      syncBulkBar();
    });
  }

  document.querySelectorAll('.row-checkbox').forEach(function (cb) {
    cb.addEventListener('change', syncBulkBar);
  });

  if (btnBulkCancel) {
    btnBulkCancel.addEventListener('click', function () {
      document.querySelectorAll('.row-checkbox').forEach(function (cb) { cb.checked = false; });
      if (chkAll) { chkAll.checked = false; chkAll.indeterminate = false; }
      bulkBar.classList.add('bulk-action-bar--hidden');
    });
  }

  if (btnBulkDel) {
    btnBulkDel.addEventListener('click', async function () {
      var checked = getChecked();
      if (checked.length === 0) { return; }

      var names = checked.map(function (cb) {
        var row = cb.closest('tr');
        return row ? (row.dataset.fullName || row.dataset.username || 'account') : 'account';
      });
      var preview = names.slice(0, 3).join(', ') + (names.length > 3 ? '…' : '');
      if (!window.confirm('Delete ' + checked.length + ' account(s)?\n' + preview + '\n\nThis cannot be undone.')) { return; }

      var ids = checked.map(function (cb) { return cb.value; });
      btnBulkDel.disabled    = true;
      btnBulkDel.textContent = 'Deleting…';

      var result = await postJson(btnBulkDel.dataset.bulkDeleteUrl, { ids: ids });
      if (!result.success) {
        btnBulkDel.disabled    = false;
        btnBulkDel.innerHTML   = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm1 7h2v8h-2v-8Zm4 0h2v8h-2v-8ZM6 7h12l-1 13a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6 7Z"/></svg> Delete Selected';
        showToast(result.message || 'Bulk delete failed.', 'error');
        return;
      }

      showToast(result.message || 'Accounts deleted.', 'success');
      setTimeout(function () { window.location.reload(); }, 700);
    });
  }
});

// ── Create/Edit modal logic ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  var openButton     = document.getElementById('open-assignment-modal');
  var modal          = document.getElementById('assignment-modal');
  var form           = document.getElementById('assignment-account-form');
  var submitButton   = document.getElementById('assignment-submit');
  var modalTitle     = document.getElementById('assignment-modal-title');
  var modalSubtitle  = document.getElementById('assignment-modal-subtitle');
  var viewModal      = document.getElementById('view-account-modal');

  var accountId      = document.getElementById('assignment-account-id');
  var role           = document.getElementById('assignment-role');
  var statusSelect   = document.getElementById('assignment-status');

  var standardSection = document.getElementById('acc-standard-section');
  var usernameInput   = document.getElementById('assignment-username');
  var passwordInput   = document.getElementById('assignment-password');

  var codeSection   = document.getElementById('acc-code-section');
  var nameInput     = document.getElementById('assignment-name');
  var btnGenerate   = document.getElementById('btn-generate-code');
  var codeDisplay   = document.getElementById('acc-code-display');
  var codeValue     = document.getElementById('acc-code-value');
  var btnCopyCode   = document.getElementById('btn-copy-code');

  var successCard        = document.getElementById('acc-success-card');
  var successMsg         = document.getElementById('acc-success-msg');
  var successCode        = document.getElementById('acc-success-code');
  var btnCopySuccessCode = document.getElementById('btn-copy-success-code');

  var pageType = (document.body.dataset.accountPage || 'all');
  var defaultRole = document.body.dataset.defaultRole || 'Tabulator';

  var _generatedCode = '';

  if (!openButton || !modal || !form) { return; }

  configureRoleSelect();

  function configureRoleSelect() {
    if (!role) return;
    Array.prototype.forEach.call(role.options, function (opt) {
      if (pageType === 'tabulator') {
        opt.hidden = opt.value !== 'Tabulator';
        opt.disabled = opt.value !== 'Tabulator';
      } else if (pageType === 'judge_scorer') {
        opt.hidden = opt.value === 'Tabulator';
        opt.disabled = opt.value === 'Tabulator';
      }
    });
    if (pageType === 'tabulator') {
      role.value = 'Tabulator';
      role.disabled = true;
    } else if (pageType === 'judge_scorer') {
      role.disabled = false;
      if (role.value === 'Tabulator') role.value = defaultRole;
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function isCodeRole(r) {
    return r === 'Judge' || r === 'Scorer';
  }

  function generateCode() {
    var chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    var code  = '';
    for (var i = 0; i < 8; i++) {
      code += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return code;
  }

  function roleSubtitle(r) {
    if (r === 'Judge')  { return 'Enter the judge\'s name and generate an access code to share with them.'; }
    if (r === 'Scorer') { return 'Enter the scorer\'s name and generate an access code to share with them.'; }
    return 'Tabulators sign in via the web portal with their username and password.';
  }

  function applyRoleMode(r, isCreating) {
    var code = isCodeRole(r);

    standardSection.classList.toggle('acc-section--hidden', code);
    codeSection.classList.toggle('acc-code-section--hidden', !code);
    successCard.classList.add('acc-success-card--hidden');

    usernameInput.required = !code;
    passwordInput.required = !code && isCreating;
    nameInput.required     = code;

    _generatedCode = '';
    codeDisplay.classList.add('acc-code-display--hidden');
    codeValue.textContent = '';
    resetGenerateBtn();

    modalSubtitle.textContent = roleSubtitle(r);

    // For code-roles in CREATE mode: hide "Create Account" — generate button does it.
    // For code-roles in EDIT mode: keep "Save" button visible.
    // For Tabulator: always show.
    if (code && isCreating) {
      submitButton.classList.add('acc-btn--hidden');
    } else {
      submitButton.classList.remove('acc-btn--hidden');
    }
  }

  function resetGenerateBtn() {
    btnGenerate.disabled = false;
    btnGenerate.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 8C8 10 5.9 16.17 3.82 19.82L5.71 21l1-1.85A4.52 4.52 0 0 0 8 20c4 0 4-2 8-2s4 2 8 2v-2c-4 0-4-2-8-2-.63 0-1.17.06-1.67.14C14.43 11.22 15.06 8.07 17 8Z"/></svg> Generate Access Code';
  }

  // ── Role change ────────────────────────────────────────────────────────────

  role.addEventListener('change', function () {
    applyRoleMode(role.value, !accountId.value);
  });

  // ── Generate Access Code button ────────────────────────────────────────────

  btnGenerate.addEventListener('click', async function () {
    var roleVal    = role.value;
    var isCreating = !accountId.value;

    // Require name before generating
    var fullName = nameInput.value.trim();
    if (!fullName) {
      showToast('Please enter a name first.', 'warning');
      nameInput.focus();
      return;
    }

    var code = generateCode();
    _generatedCode = code;
    codeValue.textContent = code;
    codeDisplay.classList.remove('acc-code-display--hidden');

    // ── CREATE mode: save account immediately ──────────────────────────────
    if (isCreating) {
      btnGenerate.disabled     = true;
      btnGenerate.textContent  = 'Creating account…';

      var result = await postJson(modal.dataset.endpoint, {
        full_name:   fullName,
        access_code: code,
        role:        roleVal,
        is_active:   statusSelect.value === 'active',
      });

      if (!result.success) {
        btnGenerate.disabled    = false;
        btnGenerate.innerHTML   =
          '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 8C8 10 5.9 16.17 3.82 19.82L5.71 21l1-1.85A4.52 4.52 0 0 0 8 20c4 0 4-2 8-2s4 2 8 2v-2c-4 0-4-2-8-2-.63 0-1.17.06-1.67.14C14.43 11.22 15.06 8.07 17 8Z"/></svg> Try Again';
        _generatedCode          = '';
        codeValue.textContent   = '';
        codeDisplay.classList.add('acc-code-display--hidden');
        showToast(result.message || 'Failed to create account.', 'error');
        return;
      }

      // Show success card — account is now in the database
      showSuccessCard(fullName, roleVal, result.username, code);
      return;
    }

    // ── EDIT mode: just show new code (committed on Save) ─────────────────
    btnGenerate.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 8C8 10 5.9 16.17 3.82 19.82L5.71 21l1-1.85A4.52 4.52 0 0 0 8 20c4 0 4-2 8-2s4 2 8 2v-2c-4 0-4-2-8-2-.63 0-1.17.06-1.67.14C14.43 11.22 15.06 8.07 17 8Z"/></svg> Regenerate Code';
  });

  btnCopyCode.addEventListener('click', function () {
    copyToClipboard(codeValue.textContent);
    showToast('Code copied!', 'success');
  });

  btnCopySuccessCode.addEventListener('click', function () {
    copyToClipboard(successCode.textContent);
    showToast('Code copied!', 'success');
  });

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function () {});
    } else {
      var el = document.createElement('textarea');
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
    }
  }

  // ── Edit / Delete row buttons ──────────────────────────────────────────────

  openButton.addEventListener('click', function () { openCreateModal(); });

  document.querySelectorAll('.edit-account').forEach(function (button) {
    button.addEventListener('click', function () {
      var row = button.closest('tr');
      if (!row) { return; }
      openEditModal(row, button.dataset.updateUrl);
    });
  });

  document.querySelectorAll('.view-account').forEach(function (button) {
    button.addEventListener('click', function () {
      var row = button.closest('tr');
      if (!row) { return; }
      openViewModal(row);
    });
  });

  document.querySelectorAll('.delete-account').forEach(function (button) {
    button.addEventListener('click', async function () {
      var row  = button.closest('tr');
      var name = row ? (row.dataset.fullName || row.dataset.username || 'this account') : 'this account';
      if (!window.confirm('Delete ' + name + '? This action cannot be undone.')) { return; }

      button.disabled = true;
      var result = await postJson(button.dataset.deleteUrl, {});
      if (!result.success) {
        button.disabled = false;
        showToast(result.message || 'Unable to delete account.', 'error');
        return;
      }
      showToast(result.message || 'Account deleted.', 'success');
      setTimeout(function () { window.location.reload(); }, 700);
    });
  });

  // ── Close ──────────────────────────────────────────────────────────────────

  modal.addEventListener('click', function (e) {
    if (e.target.dataset.closeModal === 'true') { closeModal(); }
  });

  if (viewModal) {
    viewModal.addEventListener('click', function (e) {
      if (e.target.dataset.closeViewModal === 'true') { closeViewModal(); }
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (viewModal && !viewModal.classList.contains('hidden')) { closeViewModal(); return; }
      if (!modal.classList.contains('hidden')) { closeModal(); }
    }
  });

  function finishDoneFlow() {
    form.dataset.doneMode = '';
    submitButton.type = 'submit';
    submitButton.removeEventListener('click', onDoneClick);
    closeModal();
    window.location.reload();
  }

  function onDoneClick(e) {
    if (form.dataset.doneMode !== '1') return;
    e.preventDefault();
    e.stopPropagation();
    finishDoneFlow();
  }

  // ── Form submit (Tabulator create/edit, Judge/Scorer edit-save, Done) ──────

  form.addEventListener('submit', async function (e) {
    e.preventDefault();

    if (form.dataset.doneMode === '1') {
      finishDoneFlow();
      return;
    }

    var editing  = Boolean(accountId.value);
    var isActive = statusSelect.value === 'active';
    var roleVal  = role.value;
    var payload  = { role: roleVal, is_active: isActive };

    if (isCodeRole(roleVal)) {
      // Edit-save for Judge/Scorer
      payload.full_name   = nameInput.value.trim();
      payload.access_code = _generatedCode; // blank = keep existing password
      if (!payload.full_name) {
        showToast('Please enter a name.', 'warning');
        return;
      }
    } else {
      payload.full_name = usernameInput.value.trim();
      payload.username  = usernameInput.value.trim();
      payload.password  = passwordInput.value;
      if (!payload.username || (!editing && !payload.password)) {
        showToast('Username and password are required.', 'warning');
        return;
      }
    }

    submitButton.disabled    = true;
    submitButton.textContent = editing ? 'Saving…' : 'Creating…';

    var result = await postJson(modal.dataset.endpoint, payload);
    if (!result.success) {
      submitButton.disabled    = false;
      submitButton.textContent = editing ? 'Save Changes' : 'Create Account';
      showToast(result.message || 'Unable to save account.', 'error');
      return;
    }

    // If edit generated a new access code, show it in the success card
    if (isCodeRole(roleVal) && result.access_code) {
      showSuccessCard(payload.full_name, roleVal, null, result.access_code);
      return;
    }

    showToast(result.message || 'Account saved.', 'success');
    closeModal();
    setTimeout(function () { window.location.reload(); }, 700);
  });

  // ── Success card ───────────────────────────────────────────────────────────

  function showSuccessCard(fullName, roleLabel, username, code) {
    standardSection.classList.add('acc-section--hidden');
    codeSection.classList.add('acc-code-section--hidden');
    successCard.classList.remove('acc-success-card--hidden');

    var line = roleLabel + ' account for ' + fullName + ' created.';
    if (username) { line += ' Username: ' + username; }
    successMsg.textContent  = line;
    successCode.textContent = code;

    usernameInput.required = false;
    passwordInput.required = false;
    nameInput.required = false;

    submitButton.classList.remove('acc-btn--hidden');
    submitButton.disabled    = false;
    submitButton.textContent = 'Done';
    submitButton.type = 'button';
    submitButton.addEventListener('click', onDoneClick);
    form.dataset.doneMode    = '1';
  }

  // ── Open helpers ───────────────────────────────────────────────────────────

  function openCreateModal() {
    modal.dataset.endpoint   = openButton.dataset.createUrl;
    form.dataset.doneMode    = '';
    submitButton.type = 'submit';
    submitButton.removeEventListener('click', onDoneClick);
    accountId.value          = '';
    usernameInput.value      = '';
    passwordInput.value      = '';
    nameInput.value          = '';
    role.value               = pageType === 'judge_scorer' ? defaultRole : 'Tabulator';
    statusSelect.value       = 'active';
    modalTitle.textContent   = 'Create Account';
    submitButton.disabled    = false;
    submitButton.textContent = 'Create Account';
    configureRoleSelect();
    applyRoleMode(role.value, true);
    openModal();
    if (isCodeRole(role.value)) { nameInput.focus(); } else { usernameInput.focus(); }
  }

  function openEditModal(row, endpoint) {
    modal.dataset.endpoint   = endpoint;
    form.dataset.doneMode    = '';
    submitButton.type = 'submit';
    submitButton.removeEventListener('click', onDoneClick);
    accountId.value          = row.dataset.accountId || '';
    var validRoles           = ['Tabulator', 'Judge', 'Scorer'];
    var assignedRole         = (row.dataset.role || defaultRole).trim();
    var matched              = validRoles.find(function (r) {
      return r.toLowerCase() === assignedRole.toLowerCase();
    }) || defaultRole;

    role.value               = matched;
    statusSelect.value       = row.dataset.status === 'active' ? 'active' : 'deactive';
    modalTitle.textContent   = 'Edit Account';
    submitButton.disabled    = false;
    submitButton.textContent = 'Save Changes';
    configureRoleSelect();

    if (isCodeRole(matched)) {
      nameInput.value     = row.dataset.fullName || '';
      usernameInput.value = '';
      passwordInput.value = '';
      _generatedCode = '';
      if (row.dataset.accessCode && row.dataset.accessCode !== '—') {
        _generatedCode = row.dataset.accessCode;
        codeValue.textContent = _generatedCode;
        codeDisplay.classList.remove('acc-code-display--hidden');
      }
    } else {
      usernameInput.value = row.dataset.username || '';
      passwordInput.value = '';
      nameInput.value     = '';
    }

    passwordInput.required = false;
    applyRoleMode(matched, false);
    openModal();
    if (isCodeRole(matched)) { nameInput.focus(); } else { usernameInput.focus(); }
  }

  function openViewModal(row) {
    if (!viewModal) return;
    var assignedRole = (row.dataset.role || '').trim();
    var isCode = isCodeRole(assignedRole);
    var nameField = document.getElementById('view-field-name');
    var codeField = document.getElementById('view-field-code');
    var nameEl = document.getElementById('view-account-name');
    var usernameEl = document.getElementById('view-account-username');
    var codeEl = document.getElementById('view-account-code');
    var roleEl = document.getElementById('view-account-role');
    var statusEl = document.getElementById('view-account-status');

    if (nameField) nameField.hidden = !isCode;
    if (codeField) codeField.hidden = !isCode;
    if (nameEl) nameEl.textContent = row.dataset.fullName || '—';
    if (usernameEl) usernameEl.textContent = row.dataset.username || '—';
    if (codeEl) codeEl.textContent = row.dataset.accessCode || '—';
    if (roleEl) roleEl.textContent = assignedRole || '—';
    if (statusEl) {
      statusEl.textContent = row.dataset.status === 'active' ? 'Active' : 'Inactive';
    }

    viewModal.classList.remove('hidden');
    viewModal.setAttribute('aria-hidden', 'false');
  }

  function closeViewModal() {
    if (!viewModal) return;
    viewModal.classList.add('hidden');
    viewModal.setAttribute('aria-hidden', 'true');
  }

  function openModal() {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeModal() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    form.dataset.doneMode = '';
    submitButton.type = 'submit';
    submitButton.removeEventListener('click', onDoneClick);
  }
});

// ── Shared utilities ──────────────────────────────────────────────────────────

async function postJson(url, payload) {
  try {
    var response = await fetch(url, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken':  getCsrfToken(),
      },
      body: JSON.stringify(payload || {}),
    });
    var text = await response.text();
    var data;
    try { data = JSON.parse(text); } catch (_) {
      return { success: false, message: 'Request failed (' + response.status + ').' };
    }
    if (!response.ok) {
      return { success: false, message: data.message || 'Request failed (' + response.status + ').' };
    }
    return data;
  } catch (_) {
    return { success: false, message: 'Network request failed.' };
  }
}

function getCsrfToken() {
  var el = document.querySelector('input[name="csrfmiddlewaretoken"]');
  if (el && el.value) { return el.value; }
  var name    = 'csrftoken=';
  var cookies = document.cookie.split(';');
  for (var i = 0; i < cookies.length; i++) {
    var c = cookies[i].trim();
    if (c.startsWith(name)) { return decodeURIComponent(c.slice(name.length)); }
  }
  return '';
}

function showToast(message, type) {
  var toast = document.createElement('div');
  toast.className   = 'toast-message ' + (type || 'success');
  toast.textContent = message;
  document.body.appendChild(toast);
  toast.offsetHeight;
  toast.classList.add('visible');
  setTimeout(function () {
    toast.classList.remove('visible');
    setTimeout(function () { toast.remove(); }, 350);
  }, 3500);
}
