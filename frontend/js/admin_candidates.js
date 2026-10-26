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
  var candidateGender = document.getElementById('candidate-gender');
  var candidateDepartment = document.getElementById('candidate-department');
  var candidateImage = document.getElementById('candidate-image');
  var candidateImagePreview = document.getElementById('candidate-image-preview');
  var candidateImagePreviewWrap = document.getElementById('candidate-image-preview-wrap');
  var submitBtn = document.getElementById('candidate-submit-btn');
  var modalTitle = document.getElementById('candidate-modal-title');
  var modalSubtitle = document.getElementById('candidate-modal-subtitle');
  var preservedStatus = 'active';

  if (!openBtn || !formModal || !form) return;

  var createUrl = openBtn.dataset.createUrl || '/admin/candidates/create/';
  var pendingDelete = [];
  var selectAll = document.getElementById('select-all-candidates');
  var bulkDelete = document.getElementById('delete-selected-candidates');
  function updateSelection() {
    var boxes = Array.from(document.querySelectorAll('.candidate-select'));
    var count = boxes.filter(function (box) { return box.checked; }).length;
    selectAll.checked = boxes.length > 0 && count === boxes.length;
    selectAll.indeterminate = count > 0 && count < boxes.length;
    selectAll.disabled = !boxes.length;
    bulkDelete.disabled = !count;
    document.getElementById('candidate-selection-count').textContent = count + ' selected';
    var bulkActions = document.getElementById('candidate-bulk-actions');
    if (bulkActions) bulkActions.hidden = !count;
  }
  selectAll.addEventListener('change', function () {
    document.querySelectorAll('.candidate-select').forEach(function (box) { box.checked = selectAll.checked; });
    updateSelection();
  });
  document.querySelectorAll('.candidate-select').forEach(function (box) { box.addEventListener('change', updateSelection); });
  bulkDelete.addEventListener('click', function () {
    pendingDelete = Array.from(document.querySelectorAll('.candidate-select:checked')).map(function (box) { return box.closest('tr').querySelector('.delete-candidate'); });
    deleteNameEl.textContent = pendingDelete.length + ' selected individuals';
    openModal(deleteModal);
  });
  updateSelection();
  var eventPicker = document.getElementById('participant-event');
  if (eventPicker) eventPicker.addEventListener('change', function () {
    document.getElementById('individual-filters').submit();
  });

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
    candidateGender.value = '';
    if (candidateImage) candidateImage.value = '';
    if (candidateImagePreviewWrap) candidateImagePreviewWrap.classList.add('hidden');
    preservedStatus = 'active';
    modalTitle.textContent = 'Add Individual';
    modalSubtitle.textContent = 'Register an individual with number, name, and department.';
    submitBtn.textContent = 'Add Individual';
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
    candidateGender.value = row.dataset.gender || '';
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
    preservedStatus = row.dataset.status || 'active';
    modalTitle.textContent = 'Edit Individual';
    modalSubtitle.textContent = 'Update individual details.';
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
        document.getElementById('candidate-view-title').textContent = data.name || 'Individual';
        document.getElementById('candidate-view-subtitle').textContent = 'No. ' + (data.number || '—');
        document.getElementById('view-candidate-gender').textContent = data.gender_label || 'Not specified';
        document.getElementById('view-candidate-name').textContent = data.name || '—';
        document.getElementById('view-candidate-department').textContent = data.department_name || '—';
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
        showToast('Could not load individual details.', 'error');
      }
    });
  });

  document.querySelectorAll('.delete-candidate').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('tr');
      pendingDelete = [btn];
      if (deleteNameEl) {
        deleteNameEl.textContent = row ? (row.dataset.name || 'this individual') : 'this individual';
      }
      openModal(deleteModal);
    });
  });

  if (confirmDeleteBtn) {
    confirmDeleteBtn.addEventListener('click', async function () {
      if (!pendingDelete.length || confirmDeleteBtn.disabled) return;
      confirmDeleteBtn.disabled = true;
      bulkDelete.disabled = true;
      var targets = pendingDelete.slice();
      var deleted = 0;
      var errors = [];
      try {
        for (var i = 0; i < targets.length; i++) {
          var result = await postForm(targets[i].dataset.deleteUrl, new FormData());
          if (result.success) {
            targets[i].closest('tr').remove();
            deleted++;
          } else {
            errors.push(result.message || 'Delete failed.');
          }
        }
        pendingDelete = [];
        closeModal(deleteModal);
        if (errors.length) {
          showToast(deleted + ' deleted. ' + errors.length + ' could not be deleted. ' + errors[0], 'error');
        } else {
          window.location.reload();
        }
      } finally {
        confirmDeleteBtn.disabled = false;
        updateSelection();
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
      showToast('Individual number, name, and department are required.', 'error');
      return;
    }
    var formData = new FormData();
    formData.append('number', number);
    formData.append('name', name);
    formData.append('department_id', departmentId);
    formData.append('status', preservedStatus);
    formData.append('gender', candidateGender.value);
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
      showToast(result.message || (isEdit ? 'Individual updated.' : 'Individual created.'));
      window.location.reload();
    } else {
      showToast(result.message || 'Save failed.', 'error');
    }
  });
});
