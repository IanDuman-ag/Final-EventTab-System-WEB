document.addEventListener('DOMContentLoaded', function () {
  var openBtn = document.getElementById('open-team-modal');
  var formModal = document.getElementById('team-form-modal');
  var membersModal = document.getElementById('team-members-modal');
  var deleteModal = document.getElementById('team-delete-modal');
  var deleteTeamNameEl = document.getElementById('delete-team-name');
  var confirmDeleteBtn = document.getElementById('confirm-delete-team-btn');
  var form = document.getElementById('team-form');
  var teamId = document.getElementById('team-id');
  var teamName = document.getElementById('team-name');
  var teamCode = document.getElementById('team-code');
  var teamDepartment = document.getElementById('team-department');
  var teamImage = document.getElementById('team-image');
  var teamImagePreview = document.getElementById('team-image-preview');
  var teamImagePreviewWrap = document.getElementById('team-image-preview-wrap');
  var teamCoach = document.getElementById('team-coach');
  var teamStatus = document.getElementById('team-status');
  var submitBtn = document.getElementById('team-submit-btn');
  var modalTitle = document.getElementById('team-modal-title');

  if (!openBtn || !formModal || !form) return;

  var createUrl = openBtn.dataset.createUrl || '/admin/teams/create/';
  var pendingDelete = { url: '', triggerBtn: null };

  function getCsrfToken() {
    var el = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (el && el.value) return el.value;
    var name = 'csrftoken=';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.startsWith(name)) return decodeURIComponent(c.slice(name.length));
    }
    return '';
  }

  function showToast(message, type) {
    var toast = document.createElement('div');
    toast.className = 'toast-message ' + (type || 'success');
    toast.textContent = message;
    document.body.appendChild(toast);
    toast.offsetHeight;
    toast.classList.add('visible');
    setTimeout(function () {
      toast.classList.remove('visible');
      setTimeout(function () { toast.remove(); }, 350);
    }, 3500);
  }

  async function postJson(url, payload) {
    try {
      var response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
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

  async function postForm(url, formData) {
    try {
      var response = await fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() },
        body: formData,
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

  function setImagePreview(url) {
    if (!teamImagePreview || !teamImagePreviewWrap) return;
    if (url) {
      teamImagePreview.src = url;
      teamImagePreviewWrap.classList.remove('hidden');
    } else {
      teamImagePreview.removeAttribute('src');
      teamImagePreviewWrap.classList.add('hidden');
    }
  }

  function openModal(el) {
    el.classList.remove('hidden');
    el.setAttribute('aria-hidden', 'false');
  }

  function closeModal(el) {
    el.classList.add('hidden');
    el.setAttribute('aria-hidden', 'true');
  }

  function openCreateModal() {
    formModal.dataset.endpoint = createUrl;
    teamId.value = '';
    teamName.value = '';
    teamCode.value = '';
    teamDepartment.value = '';
    if (teamImage) teamImage.value = '';
    setImagePreview('');
    teamCoach.value = '';
    teamStatus.value = 'active';
    modalTitle.textContent = 'Add Team';
    submitBtn.textContent = 'Add Team';
    openModal(formModal);
    teamName.focus();
  }

  function openEditModal(row, endpoint) {
    formModal.dataset.endpoint = endpoint;
    teamId.value = row.dataset.teamId || '';
    teamName.value = row.dataset.name || '';
    teamCode.value = row.dataset.code || '';
    teamDepartment.value = row.dataset.departmentId || '';
    if (teamImage) teamImage.value = '';
    setImagePreview(row.dataset.imageUrl || '');
    teamCoach.value = row.dataset.coach === '—' ? '' : (row.dataset.coach || '');
    teamStatus.value = row.dataset.status || 'active';
    modalTitle.textContent = 'Edit Team';
    submitBtn.textContent = 'Save Changes';
    openModal(formModal);
    teamName.focus();
  }

  async function openViewModal(url, fallbackName) {
    var response = await fetch(url, { headers: { 'Accept': 'application/json' } });
    var data;
    try { data = await response.json(); } catch (_) {
      showToast('Unable to load team details.', 'error');
      return;
    }
    document.getElementById('team-members-title').textContent = data.name || fallbackName || 'Team';
    document.getElementById('team-members-subtitle').textContent = data.code ? ('Code: ' + data.code) : '';
    document.getElementById('view-team-department').textContent = data.department_name || '—';
    document.getElementById('view-team-coach').textContent = data.coach || '—';
    document.getElementById('view-team-status').textContent = data.status_label || '—';

    var viewImage = document.getElementById('view-team-image');
    var viewImageWrap = document.getElementById('view-team-image-wrap');
    if (viewImage && viewImageWrap) {
      if (data.image_url) {
        viewImage.src = data.image_url;
        viewImageWrap.classList.remove('hidden');
      } else {
        viewImage.removeAttribute('src');
        viewImageWrap.classList.add('hidden');
      }
    }
    openModal(membersModal);
  }

  function openDeleteModal(name, deleteUrl, triggerBtn) {
    if (!deleteModal) return;
    pendingDelete.url = deleteUrl || '';
    pendingDelete.triggerBtn = triggerBtn || null;
    if (deleteTeamNameEl) deleteTeamNameEl.textContent = name || 'this team';
    if (confirmDeleteBtn) {
      confirmDeleteBtn.disabled = false;
      confirmDeleteBtn.textContent = 'Delete Team';
    }
    openModal(deleteModal);
  }

  function closeDeleteModal() {
    if (!deleteModal) return;
    closeModal(deleteModal);
    pendingDelete.url = '';
    pendingDelete.triggerBtn = null;
  }

  async function confirmDeleteTeam() {
    if (!pendingDelete.url || !confirmDeleteBtn) return;
    confirmDeleteBtn.disabled = true;
    confirmDeleteBtn.textContent = 'Deleting…';
    if (pendingDelete.triggerBtn) pendingDelete.triggerBtn.disabled = true;
    var result = await postJson(pendingDelete.url, {});
    if (!result.success) {
      confirmDeleteBtn.disabled = false;
      confirmDeleteBtn.textContent = 'Delete Team';
      if (pendingDelete.triggerBtn) pendingDelete.triggerBtn.disabled = false;
      showToast(result.message || 'Unable to delete team.', 'error');
      return;
    }
    closeDeleteModal();
    showToast(result.message || 'Team deleted.', 'success');
    setTimeout(function () { window.location.reload(); }, 500);
  }

  openBtn.addEventListener('click', openCreateModal);

  function getTeamRowEl(btn) {
    return btn.closest('tr') || btn.closest('.teams-grid-card');
  }

  function bindTeamActions() {
    document.querySelectorAll('.edit-team').forEach(function (btn) {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function () {
        var row = getTeamRowEl(btn);
        if (!row) return;
        openEditModal(row, btn.dataset.updateUrl);
      });
    });

    document.querySelectorAll('.view-team').forEach(function (btn) {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function () {
        var row = getTeamRowEl(btn);
        openViewModal(btn.dataset.viewUrl, row ? row.dataset.name : '');
      });
    });

    document.querySelectorAll('.delete-team').forEach(function (btn) {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function () {
        var row = getTeamRowEl(btn);
        var name = row ? row.dataset.name : 'this team';
        openDeleteModal(name, btn.dataset.deleteUrl, btn);
      });
    });
  }

  var listView = document.getElementById('teams-list-view');
  var gridView = document.getElementById('teams-grid-view');
  document.querySelectorAll('.teams-view-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var mode = btn.dataset.view || 'list';
      document.querySelectorAll('.teams-view-btn').forEach(function (b) {
        var active = b === btn;
        b.classList.toggle('is-active', active);
        b.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (listView) listView.classList.toggle('is-hidden', mode !== 'list');
      if (gridView) {
        gridView.classList.toggle('hidden', mode !== 'grid');
        gridView.setAttribute('aria-hidden', mode === 'grid' ? 'false' : 'true');
      }
    });
  });

  bindTeamActions();

  formModal.addEventListener('click', function (e) {
    if (e.target.dataset.closeTeamModal === 'true') closeModal(formModal);
  });
  if (membersModal) {
    membersModal.addEventListener('click', function (e) {
      if (e.target.dataset.closeMembersModal === 'true') closeModal(membersModal);
    });
  }
  if (deleteModal) {
    deleteModal.addEventListener('click', function (e) {
      if (e.target.dataset.closeDeleteModal === 'true') closeDeleteModal();
    });
  }
  if (confirmDeleteBtn) {
    confirmDeleteBtn.addEventListener('click', confirmDeleteTeam);
  }

  if (teamImage && teamImagePreview && teamImagePreviewWrap) {
    teamImage.addEventListener('change', function () {
      var file = teamImage.files && teamImage.files[0];
      if (!file) {
        setImagePreview('');
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        setImagePreview(String(reader.result || ''));
      };
      reader.readAsDataURL(file);
    });
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var name = teamName.value.trim();
    var code = teamCode.value.trim();
    var departmentId = teamDepartment.value || '';
    if (!name || !code || !departmentId) {
      showToast('Team name, team code, and department are required.', 'warning');
      return;
    }

    var formData = new FormData();
    formData.append('name', name);
    formData.append('code', code);
    formData.append('department_id', departmentId);
    formData.append('members', '');
    formData.append('coach', teamCoach.value.trim());
    formData.append('status', teamStatus.value);
    if (teamImage && teamImage.files && teamImage.files[0]) {
      formData.append('image', teamImage.files[0]);
    }

    submitBtn.disabled = true;
    submitBtn.textContent = teamId.value ? 'Saving…' : 'Adding…';
    var result = await postForm(formModal.dataset.endpoint, formData);
    if (!result.success) {
      submitBtn.disabled = false;
      submitBtn.textContent = teamId.value ? 'Save Changes' : 'Add Team';
      showToast(result.message || 'Unable to save team.', 'error');
      return;
    }
    showToast(result.message || 'Team saved.', 'success');
    closeModal(formModal);
    setTimeout(function () { window.location.reload(); }, 500);
  });
});
