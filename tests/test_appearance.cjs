const assert=require('node:assert/strict');
const {normalizePreferences,readPreferences,createController}=require('../assets/appearance.js');
const memory=new Map();
const storage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,value)};
assert.deepEqual(normalizePreferences({}),{layout:'cinema',theme:'dark',language:'en'});
assert.deepEqual(normalizePreferences({layout:'bad',theme:'bad',language:'bad',apiKey:'secret'}),{layout:'cinema',theme:'dark',language:'en'});
assert.deepEqual(readPreferences({getItem(){throw new Error('blocked');}}),normalizePreferences({}));
const video={src:'/stream?url=local:token',currentTime:582.5};
const caption={textContent:'A selected movie caption'};
const document={documentElement:{dataset:{}},body:{dataset:{}},querySelectorAll(){return[];},getElementById(id){return id==='mainVideo'?video:id==='subtitleText'?caption:null;},dispatchEvent(){},addEventListener(){}};
const ui=createController({document,storage,i18n:{translate:text=>text}});
ui.init();
for(const layout of ['neutral','cinema','classic','studio','library','glass']){
 for(const theme of ['dark','light']){
  ui.update({layout,theme,language:'es'});
  assert.equal(document.documentElement.dataset.layout,layout);
  assert.equal(document.documentElement.dataset.theme,theme);
  assert.equal(document.documentElement.lang,'es');
  assert.equal(video.src,'/stream?url=local:token');assert.equal(video.currentTime,582.5);
  assert.equal(caption.textContent,'A selected movie caption');
 }
}
assert.deepEqual(readPreferences(storage),{layout:'glass',theme:'light',language:'es'});
assert.equal(memory.size,1);
assert.ok(![...memory.values()][0].includes('secret'));
console.log('Five layouts, two themes, persistence, English defaults and playback preservation verified.');
const AudioOption=class{constructor(text,value){this.textContent=text;this.value=value;this.attrs={};}setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}getAttribute(k){return this.attrs[k];}};
const audioDoc={...document,querySelectorAll(selector){return selector==='[data-i18n-audio]'&&this.audio?[this.audio]:[];}};
const audioUI=createController({document:audioDoc,storage:null,i18n:require('../assets/i18n.js'),optionConstructor:AudioOption});
audioUI.init();audioDoc.audio=audioUI.audioOption('Japanese (AAC) Dubbed',1);
audioUI.update({language:'pt'});assert.equal(audioDoc.audio.textContent,'Japonês (AAC) Dubbed');
audioUI.update({language:'es'});assert.equal(audioDoc.audio.textContent,'Japonés (AAC) Dubbed');
assert.equal(video.src,'/stream?url=local:token');
console.log('Audio language labels translated without altering codec or release metadata.');
