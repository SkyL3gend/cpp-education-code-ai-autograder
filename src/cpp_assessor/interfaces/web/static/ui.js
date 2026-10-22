// Presentation only: preserve form fields and server-owned assessment semantics.
document.querySelectorAll('[data-submit-form]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
    form.dataset.submitting = 'true';
    const button = event.submitter || form.querySelector('button[type="submit"]');
    if (button) { button.dataset.originalText = button.textContent; button.disabled = true; button.textContent = form.dataset.busyLabel; }
    form.setAttribute('aria-busy', 'true');
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('[data-submit-form]').forEach(form => {
    delete form.dataset.submitting;
    form.removeAttribute('aria-busy');
    form.querySelectorAll('[data-original-text]').forEach(button => { button.disabled = false; button.textContent = button.dataset.originalText; delete button.dataset.originalText; });
  });
});
// Tables remain readable on narrow screens without making the whole page scroll.
document.querySelectorAll('table.table').forEach(table => {
  if (table.parentElement.classList.contains('table-scroll')) return;
  const wrapper = document.createElement('div'); wrapper.className = 'table-scroll';
  wrapper.tabIndex = 0; table.before(wrapper); wrapper.appendChild(table);
});
