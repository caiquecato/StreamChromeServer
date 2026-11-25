(function(root){
 'use strict';
 function displayMetrics({sourceWidth,sourceHeight,width,height,pixelRatio=1}){
  if(!(sourceWidth>0&&sourceHeight>0&&width>0&&height>0))return null;
  const scale=Math.min(width/sourceWidth,height/sourceHeight)*pixelRatio;
  return {width:Math.round(sourceWidth*scale),height:Math.round(sourceHeight*scale),scale,upscaling:scale>1.001};
 }
 function splitRelease(label){
  const parts=String(label).split(' | ');
  return {provider:parts.length>1?parts.shift():'',release:parts.join(' | ')};
 }
 const api={displayMetrics,splitRelease};
 if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
 const document=root.document,$=id=>document.getElementById(id),video=$('mainVideo');
 const translate=text=>root.AppUI.translate(text);
 api.describeVideo=function(video){
  const rect=video.getBoundingClientRect();
  const size=displayMetrics({sourceWidth:video.videoWidth,sourceHeight:video.videoHeight,width:rect.width,height:rect.height,pixelRatio:root.devicePixelRatio||1});
  if(!size)return '';
  return `Displayed image: ${size.width} × ${size.height} physical px.`+' '+(size.upscaling?'The image is being enlarged.':'The image is not being enlarged; test fullscreen for VSR.');
 };
 root.PlayerPresentation=api;
 const select=$('searchResults'),choices=$('subtitleChoices');
 // The select remains the source of truth used by playback; radios only select a result.
 select.parentElement.hidden=true;
 function cell(className,text){const span=document.createElement('span');span.className=className;span.textContent=text;return span;}
 function syncSelection(){
  for(const radio of choices.querySelectorAll('input'))radio.checked=radio.value===select.value;
 }
 function renderChoices(){
  const options=Array.from(select.options).filter(option=>option.value);
  const rows=[];
  if(!options.length){const empty=document.createElement('p');empty.className='results-empty';empty.textContent=select.options[0]?.textContent||translate('Search to see available subtitles');rows.push(empty);}
  else {
   const heading=document.createElement('div');heading.className='result-heading';
   heading.append(cell('',''),cell('release-name',translate('Release / Title')),cell('release-provider',translate('Source')),cell('release-language',translate('Language')));rows.push(heading);
   for(const option of options){
    const row=document.createElement('label');row.className='subtitle-choice';
    const radio=document.createElement('input');radio.type='radio';radio.name='subtitle-release';radio.value=option.value;radio.checked=option.value===select.value;
    radio.addEventListener('change',()=>{select.value=radio.value;select.dispatchEvent(new Event('change',{bubbles:true}));syncSelection();});
    const release=splitRelease(option.textContent);
    const languageCode=option.getAttribute('data-language');
    const language=Array.from($('searchLanguage').options).find(item=>item.value===languageCode)?.textContent||languageCode||'';
    row.append(radio,cell('release-name',release.release),cell('release-provider',release.provider),cell('release-language',language));rows.push(row);
   }
  }
  choices.replaceChildren(...rows);
 }
 new MutationObserver(renderChoices).observe(select,{childList:true,subtree:true,characterData:true});
 select.addEventListener('change',syncSelection);renderChoices();
 let title='',filename='',previousLayout='',mediaMetadata={};
 function updateTitles(data){
  if(data){
   mediaMetadata=data;
   filename=data.filename||'';
   title=data.suggestion?.title||filename.replace(/\.[^.]+$/,'');
   if(data.suggestion?.year)title+=' ('+data.suggestion.year+')';
  }
  $('headerMediaTitle').textContent=title;$('headerMediaTitle').title=filename;
  if(filename){$('sessionTitle').textContent=title;$('sessionTitle').title=filename;}
  if(document.documentElement.dataset.layout==='library'&&filename){root.AppUI.text($('mediaTitleBar'),title);$('sessionCodec').textContent=filename;}
  else if(filename)root.AppUI.text($('mediaTitleBar'),filename);
 }
 function updateVideoGeometry(){
  const rect=video.getBoundingClientRect();
  const size=displayMetrics({sourceWidth:video.videoWidth,sourceHeight:video.videoHeight,width:rect.width,height:rect.height});
  if(size)$('playerWrapper').style.setProperty('--caption-base',Math.max(17,Math.min(34,size.width*.025))+'px');
  if(root.showVideoResolution)root.showVideoResolution();
 }
 function updateLayout(){
  const layout=document.documentElement.dataset.layout;
  const volume=document.querySelector('.vol-group');
  if(layout==='glass')document.querySelector('.controls-right').prepend(volume);
  else document.querySelector('.controls-left').insertBefore(volume,$('timeBox'));
  if(layout!==previousLayout){
   previousLayout=layout;
   if(layout==='studio')root.setSidePanel('subtitle');
   else document.body.dataset.panelOpen='false';
  }
  updateTitles();
  if(root.showVideoResolution)root.showVideoResolution();
 }
 root.addEventListener('streamchrome:media',event=>{updateTitles(event.detail);$('sessionThumbnail').hidden=true;});
 root.addEventListener('streamchrome:preferences',()=>{updateLayout();renderChoices();});
 video.addEventListener('loadeddata',()=>{
  if(!video.videoWidth||!video.videoHeight)return;
  // A single small session thumbnail; the visible video always stays in its native video element.
  try{const canvas=document.createElement('canvas');canvas.width=320;canvas.height=Math.round(320*video.videoHeight/video.videoWidth);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);$('sessionThumbnail').src=canvas.toDataURL('image/jpeg',.75);$('sessionThumbnail').hidden=false;}catch{}
 });
 new ResizeObserver(updateVideoGeometry).observe(video);
 document.addEventListener('fullscreenchange',updateVideoGeometry);
 video.addEventListener('loadedmetadata',updateVideoGeometry);
 const information=document.querySelector('.playback-information');
 const statistics=document.createElement('dl');statistics.className='playback-statistics';
 const close=document.createElement('button');close.className='statistics-close';close.textContent='×';close.setAttribute('aria-label','Close');close.addEventListener('click',()=>{information.open=false;});
 information.append(close,statistics);
 function updateStatistics(){
  if(!information.open)return;
  const rect=video.getBoundingClientRect(),quality=video.getVideoPlaybackQuality?.();
  const display=displayMetrics({sourceWidth:video.videoWidth,sourceHeight:video.videoHeight,width:rect.width,height:rect.height,pixelRatio:root.devicePixelRatio||1});
  let buffer=0,bufferedEnd=0;for(let i=0;i<video.buffered.length;i++){const s=video.buffered.start(i),e=video.buffered.end(i);if(e>bufferedEnd)bufferedEnd=e;if(s<=video.currentTime&&e>=video.currentTime)buffer=e-video.currentTime;}
  const dur=Number.isFinite(video.duration)&&video.duration>0?video.duration:Number(mediaMetadata.duration||0);
  const pct=dur>0?Math.min(100,(bufferedEnd/dur)*100):0;
  const bufText=dur>0?`${buffer.toFixed(1)} s (${pct.toFixed(1)}% · ${Math.round(bufferedEnd)}s / ${Math.round(dur)}s)`:`${buffer.toFixed(2)} s`;
  const netNames=['Empty','Idle','Loading data','Source error'];
  const conn=root.navigator?.connection;
  const dlSpeed=conn&&Number.isFinite(conn.downlink)?` · ${conn.downlink} Mbps`:'';
  const netText=`${translate(netNames[video.networkState]||String(video.networkState))}${dlSpeed}`;
  const rows=[['Viewport / Scale',`${Math.round(rect.width)} × ${Math.round(rect.height)} / ${(root.devicePixelRatio||1).toFixed(2)}`],['Source resolution',`${video.videoWidth} × ${video.videoHeight}`],['Displayed image',display?`${display.width} × ${display.height} px`:'—'],['Frames',quality?`${quality.droppedVideoFrames} dropped / ${quality.totalVideoFrames} total`:'Unavailable'],['Volume',`${Math.round(video.volume*100)}%${video.muted?' · Muted':''}`],['Video codec',String(mediaMetadata.video_codec||'—').toUpperCase()],['Loaded / Buffer',bufText],['Network',netText],['Playback rate',`${video.playbackRate}×`],['Media time',`${video.currentTime.toFixed(2)} s`],['Ready / Network',`${video.readyState} / ${video.networkState}`],['RTX VSR / HDR','Check NVIDIA indicator'],['Date',new Date().toLocaleString()]];
  statistics.replaceChildren(...rows.flatMap(([label,value])=>{const term=document.createElement('dt'),definition=document.createElement('dd');term.textContent=translate(label);definition.textContent=value;return [term,definition];}));
 }
 let statisticsTimer=null;
 information.addEventListener('toggle',()=>{clearInterval(statisticsTimer);statisticsTimer=null;if(information.open){updateStatistics();statisticsTimer=setInterval(updateStatistics,1000);}});
 root.addEventListener('pagehide',()=>clearInterval(statisticsTimer));
 updateLayout();
})(typeof window!=='undefined'?window:globalThis);
