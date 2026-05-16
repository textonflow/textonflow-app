// ─── Proyectos ────────────────────────────────────────────────────────────────
function _tofToken(){ return localStorage.getItem('tof_token') || ''; }

function openProjectsModal(){
  if(!_tofToken()){
    showToast && showToast('⚠️ Inicia sesión para guardar proyectos','warning');
    if(window.openAuthModal) openAuthModal();
    return;
  }
  document.getElementById('projects-modal').style.display='flex';
  loadProjectsList();
}
function closeProjectsModal(){
  document.getElementById('projects-modal').style.display='none';
}

function _buildCanvasSnapshot(){
  // Recoge el estado actual del editor desde las variables globales de app.js
  try{
    const snap = {};
    // Intentar capturar textos desde el DOM del panel
    const layers = window.textLayers || [];
    snap.textLayers = layers;
    if(window.currentImageUrl) snap.currentImageUrl = window.currentImageUrl;
    if(window.imageZoom)       snap.imageZoom = window.imageZoom;
    if(window.panX !== undefined){ snap.panX = window.panX; snap.panY = window.panY; }
    // Capturar el JSON exportado si existe
    if(typeof getExportJSON === 'function') snap.exportJSON = getExportJSON();
    return snap;
  }catch(e){ return {}; }
}

async function saveCurrentProject(){
  const nameEl = document.getElementById('project-name-input');
  const name = (nameEl.value || 'Sin título').trim();
  const btn = document.getElementById('btn-save-project');
  btn.disabled = true; btn.textContent = '…';
  try{
    // _tof_last_json contiene el estado completo: texts, imageOverlays, shapes, template_name, etc.
    const canvas_json = window._tof_last_json ? JSON.parse(JSON.stringify(window._tof_last_json)) : {};
    // thumbnail: imagen renderizada si existe, si no la imagen base del canvas
    const resultImg = document.getElementById('result-image');
    let image_url = null;
    if(resultImg && resultImg.src && resultImg.src.startsWith('http')) {
      image_url = resultImg.src;
    } else if(canvas_json.template_name && canvas_json.template_name.startsWith('http')) {
      image_url = canvas_json.template_name;
    }
    const body = { name, canvas_json, image_url };
    const r = await fetch('/projects', {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+_tofToken()},
      body: JSON.stringify(body)
    });
    if(r.ok){
      nameEl.value = '';
      showToast && showToast('✅ Proyecto "'+name+'" guardado','success');
      if(window._tofMarkClean) window._tofMarkClean();
      loadProjectsList();
    } else {
      const e = await r.json().catch(()=>({detail:'Error'}));
      showToast && showToast('❌ '+e.detail,'error');
    }
  }catch(ex){ showToast && showToast('❌ Error de red','error'); }
  btn.disabled=false; btn.textContent='💾 Guardar';
}

async function loadProjectsList(){
  const list = document.getElementById('projects-list');
  const loading = document.getElementById('projects-loading');
  if(loading) loading.style.display='block';
  try{
    const r = await fetch('/projects?limit=50',{headers:{'Authorization':'Bearer '+_tofToken()}});
    if(!r.ok) throw new Error('No autorizado');
    const data = await r.json();
    renderProjectsList(data.projects || []);
  }catch(e){
    if(list) list.innerHTML='<div style="color:#f87171;font-size:13px;padding:20px 0;text-align:center;">Error cargando proyectos</div>';
  }
}

function renderProjectsList(projects){
  const list = document.getElementById('projects-list');
  if(!list) return;
  if(!projects.length){
    list.innerHTML='<div style="text-align:center;color:#475569;padding:28px 0;font-size:13px;">No tienes proyectos guardados aún.<br><span style="font-size:12px;color:#334155;">Guarda tu diseño actual con el botón de arriba.</span></div>';
    return;
  }
  list.innerHTML = projects.map(p=>{
    const date = new Date(p.updated_at).toLocaleString('es-MX',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
    const thumb = p.image_url
      ? `<img src="${p.image_url}" style="width:52px;height:40px;object-fit:cover;border-radius:6px;flex-shrink:0;border:1px solid rgba(255,255,255,.1);">`
      : `<div style="width:52px;height:40px;border-radius:6px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;flex-shrink:0;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>`;
    return `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.05);">
      ${thumb}
      <div style="flex:1;min-width:0;">
        <div style="font-size:13px;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(p.name)}</div>
        <div style="font-size:11px;color:#475569;margin-top:2px;">${date}</div>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0;">
        <button onclick="openProject('${p.id}')" title="Abrir proyecto"
          style="padding:5px 10px;border-radius:7px;border:1px solid rgba(99,102,241,.4);background:rgba(99,102,241,.12);color:#a5b4fc;font-size:11px;font-weight:700;cursor:pointer;">Abrir</button>
        <button onclick="deleteProject('${p.id}','${escHtml(p.name)}')" title="Eliminar proyecto"
          style="padding:5px 8px;border-radius:7px;border:1px solid rgba(248,113,113,.25);background:rgba(248,113,113,.08);color:#f87171;font-size:11px;cursor:pointer;">🗑</button>
      </div>
    </div>`;
  }).join('');
}

function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function openProject(id){
  try{
    const r = await fetch('/projects/'+id,{headers:{'Authorization':'Bearer '+_tofToken()}});
    if(!r.ok) throw new Error('No encontrado');
    const proj = await r.json();
    closeProjectsModal();
    const cj = proj.canvas_json || {};

    // Restaurar textos e imageOverlays en las variables globales de app.js
    // ANTES de cargar la imagen, para que updatePreview() los pinte al terminar la carga
    if(Array.isArray(cj.texts)) {
      window.texts = JSON.parse(JSON.stringify(cj.texts));
    }
    if(Array.isArray(cj.imageOverlays)) {
      window.imageOverlays = JSON.parse(JSON.stringify(cj.imageOverlays));
    }
    if(Array.isArray(cj.shapes)) {
      window.shapes = JSON.parse(JSON.stringify(cj.shapes));
    }

    // Restaurar configuración de imagen (zoom, pan, etc.)
    if(cj.img_zoom !== undefined && window.imageData) window.imageData.zoom = cj.img_zoom;
    if(cj.img_pan_x !== undefined && window.imageData) window.imageData.panX = cj.img_pan_x;
    if(cj.img_pan_y !== undefined && window.imageData) window.imageData.panY = cj.img_pan_y;

    // Cargar imagen base (template_name es la URL de la imagen en _tof_last_json)
    const imgUrl = cj.template_name || cj.currentImageUrl || null;
    if(imgUrl){
      const urlInput = document.getElementById('image-url-input');
      if(urlInput) urlInput.value = imgUrl;
      if(typeof loadImageFromURL === 'function'){
        loadImageFromURL();
      } else if(window.imageData){
        window.imageData.filename = imgUrl;
        if(typeof updateControls === 'function') updateControls();
        if(typeof updatePreview === 'function') updatePreview();
      }
    } else {
      // Sin imagen: solo refrescar controles
      if(typeof updateControls === 'function') updateControls();
      if(typeof updatePreview === 'function') updatePreview();
    }

    showToast && showToast('📂 Proyecto "'+proj.name+'" cargado','success');
  }catch(e){ showToast && showToast('❌ Error abriendo proyecto','error'); }
}

async function deleteProject(id, name){
  if(!confirm('¿Eliminar el proyecto "'+name+'"? Esta acción no se puede deshacer.')) return;
  try{
    const r = await fetch('/projects/'+id,{method:'DELETE',headers:{'Authorization':'Bearer '+_tofToken()}});
    if(r.ok){
      showToast && showToast('🗑 Proyecto eliminado','success');
      loadProjectsList();
    } else { showToast && showToast('❌ Error eliminando','error'); }
  }catch(e){ showToast && showToast('❌ Error de red','error'); }
}

// Cerrar modal al hacer click fuera
document.getElementById('projects-modal').addEventListener('click',function(e){
  if(e.target===this) closeProjectsModal();
});
window.openProjectsModal  = openProjectsModal;
window.closeProjectsModal = closeProjectsModal;
window.saveCurrentProject = saveCurrentProject;
window.deleteProject      = deleteProject;
window.openProject        = openProject;
window.renderProjectsList = renderProjectsList;
