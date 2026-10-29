/* Faculty paper score sheets: every official write remains server validated. */
(function () {
  'use strict';
  const app=document.getElementById('ocr-app');if(!app)return;
  const byId=id=>document.getElementById('ocr-'+id);
  const event=byId('event'),round=byId('round'),judge=byId('judge'),status=byId('status');
  let current=null,snapshot=null,catalog=null,file=null,busy=false,dirty=false,page=1,pages=1,previewPage=0,ocrRunning=false;
  function message(text,error=false){status.textContent=text;status.classList.toggle('error',error);}
  async function request(params={},data=null){
    const url=new URL(app.dataset.api,location.origin);
    Object.entries(params).forEach(([key,value])=>{if(value!==''&&value!=null)url.searchParams.set(key,value);});
    const response=await fetch(url,{method:data?'POST':'GET',body:data,headers:data?{'X-CSRFToken':app.querySelector('[name=csrfmiddlewaretoken]').value}:{},cache:'no-store'});
    if(response.redirected)throw Error('Please sign in again before continuing.');
    let result;try{result=await response.json();}catch(error){throw Error('The request failed. Please try again.');}
    if(!response.ok)throw Error(result.message||'The request could not be completed.');return result;
  }
  function post(action,extra={}){const data=new FormData();data.set('action',action);if(current)data.set('sheet',current.id);Object.entries(extra).forEach(([key,value])=>data.set(key,value));return request({},data);}
  async function run(action){if(busy)return;busy=true;app.setAttribute('aria-busy','true');controls();try{await action();}catch(error){message(error.message,true);}finally{busy=false;app.removeAttribute('aria-busy');controls();}}
  function writable(){return snapshot&&catalog&&catalog.rounds.some(row=>row.id===snapshot.round_id&&row.writable)&&(!current||current.verification_status!=='confirmed');}
  function controls(){
    const ready=!!snapshot&&writable();
    byId('print').disabled=busy||(!ready&&!current);
    byId('manual').disabled=busy||!ready;
    byId('file').disabled=busy||!ready;
    byId('start').disabled=busy||!ready||(!file&&!(current&&current.has_original));
    ['save','verify'].forEach(id=>byId(id).disabled=busy||!ready);
    byId('confirm').disabled=busy||!ready||!current||current.verification_status!=='verified'||dirty||!byId('compared').checked;
    app.querySelectorAll('#ocr-scores input').forEach(input=>input.disabled=busy||!ready);
    [event,round,judge].forEach(select=>{select.disabled=busy||(select!==event&&!select.options.length);});
    byId('remove').disabled=busy||!ready;byId('remove').hidden=!file&&!current?.has_original;
    byId('previous-page').disabled=previewPage<=0;
    byId('next-page').disabled=!current||previewPage>=current.pages-1;
    const step=current?.verification_status==='confirmed'||current?.verification_status==='verified'?5:ocrRunning?3:['completed','failed','manual'].includes(current?.ocr_status)||dirty?4:snapshot?2:1;
    app.querySelectorAll('[data-step]').forEach(item=>{if(Number(item.dataset.step)===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});
  }
  function options(select,rows,placeholder){select.replaceChildren();if(placeholder)select.add(new Option(placeholder,''));rows.forEach(row=>{const option=new Option(row.name,row.id);if(row.disabled)option.disabled=true;select.add(option);});}
  function clearSheet(){current=null;snapshot=null;file=null;dirty=false;previewPage=0;byId('file').value='';byId('filename').textContent='No file selected';byId('remove').hidden=true;byId('compared').checked=false;document.querySelector('.ocr-preview').hidden=true;byId('sheet-id').textContent='';renderScores();}
  async function chooseEvent(roundId='',judgeId=''){
    if(!event.value){options(round,[],'Select round');options(judge,[],'Select judge');catalog=null;return;}
    catalog=await request({event:event.value,round:roundId});
    options(round,catalog.rounds.map(row=>({...row,name:row.name+(row.writable?' — Active':' — View only')})));
    if(roundId)round.value=roundId;else round.value=catalog.rounds.find(row=>row.writable)?.id||catalog.rounds[0]?.id||'';
    if(round.value!==roundId)catalog=await request({event:event.value,round:round.value});
    options(judge,catalog.judges.map(row=>({...row,name:`${row.name} — ${row.status} (${row.progress}%)`})),'Select judge');
    if(judgeId)judge.value=judgeId;
  }
  function renderScores(){
    const table=byId('scores'),head=table.querySelector('thead'),body=table.querySelector('tbody');head.replaceChildren();body.replaceChildren();
    if(!snapshot){const tr=body.insertRow();tr.insertCell().textContent='Select an event, round, and judge.';return;}
    const heading=document.createElement('tr');['Participant',...snapshot.columns.map(col=>`${col.name} (${col.maximum} pts)`),'Raw total'].forEach(text=>{const th=document.createElement('th');th.textContent=text;heading.append(th);});head.append(heading);
    snapshot.participants.forEach(participant=>{
      const row=body.insertRow();row.insertCell().textContent=participant.name;
      snapshot.columns.forEach(col=>{
        const key=`${participant.id}/${col.key}`,cell=row.insertCell(),input=document.createElement('input');
        input.type='number';input.min=col.minimum;input.max=col.maximum;input.step=col.ranking?'1':'.01';input.required=true;input.dataset.key=key;
        input.setAttribute('aria-label',`${participant.name}, ${col.name}, maximum ${col.maximum}`);
        const extracted=current?.extracted[key];input.value=current?.corrected[key]??extracted?.value??'';
        if(extracted?.needs_review||!input.value)input.classList.add('needs-review');
        input.title=extracted?`OCR confidence: ${Math.round(extracted.confidence)}%. Check the original.`:'Enter the score from the paper sheet.';
        input.addEventListener('input',()=>{dirty=true;byId('compared').checked=false;totals(row);controls();});cell.append(input);
      });
      row.insertCell().append(document.createElement('output'));totals(row);
    });controls();
  }
  function totals(row){const inputs=[...row.querySelectorAll('input')];row.querySelector('output').textContent=inputs.every(input=>input.value!==''&&input.validity.valid)?inputs.reduce((sum,input)=>sum+Number(input.value),0).toFixed(2):'—';}
  function scores(){const values={};byId('scores').querySelectorAll('input').forEach(input=>{values[input.dataset.key]=input.value;});return values;}
  async function ensureSheet(){
    if(!current){current=await post('create',{event:event.value,round:round.value,judge:judge.value});snapshot=current.snapshot;byId('sheet-id').textContent=`Sheet ${current.id}`;}
    return current;
  }
  async function upload(){
    await ensureSheet();if(!file)return;
    current=await post('upload',{file});snapshot=current.snapshot;file=null;byId('file').value='';byId('remove').hidden=true;previewPage=0;showPreview();renderScores();
  }
  function showPreview(){
    const preview=document.querySelector('.ocr-preview');preview.hidden=!current?.has_original;if(preview.hidden)return;
    const url=new URL(current.original,location.origin);url.searchParams.set('preview','1');url.searchParams.set('page',previewPage);url.searchParams.set('revision',Date.now());
    byId('image').src=url;byId('zoom').href=url;byId('original').href=current.original;byId('page-number').textContent=`Page ${previewPage+1} of ${current.pages}`;controls();
  }
  function selectedFile(chosen){if(!chosen)return;if(chosen.size>10*1024*1024){message('Choose a file under 10 MB.',true);return;}if(!/\.(jpe?g|png|pdf)$/i.test(chosen.name)){message('Use JPG, PNG, or PDF files.',true);return;}file=chosen;byId('filename').textContent=chosen.name;byId('remove').hidden=false;message('File selected. Start OCR or use Manual Entry to upload it.');controls();}
  event.addEventListener('change',()=>run(async()=>{clearSheet();await chooseEvent();message('Select a round and judge.');}));
  round.addEventListener('change',()=>run(async()=>{clearSheet();await chooseEvent(round.value);message('Select a judge.');}));
  judge.addEventListener('change',()=>run(async()=>{clearSheet();if(!judge.value)return;const data=await request({event:event.value,round:round.value,judge:judge.value});snapshot=data.snapshot;renderScores();message('Generate the template, upload a sheet, or use manual entry.');}));
  byId('file').addEventListener('change',()=>selectedFile(byId('file').files[0]));
  byId('remove').addEventListener('click',()=>run(async()=>{if(file){file=null;byId('file').value='';}else if(current?.has_original){current=await post('remove');renderScores();showPreview();await refreshRecords();}byId('filename').textContent=current?.has_original?'Uploaded original retained':'No file selected';controls();}));
  const drop=byId('drop');['dragenter','dragover'].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add('dragging');}));['dragleave','drop'].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove('dragging');}));drop.addEventListener('drop',e=>{if(!byId('file').disabled)selectedFile(e.dataTransfer.files[0]);});
  byId('print').addEventListener('click',()=>{const windowRef=window.open('about:blank','_blank');if(windowRef)windowRef.opener=null;run(async()=>{try{await ensureSheet();if(windowRef)windowRef.location.href=current.print;else message('Allow popups to view the printable sheet.',true);await refreshRecords();}catch(error){if(windowRef)windowRef.close();throw error;}});});
  byId('manual').addEventListener('click',()=>run(async()=>{await ensureSheet();await upload();current=await post('manual');byId('compared').checked=false;renderScores();message('Enter every score from the original paper. Upload the original before confirmation.');await refreshRecords();}));
  byId('start').addEventListener('click',()=>run(async()=>{
    if(current?.verification_status==='verified'&&!window.confirm('Extract again? The verified draft will need a new review.'))return;
    await upload();message('Processing locally. Please keep this page open.');ocrRunning=true;controls();try{current=await post('process');}finally{ocrRunning=false;}snapshot=current.snapshot;dirty=false;renderScores();showPreview();message(current.error||'OCR completed. Compare every value with the original before verification.',!!current.error);await refreshRecords();
  }));
  byId('save').addEventListener('click',()=>run(async()=>{const values=scores();await ensureSheet();await upload();current=await post('save',{scores:JSON.stringify(values)});dirty=false;message('Draft saved. No official scores have been submitted.');await refreshRecords();}));
  byId('verify').addEventListener('click',()=>run(async()=>{
    if(!byId('compared').checked)throw Error('Compare every score with the original and check the verification statement.');
    const values=scores();await ensureSheet();if(file)throw Error('Upload the replacement original with Manual Entry before verifying.');
    current=await post('verify',{scores:JSON.stringify(values),reviewed:'true'});dirty=false;message('Scores verified. Review the confirmation details before submitting.');await refreshRecords();
  }));
  byId('compared').addEventListener('change',controls);
  const dialog=byId('confirm-dialog');byId('confirm').addEventListener('click',()=>{
    const details=byId('confirm-details');details.replaceChildren();
    Object.entries({Event:snapshot.event,Round:snapshot.round,Judge:snapshot.judge,Participants:snapshot.participants.length,'Completed scores':Object.keys(current.corrected).length}).forEach(([key,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=String(value);details.append(dt,dd);});dialog.showModal();
  });
  byId('cancel-confirm').addEventListener('click',()=>dialog.close());
  byId('submit-confirm').addEventListener('click',()=>run(async()=>{
    byId('submit-confirm').disabled=true;
    try{current=await post('confirm',{acknowledged:'true'});dirty=false;dialog.close();renderScores();message('Judge submission confirmed. It is now included in My Events and Round Management.');await refreshRecords();}finally{byId('submit-confirm').disabled=false;}
  }));
  byId('previous-page').addEventListener('click',()=>{if(previewPage>0){previewPage--;showPreview();}});byId('next-page').addEventListener('click',()=>{if(current&&previewPage<current.pages-1){previewPage++;showPreview();}});
  async function loadRecord(id){current=await request({sheet:id});snapshot=current.snapshot;event.value=snapshot.event_id;await chooseEvent(snapshot.round_id,snapshot.judge_id);dirty=false;file=null;byId('compared').checked=false;byId('sheet-id').textContent=`Sheet ${current.id}`;byId('filename').textContent=current.has_original?'Uploaded original':'No original uploaded';previewPage=0;renderScores();showPreview();message(current.error||(current.verification_status==='confirmed'?'Confirmed score sheet — view only.':'Review this score sheet.'));document.querySelector('.ocr-selection').scrollIntoView({behavior:'smooth'});}
  async function refreshRecords(){
    const params={mode:'records',page,q:byId('search').value};['event','round','judge','ocr','verification'].forEach(key=>{params[key]=document.getElementById(`filter-${key}`).value;});
    const data=await request(params);pages=data.pages;page=data.page;['round','judge'].forEach(key=>{const select=document.getElementById(`filter-${key}`),value=select.value;options(select,data[`${key}_options`],key==='round'?'All rounds':'All judges');select.value=value;});
    app.querySelectorAll('[data-count]').forEach(node=>node.textContent=data.summary[node.dataset.count]);
    const body=byId('records-body');body.replaceChildren();
    data.records.forEach((record,index)=>{const row=body.insertRow();[String((page-1)*10+index+1),record.judge,record.event,record.round,record.uploaded_at?new Date(record.uploaded_at).toLocaleString():'Not uploaded'].forEach(text=>{row.insertCell().textContent=text;});
      ['ocr','verification'].forEach(key=>{const span=document.createElement('span');span.className=`ocr-badge ${record[key]}`;span.textContent=record[key].replaceAll('_',' ');row.insertCell().append(span);});row.insertCell().textContent=record.method;
      const action=row.insertCell();const view=document.createElement('button');view.type='button';view.textContent=record.verification==='confirmed'?'View':'Review';view.addEventListener('click',()=>run(()=>loadRecord(record.id)));action.append(view);
      if(record.ocr==='failed'&&record.verification!=='confirmed'){const retry=document.createElement('button');retry.type='button';retry.textContent='Retry OCR';retry.addEventListener('click',()=>run(async()=>{await loadRecord(record.id);current=await post('process');snapshot=current.snapshot;renderScores();message(current.error||'Extraction completed. Review the results.',!!current.error);await refreshRecords();}));action.append(retry);}
    });
    if(!data.records.length){const cell=body.insertRow().insertCell();cell.colSpan=9;cell.textContent='No score sheets match your filters.';}
    document.getElementById('records-page').textContent=`Page ${page} of ${pages}`;document.getElementById('records-previous').disabled=page<=1;document.getElementById('records-next').disabled=page>=pages;return data;
  }
  byId('filters-toggle').addEventListener('click',()=>{const filters=document.querySelector('.ocr-filters');filters.hidden=!filters.hidden;byId('filters-toggle').setAttribute('aria-expanded',String(!filters.hidden));});
  byId('filter-apply').addEventListener('click',()=>run(async()=>{page=1;await refreshRecords();}));
  let timer;byId('search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>run(async()=>{page=1;await refreshRecords();}),350);});
  document.getElementById('records-previous').addEventListener('click',()=>run(async()=>{page--;await refreshRecords();}));document.getElementById('records-next').addEventListener('click',()=>run(async()=>{page++;await refreshRecords();}));
  run(async()=>{const data=await refreshRecords();options(event,data.events,'Select event');options(document.getElementById('filter-event'),data.events,'All events');if(!data.events.length)message('No criteria-based events are assigned to you.');});
})();
