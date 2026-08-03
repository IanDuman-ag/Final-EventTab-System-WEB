(function () {
  var urls = window.SA_USER_URLS || {};
  var modal = document.getElementById('sa-user-modal');
  var form = document.getElementById('sa-user-form');
  var title = document.getElementById('sa-modal-title');
  var idEl = document.getElementById('sa-user-id');
  var passwordWrap = document.getElementById('sa-password-wrap');

  function openModal(mode, data) {
    modal.classList.add('is-open');
    title.textContent = mode === 'edit' ? 'Edit User' : 'Create User';
    idEl.value = data && data.id ? data.id : '';
    document.getElementById('sa-username').value = (data && data.username) || '';
    document.getElementById('sa-email').value = (data && data.email) || '';
    var role = (data && data.role) || 'Admin';
    if (role === 'Super Admin') role = 'Admin';
    document.getElementById('sa-role').value = role;
    document.getElementById('sa-department').value = (data && data.department && data.department !== '—') ? data.department : '';
    document.getElementById('sa-password').value = '';
    document.getElementById('sa-active').checked = !data || data.active !== '0';
    passwordWrap.style.display = mode === 'edit' ? 'grid' : 'grid';
    document.getElementById('sa-password').placeholder = mode === 'edit' ? 'Leave blank to keep' : 'Required';
    document.getElementById('sa-password').required = mode !== 'edit';
  }

  function closeModal() { modal.classList.remove('is-open'); }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': urls.csrf
      },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
  }

  document.getElementById('sa-open-create').addEventListener('click', function () { openModal('create'); });
  document.getElementById('sa-modal-cancel').addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var id = idEl.value;
    var payload = {
      username: document.getElementById('sa-username').value,
      email: document.getElementById('sa-email').value,
      role: document.getElementById('sa-role').value,
      department: document.getElementById('sa-department').value,
      password: document.getElementById('sa-password').value,
      is_active: document.getElementById('sa-active').checked
    };
    var url = id ? urls.update.replace('__ID__', id) : urls.create;
    post(url, payload).then(function (res) {
      if (!res.ok || !res.j.success) {
        alert((res.j && res.j.message) || 'Request failed');
        return;
      }
      location.reload();
    });
  });

  document.querySelectorAll('.sa-edit').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openModal('edit', btn.dataset);
    });
  });

  document.querySelectorAll('.sa-view').forEach(function (btn) {
    btn.addEventListener('click', function () {
      fetch(urls.detail.replace('__ID__', btn.dataset.id))
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.success) { alert(j.message || 'Not found'); return; }
          var u = j.user;
          alert([u.name, u.username, u.email || '—', u.role, u.department, u.status, 'Last login: ' + u.last_login].join('\n'));
        });
    });
  });

  document.querySelectorAll('.sa-reset').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!confirm('Reset password for this user?')) return;
      post(urls.reset.replace('__ID__', btn.dataset.id), {}).then(function (res) {
        if (!res.ok || !res.j.success) { alert((res.j && res.j.message) || 'Failed'); return; }
        alert('Temporary password: ' + res.j.temporary_password);
      });
    });
  });

  document.querySelectorAll('.sa-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      post(urls.setActive.replace('__ID__', btn.dataset.id), { active: btn.dataset.active === '1' }).then(function (res) {
        if (!res.ok || !res.j.success) { alert((res.j && res.j.message) || 'Failed'); return; }
        location.reload();
      });
    });
  });

  document.querySelectorAll('.sa-delete').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!confirm('Delete this user permanently?')) return;
      post(urls.delete.replace('__ID__', btn.dataset.id), {}).then(function (res) {
        if (!res.ok || !res.j.success) { alert((res.j && res.j.message) || 'Failed'); return; }
        location.reload();
      });
    });
  });
})();
