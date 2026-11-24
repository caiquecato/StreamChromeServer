(function(){
 'use strict';
 const $=id=>document.getElementById(id);let current=null,timer=null,analyzing=false,analysisGeneration=0,analyzedUrl='',prepared=null;
 const message=text=>AppUI.text($('downloadStatus'),text);
 function stopPolling(){clearTimeout(timer);timer=null;}
 function render(job){
  current=job;try{localStorage.setItem('streamchrome.download.id',job.id);}catch{}const active=['downloading','processing'].includes(job.state);
  $('downloadDetails').hidden=false;AppUI.text($('downloadTitle'),job.title);
  $('downloadQualityField').hidden=job.state!=='ready';
  $('downloadQuality').disabled=active||job.state!=='ready';
  $('chooseDownloadFolder').disabled=active;
  if(job.state==='ready'&&!$('downloadQuality').options.length)$('downloadQuality').replaceChildren(AppUI.option('Best available','best'),...job.heights.map(height=>new Option(height+'p',String(height))));
  $('startDownload').hidden=job.state!=='ready';$('cancelDownload').hidden=!active;$('openDownload').hidden=job.state!=='complete';
  $('downloadProgress').hidden=!active;
  const progress=job.total>0?Math.min(100,job.downloaded/job.total*100):0;
  if(job.total>0)$('downloadProgress').value=progress;else $('downloadProgress').removeAttribute('value');
  if(job.state==='downloading')message(`${progress.toFixed(1)}% · ${(job.speed/1048576).toFixed(1)} MB/s`);
  else if(job.state==='processing')message('Preparing the downloaded video...');
  else if(job.state==='complete'){message('Download complete.');window.loadCatalog?.();}
  else if(job.state==='cancelled')message('Download cancelled.');
  else if(job.state==='failed')message(job.error);
 }
 async function poll(){
  if(!current)return;
  try{const job=await api('/api/download/status?id='+encodeURIComponent(current.id));render(job);if(['downloading','processing'].includes(job.state))timer=setTimeout(poll,1000);}
  catch(error){message(error.message);timer=setTimeout(poll,3000);}
 }
 window.prepareLinkDownload=function(url){
  if(url===analyzedUrl&&prepared)return prepared;
  analyzedUrl=url;const generation=++analysisGeneration;
  const keepActive=current&&['downloading','processing'].includes(current.state);
  analyzing=true;
  if(!keepActive){stopPolling();current=null;$('downloadDetails').hidden=true;message('Analyzing link...');}
  prepared=(async()=>{
   try{
    const job=await api('/api/download/analyze',{url});
    if(generation!==analysisGeneration)return null;
    if(!keepActive){
     $('downloadQuality').replaceChildren(AppUI.option('Best available','best'),...job.heights.map(height=>new Option(height+'p',String(height))));
     render(job);message('Choose a quality and download.');
    }
    return job;
   }catch(error){if(generation===analysisGeneration&&!keepActive)message(error.message);return null;}
   finally{if(generation===analysisGeneration)analyzing=false;}
  })();
  return prepared;
 };
 $('startDownload').addEventListener('click',async()=>{
  if(!current||current.state!=='ready')return;
  $('startDownload').disabled=true;
  try{render(await api('/api/download/start',{id:current.id,quality:$('downloadQuality').value}));stopPolling();timer=setTimeout(poll,250);}
  catch(error){message(error.message);}finally{$('startDownload').disabled=false;}
 });
 $('cancelDownload').addEventListener('click',async()=>{
  if(!current)return;$('cancelDownload').disabled=true;
  try{render(await api('/api/download/cancel',{id:current.id}));stopPolling();timer=setTimeout(poll,250);}
  catch(error){message(error.message);}finally{$('cancelDownload').disabled=false;}
 });
 $('openDownload').addEventListener('click',async()=>{
  if(current?.state!=='complete')return;
  $('openDownload').disabled=true;
  try{const media=await api('/api/download/open',{id:current.id});await loadDownloadedVideo(media.source,media.filename);}
  catch(error){message(error.message);}finally{$('openDownload').disabled=false;}
 });
 $('chooseDownloadFolder').addEventListener('click',async()=>{
  $('chooseDownloadFolder').disabled=true;
  try{const data=await api('/api/download/folder',{});AppUI.text($('downloadFolder'),data.folder);}
  catch(error){message(error.message);}finally{$('chooseDownloadFolder').disabled=false;}
 });
 try{const previous=localStorage.getItem('streamchrome.download.id');if(previous){current={id:previous};api('/api/download/status?id='+encodeURIComponent(previous)).then(job=>{if(analysisGeneration!==0)return;render(job);if(['downloading','processing'].includes(job.state))timer=setTimeout(poll,1000);}).catch(()=>{try{localStorage.removeItem('streamchrome.download.id');}catch{}});}}catch{}
 $('inputUrl').addEventListener('input',()=>{
  if(current&&['downloading','processing'].includes(current.state))return;
  analysisGeneration++;analyzing=false;analyzedUrl='';prepared=null;current=null;stopPolling();$('downloadDetails').hidden=true;message('');
  try{localStorage.removeItem('streamchrome.download.id');}catch{}
 });
 window.addEventListener('pagehide',stopPolling);
})();
