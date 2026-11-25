const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{listeners:{},dataset:{},addEventListener(name,fn){this.listeners[name]=fn;}});return nodes.get(id);}
let poll,reloads=0,serverVersion='1.1.0';
const window={AppUI:{translate:s=>s},location:{reload(){reloads++;}}};
const context={window,document:{readyState:'complete',getElementById:node,querySelectorAll:()=>[]},
 fetch:async path=>({ok:true,json:async()=>path==='/api/updates/apply'?{restarting:true,latest_version:'1.2.0'}:{version:serverVersion}}),
 setInterval(fn){poll=fn;return 1;},clearInterval(){},setTimeout(){}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../assets/settings.js'),'utf8'),context);
(async()=>{
 await node('applyUpdate').listeners.click();
 await poll();assert.equal(reloads,0,'Do not reload while the old server is still shutting down');
 serverVersion='1.2.0';await poll();assert.equal(reloads,1,'Reload after the expected updated version is serving');
 console.log('Settings update waits for the updated server before reloading.');
})().catch(error=>{console.error(error);process.exitCode=1;});
