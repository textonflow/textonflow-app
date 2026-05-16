function cdHelpModal(tab){
  var m=document.getElementById('cd-help-modal');
  m.style.display='flex';
  cdHelpTab(tab||'event');
  m.onclick=function(e){if(e.target===m)m.style.display='none';};
}
function cdHelpTab(tab){
  ['event','urgency'].forEach(function(t){
    document.getElementById('cd-help-tab-'+t).style.color=t===tab?'#a5b4fc':'#64748b';
    document.getElementById('cd-help-tab-'+t).style.borderBottom=t===tab?'2.5px solid #6366f1':'2.5px solid transparent';
    document.getElementById('cd-help-pane-'+t).style.display=t===tab?'':'none';
  });
}
