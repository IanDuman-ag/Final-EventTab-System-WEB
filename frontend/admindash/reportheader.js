(() => {
  'use strict';
  const form = document.getElementById('branding-form');
  if (!form) return;
  const saved = JSON.parse(document.getElementById('saved-report-branding').textContent);
  const preview = document.getElementById('header-preview');
  const objectUrls = {left: null, right: null};
  const fields = {school_name: 'school', campus_name: 'campus', report_subtitle: 'subtitle'};
  const totalForms = form.elements['extra_logos-TOTAL_FORMS'];
  const initialTotal = Number(totalForms.value);
  const extraUrls = new Map();
  const extraEditors = () => Array.from(form.querySelectorAll('[data-extra-editor]'));
  const extraField = (editor, name) => editor.querySelector(`[name$="-${name}"]`);
  const updateExtras = () => {
    ['left', 'right'].forEach(side => {
      const container = preview.querySelector(`[data-branding-extra-${side}]`);
      if (container) container.replaceChildren();
      let count = 0;
      extraEditors().filter(editor => editor.dataset.side === side).forEach(editor => {
        const removed = extraField(editor, 'DELETE').checked;
        editor.hidden = removed;
        extraField(editor, 'image').required = !removed && !editor.dataset.savedUrl;
        if (!removed) count += 1;
        const source = removed ? '' : (extraUrls.get(editor) || editor.dataset.savedUrl || '');
        const image = editor.querySelector('[data-extra-image]');
        const placeholder = editor.querySelector('[data-extra-placeholder]');
        image.hidden = !source;
        if (source) image.src = source;
        else image.removeAttribute('src');
        placeholder.hidden = !!source;
        if (container && source) {
          const logo = document.createElement('img');
          logo.src = source;
          logo.alt = `Additional ${side} header logo`;
          container.appendChild(logo);
        }
      });
      const button = form.querySelector(`[data-add-logo="${side}"]`);
      button.disabled = count >= 5;
    });
  };
  const bindExtra = editor => {
    const input = extraField(editor, 'image');
    input.classList.add('form-control');
    input.accept = 'image/png,image/jpeg,image/webp';
    input.addEventListener('change', () => {
      if (extraUrls.has(editor)) URL.revokeObjectURL(extraUrls.get(editor));
      extraUrls.delete(editor);
      if (input.files[0]) extraUrls.set(editor, URL.createObjectURL(input.files[0]));
      updateExtras();
    });
    editor.querySelector('[data-remove-extra]').addEventListener('click', () => {
      extraField(editor, 'DELETE').checked = true;
      input.value = '';
      if (extraUrls.has(editor)) URL.revokeObjectURL(extraUrls.get(editor));
      extraUrls.delete(editor);
      updateExtras();
      form.querySelector(`[data-add-logo="${editor.dataset.side}"]`).focus();
    });
  };
  extraEditors().forEach(bindExtra);
  form.querySelectorAll('[data-add-logo]').forEach(button => {
    button.addEventListener('click', () => {
      const side = button.dataset.addLogo;
      if (extraEditors().filter(editor => editor.dataset.side === side && !extraField(editor, 'DELETE').checked).length >= 5) return;
      // Reuse an unsaved removed row so repeated add/remove never inflates the formset.
      let editor = extraEditors().find(item => item.dataset.side === side && !extraField(item, 'id').value && extraField(item, 'DELETE').checked);
      if (editor) {
        extraField(editor, 'DELETE').checked = false;
      } else {
        const fragment = document.getElementById('extra-logo-template').content.cloneNode(true);
        const index = Number(totalForms.value);
        fragment.querySelectorAll('*').forEach(element => {
          ['id', 'name', 'for'].forEach(attribute => {
            if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).replace(/__prefix__/g, String(index)));
          });
        });
        editor = fragment.querySelector('[data-extra-editor]');
        editor.dataset.side = side;
        extraField(editor, 'side').value = side;
        editor.querySelector('label').textContent = `Additional ${side} logo`;
        form.querySelector(`[data-extra-list="${side}"]`).appendChild(fragment);
        totalForms.value = index + 1;
        bindExtra(editor);
      }
      updateExtras();
      extraField(editor, 'image').focus();
    });
  });

  const updateText = () => {
    Object.entries(fields).forEach(([name, hook]) => {
      const element = preview.querySelector(`[data-branding-${hook}]`);
      if (element) {
        const value = form.elements[name].value.trim();
        element.textContent = value || (name === 'school_name' ? 'EventTab' : '');
        element.hidden = !value && name !== 'school_name';
      }
    });
  };
  const updateLogo = side => {
    const input = form.elements[side === 'left' ? 'school_logo' : 'report_right_logo'];
    const remove = form.elements[`remove_${side}_logo`].checked;
    const source = remove ? '' : (objectUrls[side] || saved[`${side}_logo`] || '');
    const images = [preview.querySelector(`[data-branding-${side}]`), document.querySelector(`[data-logo-editor-image="${side}"]`)];
    images.forEach(image => {
      if (!image) return;
      if (source) image.src = source;
      else image.removeAttribute('src');
      image.hidden = !source;
    });
    document.querySelector(`[data-logo-placeholder="${side}"]`).hidden = !!source;
    if (input.files.length && remove) input.value = '';
  };
  Object.keys(fields).forEach(name => form.elements[name].addEventListener('input', updateText));
  ['left', 'right'].forEach(side => {
    const input = form.elements[side === 'left' ? 'school_logo' : 'report_right_logo'];
    const remove = form.elements[`remove_${side}_logo`];
    input.addEventListener('change', () => {
      if (objectUrls[side]) URL.revokeObjectURL(objectUrls[side]);
      objectUrls[side] = input.files[0] ? URL.createObjectURL(input.files[0]) : null;
      if (input.files.length) remove.checked = false;
      updateLogo(side);
    });
    remove.addEventListener('change', () => {
      if (remove.checked && objectUrls[side]) {
        URL.revokeObjectURL(objectUrls[side]);
        objectUrls[side] = null;
      }
      updateLogo(side);
    });
  });
  form.addEventListener('reset', () => {
    requestAnimationFrame(() => {
      Object.keys(fields).forEach(name => {form.elements[name].value = saved[name] || '';});
      ['left', 'right'].forEach(side => {
        if (objectUrls[side]) URL.revokeObjectURL(objectUrls[side]);
        objectUrls[side] = null;
        form.elements[`remove_${side}_logo`].checked = false;
        updateLogo(side);
      });
      extraUrls.forEach(url => URL.revokeObjectURL(url));
      extraUrls.clear();
      extraEditors().forEach(editor => {
        const index = Number(extraField(editor, 'image').name.match(/-(\d+)-image$/)[1]);
        if (index >= initialTotal) editor.remove();
        else {
          extraField(editor, 'image').value = '';
          extraField(editor, 'DELETE').checked = !extraField(editor, 'id').value;
        }
      });
      totalForms.value = initialTotal;
      updateExtras();
      updateText();
    });
  });
  document.getElementById('preview-header-button').addEventListener('click', () => {
    preview.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center'});
    preview.focus({preventScroll: true});
  });
  form.addEventListener('submit', () => {
    const button = document.getElementById('save-branding');
    button.disabled = true;
    button.textContent = 'Saving…';
  });
  window.addEventListener('pageshow', () => {
    const button = document.getElementById('save-branding');
    button.disabled = false;
    button.textContent = 'Save Changes';
  });
  window.addEventListener('beforeunload', () => {
    Object.values(objectUrls).forEach(url => {if (url) URL.revokeObjectURL(url);});
    extraUrls.forEach(url => URL.revokeObjectURL(url));
  });
  updateText();
  ['left', 'right'].forEach(updateLogo);
  updateExtras();
})();
