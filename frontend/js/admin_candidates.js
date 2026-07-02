document.addEventListener('DOMContentLoaded', function () {
  var openBtn = document.getElementById('open-candidate-modal');
  var formModal = document.getElementById('candidate-form-modal');
  var viewModal = document.getElementById('candidate-view-modal');
  var deleteModal = document.getElementById('candidate-delete-modal');
  var deleteNameEl = document.getElementById('delete-candidate-name');
  var confirmDeleteBtn = document.getElementById('confirm-delete-candidate-btn');
  var form = document.getElementById('candidate-form');
  var candidateId = document.getElementById('candidate-id');
  var candidateNumber = document.getElementById('candidate-number');
  var candidateName = document.getElementById('candidate-name');
  var candidateDepartment = document.getElementById('candidate-department');
  var candidateImage = document.getElementById('candidate-image');
  var candidateImagePreview = document.getElementById('candidate-image-preview');
  var candidateImagePreviewWrap = document.getElementById('candidate-image-preview-wrap');
  var candidateStatus = document.getElementById('candidate-status');
  var submitBtn = document.getElementById('candidate-submit-btn');
  var modalTitle = document.getElementById('candidate-modal-title');
  var modalSubtitle = document.getElementById('candidate-modal-subtitle');

  if (!openBtn || !formModal || !form) return;

  var createUrl = openBtn.dataset.createUrl || '/admin/candidates/create/';
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

  async function postForm(url, formData) {
    try {
      var response = await fetch(url, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCsrfToken(),
        },
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

  function openModal(el) {
    el.classList.remove('hidden');
    el.setAttribute('aria-hidden', 'false');
  }

  function closeModal(el) {
    el.classList.add('hidden');
    el.setAttribute('aria-hidden', 'true');
  }

  function resetForm() {
    candidateId.value = '';
    candidateNumber.value = '';
    candidateName.value = '';
    candidateDepartment.value = '';
    if (candidateImage) candidateImage.value = '';
    if (candidateImagePreviewWrap) candidateImagePreviewWrap.classList.add('hidden');
    candidateStatus.value = 'active';
    modalTitle.textContent = 'Add Candidate';
    modalSubtitle.textContent = 'Register a candidate with number, name, and department.';
    submitBtn.textContent = 'Add Candidate';
  }

  function openCreateModal() {
    resetForm();
    openModal(formModal);
    candidateNumber.focus();
  }

  function openEditModal(row) {
    candidateId.value = row.dataset.candidateId || '';
    candidateNumber.value = row.dataset.number || '';
    candidateName.value = row.dataset.name || '';
    candidateDepartment.value = row.dataset.departmentId || '';
    if (candidateImage) candidateImage.value = '';
    if (candidateImagePreview && candidateImagePreviewWrap) {
      if (row.dataset.imageUrl) {
        candidateImagePreview.src = row.dataset.imageUrl;
        candidateImagePreviewWrap.classList.remove('hidden');
      } else {
        candidateImagePreview.removeAttribute('src');
        candidateImagePreviewWrap.classList.add('hidden');
      }
    }
    candidateStatus.value = row.dataset.status || 'active';
    modalTitle.textContent = 'Edit Candidate';
    modalSubtitle.textContent = 'Update candidate details.';
    submitBtn.textContent = 'Save Changes';
    openModal(formModal);
    candidateName.focus();
  }

  openBtn.addEventListener('click', openCreateModal);

  document.querySelectorAll('[data-close-candidate-modal="true"]').forEach(function (el) {
    el.addEventListener('click', function () { closeModal(formModal); });
  });

  document.querySelectorAll('[data-close-candidate-view-modal="true"]').forEach(function (el) {
    el.addEventListener('click', function () { closeModal(viewModal); });
  });

  document.querySelectorAll('[data-close-candidate-delete-modal="true"]').forEach(function (el) {
    el.addEventListener('click', function () { closeModal(deleteModal); });
  });

  document.querySelectorAll('.edit-candidate').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('tr');
      if (row) openEditModal(row);
    });
  });

  document.querySelectorAll('.view-candidate').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      var url = btn.dataset.viewUrl;
      if (!url) return;
      try {
        var response = await fetch(url);
        var data = await response.json();
        document.getElementById('candidate-view-title').textContent = data.name || 'Candidate';
        document.getElementById('candidate-view-subtitle').textContent = 'No. ' + (data.number || '—');
        document.getElementById('view-candidate-name').textContent = data.name || '—';
        document.getElementById('view-candidate-department').textContent = data.department_name || '—';
        document.getElementById('view-candidate-status').textContent = data.status_label || '—';
        var viewImage = document.getElementById('view-candidate-image');
        var viewImageWrap = document.getElementById('view-candidate-image-wrap');
        if (viewImage && viewImageWrap) {
          if (data.image_url) {
            viewImage.src = data.image_url;
            viewImageWrap.classList.remove('hidden');
          } else {
            viewImage.removeAttribute('src');
            viewImageWrap.classList.add('hidden');
          }
        }
        openModal(viewModal);
      } catch (_) {
        showToast('Could not load candidate details.', 'error');
      }
    });
  });

  document.querySelectorAll('.delete-candidate').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('tr');
      pendingDelete.url = btn.dataset.deleteUrl || '';
      pendingDelete.triggerBtn = btn;
      if (deleteNameEl) {
        deleteNameEl.textContent = row ? (row.dataset.name || 'this candidate') : 'this candidate';
      }
      openModal(deleteModal);
    });
  });

  if (confirmDeleteBtn) {
    confirmDeleteBtn.addEventListener('click', async function () {
      if (!pendingDelete.url) return;
      confirmDeleteBtn.disabled = true;
      var result = await postJson(pendingDelete.url, {});
      confirmDeleteBtn.disabled = false;
      if (result.success) {
        closeModal(deleteModal);
        showToast(result.message || 'Candidate deleted.');
        var row = pendingDelete.triggerBtn && pendingDelete.triggerBtn.closest('tr');
        if (row) row.remove();
        pendingDelete = { url: '', triggerBtn: null };
      } else {
        showToast(result.message || 'Delete failed.', 'error');
      }
    });
  }

  if (candidateImage && candidateImagePreview && candidateImagePreviewWrap) {
    candidateImage.addEventListener('change', function () {
      var file = candidateImage.files && candidateImage.files[0];
      if (!file) {
        candidateImagePreview.removeAttribute('src');
        candidateImagePreviewWrap.classList.add('hidden');
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        candidateImagePreview.src = String(reader.result || '');
        candidateImagePreviewWrap.classList.remove('hidden');
      };
      reader.readAsDataURL(file);
    });
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var number = candidateNumber.value.trim();
    var name = candidateName.value.trim();
    var departmentId = candidateDepartment.value || null;
    if (!number || !name || !departmentId) {
      showToast('Candidate number, name, and department are required.', 'error');
      return;
    }
    var formData = new FormData();
    formData.append('number', number);
    formData.append('name', name);
    formData.append('department_id', departmentId);
    formData.append('status', candidateStatus.value);
    if (candidateImage && candidateImage.files && candidateImage.files[0]) {
      formData.append('image', candidateImage.files[0]);
    }
    submitBtn.disabled = true;
    var isEdit = !!candidateId.value;
    var url = isEdit
      ? '/admin/candidates/' + candidateId.value + '/edit/'
      : createUrl;
    var result = await postForm(url, formData);
    submitBtn.disabled = false;

    if (result.success) {
      closeModal(formModal);
      showToast(result.message || (isEdit ? 'Candidate updated.' : 'Candidate created.'));
      window.location.reload();
    } else {
      showToast(result.message || 'Save failed.', 'error');
    }
  });
});
