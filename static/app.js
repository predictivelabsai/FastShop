(function(){
  function drawer(){return document.getElementById('ai-drawer');}
  window.toggleAssistant=function(force){var d=drawer();if(!d)return;var open=typeof force==='boolean'?force:!d.classList.contains('open');d.classList.toggle('open',open);d.setAttribute('aria-hidden',open?'false':'true');if(open){var i=d.querySelector('input[name=message]');if(i)i.focus();}};
  window.toggleCopilot=function(){var shell=document.querySelector('.merchant-shell');if(shell)shell.classList.toggle('copilot-closed');};
  window.askSample=function(text,formId){var f=document.getElementById(formId||'ai-form');if(!f)return;var i=f.querySelector('input[name=message]');i.value=text;f.requestSubmit();};
  async function send(form){
    var input=form.querySelector('input[name=message]'), box=form.closest('.ai-drawer,.copilot').querySelector('.ai-messages');
    var text=input.value.trim();if(!text)return;var route=window.location.pathname;
    var mine=document.createElement('div');mine.className='ai-msg user';mine.textContent=text;box.appendChild(mine);input.value='';
    var wait=document.createElement('div');wait.className='ai-msg';wait.textContent='Thinking…';box.appendChild(wait);box.scrollTop=box.scrollHeight;
    try{var body=new URLSearchParams({message:text,route:route,csrf_token:form.querySelector('[name=csrf_token]').value});var response=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body});var data=await response.json();wait.textContent=data.answer||data.error||'I could not answer that.';}catch(e){wait.textContent='The assistant is temporarily unavailable.';}box.scrollTop=box.scrollHeight;
  }
  document.addEventListener('submit',function(e){if(e.target.matches('.ai-form')){e.preventDefault();send(e.target);}});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')window.toggleAssistant(false);});
})();

