function toggleShortcuts(){
  var el=document.getElementById('tof-shortcuts-overlay');
  if(!el)return;
  el.style.display=el.style.display==='flex'?'none':'flex';
}
function closeShortcuts(){
  var el=document.getElementById('tof-shortcuts-overlay');
  if(el)el.style.display='none';
}
