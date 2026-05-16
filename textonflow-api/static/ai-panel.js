/* ══ IA CORE — JavaScript ══ */

let _iacLastDesignLayout = null;
let _iacLastVariants = null;
let _iacLastBrandKit = null;

function iacSetTab(tab) {
  document.getElementById('iac-tab-design').style.display = tab === 'design' ? '' : 'none';
  document.getElementById('iac-tab-copy').style.display   = tab === 'copy'   ? '' : 'none';
  const btnD = document.getElementById('iac-tab-btn-design');
  const btnC = document.getElementById('iac-tab-btn-copy');
  btnD.style.background = tab === 'design' ? '#7c6eff' : '#1a1a30';
  btnD.style.color      = tab === 'design' ? '#fff'    : '#888';
  btnC.style.background = tab === 'copy'   ? '#7c6eff' : '#1a1a30';
  btnC.style.color      = tab === 'copy'   ? '#fff'    : '#888';
  if (tab === 'copy') _iacRefreshCopyText();
}

function _iacStatus(elId, msg, type) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.style.display = msg ? '' : 'none';
  el.textContent = msg || '';
  const colors = {info:'rgba(124,110,255,.15)',error:'rgba(239,68,68,.15)',success:'rgba(34,197,94,.15)'};
  const texts  = {info:'#a5b4fc',error:'#fca5a5',success:'#86efac'};
  el.style.background = colors[type] || colors.info;
  el.style.color = texts[type] || texts.info;
}

/* ─── FEATURE 1: Diseñar desde descripción ─── */
async function iacGenerateDesign() {
  const desc = (document.getElementById('iac-design-input').value || '').trim();
  if (!desc) {
    _iacStatus('iac-design-status','✏️ Escribe una descripción primero','error');
    showNotif('Describe el diseño antes de generar el layout', 'warn');
    return;
  }
  const btn = document.getElementById('iac-design-btn');
  const btnOrigHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span style="display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;"></span> Generando…';
  document.getElementById('iac-design-result').style.display = 'none';
  _iacStatus('iac-design-status','⏳ Consultando IA, espera unos segundos…','info');
  try {
    const r = await fetch('/api/ai/design-layout', {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'Authorization':'Bearer '+_tofToken()},
      body: JSON.stringify({description: desc, canvas_width: (typeof imageData!=='undefined'&&imageData.width)||1080, canvas_height: (typeof imageData!=='undefined'&&imageData.height)||1080})
    });
    const d = await r.json();
    if (!r.ok) {
      const msg = d.detail || 'Error IA';
      _iacStatus('iac-design-status', '❌ ' + msg, 'error');
      showNotif('IA Core: ' + msg, 'error');
      return;
    }
    _iacLastDesignLayout = d;
    document.getElementById('iac-design-result').style.display = '';
    const bgEl = document.getElementById('iac-bg-suggestion');
    if (d.background_suggestion) bgEl.textContent = '💡 Fondo sugerido: ' + d.background_suggestion;
    else bgEl.textContent = '';
    const n = (d.texts||[]).length;
    _iacStatus('iac-design-status', `✓ ${n} capa${n!==1?'s':''} generada${n!==1?'s':''}. Elige acción abajo.`, 'success');
    showNotif('✓ Layout IA listo — ' + n + ' capas generadas', 'success');
  } catch(e) {
    _iacStatus('iac-design-status','❌ Error de red: '+e.message,'error');
    showNotif('IA Core: error de conexión — ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = btnOrigHTML;
  }
}

function iacApplyDesign(mode) {
  if (!_iacLastDesignLayout) return;
  const newTexts = _iacLastDesignLayout.texts || [];
  if (!newTexts.length) return;
  try {
    if (mode === 'replace') {
      texts = JSON.parse(JSON.stringify(newTexts));
    } else {
      texts = [...texts, ...JSON.parse(JSON.stringify(newTexts))];
    }
    activeTextIndex = 0; selectedTexts = [];
    saveHistory(); updateControls(); updatePreview(); updateJSON();
    if (typeof renderFormasPanel === 'function') renderFormasPanel();
    showToast && showToast('✓ Layout IA aplicado — ' + newTexts.length + ' capas', 1800);
    document.getElementById('iac-design-result').style.display = 'none';
    _iacStatus('iac-design-status','','info');
    if (typeof _openAccordion === 'function') _openAccordion('textos');
  } catch(e) { _iacStatus('iac-design-status','Error al aplicar: '+e.message,'error'); }
}

/* ─── FEATURE 2: Copy Suggestions ─── */
function _iacRefreshCopyText() {
  const el = document.getElementById('iac-copy-current');
  if (!el) return;
  if (typeof texts !== 'undefined' && texts.length > 0 && activeTextIndex >= 0) {
    el.textContent = texts[activeTextIndex]?.text || '(capa vacía)';
  } else {
    el.textContent = '(selecciona una capa de texto primero)';
  }
}

async function iacGetCopySuggestions() {
  _iacRefreshCopyText();
  const currentText = (typeof texts !== 'undefined' && texts[activeTextIndex]?.text) || '';
  if (!currentText.trim()) {
    _iacStatus('iac-copy-status','Selecciona una capa de texto en el canvas primero','error');
    showNotif('Selecciona una capa de texto en el canvas antes de usar Copy', 'error');
    return;
  }
  const context = (document.getElementById('iac-copy-context').value || '').trim();
  const btn = document.getElementById('iac-copy-btn');
  const btnOrigHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span style="display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;"></span> Generando…';
  document.getElementById('iac-copy-suggestions').style.display = 'none';
  _iacStatus('iac-copy-status','⏳ Consultando IA, espera unos segundos…','info');
  try {
    const r = await fetch('/api/ai/copy-suggestions', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({current_text: currentText, context: context || null})
    });
    const d = await r.json();
    if (!r.ok) {
      const msg = d.detail || 'Error IA';
      _iacStatus('iac-copy-status', '❌ ' + msg, 'error');
      showNotif('IA Core: ' + msg, 'error');
      return;
    }
    const container = document.getElementById('iac-copy-suggestions');
    container.innerHTML = '';
    (d.suggestions || []).forEach((s, i) => {
      const pill = document.createElement('div');
      pill.style.cssText = 'background:#0d0d1f;border:1px solid #2a2a50;border-radius:7px;padding:9px 11px;cursor:pointer;font-size:12px;color:#d0d0e8;transition:border-color .2s;position:relative;';
      pill.innerHTML = `<div style="font-size:10px;font-weight:700;color:#7c6eff;margin-bottom:3px;">Variante ${String.fromCharCode(65+i)}</div>${s}<div style="font-size:10px;color:#555;margin-top:6px;">Clic para aplicar →</div>`;
      pill.onmouseover = () => pill.style.borderColor = '#7c6eff';
      pill.onmouseout  = () => pill.style.borderColor = '#2a2a50';
      pill.onclick = () => iacApplyCopy(s);
      container.appendChild(pill);
    });
    container.style.display = 'flex';
    _iacStatus('iac-copy-status','','info');
    showNotif('✓ 3 variaciones de copy generadas', 'success');
  } catch(e) {
    _iacStatus('iac-copy-status','❌ Error: '+e.message,'error');
    showNotif('IA Core: error de conexión — ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = btnOrigHTML;
  }
}

function iacApplyCopy(text) {
  if (typeof texts==='undefined' || activeTextIndex < 0 || activeTextIndex >= texts.length) return;
  texts[activeTextIndex].text = text;
  saveHistory(); updateControls(); updatePreview(); updateJSON();
  showToast && showToast('✓ Copy aplicado', 1200);
}

/* ─── FEATURE 3: Brand Kit ─── */
function openIacBrandKit() {
  const m = document.getElementById('iac-brand-modal');
  m.style.display = 'flex';
  document.getElementById('iac-brand-result').style.display = 'none';
  _iacStatus('iac-brand-status','','info');
}
function closeIacBrandKit() {
  document.getElementById('iac-brand-modal').style.display = 'none';
}

async function iacExtractBrandKit() {
  const url = (document.getElementById('iac-brand-url').value || '').trim();
  if (!url) { _iacStatus('iac-brand-status','Pega la URL del logo primero','error'); return; }
  const btn = document.getElementById('iac-brand-btn');
  btn.disabled = true;
  document.getElementById('iac-brand-result').style.display = 'none';
  _iacStatus('iac-brand-status','⏳ Analizando logo con IA…','info');
  try {
    const r = await fetch('/api/ai/brand-kit', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({image_url: url})
    });
    const d = await r.json();
    if (!r.ok) { _iacStatus('iac-brand-status', d.detail || 'Error IA','error'); return; }
    _iacLastBrandKit = d;
    // Render swatches
    const sw = document.getElementById('iac-brand-swatches');
    sw.innerHTML = '';
    (d.colors || []).forEach(hex => {
      const s = document.createElement('div');
      s.title = hex;
      s.style.cssText = `width:36px;height:36px;border-radius:8px;background:${hex};border:2px solid #2a2a50;cursor:pointer;flex-shrink:0;`;
      s.onclick = () => navigator.clipboard && navigator.clipboard.writeText(hex);
      sw.appendChild(s);
    });
    const info = document.getElementById('iac-brand-info');
    info.innerHTML = `<strong style="color:#d0d0e8">Estilo:</strong> ${d.style || '—'}<br><strong style="color:#d0d0e8">Fuente sugerida:</strong> ${d.font_suggestion || '—'}<br><em>${d.description || ''}</em>`;
    document.getElementById('iac-brand-result').style.display = '';
    _iacStatus('iac-brand-status','✓ Paleta extraída — Copia los colores o aplica al editor','success');
  } catch(e) { _iacStatus('iac-brand-status','Error: '+e.message,'error');
  } finally { btn.disabled = false; }
}

function iacApplyBrandKit() {
  if (!_iacLastBrandKit || !_iacLastBrandKit.colors) return;
  if (typeof brandColors !== 'undefined') {
    brandColors = [..._iacLastBrandKit.colors];
    try { localStorage.setItem('textonflow_brand_colors', JSON.stringify(brandColors)); } catch(_){}
  }
  closeIacBrandKit();
  showToast && showToast('🎨 Brand Kit aplicado — ' + (_iacLastBrandKit.colors.length) + ' colores cargados', 2200);
}

/* ─── FEATURE 4: A/B Variant Generator ─── */
function closeIacAB() {
  document.getElementById('iac-ab-modal').style.display = 'none';
}

async function iacGenerateVariants() {
  if (typeof texts === 'undefined' || !texts.length) {
    showToast && showToast('❌ Agrega al menos una capa de texto primero', 1800); return;
  }
  const modal = document.getElementById('iac-ab-modal');
  modal.style.display = 'flex';
  document.getElementById('iac-ab-loading').style.display = '';
  document.getElementById('iac-ab-variants').style.display = 'none';
  try {
    const r = await fetch('/api/ai/ab-variants', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({texts: JSON.parse(JSON.stringify(texts)), context: null})
    });
    const d = await r.json();
    if (!r.ok) { showToast && showToast('❌ '+(d.detail||'Error IA'),'error'); modal.style.display='none'; return; }
    _iacLastVariants = d.variants || [];
    const container = document.getElementById('iac-ab-variants');
    container.innerHTML = '';
    _iacLastVariants.forEach((v, i) => {
      const card = document.createElement('div');
      card.style.cssText = 'background:#0d0d1f;border:1px solid #2a2a50;border-radius:10px;padding:14px;';
      const swatchesHtml = (v.color_overrides ? Object.values(v.color_overrides) : []).filter(c=>c&&c.startsWith('#')).map(c=>`<div style="display:inline-block;width:18px;height:18px;border-radius:4px;background:${c};margin-right:4px;vertical-align:middle;"></div>`).join('');
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <span style="color:#c4b5fd;font-size:13px;font-weight:800;">${v.label||'Variante '+(i+1)}</span>
          <div>${swatchesHtml}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:10px;">
          ${(v.texts||[]).slice(0,3).map(t=>`<div style="font-size:11px;color:#888;background:#131325;border-radius:5px;padding:5px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t.text||''}</div>`).join('')}
        </div>
        <button onclick="iacApplyVariant(${i})" style="width:100%;background:linear-gradient(135deg,#7c6eff,#a78bfa);color:#fff;border:none;border-radius:7px;padding:9px;font-size:12px;font-weight:700;cursor:pointer;">✓ Usar esta variante</button>
      `;
      container.appendChild(card);
    });
    document.getElementById('iac-ab-loading').style.display = 'none';
    container.style.display = 'flex';
  } catch(e) {
    showToast && showToast('❌ Error: '+e.message, 1800);
    modal.style.display = 'none';
  }
}

function iacApplyVariant(idx) {
  if (!_iacLastVariants || !_iacLastVariants[idx]) return;
  const variant = _iacLastVariants[idx];
  if (variant.texts && variant.texts.length) {
    texts = JSON.parse(JSON.stringify(variant.texts));
    activeTextIndex = 0; selectedTexts = [];
    saveHistory(); updateControls(); updatePreview(); updateJSON();
    if (typeof renderFormasPanel === 'function') renderFormasPanel();
  }
  closeIacAB();
  showToast && showToast('✓ Variante "'+variant.label+'" aplicada', 2000);
}

// ── ONBOARDING GUIADO 3 PASOS ─────────────────────────────────────────────────
var _OB_KEY = 'tof_onboarding_done';
var _obStep = 0;

var _obIcons = {
  palette: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="13.5" cy="6.5" r="1"/><circle cx="17.5" cy="10.5" r="1"/><circle cx="8.5" cy="7.5" r="1"/><circle cx="6.5" cy="12.5" r="1"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/></svg>',
  smartphone: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/></svg>',
  zap: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  star: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
  layers: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
  image: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  type: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>',
  eye: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
  plug: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
  clipboard: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></svg>',
  link: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  target: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>'
};

var _obSteps = [
  {
    icon: 'palette',
    badge: 'Paso 1 de 3',
    title: 'Bienvenido a TextOnFlow',
    subtitle: 'Imágenes personalizadas para cada usuario de ManyChat — en segundos.',
    content: [
      { icon: 'smartphone', label: 'Para agencias ManyChat', text: 'Convierte variables como <code>{{nombre}}</code> o <code>{{ciudad}}</code> en imágenes únicas para cada contacto. Sin programar.' },
      { icon: 'zap', label: 'API + Editor visual', text: 'Diseña en el editor visual, genera el JSON, conéctalo a tu flujo de ManyChat. Tu imagen se crea automáticamente al activar el mensaje.' },
      { icon: 'star', label: '7 días de trial ilimitado', text: 'Renders ilimitados durante 7 días. Sin tarjeta de crédito. Puedes pasarte a Starter ($29/mes) o Agency ($79/mes) cuando estés listo.' }
    ],
    cta: 'Empezar el tour →'
  },
  {
    icon: 'layers',
    badge: 'Paso 2 de 3',
    title: 'Diseña tu primera imagen',
    subtitle: 'El editor tiene todo lo que necesitas en el panel derecho.',
    content: [
      { icon: 'image', label: 'Imagen de fondo', text: 'Pega la URL de una imagen o sube una. Puede ser el logo de tu cliente, un banner o una plantilla.' },
      { icon: 'type', label: 'Añade capas de texto', text: 'Haz clic en <strong>+ Añadir texto</strong>. Escribe el texto con variables: <code>Hola, {{nombre}}!</code> — cada usuario verá su nombre.' },
      { icon: 'eye', label: 'Visualizar', text: 'Haz clic en <strong>Visualizar</strong>. Verás la imagen generada y el JSON listo para ManyChat. Puedes probar con valores reales.' }
    ],
    cta: 'Siguiente →'
  },
  {
    icon: 'plug',
    badge: 'Paso 3 de 3',
    title: 'Conecta con ManyChat',
    subtitle: 'Copia el JSON y activa tu flujo. 5 minutos de integración.',
    content: [
      { icon: 'clipboard', label: 'Copia el JSON', text: 'Después de visualizar, haz clic en <strong>Copiar JSON</strong>. Ese JSON contiene toda la configuración de tu imagen.' },
      { icon: 'link', label: 'En ManyChat: HTTP Request', text: 'Crea un bloque <strong>HTTP Request → POST</strong> con URL: <code>https://www.textonflow.com/generate-multi</code>. Header: <code>Authorization: Bearer [tu token]</code>.' },
      { icon: 'target', label: 'Pega y activa variables', text: 'Pega el JSON en el body. Reemplaza los valores fijos por las variables de ManyChat de tu flujo. ¡Listo! Cada usuario recibirá su imagen personalizada.' }
    ],
    cta: '¡Empezar a diseñar!'
  }
];

function showOnboarding() {
  if (localStorage.getItem('tof_ob_hidden')) return;
  _obStep = 0;
  _renderObStep();
  document.getElementById('ob-modal').style.display = 'flex';
}

function openOnboarding() {
  _obStep = 0;
  _renderObStep();
  var el = document.getElementById('ob-modal');
  if (el) { el.style.opacity = '1'; el.style.display = 'flex'; }
}

function _renderObStep() {
  var s = _obSteps[_obStep];
  var el = document.getElementById('ob-modal');
  if (!el) return;

  // Dots
  var dots = '';
  for (var i = 0; i < _obSteps.length; i++) {
    dots += '<div style="width:' + (i===_obStep?24:8) + 'px;height:8px;border-radius:4px;background:' + (i===_obStep?'#7c6eff':'#2a2a3e') + ';transition:all .3s;"></div>';
  }

  // Content items
  var items = s.content.map(function(c) {
    var itemSvg = (_obIcons[c.icon] || '').replace('stroke="currentColor"', 'stroke="#a5b4fc"');
    return '<div style="display:flex;gap:10px;align-items:flex-start;padding:10px 12px;background:#0d0d1e;border:1px solid #1e1e38;border-radius:10px;">' +
      '<div style="flex-shrink:0;width:30px;height:30px;border-radius:8px;background:rgba(124,110,255,.12);border:1px solid rgba(124,110,255,.2);display:flex;align-items:center;justify-content:center;margin-top:1px;">' + itemSvg + '</div>' +
      '<div style="min-width:0;overflow-wrap:break-word;word-break:break-word;flex:1;">' +
        '<div style="font-size:.82rem;font-weight:700;color:#a5b4fc;margin-bottom:3px;">' + c.label + '</div>' +
        '<div style="font-size:.86rem;color:#9090b8;line-height:1.55;">' + c.text + '</div>' +
      '</div></div>';
  }).join('');

  var mainSvg = (_obIcons[s.icon] || '').replace('stroke="currentColor"', 'stroke="#a5b4fc"');
  document.getElementById('ob-content').innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">' +
      '<div style="width:48px;height:48px;border-radius:12px;background:rgba(124,110,255,.15);border:1px solid rgba(124,110,255,.25);display:flex;align-items:center;justify-content:center;">' + mainSvg + '</div>' +
      '<div style="display:flex;gap:6px;align-items:center;">' + dots + '</div>' +
    '</div>' +
    '<div style="font-size:.75rem;font-weight:800;letter-spacing:.8px;text-transform:uppercase;color:#7c6eff;margin-bottom:6px;">' + s.badge + '</div>' +
    '<h2 style="font-size:1.35rem;font-weight:900;color:#fff;margin-bottom:6px;letter-spacing:-.3px;">' + s.title + '</h2>' +
    '<p style="font-size:.88rem;color:#8080a0;margin-bottom:18px;line-height:1.6;">' + s.subtitle + '</p>' +
    '<div style="display:flex;flex-direction:column;gap:10px;margin-bottom:24px;">' + items + '</div>' +
    '<div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;">' +
      (_obStep > 0 ? '<button onclick="_obPrev()" style="flex:0 0 auto;padding:10px 16px;background:#1a1a2e;border:1px solid #2a2a3e;border-radius:8px;color:#8080a0;font-size:13px;cursor:pointer;">← Atrás</button>' : '') +
      '<button onclick="_obNext()" style="flex:1;padding:12px 20px;background:linear-gradient(135deg,#6d28d9,#4f46e5);border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;transition:opacity .2s;" onmouseover="this.style.opacity=\'.88\'" onmouseout="this.style.opacity=\'1\'">' + s.cta + '</button>' +
      '<button onclick="_obSkip()" style="flex:0 0 auto;padding:10px 12px;background:none;border:none;color:#fff;font-size:12px;cursor:pointer;text-decoration:underline;opacity:.7;">Cerrar</button>' +
    '</div>' +
    '<label style="display:flex;align-items:center;gap:7px;cursor:pointer;padding:7px 10px;background:#080812;border:1px solid #1a1a30;border-radius:8px;">' +
      '<input type="checkbox" id="ob-hide-chk" ' + (localStorage.getItem('tof_ob_hidden') ? 'checked' : '') + ' onchange="if(this.checked){localStorage.setItem(\'tof_ob_hidden\',\'1\');}else{localStorage.removeItem(\'tof_ob_hidden\');}" style="accent-color:#7c6eff;width:13px;height:13px;cursor:pointer;">' +
      '<span style="font-size:11px;color:#505070;">No mostrar al iniciar (puedo abrirlo cuando quiera)</span>' +
    '</label>';
}

function _obNext() {
  if (_obStep < _obSteps.length - 1) {
    _obStep++;
    _renderObStep();
  } else {
    _obFinish();
  }
}

function _obPrev() {
  if (_obStep > 0) {
    _obStep--;
    _renderObStep();
  }
}

function _obSkip() { _obFinish(); }

function _obFinish() {
  var el = document.getElementById('ob-modal');
  if (el) { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(function(){ el.style.display = 'none'; el.style.opacity = '1'; }, 300); }
  if (typeof showToast === 'function') showToast('✦ Tutorial disponible en el botón inferior del panel →', 3500);
}

// Mostrar onboarding solo si ya aceptó términos (no interrumpir ese flujo)
(function _checkOnboarding(){
  var hidden = localStorage.getItem('tof_ob_hidden');
  var termsAccepted = localStorage.getItem('tof_terms_v2');
  if (!hidden && termsAccepted) {
    setTimeout(showOnboarding, 1200);
  }
})();

// ── Enviar a ManyChat: genera URL única sin copy-paste ──────────────────────
async function sendToManyChat() {
  var tok = localStorage.getItem('tof_token') || sessionStorage.getItem('tof_token') || '';
  if (!tok) {
    if (typeof openAuthModal === 'function') openAuthModal('login', '🔒 Inicia sesión para usar "Enviar a ManyChat"');
    return;
  }
  var jsonEl = document.getElementById('json-output');
  if (!jsonEl) { if (typeof showNotif === 'function') showNotif('No hay diseño activo', 'error'); return; }
  var payload;
  try { payload = JSON.parse(jsonEl.textContent); } catch(e) { if (typeof showNotif === 'function') showNotif('JSON inválido', 'error'); return; }

  // Mostrar modal de carga
  _mcShowModal('loading');

  try {
    var res = await fetch('/api/mc/template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok },
      body: JSON.stringify({ payload: payload })
    });
    var data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error ' + res.status);
    _mcShowModal('success', data.render_url);
  } catch(e) {
    _mcShowModal('error', '', e.message);
  }
}

function _mcShowModal(state, url, errMsg) {
  var m = document.getElementById('mc-modal');
  if (!m) return;
  m.style.display = 'flex';

  var body = document.getElementById('mc-modal-body');
  if (state === 'loading') {
    body.innerHTML = '<div style="text-align:center;padding:20px 0;"><span class="gen-spinner" style="width:28px;height:28px;border-width:3px;"></span><div style="margin-top:14px;color:#9ca3ff;font-size:13px;">Guardando plantilla…</div></div>';
    return;
  }
  if (state === 'error') {
    body.innerHTML = '<div style="color:#f87171;font-size:13px;text-align:center;padding:10px 0;">Error: ' + (errMsg||'Inténtalo de nuevo') + '</div>';
    return;
  }
  // success
  var exampleUrl = url + '?text=Hola {{nombre}}&image_url={{foto_portada}}';
  body.innerHTML =
    '<p style="margin:0 0 10px;font-size:12px;color:#a0aec0;line-height:1.6;">Pega esta URL en el <strong style="color:#e2e8f0;">paso HTTP Request</strong> de ManyChat (método GET). Tus variables de ManyChat se convierten automáticamente en los parámetros de la URL.</p>' +
    '<div style="background:#0a0a1a;border:1.5px solid #0084FF;border-radius:8px;padding:10px 12px;font-size:11px;color:#60a5fa;font-family:monospace;word-break:break-all;margin-bottom:12px;">' + url + '</div>' +
    '<p style="margin:0 0 6px;font-size:11px;color:#6b7280;">Ejemplo con tus variables ManyChat:</p>' +
    '<div style="background:#0d0d1a;border:1px solid #2a2a45;border-radius:6px;padding:8px 10px;font-size:10px;color:#7c6eff;font-family:monospace;word-break:break-all;margin-bottom:14px;">' + exampleUrl + '</div>' +
    '<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">' +
      '<button id="mc-copy-url-btn" onclick="_mcCopyUrl(\'' + url + '\',this)" style="display:flex;align-items:center;gap:6px;padding:9px 16px;background:linear-gradient(135deg,#0084FF,#00C6FF);color:white;border:none;border-radius:8px;cursor:pointer;font-weight:700;font-size:12px;">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>' +
        '<span>Copiar URL</span>' +
      '</button>' +
      '<button onclick="document.getElementById(\'mc-modal\').style.display=\'none\'" style="padding:9px 16px;background:#1e1e3a;color:#9ca3ff;border:1.5px solid #3a3a5c;border-radius:8px;cursor:pointer;font-size:12px;">Cerrar</button>' +
    '</div>' +
    '<div style="margin-top:14px;padding:10px;background:#1a0f00;border-left:3px solid #f59e0b;border-radius:0 6px 6px 0;font-size:11px;color:#d4a017;line-height:1.5;">' +
      '<strong>¿Cómo configurarlo en ManyChat?</strong><br>' +
      '1. En tu Flow agrega un paso <em>HTTP Request</em><br>' +
      '2. Método: <strong>GET</strong><br>' +
      '3. URL: pega la URL de arriba<br>' +
      '4. Agrega tus params: <code>text={{nombre}}</code>, <code>image_url={{foto}}</code><br>' +
      '5. Guarda la respuesta: <code>{{image_url}}</code> → úsala en el siguiente mensaje' +
    '</div>';
}

function _mcCopyUrl(url, btn) {
  navigator.clipboard.writeText(url).then(function() {
    if (btn) {
      var span = btn.querySelector('span') || btn;
      var orig = span.textContent;
      span.textContent = '✓ Copiado';
      btn.style.background = 'linear-gradient(135deg,#16a34a,#22c55e)';
      setTimeout(function() {
        span.textContent = orig;
        btn.style.background = 'linear-gradient(135deg,#0084FF,#00C6FF)';
      }, 2200);
    }
    if (typeof showNotif === 'function') showNotif('URL copiada al portapapeles', 'success');
  }).catch(function() {
    var ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    if (typeof showNotif === 'function') showNotif('URL copiada', 'success');
  });
}

// ── Exportar GIF animado desde el resultado ─────────────────────────────────
async function exportAnimatedGIF() {
  var tok = localStorage.getItem('tof_token') || sessionStorage.getItem('tof_token') || '';
  if (!tok) { if (typeof showNotif === 'function') showNotif('Inicia sesión para exportar GIF', 'error'); return; }

  var btn = document.getElementById('result-gif-btn');
  if (!btn) return;
  var origContent = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin-ring"></span> Generando GIF…';

  try {
    // Tomamos el JSON actual del editor
    var jsonEl = document.getElementById('json-output');
    if (!jsonEl) throw new Error('No se encontró el JSON del diseño');
    var payload = JSON.parse(jsonEl.textContent);

    // Parámetros de animación
    var animType = 'typewriter'; // Por defecto typewriter
    payload.animation_type = animType;
    payload.animated_text_index = 0; // Primer texto
    payload.gif_fps = 12;
    payload.hold_seconds = 1.5;
    payload.gif_loop = 0;
    payload.render_scale = 1;

    // Adjuntar imagen base64 si disponible
    var templateUrl = payload.template_name;
    if (templateUrl && templateUrl.startsWith('http')) {
      try {
        var pxResp = await fetch('/proxy-image?url=' + encodeURIComponent(templateUrl));
        if (pxResp.ok) {
          var pxAb = await pxResp.arrayBuffer();
          var pxArr = new Uint8Array(pxAb);
          var pxStr = '';
          for (var i = 0; i < pxArr.length; i++) pxStr += String.fromCharCode(pxArr[i]);
          payload.template_image_b64 = btoa(pxStr);
        }
      } catch(_) {}
    }

    var headers = { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok };

    if (typeof showNotif === 'function') showNotif('⏳ Generando GIF… puede tardar 10-20 segundos', 'info');

    var res = await fetch('/api/gif/generate', { method: 'POST', headers: headers, body: JSON.stringify(payload) });
    if (!res.ok) {
      var errData = await res.json().catch(function(){ return {}; });
      throw new Error(errData.detail || 'Error ' + res.status);
    }

    var blob = await res.blob();
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'textonflow-animated.gif';
    a.click();
    URL.revokeObjectURL(a.href);

    if (typeof showNotif === 'function') showNotif('✅ GIF animado descargado', 'success');

  } catch (e) {
    if (typeof showNotif === 'function') showNotif('Error generando GIF: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = origContent;
  }
}
window.iacSetTab            = iacSetTab;
window.iacGenerateDesign    = iacGenerateDesign;
window.iacApplyDesign       = iacApplyDesign;
window.iacGetCopySuggestions= iacGetCopySuggestions;
window.iacApplyCopy         = iacApplyCopy;
window.openIacBrandKit      = openIacBrandKit;
window.closeIacBrandKit     = closeIacBrandKit;
window.iacExtractBrandKit   = iacExtractBrandKit;
window.iacApplyBrandKit     = iacApplyBrandKit;
window.closeIacAB           = closeIacAB;
window.iacGenerateVariants  = iacGenerateVariants;
window.iacApplyVariant      = iacApplyVariant;
window.openOnboarding       = openOnboarding;
window.showOnboarding       = showOnboarding;
window.sendToManyChat       = sendToManyChat;
window.exportAnimatedGIF    = exportAnimatedGIF;
