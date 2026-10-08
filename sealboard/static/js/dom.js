// Tiny DOM helpers shared by the page modules.
export const $ = id => document.getElementById(id);

// <button type="button"> with a visible label, a tooltip and an accessible name.
export function button(label, title, props = {}) {
  const b = document.createElement('button');
  Object.assign(b, { type: 'button', textContent: label, title }, props);
  b.setAttribute('aria-label', title);
  return b;
}
