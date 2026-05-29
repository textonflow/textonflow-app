// ─── Mesas de trabajo / Pestañas (Artboards) ──────────────────────────────────
// Permite tener varios diseños dentro del mismo archivo, cambiar entre ellos sin
// perder nada, cada uno con su propio JSON. Aislado: no modifica app.js.
(function(){
  'use strict';

  var LS_KEY = 'tof_artboards';
  var FILE_VERSION = 'tof-artboards-1';

  function _copy(o){ try{ return JSON.parse(JSON.stringify(o||null)); }catch(e){ return null; } }
  function _uid(){ return 'b_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,7); }

  // Estado del sistema de pestañas
  window._tofBoards = window._tofBoards || [];
  window._tofActiveBoard = window._tofActiveBoard || 0;

  // ── Captura el estado vivo del editor en un objeto de diseño ─────────────────
  // Usa el puente oficial de app.js (estado léxico real). Fallback al input de URL.
  function snapLive(){
    try{
      if(typeof window.tofGetEditorState === 'function'){
        var st = window.tofGetEditorState();
        return {
          texts: _copy(st.texts) || [],
          imageOverlays: _copy(st.imageOverlays) || [],
          shapes: _copy(st.shapes) || [],
          imgUrl: st.imgUrl || null
        };
      }
    }catch(e){}
    // Fallback mínimo (no debería ocurrir)
    var imgUrl = null;
    try{ var ui = document.getElementById('image-url-input'); if(ui && ui.value) imgUrl = ui.value.trim(); }catch(e2){}
    return { texts:[], imageOverlays:[], shapes:[], imgUrl: imgUrl || null };
  }

  // ── Limpia el lienzo (estado sin imagen) ─────────────────────────────────────
  function clearCanvas(){
    try{
      var c = document.getElementById('preview-canvas');
      if(c){ var x = c.getContext('2d'); if(x) x.clearRect(0,0,c.width,c.height); }
      // Resetear el estado léxico real (imageData/loadedImg) vía puente de app.js
      if(typeof window.tofResetImageState === 'function') window.tofResetImageState();
      var uz = document.getElementById('upload-zone');
      var cw = document.getElementById('canvas-wrapper');
      if(uz) uz.classList.remove('has-image');
      if(cw) cw.classList.remove('active');
      var ui = document.getElementById('image-url-input');
      if(ui) ui.value = '';
    }catch(e){}
  }

  function _refreshPanels(){
    try{ if(typeof updateControls==='function') updateControls(); }catch(e){}
    try{ if(typeof updatePreview==='function') updatePreview(); }catch(e){}
    try{ if(typeof updateJSON==='function') updateJSON(); }catch(e){}
    try{ if(typeof renderStickerThumbnails==='function') renderStickerThumbnails(); }catch(e){}
    try{ if(typeof renderShapesOnCanvas==='function') renderShapesOnCanvas(); }catch(e){}
    try{ if(typeof renderFormasPanel==='function') renderFormasPanel(); }catch(e){}
  }

  // ── Restaura un diseño en el editor (mismo patrón que openProject) ───────────
  function restoreDesign(d){
    if(!d) d = { texts:[], imageOverlays:[], shapes:[], imgUrl:null };
    // Setear los arrays ANTES de cargar la imagen, para que updatePreview los pinte.
    // Usa el puente oficial (estado léxico real de app.js).
    if(typeof window.tofApplyEditorArrays === 'function'){
      window.tofApplyEditorArrays({
        texts: _copy(d.texts) || [],
        imageOverlays: _copy(d.imageOverlays) || [],
        shapes: _copy(d.shapes) || []
      });
    }

    var imgUrl = d.imgUrl || null;
    if(imgUrl){
      var urlInput = document.getElementById('image-url-input');
      if(urlInput) urlInput.value = imgUrl;
      if(typeof loadImageFromURL === 'function'){
        loadImageFromURL(); // async: applyImageToCanvas pinta textos/overlays/shapes + updateJSON
      } else {
        if(window.imageData) window.imageData.filename = imgUrl;
        _refreshPanels();
      }
    } else {
      clearCanvas();
      _refreshPanels();
    }
  }

  // ── Persistencia local (sobrevive recargas) ──────────────────────────────────
  function persist(){
    try{
      if(window._tofBoards[window._tofActiveBoard]){
        window._tofBoards[window._tofActiveBoard].data = snapLive();
      }
      localStorage.setItem(LS_KEY, JSON.stringify({
        boards: window._tofBoards,
        active: window._tofActiveBoard
      }));
    }catch(e){}
  }

  function loadStored(){
    try{
      var raw = localStorage.getItem(LS_KEY);
      if(!raw) return null;
      var obj = JSON.parse(raw);
      if(obj && Array.isArray(obj.boards) && obj.boards.length){
        return obj;
      }
    }catch(e){}
    return null;
  }

  // ── Operaciones de pestañas ──────────────────────────────────────────────────
  function switchBoard(i){
    if(i === window._tofActiveBoard) return;
    if(i < 0 || i >= window._tofBoards.length) return;
    // Guardar lo que hay en la pestaña activa
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    window._tofActiveBoard = i;
    restoreDesign(window._tofBoards[i].data);
    renderTabs();
    persist();
  }

  function _nextName(){
    var n = window._tofBoards.length + 1;
    return 'Diseño ' + n;
  }

  function addBoard(){
    // Guardar pestaña actual
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var b = { id:_uid(), name:_nextName(), data:{ texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
    window._tofBoards.push(b);
    window._tofActiveBoard = window._tofBoards.length - 1;
    restoreDesign(b.data);
    renderTabs();
    persist();
    _toast('➕ Pestaña nueva creada');
  }

  function duplicateBoard(){
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var src = window._tofBoards[window._tofActiveBoard];
    var copy = { id:_uid(), name:(src.name||'Diseño') + ' (copia)', data:_copy(src.data) };
    window._tofBoards.splice(window._tofActiveBoard + 1, 0, copy);
    window._tofActiveBoard = window._tofActiveBoard + 1;
    restoreDesign(copy.data);
    renderTabs();
    persist();
    _toast('⧉ Pestaña duplicada');
  }

  function deleteBoard(i){
    if(window._tofBoards.length <= 1){
      _toast('⚠️ Debe quedar al menos una pestaña', 'warning');
      return;
    }
    var name = (window._tofBoards[i] && window._tofBoards[i].name) || 'esta pestaña';
    if(!confirm('¿Eliminar "' + name + '"? No se puede deshacer.')) return;
    window._tofBoards.splice(i, 1);
    if(window._tofActiveBoard >= window._tofBoards.length){
      window._tofActiveBoard = window._tofBoards.length - 1;
    } else if(i < window._tofActiveBoard){
      window._tofActiveBoard--;
    } else if(i === window._tofActiveBoard){
      // se borró la activa: cargar la que quedó en su lugar
    }
    restoreDesign(window._tofBoards[window._tofActiveBoard].data);
    renderTabs();
    persist();
  }

  function renameBoard(i){
    var b = window._tofBoards[i];
    if(!b) return;
    var nuevo = prompt('Nombre de la pestaña:', b.name || '');
    if(nuevo === null) return;
    nuevo = nuevo.trim();
    if(!nuevo) return;
    b.name = nuevo.slice(0, 40);
    renderTabs();
    persist();
  }

  // ── Guardar / Abrir como archivo ─────────────────────────────────────────────
  function exportFile(){
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var payload = { __tof: FILE_VERSION, savedAt:new Date().toISOString(), active:window._tofActiveBoard, boards:window._tofBoards };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type:'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    var d = new Date();
    var stamp = d.getFullYear() + ('0'+(d.getMonth()+1)).slice(-2) + ('0'+d.getDate()).slice(-2);
    a.href = url;
    a.download = 'textonflow-disenos-' + stamp + '.json';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
    _toast('💾 Archivo descargado (' + window._tofBoards.length + ' pestañas)');
  }

  function importFile(){
    var inp = document.getElementById('tof-artboard-file');
    if(inp) inp.click();
  }

  function _onFilePicked(ev){
    var file = ev.target && ev.target.files && ev.target.files[0];
    if(!file) return;
    var reader = new FileReader();
    reader.onload = function(e){
      try{
        var obj = JSON.parse(e.target.result);
        if(!obj || !Array.isArray(obj.boards) || !obj.boards.length){
          throw new Error('formato');
        }
        if(!confirm('Abrir este archivo reemplazará las pestañas actuales (' + window._tofBoards.length + '). ¿Continuar?')){
          ev.target.value = '';
          return;
        }
        window._tofBoards = obj.boards.map(function(b){
          return { id:b.id || _uid(), name:b.name || 'Diseño', data:b.data || { texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
        });
        window._tofActiveBoard = (typeof obj.active === 'number' && obj.active < window._tofBoards.length) ? obj.active : 0;
        restoreDesign(window._tofBoards[window._tofActiveBoard].data);
        renderTabs();
        persist();
        _toast('📂 Archivo abierto (' + window._tofBoards.length + ' pestañas)');
      }catch(err){
        _toast('❌ Archivo no válido', 'error');
      }
      ev.target.value = '';
    };
    reader.readAsText(file);
  }

  function _toast(msg, type){
    try{ if(typeof showToast === 'function'){ showToast(msg, type || 'success'); return; } }catch(e){}
    try{ if(typeof showNotif === 'function'){ showNotif(msg, type || 'success'); return; } }catch(e){}
  }

  // ── Render de la barra de pestañas ───────────────────────────────────────────
  function renderTabs(){
    var bar = document.getElementById('tof-artboard-bar');
    if(!bar) return;
    var tabs = window._tofBoards.map(function(b, i){
      var active = (i === window._tofActiveBoard);
      var nm = String(b.name || ('Diseño ' + (i+1)))
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return '' +
        '<div class="tof-tab' + (active ? ' tof-tab-active' : '') + '" ' +
             'onclick="tofSwitchBoard(' + i + ')" ' +
             'ondblclick="tofRenameBoard(' + i + ')" ' +
             'title="Clic: abrir · Doble clic: renombrar">' +
          '<span class="tof-tab-name">' + nm + '</span>' +
          '<span class="tof-tab-x" onclick="event.stopPropagation();tofDeleteBoard(' + i + ')" title="Eliminar pestaña">&times;</span>' +
        '</div>';
    }).join('');

    bar.innerHTML =
      '<div class="tof-tabs-scroll">' + tabs + '</div>' +
      '<div class="tof-tabs-actions">' +
        '<button class="tof-tab-btn" onclick="tofAddBoard()" title="Nueva pestaña en blanco">+ Nuevo</button>' +
        '<button class="tof-tab-btn" onclick="tofDuplicateBoard()" title="Duplicar la pestaña actual">⧉ Duplicar</button>' +
        '<span class="tof-tab-sep"></span>' +
        '<button class="tof-tab-btn" onclick="tofExportFile()" title="Guardar todas las pestañas como archivo">💾 Guardar archivo</button>' +
        '<button class="tof-tab-btn" onclick="tofImportFile()" title="Abrir un archivo de pestañas">📂 Abrir archivo</button>' +
      '</div>';
  }

  function injectStyles(){
    if(document.getElementById('tof-artboard-styles')) return;
    var css = '' +
      '#tof-artboard-bar{display:flex;align-items:center;gap:10px;width:100%;box-sizing:border-box;' +
        'padding:6px 10px;background:rgba(15,23,42,.92);border-bottom:1px solid rgba(255,255,255,.08);' +
        'position:sticky;top:0;z-index:120;backdrop-filter:blur(6px);}' +
      '.tof-tabs-scroll{display:flex;align-items:center;gap:6px;flex:1;min-width:0;overflow-x:auto;padding-bottom:2px;}' +
      '.tof-tabs-scroll::-webkit-scrollbar{height:6px;}' +
      '.tof-tabs-scroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:6px;}' +
      '.tof-tab{display:flex;align-items:center;gap:6px;flex-shrink:0;max-width:170px;cursor:pointer;' +
        'padding:6px 10px;border-radius:8px 8px 0 0;border:1px solid rgba(255,255,255,.08);border-bottom:none;' +
        'background:rgba(255,255,255,.04);color:#94a3b8;font-size:12px;font-weight:600;user-select:none;' +
        'transition:background .15s,color .15s;}' +
      '.tof-tab:hover{background:rgba(255,255,255,.08);color:#cbd5e1;}' +
      '.tof-tab-active{background:linear-gradient(135deg,#7c6eff,#a78bfa);color:#fff;border-color:transparent;}' +
      '.tof-tab-active:hover{color:#fff;}' +
      '.tof-tab-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px;}' +
      '.tof-tab-x{opacity:.55;font-size:15px;line-height:1;padding:0 2px;border-radius:4px;}' +
      '.tof-tab-x:hover{opacity:1;background:rgba(0,0,0,.25);color:#fff;}' +
      '.tof-tabs-actions{display:flex;align-items:center;gap:6px;flex-shrink:0;}' +
      '.tof-tab-btn{cursor:pointer;padding:6px 10px;border-radius:8px;border:1px solid rgba(124,110,255,.35);' +
        'background:rgba(124,110,255,.12);color:#c4b5fd;font-size:11px;font-weight:700;white-space:nowrap;' +
        'transition:background .15s;}' +
      '.tof-tab-btn:hover{background:rgba(124,110,255,.25);color:#fff;}' +
      '.tof-tab-sep{width:1px;height:18px;background:rgba(255,255,255,.12);margin:0 2px;}' +
      '@media(max-width:768px){.tof-tab-btn{font-size:10px;padding:5px 7px;}.tof-tab{max-width:120px;}}';
    var st = document.createElement('style');
    st.id = 'tof-artboard-styles';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ── Inicialización ───────────────────────────────────────────────────────────
  function init(){
    injectStyles();

    // Asegurar el input file oculto
    if(!document.getElementById('tof-artboard-file')){
      var inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'application/json,.json';
      inp.id = 'tof-artboard-file'; inp.style.display = 'none';
      inp.addEventListener('change', _onFilePicked);
      document.body.appendChild(inp);
    }

    var stored = loadStored();
    if(stored){
      window._tofBoards = stored.boards.map(function(b){
        return { id:b.id || _uid(), name:b.name || 'Diseño', data:b.data || { texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
      });
      window._tofActiveBoard = (typeof stored.active === 'number' && stored.active < window._tofBoards.length) ? stored.active : 0;
      restoreDesign(window._tofBoards[window._tofActiveBoard].data);
    } else {
      // Envolver el diseño actual (lo que app.js ya restauró) como primera pestaña
      window._tofBoards = [{ id:_uid(), name:'Diseño 1', data:snapLive() }];
      window._tofActiveBoard = 0;
    }
    renderTabs();

    // Autoguardado periódico (sobrevive recargas/cierres)
    setInterval(persist, 4000);
    window.addEventListener('beforeunload', persist);
    document.addEventListener('visibilitychange', function(){ if(document.hidden) persist(); });
  }

  // Esperar a que app.js termine de inicializar (loadPreferences, imageData, etc.)
  function boot(){
    var tries = 0;
    (function wait(){
      tries++;
      var ready = (typeof updatePreview === 'function') && (typeof updateJSON === 'function') && (window.imageData !== undefined);
      if(ready || tries > 40){
        init();
      } else {
        setTimeout(wait, 150);
      }
    })();
  }

  if(document.readyState === 'complete' || document.readyState === 'interactive'){
    setTimeout(boot, 400);
  } else {
    window.addEventListener('load', function(){ setTimeout(boot, 400); });
  }

  // Exponer handlers para los onclick
  window.tofSwitchBoard    = switchBoard;
  window.tofAddBoard       = addBoard;
  window.tofDuplicateBoard = duplicateBoard;
  window.tofDeleteBoard    = deleteBoard;
  window.tofRenameBoard    = renameBoard;
  window.tofExportFile     = exportFile;
  window.tofImportFile     = importFile;
  window._tofPersistBoards = persist;
  window._tofRenderTabs    = renderTabs;
})();
